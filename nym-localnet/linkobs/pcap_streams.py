#!/usr/bin/env python3
"""Turn a packet capture of client<->gateway links into per-cell events, the way a network observer
(ISP, hotspot, backbone tap) would see them: no gateway cooperation, no Sphinx-level log.

Input:  a pcap written by tcpdump (loopback DLT_NULL, Ethernet, or raw IP), covering the TCP
        connections between Nym clients and a gateway's client WebSocket port.
Output: CSV rows  conn,direction,t_ns,len,kind   where
        conn       = "<client ip>:<client port>" (one Nym client's WebSocket connection)
        direction  = to_gw (client -> gateway) | from_gw (gateway -> client)
        t_ns       = capture timestamp of the TCP segment that completed the frame
        len        = WebSocket frame payload length (bytes); for an encrypted stream, the TCP
                     segment payload length (coalesced records are split later by size)
        kind       = ws (one WebSocket data frame) | seg (one TCP segment; --tls mode)

Plain ws:// (the localnet default) is parsed exactly: TCP streams are reassembled per direction
and WebSocket data frames are delimited, so every frame is one gateway message carrying one
Sphinx packet (plus a few bytes of framing).  With --tls the stream is opaque, so each TCP segment
is one event and link_export.py decomposes segment sizes into cells.

With --identities, the client identity key that each plain ws:// connection sends in its handshake
(authenticateV2 / handshakePayload, a JSON text frame) is written as conn,identity (base58, as in a
Nym client address), which labels a connection with its client without any client-side log.

Only the standard library is used; a 240 s run with 30 clients (~2 M segments) parses in ~1 min.
"""
import argparse, json, struct, sys, csv
from collections import defaultdict

DLT_NULL, DLT_EN10MB, DLT_RAW, DLT_LOOP, DLT_LINUX_SLL = 0, 1, 12, 108, 113


def pcap_records(path):
    """Yield (t_ns, link_type, bytes) for each record of a pcap (not pcapng) file."""
    with open(path, 'rb') as f:
        gh = f.read(24)
        magic = struct.unpack('<I', gh[:4])[0]
        if magic == 0xa1b2c3d4:
            endian, nano = '<', False
        elif magic == 0xd4c3b2a1:
            endian, nano = '>', False
        elif magic == 0xa1b23c4d:
            endian, nano = '<', True
        elif magic == 0x4d3cb2a1:
            endian, nano = '>', True
        else:
            raise SystemExit(f'{path}: not a pcap file (magic {magic:#x}); pcapng is not supported, use tcpdump -w')
        link = struct.unpack(endian + 'I', gh[20:24])[0]
        while True:
            rh = f.read(16)
            if len(rh) < 16:
                return
            ts_sec, ts_frac, incl, _orig = struct.unpack(endian + 'IIII', rh)
            data = f.read(incl)
            if len(data) < incl:
                return
            t_ns = ts_sec * 1_000_000_000 + (ts_frac if nano else ts_frac * 1000)
            yield t_ns, link, data


def ip_payload(link, data):
    """Strip the link layer; return the IPv4 packet bytes or None."""
    if link == DLT_NULL or link == DLT_LOOP:
        fam = struct.unpack('=I', data[:4])[0] if link == DLT_NULL else struct.unpack('>I', data[:4])[0]
        return data[4:] if fam in (2, 0x02000000) else None       # AF_INET (either byte order)
    if link == DLT_EN10MB:
        return data[14:] if data[12:14] == b'\x08\x00' else None
    if link == DLT_LINUX_SLL:
        return data[16:] if data[14:16] == b'\x08\x00' else None
    if link == DLT_RAW:
        return data
    return None


def tcp_fields(ip):
    """(src, sport, dst, dport, seq, flags, payload) for an IPv4/TCP packet, else None."""
    if len(ip) < 20 or ip[0] >> 4 != 4 or ip[9] != 6:
        return None
    ihl = (ip[0] & 0x0f) * 4
    total = struct.unpack('>H', ip[2:4])[0]
    src = '.'.join(map(str, ip[12:16])); dst = '.'.join(map(str, ip[16:20]))
    tcp = ip[ihl:total] if total >= ihl else ip[ihl:]
    if len(tcp) < 20:
        return None
    sport, dport, seq, _ack, off_flags = struct.unpack('>HHIIH', tcp[:14])
    doff = (off_flags >> 12) * 4
    return src, sport, dst, dport, seq, off_flags & 0x1ff, tcp[doff:]


B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def b58(raw):
    n, s = int.from_bytes(raw, 'big'), ''
    while n:
        n, r = divmod(n, 58); s = B58[r] + s
    return '1' * (len(raw) - len(raw.lstrip(b'\0'))) + s


def client_identity(frame):
    """The client's ed25519 identity key (base58) from a masked text frame of the gateway handshake:
    authenticateV2 carries it as clientIdentity, the registration's handshakePayload as the first
    32 bytes of data. None for any other frame."""
    key, data = frame[:4], frame[4:]
    try:
        msg = json.loads(bytes(c ^ key[k % 4] for k, c in enumerate(data)))
    except ValueError:
        return None
    if msg.get('type') == 'authenticateV2':
        raw = msg.get('clientIdentity')
    elif msg.get('type') == 'handshakePayload':
        raw = msg.get('data', [])[:32]
    else:
        return None
    return b58(bytes(raw)) if raw and len(raw) == 32 else None


class Stream:
    """One direction of one TCP connection: in-order byte stream with per-byte timestamps, plus a
    WebSocket frame parser that skips the HTTP upgrade and emits one event per data frame."""

    def __init__(self):
        self.next_seq = None
        self.base = None
        self.pending = {}          # seq -> (t_ns, payload) for out-of-order segments
        self.buf = bytearray()
        self.times = []            # (end_offset_in_buf, t_ns) of each appended segment
        self.consumed = 0          # bytes of buf already parsed (kept small by trimming)
        self.upgraded = False
        self.events = []           # (t_ns, len, opcode)
        self.identity = None       # client identity key (base58) read from the handshake, to_gw only

    def add(self, seq, t_ns, payload, syn):
        # TCP sequence numbers are 32-bit and wrap: work with offsets relative to the first seen
        # sequence number, unwrapped against the expected position (a stream may start anywhere
        # in the 32-bit space; one wrap in ~4 GB of data is handled, as are several)
        if self.base is None:
            self.base = seq
        rel = (seq - self.base) % (1 << 32)
        if self.next_seq is not None:
            # choose the unwrapped value closest to the expected position
            k = (self.next_seq - rel + (1 << 31)) >> 32
            rel += k << 32
        seq = rel
        if syn:
            self.next_seq = seq + 1
            return
        if not payload:
            return
        if self.next_seq is None:
            self.next_seq = seq
        if seq < self.next_seq:                   # retransmission / overlap
            cut = self.next_seq - seq
            if cut >= len(payload):
                return
            payload, seq = payload[cut:], self.next_seq
        if seq > self.next_seq:
            self.pending[seq] = (t_ns, payload)
            return
        self._append(t_ns, payload)
        while self.next_seq in self.pending:
            t2, p2 = self.pending.pop(self.next_seq)
            self._append(t2, p2)

    def _append(self, t_ns, payload):
        self.buf += payload
        self.next_seq += len(payload)
        self.times.append((len(self.buf), t_ns))
        self._parse()

    def _time_at(self, end_offset):
        # timestamp of the segment that delivered byte end_offset-1
        for end, t in self.times:
            if end >= end_offset:
                return t
        return self.times[-1][1]

    def _trim(self):
        if self.consumed > 1 << 20:
            del self.buf[:self.consumed]
            self.times = [(e - self.consumed, t) for e, t in self.times if e > self.consumed]
            self.consumed = 0

    def _parse(self):
        b = self.buf; i = self.consumed
        if not self.upgraded:
            j = b.find(b'\r\n\r\n', i)
            if j < 0:
                return
            i = j + 4; self.upgraded = True
        while True:
            if len(b) - i < 2:
                break
            b0, b1 = b[i], b[i + 1]
            opcode, masked, ln = b0 & 0x0f, b1 >> 7, b1 & 0x7f
            hdr = 2
            if ln == 126:
                if len(b) - i < 4: break
                ln = struct.unpack('>H', b[i + 2:i + 4])[0]; hdr = 4
            elif ln == 127:
                if len(b) - i < 10: break
                ln = struct.unpack('>Q', b[i + 2:i + 10])[0]; hdr = 10
            if masked:
                hdr += 4
            if len(b) - i < hdr + ln:
                break
            if opcode in (1, 2):                      # text / binary data frame
                self.events.append((self._time_at(i + hdr + ln), ln, opcode))
            if opcode == 1 and masked and self.identity is None:
                self.identity = client_identity(b[i + hdr - 4:i + hdr + ln])
            i += hdr + ln
        self.consumed = i
        self._trim()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pcap')
    ap.add_argument('--gateway-ports', default='9100,9101', help='gateway WebSocket ports as seen on the captured link')
    ap.add_argument('--tls', action='store_true', help='encrypted stream: emit one event per TCP segment instead of per WebSocket frame')
    ap.add_argument('--conn-prefix', default='', help='prefix for connection ids (e.g. the gateway name when merging captures)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--identities', help='also write conn,identity (the client identity key from the plain ws:// handshake) to this CSV')
    a = ap.parse_args()
    gw_ports = {int(p) for p in a.gateway_ports.split(',')}
    streams = defaultdict(Stream)        # (conn, direction) -> Stream
    seg_events = []
    n = 0
    for t_ns, link, data in pcap_records(a.pcap):
        ip = ip_payload(link, data)
        if ip is None:
            continue
        f = tcp_fields(ip)
        if f is None:
            continue
        src, sport, dst, dport, seq, flags, payload = f
        if dport in gw_ports:
            conn, direction = f'{a.conn_prefix}{src}:{sport}', 'to_gw'
        elif sport in gw_ports:
            conn, direction = f'{a.conn_prefix}{dst}:{dport}', 'from_gw'
        else:
            continue
        n += 1
        if a.tls:
            if payload:
                seg_events.append((conn, direction, t_ns, len(payload)))
        else:
            streams[(conn, direction)].add(seq, t_ns, payload, bool(flags & 0x02))
    with open(a.out, 'w', newline='') as fh:
        w = csv.writer(fh); w.writerow(['conn', 'direction', 't_ns', 'len', 'kind'])
        if a.tls:
            for conn, direction, t_ns, ln in seg_events:
                w.writerow([conn, direction, t_ns, ln, 'seg'])
            print(f'{n} TCP segments, {len(seg_events)} with payload -> {a.out}', file=sys.stderr)
        else:
            total = 0
            for (conn, direction), s in streams.items():
                for t_ns, ln, op in s.events:
                    w.writerow([conn, direction, t_ns, ln, 'ws']); total += 1
            print(f'{n} TCP segments, {len(streams)//2} connections, {total} WebSocket data frames -> {a.out}', file=sys.stderr)
    if a.identities and not a.tls:
        ids = [(conn, s.identity) for (conn, d), s in streams.items() if d == 'to_gw' and s.identity]
        with open(a.identities, 'w', newline='') as fh:
            w = csv.writer(fh); w.writerow(['conn', 'identity']); w.writerows(ids)
        print(f'{len(ids)} connections with a client identity -> {a.identities}', file=sys.stderr)


if __name__ == '__main__':
    main()
