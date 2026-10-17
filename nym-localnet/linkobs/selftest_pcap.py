#!/usr/bin/env python3
"""Self-test for pcap_streams.py without a capture: writes a synthetic loopback pcap with one
client<->gateway WebSocket connection (handshake, then masked client frames and unmasked server
frames, some split across TCP segments, one pair coalesced into a single segment, one segment
delivered out of order) and checks that the parser recovers every frame with the right size,
direction and timestamp."""
import os, struct, subprocess, sys, tempfile, csv

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT, GW = ('127.0.0.1', 51000), ('127.0.0.1', 9100)


def ipv4_tcp(src, dst, seq, flags, payload):
    tcp = struct.pack('>HHIIHHHH', src[1], dst[1], seq % (1 << 32), 0, (5 << 12) | flags, 65535, 0, 0) + payload
    total = 20 + len(tcp)
    ip = struct.pack('>BBHHHBBH4s4s', 0x45, 0, total, 0, 0, 64, 6, 0,
                     bytes(map(int, src[0].split('.'))), bytes(map(int, dst[0].split('.')))) + tcp
    return struct.pack('=I', 2) + ip          # DLT_NULL: AF_INET family header (host order)


def ws_frame(payload, masked):
    n = len(payload)
    hdr = bytes([0x82]) + (bytes([(0x80 if masked else 0) | 126]) + struct.pack('>H', n) if n >= 126 else bytes([(0x80 if masked else 0) | n]))
    if masked:
        key = b'\x01\x02\x03\x04'
        return hdr + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return hdr + payload


def main():
    recs = []                                    # (t_us, bytes)
    t = 1_700_000_000_000_000
    c_seq, g_seq = (1 << 32) - 3000, 5000      # client ISN just below 2^32: its stream wraps
    def add(src, dst, seq, flags, payload, dt_us):
        nonlocal t
        t += dt_us; recs.append((t, ipv4_tcp(src, dst, seq, flags, payload)))
    add(CLIENT, GW, c_seq, 0x02, b'', 0); c_seq += 1                                   # SYN
    add(GW, CLIENT, g_seq, 0x12, b'', 100); g_seq += 1                                 # SYN/ACK
    req = b'GET / HTTP/1.1\r\nUpgrade: websocket\r\n\r\n'; add(CLIENT, GW, c_seq, 0x18, req, 100); c_seq += len(req)
    rsp = b'HTTP/1.1 101 Switching Protocols\r\n\r\n'; add(GW, CLIENT, g_seq, 0x18, rsp, 100); g_seq += len(rsp)
    expected = []                                 # (direction, len, t_us)
    # client -> gateway: three 2413-byte frames, the second split over two segments
    for i in range(3):
        f = ws_frame(bytes([i]) * 2413, True)
        if i == 1:
            add(CLIENT, GW, c_seq, 0x18, f[:1000], 1000); c_seq += 1000
            add(CLIENT, GW, c_seq, 0x18, f[1000:], 500); c_seq += len(f) - 1000
        else:
            add(CLIENT, GW, c_seq, 0x18, f, 1000); c_seq += len(f)
        expected.append(('to_gw', 2413, t))
    # gateway -> client: a 2413 payload frame and a 386 ack frame coalesced in ONE segment, then an
    # ack frame delivered before the preceding payload frame (out of order)
    f1, f2 = ws_frame(b'\x10' * 2413, False), ws_frame(b'\x11' * 386, False)
    add(GW, CLIENT, g_seq, 0x18, f1 + f2, 2000); g_seq += len(f1) + len(f2)
    expected += [('from_gw', 2413, t), ('from_gw', 386, t)]
    f3, f4 = ws_frame(b'\x12' * 2413, False), ws_frame(b'\x13' * 386, False)
    add(GW, CLIENT, g_seq + len(f3), 0x18, f4, 1000)            # f4 arrives first (seq after f3)
    t_f4 = t
    add(GW, CLIENT, g_seq, 0x18, f3, 300); g_seq += len(f3) + len(f4)
    # f4 can only be parsed once f3 has filled the gap, but its bytes crossed the wire at t_f4:
    # a network observer timestamps what it saw on the link, so f4 keeps its own capture time
    expected += [('from_gw', 2413, t), ('from_gw', 386, t_f4)]
    with tempfile.TemporaryDirectory() as d:
        pcap = os.path.join(d, 't.pcap'); out = os.path.join(d, 'ev.csv')
        with open(pcap, 'wb') as fh:
            fh.write(struct.pack('<IHHiIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 0))
            for ts, b in recs:
                fh.write(struct.pack('<IIII', ts // 1_000_000, ts % 1_000_000, len(b), len(b)) + b)
        subprocess.run([sys.executable, os.path.join(HERE, 'pcap_streams.py'), pcap, '--out', out], check=True, capture_output=True)
        got = [(r['direction'], int(r['len']), int(r['t_ns']) // 1000) for r in csv.DictReader(open(out))]
    got.sort(key=lambda x: (x[0] != 'to_gw', x[2], -x[1])); expected.sort(key=lambda x: (x[0] != 'to_gw', x[2], -x[1]))
    ok = got == expected
    print('expected:', expected); print('got     :', got)
    print('SELFTEST', 'PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
