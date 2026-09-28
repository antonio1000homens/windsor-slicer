#!/usr/bin/env python3
"""Direct end-to-end smoke test for the authenticated slicer MCP."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


class SmokeFailure(RuntimeError):
    pass


def current_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SmokeFailure("unable to resolve current git commit")
    commit = completed.stdout.strip().lower()
    if len(commit) != 40:
        raise SmokeFailure("current git commit is not a full SHA")
    return commit


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Exercise the complete authenticated MCP path: immutable workspace, "
            "model generation/retrieval, Bambu Studio validation and sliced 3MF retrieval."
        )
    )
    result.add_argument(
        "--url",
        default=os.environ.get("SLICER_MCP_URL", "http://127.0.0.1:8000/mcp"),
    )
    result.add_argument("--repository", required=True, help="Exact allowlisted owner/repo name")
    result.add_argument("--commit", required=True, help="Exact immutable 40-character commit")
    result.add_argument("--model", required=True, help="Model key from .windsor-slicer.yaml")
    result.add_argument("--expect-category", help="Expect a structured fatal category instead of a successful artifact")
    result.add_argument(
        "--download-dir",
        default="artifacts/slicer-smoke",
        help="Directory for retrieved generated models and sliced 3MF files.",
    )
    return result


def _tool_error_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return " | ".join(parts) or "MCP tool call failed"


async def call_tool(
    client: Client,
    name: str,
    arguments: dict[str, Any],
    timings: dict[str, float],
    *,
    allow_not_ok: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    result = await client.call_tool(name, arguments)
    timings[name] = round(time.monotonic() - started, 3)
    if result.is_error:
        raise SmokeFailure(f"{name}: {_tool_error_text(result)}")
    payload = result.structured_content
    if not isinstance(payload, dict):
        raise SmokeFailure(f"{name}: response did not contain structured output")
    if payload.get("ok") is False and not allow_not_ok:
        raise SmokeFailure(f"{name}: {payload.get('error') or 'operation failed'}")
    return payload


def decode_retrieved_file(payload: dict[str, Any], label: str) -> bytes:
    encoded = payload.get("base64")
    if not isinstance(encoded, str) or not encoded:
        raise SmokeFailure(f"{label} retrieval did not return file data")
    data = base64.b64decode(encoded, validate=True)
    digest = hashlib.sha256(data).hexdigest()
    if digest != payload.get("sha256"):
        raise SmokeFailure(f"{label} SHA-256 does not match server metadata")
    if len(data) != payload.get("size_bytes"):
        raise SmokeFailure(f"{label} size does not match server metadata")
    return data


async def run(args: argparse.Namespace) -> int:
    token = os.environ.get("SLICER_MCP_BEARER_TOKEN", "").strip()
    if not token:
        raise SmokeFailure("SLICER_MCP_BEARER_TOKEN is required")

    commit = args.commit.strip().lower()
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise SmokeFailure("--commit must be an exact 40-character SHA")
    timings: dict[str, float] = {}
    overall_started = time.monotonic()

    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(30.0, read=1800.0),
    ) as http_client:
        transport = streamable_http_client(args.url, http_client=http_client)
        connect_started = time.monotonic()
        async with Client(transport) as client:
            timings["connect"] = round(time.monotonic() - connect_started, 3)

            listed = await client.list_tools()
            names = {tool.name for tool in listed.tools}
            required = {
                "slicer_prepare_workspace",
                "slicer_generate_model",
                "slicer_get_model",
                "slicer_validate_for_print",
                "slicer_get_diagnostics",
                "slicer_get_artifact",
            }
            missing = sorted(required - names)
            if missing:
                raise SmokeFailure(
                    "MCP server is missing required tools: " + ", ".join(missing)
                )

            workspace = await call_tool(
                client,
                "slicer_prepare_workspace",
                {"repository": args.repository, "commit": commit},
                timings,
            )
            workspace_id = str(workspace["workspace"])
            resolved_commit = str(workspace["commit"])
            if resolved_commit != commit:
                raise SmokeFailure(
                    f"workspace resolved {resolved_commit}, expected {commit}"
                )

            generated = await call_tool(
                client,
                "slicer_generate_model",
                {
                    "workspace": workspace_id,
                    "model": args.model,
                },
                timings,
            )
            model_path = str(generated["path"])
            generated_file = await call_tool(
                client,
                "slicer_get_model",
                {"path": model_path, "include_base64": True},
                timings,
            )
            generated_data = decode_retrieved_file(
                generated_file,
                "generated model",
            )

            validation = await call_tool(
                client,
                "slicer_validate_for_print",
                {"workspace": workspace_id, "path": model_path},
                timings,
                allow_not_ok=bool(args.expect_category),
            )
            if args.expect_category:
                categories = validation.get("fatal_categories", [])
                if args.expect_category not in categories:
                    diagnostics = None
                    log_path = validation.get("log")
                    if isinstance(log_path, str) and log_path:
                        diagnostics = await call_tool(client, "slicer_get_diagnostics", {"log_path": log_path}, timings)
                    raise SmokeFailure(f"expected fatal category {args.expect_category}; got {categories}; diagnostics={diagnostics}")
                print(json.dumps({"ok": True, "repository": args.repository, "commit": commit, "workspace": workspace_id, "model": args.model, "fatal_categories": categories, "expected_category": args.expect_category, "timings_seconds": timings}, indent=2))
                return 0
            if not validation.get("ready_for_print"):
                diagnostics: dict[str, Any] | None = None
                log_path = validation.get("log")
                if isinstance(log_path, str) and log_path:
                    diagnostics = await call_tool(
                        client,
                        "slicer_get_diagnostics",
                        {"log_path": log_path},
                        timings,
                    )
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "stage": "slicer_validate_for_print",
                            "commit": commit,
                            "workspace": workspace_id,
                            "model": model_path,
                            "categories": validation.get("categories", []),
                            "fatal_categories": validation.get(
                                "fatal_categories", []
                            ),
                            "diagnostics": diagnostics,
                            "timings_seconds": timings,
                        },
                        indent=2,
                    )
                )
                return 2

            artifact_path = validation.get("artifact")
            if not isinstance(artifact_path, str) or not artifact_path:
                raise SmokeFailure("successful validation did not return an artifact")

            artifact = await call_tool(
                client,
                "slicer_get_artifact",
                {"path": artifact_path, "include_base64": True},
                timings,
            )

    artifact_data = decode_retrieved_file(artifact, "sliced artifact")

    download_dir = Path(args.download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    generated_destination = download_dir / Path(model_path).name
    artifact_destination = download_dir / Path(artifact_path).name
    generated_destination.write_bytes(generated_data)
    artifact_destination.write_bytes(artifact_data)

    timings["overall"] = round(time.monotonic() - overall_started, 3)
    report = {
        "ok": True,
        "repository": args.repository,
        "commit": commit,
        "workspace": workspace_id,
        "model": args.model,
        "generated_model": model_path,
        "provider": validation.get("provider"),
        "provider_version": validation.get("provider_version"),
        "machine_profile": validation.get("machine_profile"),
        "process_profile": validation.get("process_profile"),
        "filament_profile": validation.get("filament_profile"),
        "categories": validation.get("categories", []),
        "generated_model_downloaded_to": str(generated_destination),
        "generated_model_size_bytes": len(generated_data),
        "generated_model_sha256": hashlib.sha256(generated_data).hexdigest(),
        "artifact": artifact_path,
        "artifact_downloaded_to": str(artifact_destination),
        "artifact_size_bytes": len(artifact_data),
        "artifact_sha256": hashlib.sha256(artifact_data).hexdigest(),
        "timings_seconds": timings,
    }
    print(json.dumps(report, indent=2))
    return 0


def main() -> None:
    args = parser().parse_args()
    try:
        code = asyncio.run(run(args))
    except (SmokeFailure, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2), file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
