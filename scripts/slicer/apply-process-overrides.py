#!/usr/bin/env python3
"""Apply the small allowlisted Windsor support policy to a flattened profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SUPPORT_TYPES = {"normal-auto": "normal(auto)", "tree-auto": "tree(auto)"}
ALLOWED_MODES = {"off", *SUPPORT_TYPES}


def apply_overrides(process_path: Path, support_mode: str | None) -> dict[str, Any]:
    if support_mode is not None and support_mode not in ALLOWED_MODES:
        raise ValueError("support_mode must be off, normal-auto, or tree-auto")
    try:
        process = json.loads(process_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("flattened process profile is missing or invalid JSON") from error
    if not isinstance(process, dict):
        raise ValueError("flattened process profile must be a JSON object")

    if support_mode is not None:
        enabled = support_mode != "off"
        process["enable_support"] = "1" if enabled else "0"
        support_type = SUPPORT_TYPES.get(support_mode)
        if support_type is not None:
            process["support_type"] = support_type
        else:
            inherited_type = process.get("support_type")
            support_type = inherited_type if isinstance(inherited_type, str) else None
        process_path.write_text(json.dumps(process, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        enabled_value = process.get("enable_support")
        enabled = (
            enabled_value.strip().casefold() in {"1", "true", "yes"}
            if isinstance(enabled_value, str)
            else enabled_value is True or enabled_value == 1
        )
        support_type = process.get("support_type")
        if not isinstance(support_type, str):
            support_type = None
    raw_angle = process.get("support_threshold_angle")
    try:
        threshold_angle = float(raw_angle)
        if threshold_angle.is_integer():
            threshold_angle = int(threshold_angle)
    except (TypeError, ValueError):
        threshold_angle = None
    raw_build_plate_only = process.get("support_on_build_plate_only")
    build_plate_only = (
        raw_build_plate_only.strip().casefold() in {"1", "true", "yes"}
        if isinstance(raw_build_plate_only, str)
        else raw_build_plate_only if isinstance(raw_build_plate_only, bool) else None
    )
    return {
        "mode": support_mode,
        "enabled": enabled,
        "type": support_type,
        "threshold_angle": threshold_angle,
        "build_plate_only": build_plate_only,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--process", type=Path, required=True)
    parser.add_argument("--support-mode", choices=sorted(ALLOWED_MODES))
    args = parser.parse_args()
    result = apply_overrides(args.process, args.support_mode)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
