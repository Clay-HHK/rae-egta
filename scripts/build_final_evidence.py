#!/usr/bin/env python3
"""CLI wrapper for the immutable final RAE/EGTA evidence bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from rae_egta.egta.final_evidence import build_final_bundle


def build_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=repo_root / "audit/final",
    )
    parser.add_argument(
        "--noise-inference-root",
        type=Path,
        default=(
            repo_root
            / "data/noise_selection"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = build_final_bundle(
        repo_root=args.repo_root,
        output_root=args.output_root,
        noise_inference_root=args.noise_inference_root,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "output_root": manifest["output_root"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
