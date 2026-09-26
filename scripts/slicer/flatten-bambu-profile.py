#!/usr/bin/env python3
"""Flatten Bambu Studio system profiles for CLI slicing.

Bambu Studio's CLI expects full machine/process/filament settings rather than
the small inherited JSON files under resources/profiles. This utility resolves
both `inherits` and `include` chains from the BBL profile tree and emits
standalone JSON files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_profiles(root: Path) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for path in root.rglob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        if isinstance(name, str) and name:
            data = dict(data)
            data["__source_path"] = str(path)
            profiles[name] = data
    return profiles


def merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key.startswith("__"):
            continue
        result[key] = value
    return result


def resolve(
    name: str,
    profiles: dict[str, dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    stack: tuple[str, ...] = (),
) -> dict[str, Any]:
    if name in cache:
        return dict(cache[name])
    if name in stack:
        raise ValueError(f"profile dependency cycle: {' -> '.join((*stack, name))}")
    try:
        current = profiles[name]
    except KeyError as exc:
        raise KeyError(f"profile dependency not found: {name!r}") from exc

    result: dict[str, Any] = {}
    parent = current.get("inherits")
    if isinstance(parent, str) and parent:
        result = merge(result, resolve(parent, profiles, cache, (*stack, name)))

    includes = current.get("include", [])
    if isinstance(includes, str):
        includes = [includes]
    if isinstance(includes, list):
        for include in includes:
            if not isinstance(include, str) or not include:
                continue
            result = merge(
                result, resolve(include, profiles, cache, (*stack, name))
            )

    own = {
        key: value
        for key, value in current.items()
        if key not in {"inherits", "include", "__source_path"}
    }
    result = merge(result, own)
    cache[name] = dict(result)
    return result


def write_profile(
    name: str,
    kind: str,
    output: Path,
    profiles: dict[str, dict[str, Any]],
    cache: dict[str, dict[str, Any]],
) -> None:
    resolved = resolve(name, profiles, cache)
    actual_kind = resolved.get("type")
    if actual_kind != kind:
        raise ValueError(
            f"{name!r} resolved to type {actual_kind!r}, expected {kind!r}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(resolved, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"{kind}: {name} -> {output}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-root", type=Path, required=True)
    parser.add_argument("--machine", default="Bambu Lab H2D 0.4 nozzle")
    parser.add_argument("--process", default="0.20mm Standard @BBL H2D")
    parser.add_argument("--filament", default="Bambu PLA Basic @BBL H2D")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    root = args.profile_root.resolve()
    if not root.is_dir():
        raise SystemExit(f"profile root does not exist: {root}")

    profiles = load_profiles(root)
    if not profiles:
        raise SystemExit(f"no profiles found under {root}")

    cache: dict[str, dict[str, Any]] = {}
    write_profile(
        args.machine, "machine", args.output_dir / "machine.json", profiles, cache
    )
    write_profile(
        args.process, "process", args.output_dir / "process.json", profiles, cache
    )
    write_profile(
        args.filament,
        "filament",
        args.output_dir / "filament.json",
        profiles,
        cache,
    )


if __name__ == "__main__":
    main()
