"""Application handoff presence checks; run app.replay for validated replay."""

import argparse
from pathlib import Path
from typing import Sequence

from app.preflight import missing_p0_inputs


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser(
        "check-inputs", help="Check presence of required P0 handoff files only."
    )
    check.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent,
        help="Project root (defaults to the directory containing app/).",
    )
    args = parser.parse_args(argv)
    missing = missing_p0_inputs(args.root)
    if missing:
        print("P0 blocked: required handoff files are missing:")
        for path in missing:
            print(f"  {path.as_posix()}")
        print("Replay is unavailable until the required original files are present.")
        return 2
    print("Required document paths are present.")
    print("This checks presence only, not contents, fixtures, or P0 acceptance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
