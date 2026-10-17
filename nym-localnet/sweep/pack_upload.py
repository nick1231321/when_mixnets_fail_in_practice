#!/usr/bin/env python3
"""Pack the finished runs of a collection that were not packed yet into zip files for uploading
(e.g. to OneDrive by hand). Nothing is deleted: a run gets a PACKED marker naming its zip, and the
next call only packs newer runs. Delete a zip yourself once it is uploaded.

Each zip holds <collection>/runs/<run>/... for its runs plus the collection's current plan.csv,
manifest.csv and README.md, so every part is self-describing. Already compressed files (.gz) and
pcaps (encrypted traffic, incompressible) are stored as they are; the rest is deflated.

  pack_upload.py --collection ~/flow-matching-data/2026-10-17-hetero --out ~/flow-matching-data/upload [--max-gb 4]
"""
import argparse, zipfile
from datetime import datetime
from pathlib import Path

STORED = ('.gz', '.pcap', '.zip')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--collection', required=True, type=Path, help='the folder with plan.csv and main/runs/')
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--max-gb', type=float, default=4.0, help='start a new zip above this size')
    a = ap.parse_args()
    col = a.collection.expanduser().resolve(); out = a.out.expanduser(); out.mkdir(parents=True, exist_ok=True)
    runs = sorted(d.parent for d in col.glob('*/runs/*/DONE') if not (d.parent / 'PACKED').exists())
    if not runs:
        print('nothing new to pack'); return
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    part, zf, size, packed, parts = 0, None, 0, 0, []
    for run in runs:
        files = [f for f in run.rglob('*') if f.is_file() and f.name != 'PACKED']
        run_size = sum(f.stat().st_size for f in files)
        if zf is None or size + run_size > a.max_gb * 1e9:
            if zf:
                zf.close()
            part += 1
            name = out / f'{col.name}_{stamp}_part{part:02d}.zip'
            zf = zipfile.ZipFile(name, 'w'); size = 0; parts.append(name)
            for extra in ('plan.csv', 'README.md', *[str(m.relative_to(col)) for m in col.glob('*/manifest.csv')]):
                if (col / extra).exists():
                    zf.write(col / extra, f'{col.name}/{extra}', zipfile.ZIP_DEFLATED)
        for f in files:
            method = zipfile.ZIP_STORED if f.suffix in STORED else zipfile.ZIP_DEFLATED
            zf.write(f, f'{col.name}/{f.relative_to(col)}', method)
        (run / 'PACKED').write_text(Path(zf.filename).name)
        size += run_size; packed += 1
    zf.close()
    for p in parts:
        print(f'{p}  {p.stat().st_size / 1e9:.2f} GB')
    print(f'packed {packed} runs into {len(parts)} zip(s); upload them, then delete them')


if __name__ == '__main__':
    main()
