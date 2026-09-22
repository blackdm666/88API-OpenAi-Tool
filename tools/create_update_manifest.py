#!/usr/bin/env python3
"""Create the HTTPS cloud-update manifest for a packaged EXE."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 88API 桌面程序更新清单")
    parser.add_argument("--version", required=True)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--download-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--release-url", default="")
    parser.add_argument("--notes", action="append", default=[])
    parser.add_argument("--mandatory", action="store_true")
    args = parser.parse_args()

    exe = args.exe.expanduser().resolve()
    if not exe.is_file() or exe.suffix.lower() != ".exe":
        raise SystemExit(f"EXE 不存在或扩展名无效：{exe}")
    digest = hashlib.sha256()
    size = 0
    with exe.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)

    payload = {
        "schema": 1,
        "channel": "stable",
        "version": args.version,
        "download_url": args.download_url,
        "release_url": args.release_url,
        "sha256": digest.hexdigest(),
        "size": size,
        "notes": args.notes,
        "mandatory": bool(args.mandatory),
    }
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成更新清单: {output}")
    print(f"SHA-256: {payload['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
