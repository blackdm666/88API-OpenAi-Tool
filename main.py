#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from token_manager.constants import APP_VERSION
from token_manager.gui import run_app
from token_manager.updater import UpdateError, apply_update


def main() -> None:
    parser = argparse.ArgumentParser(description="88API号池自动维护工具")
    parser.add_argument("--version", action="version", version=f"v{APP_VERSION}")
    parser.add_argument("--apply-update", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--update-package", default="", help=argparse.SUPPRESS)
    parser.add_argument("--update-target", default="", help=argparse.SUPPRESS)
    parser.add_argument("--update-pid", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("command", nargs="?", default="gui", choices=["gui"])
    args = parser.parse_args()
    if args.apply_update:
        try:
            return_code = apply_update(
                args.update_package,
                args.update_target,
                parent_pid=args.update_pid,
            )
        except UpdateError as exc:
            print(f"更新失败：{exc}", file=sys.stderr)
            return_code = 1
        raise SystemExit(return_code)
    run_app()


if __name__ == "__main__":
    main()
