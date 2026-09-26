#!/usr/bin/env python3
"""Normalize Bambu Studio CLI diagnostics into stable validation categories."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


RULES = [
    (
        "FLOATING_REGION",
        re.compile(
            r"floating\s+(?:cantilever|region|part|object)|unsupported\s+region",
            re.I,
        ),
    ),
    (
        "EMPTY_LAYERS",
        re.compile(
            r"empty\s+layers?|no\s+printable|nothing\s+to\s+print|zero\s+layers",
            re.I,
        ),
    ),
    (
        "OUTSIDE_BUILD_VOLUME",
        re.compile(
            r"outside.*(?:build|printable).*volume|"
            r"exceeds.*(?:build|printable).*volume",
            re.I,
        ),
    ),
    (
        "PROFILE_MISMATCH",
        re.compile(
            r"profile.*(?:mismatch|incompatible)|"
            r"not\s+compatible\s+with.*printer",
            re.I,
        ),
    ),
    (
        "INVALID_GEOMETRY",
        re.compile(
            r"invalid\s+geometry|non[- ]manifold|self[- ]intersect",
            re.I,
        ),
    ),
]

NEGATIVE_RETURN = re.compile(
    r"\breturn_code\b[^\n]*-[0-9]+|"
    r"run found error,\s*return\s+-[0-9]+",
    re.I,
)

FATAL = {
    "FLOATING_REGION",
    "EMPTY_LAYERS",
    "OUTSIDE_BUILD_VOLUME",
    "PROFILE_MISMATCH",
    "INVALID_GEOMETRY",
    "SLICER_ERROR",
    "MISSING_OUTPUT",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slicer-exit", type=int, default=0)
    parser.add_argument("--artifact", type=Path)
    args = parser.parse_args()

    text = (
        args.log.read_text(encoding="utf-8", errors="replace")
        if args.log.exists()
        else ""
    )
    categories: list[str] = []

    for category, pattern in RULES:
        if pattern.search(text):
            categories.append(category)

    if args.slicer_exit != 0:
        categories.append("SLICER_ERROR")

    # Bambu emits some non-fatal messages at [error] level while still
    # producing a valid slice (for example Invalid T command). Process exit
    # and explicit negative-return markers are authoritative instead.
    if NEGATIVE_RETURN.search(text):
        categories.append("SLICER_ERROR")

    if args.artifact is not None and (
        not args.artifact.is_file() or args.artifact.stat().st_size == 0
    ):
        categories.append("MISSING_OUTPUT")

    categories = sorted(set(categories))
    fatal = sorted(set(categories) & FATAL)

    result = {
        "ok": not fatal,
        "categories": categories,
        "fatal_categories": fatal,
        "slicer_exit": args.slicer_exit,
        "artifact": str(args.artifact) if args.artifact else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result))

    if fatal:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
