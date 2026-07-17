#!/usr/bin/env python3
"""只读探测 hive.obs.v1 是否在变、头结构如何（证伪/补证裁判字段）。"""

from __future__ import annotations

import argparse
import os
import struct
import time
from pathlib import Path


def default_obs_path() -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    return Path(local) / "RobotBridgeDemo" / "Intermediate" / "HiveShm" / "hive.obs.v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=default_obs_path())
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()
    path: Path = args.path
    if not path.is_file():
        print(f"missing {path}")
        return 2
    data = path.read_bytes()
    print(f"path={path} size={len(data)}")
    if len(data) < 16 or data[:4] != b"HIVE":
        print("not HIVE magic")
        return 3
    ver_a, ver_b = struct.unpack_from("<HH", data, 4)
    print(f"magic=HIVE header_u16=({ver_a},{ver_b})")
    print("first 64 bytes:", data[:64].hex(" "))
    prev = data
    t0 = time.time()
    changes = 0
    while time.time() - t0 < args.seconds:
        time.sleep(0.2)
        cur = path.read_bytes()
        if cur != prev:
            changes += 1
            diff = sum(1 for a, b in zip(cur, prev) if a != b) + abs(len(cur) - len(prev))
            print(f"change#{changes} byte_diff~{diff}")
            prev = cur
    print(f"done changes={changes} in {args.seconds}s")
    print("NOTE: presence of changes != Health/Heat fields. Map via in-match diffs before using as HUD source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
