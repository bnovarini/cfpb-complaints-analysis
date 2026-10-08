"""Command line: build the Parquet files from CFPB's sources, or check an existing set."""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cfpb-complaints")
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("command", choices=["build", "info"])
    a = ap.parse_args(argv)
    if a.command == "build":
        from .build import build
        build(a.data)
    else:
        import os
        os.environ["CFPB_DATA_DIR"] = str(a.data / "parquet")
        from .mcp_server import dataset_info
        import json
        print(json.dumps(dataset_info(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
