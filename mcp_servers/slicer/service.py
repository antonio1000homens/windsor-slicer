"""Shared slicer service used by the MCP server.

This layer intentionally calls the same repository scripts used by GitHub
Actions and Codespaces. It does not reimplement Bambu Studio slicing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp_servers.slicer.activity import command_error, command_request, command_response
from mcp_servers.slicer.workspace import WorkspaceError, WorkspaceManager


ROOT = Path(__file__).resolve().parents[2]
SLICE_SCRIPT = ROOT / "scripts/slicer/slice-stl.sh"
INSTALL_SCRIPT = ROOT / "scripts/slicer/install-bambu-studio.sh"
DEFAULT_MACHINE = "Bambu Lab H2D 0.4 nozzle"
DEFAULT_PROCESS = "0.20mm Standard @BBL H2D"
DEFAULT_FILAMENT = "Bambu PLA Basic @BBL H2D"
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_RETENTION_HOURS = 24


def _resolve_profile(requested: str | None, env_name: str, fallback: str) -> str:
    if requested is not None and requested.strip():
        return requested.strip()
    configured = os.environ.get(env_name, "").strip()
    return configured or fallback


def _runtime_input_root() -> Path:
    configured = os.environ.get("SLICER_RUNTIME_ROOT", "").strip()
    runtime = Path(configured).expanduser() if configured else ROOT / "runtime"
    return (runtime / "inputs").resolve()


ALLOWED_INPUT_ROOTS = (_runtime_input_root(),)
OUTPUT_ROOT = (ROOT / "artifacts/slicer").resolve()


class SlicerServiceError(RuntimeError):
    """Expected, audit-safe slicer service failure."""


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _repo_relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _resolve_model(path_value: str) -> Path:
    if not path_value or not str(path_value).strip():
        raise SlicerServiceError("model path is required")

    requested = Path(path_value)
    candidate = requested if requested.is_absolute() else ROOT / requested
    path = candidate.resolve()

    if not any(_is_relative_to(path, root) for root in ALLOWED_INPUT_ROOTS):
        raise SlicerServiceError(
            "model path must be under runtime/inputs/"
        )
    if path.suffix.lower() not in {".stl", ".3mf"}:
        raise SlicerServiceError("model path must end in .stl or .3mf")
    if not path.is_file():
        raise SlicerServiceError(f"model does not exist: {_repo_relative(path)}")
    return path


def _resolve_output(path: Path) -> Path:
    resolved = path.resolve()
    if not _is_relative_to(resolved, OUTPUT_ROOT):
        raise SlicerServiceError("slicer output must remain under artifacts/slicer/")
    return resolved


def _inline_transfer_limit() -> int:
    raw = os.environ.get("SLICER_MCP_MAX_ARTIFACT_BYTES", "10485760").strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise SlicerServiceError(
            "SLICER_MCP_MAX_ARTIFACT_BYTES must be an integer"
        ) from error
    if value < 1:
        raise SlicerServiceError(
            "SLICER_MCP_MAX_ARTIFACT_BYTES must be greater than zero"
        )
    return value


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file incrementally so metadata reads do not scale memory with size."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_transfer_payload(
    path: Path,
    *,
    include_base64: bool,
    allowed_suffixes: set[str],
    kind: str,
) -> dict[str, Any]:
    """Return bounded file-transfer metadata without changing storage roots."""
    suffix = path.suffix.lower()
    if suffix not in allowed_suffixes:
        if kind == "artifact":
            raise SlicerServiceError("artifact must be a .3mf file")
        raise SlicerServiceError("model path must end in .stl or .3mf")
    if not path.is_file():
        raise SlicerServiceError(f"{kind} does not exist or is empty")

    size = path.stat().st_size
    if size == 0:
        raise SlicerServiceError(f"{kind} does not exist or is empty")

    if include_base64:
        limit = _inline_transfer_limit()
        if size > limit:
            raise SlicerServiceError(
                f"{kind} is too large for inline MCP transfer"
            )
        # Bound the allocation even if the file changes after stat().
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise SlicerServiceError(
                f"{kind} is too large for inline MCP transfer"
            )
        size = len(data)
        digest = hashlib.sha256(data).hexdigest()
    else:
        data = None
        digest = _sha256_file(path)

    result: dict[str, Any] = {
        "ok": True,
        "path": _repo_relative(path),
        "filename": path.name,
        "size_bytes": size,
        "sha256": digest,
    }
    if data is not None:
        result["base64"] = base64.b64encode(data).decode("ascii")
    return result


def _safe_output_stem(value: str) -> str:
    """Return a bounded filesystem-safe label for MCP output directories."""
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    sanitized = sanitized.strip("._-")
    if not sanitized:
        return "model"
    return sanitized[:64]


def _retention_hours() -> int:
    raw = os.environ.get("SLICER_MCP_RETENTION_HOURS", "").strip()
    if not raw:
        return DEFAULT_RETENTION_HOURS
    try:
        value = int(raw)
    except ValueError as error:
        raise SlicerServiceError(
            "SLICER_MCP_RETENTION_HOURS must be an integer"
        ) from error
    if value < 1 or value > 24 * 30:
        raise SlicerServiceError(
            "SLICER_MCP_RETENTION_HOURS must be between 1 and 720"
        )
    return value


def _cleanup_stale_mcp_outputs(*, now: float | None = None) -> int:
    """Remove only stale MCP-owned output directories below artifacts/slicer."""
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    cutoff = (time.time() if now is None else now) - (_retention_hours() * 3600)
    removed = 0

    for candidate in OUTPUT_ROOT.glob("mcp-*"):
        try:
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            resolved = candidate.resolve()
            if not _is_relative_to(resolved, OUTPUT_ROOT):
                continue
            if candidate.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(candidate)
            removed += 1
        except FileNotFoundError:
            continue

    return removed


def _validate_result_payload(result: Any) -> dict[str, Any]:
    """Validate the trusted shape of result.json before exposing it through MCP."""
    if not isinstance(result, dict):
        raise SlicerServiceError("slicer result.json must contain a JSON object")

    if not isinstance(result.get("ok"), bool):
        raise SlicerServiceError("slicer result field 'ok' must be boolean")

    for field in ("categories", "fatal_categories"):
        value = result.get(field, [])
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise SlicerServiceError(
                f"slicer result field '{field}' must be a list of strings"
            )
        result[field] = value

    slicer_exit = result.get("slicer_exit")
    if slicer_exit is not None and not isinstance(slicer_exit, int):
        raise SlicerServiceError("slicer result field 'slicer_exit' must be integer")

    for field in ("input", "artifact"):
        value = result.get(field)
        if value is not None and not isinstance(value, str):
            raise SlicerServiceError(
                f"slicer result field '{field}' must be a string or null"
            )

    for field in ("slice_seconds", "slice_milliseconds"):
        value = result.get(field)
        if value is not None and not isinstance(value, (int, float)):
            raise SlicerServiceError(
                f"slicer result field '{field}' must be numeric when present"
            )

    return result


def _find_slicer() -> Path | None:
    explicit = (
        os.environ.get("BAMBU_STUDIO_BIN", "").strip()
        or os.environ.get("SLICER_PATH", "").strip()
    )
    candidates = [
        Path(explicit).expanduser() if explicit else None,
        ROOT / ".tools/bin/bambu-studio",
        Path("/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"),
        Path("/Applications/Bambu Studio.app/Contents/MacOS/BambuStudio"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def _find_profile_root() -> Path | None:
    explicit = os.environ.get("BAMBU_PROFILE_ROOT", "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.is_dir():
            return path

    tools_root = ROOT / ".tools/bambu-studio"
    if tools_root.is_dir():
        matches = sorted(tools_root.glob("*/app/resources/profiles/BBL"), reverse=True)
        for match in matches:
            if match.is_dir():
                return match.resolve()

    slicer = _find_slicer()
    if slicer and sys.platform == "darwin":
        contents_root = slicer.parents[1]
        candidate = contents_root / "Resources/profiles/BBL"
        if candidate.is_dir():
            return candidate.resolve()
    return None


def _run_process_group(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run a slicer wrapper and guarantee descendants are cleaned up on timeout."""
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "text": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True

    started = command_request(command, cwd=cwd)
    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except Exception as error:
        command_error(command, error, started)
        raise

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
        else:  # pragma: no cover - development fallback outside Linux/macOS
            process.kill()
            stdout, stderr = process.communicate()

        error.stdout = stdout
        error.stderr = stderr
        command_error(command, error, started)
        raise

    completed = subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )
    command_response(
        command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        started=started,
    )
    return completed


@dataclass
class BambuStudioProvider:
    """Thin provider over the repository's proven Bambu Studio scripts."""

    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @property
    def name(self) -> str:
        return "bambustudio"

    def capabilities(self) -> dict[str, Any]:
        slicer = _find_slicer()
        profile_root = _find_profile_root()
        return {
            "provider": self.name,
            "available": slicer is not None,
            "executable": _repo_relative(slicer) if slicer else None,
            "profile_root": _repo_relative(profile_root) if profile_root else None,
            "install_script_available": INSTALL_SCRIPT.is_file(),
            "slice_script_available": SLICE_SCRIPT.is_file(),
            "version": os.environ.get("BAMBU_STUDIO_VERSION", "v02.08.02.61"),
            "default_machine_profile": _resolve_profile(
                None, "SLICER_MACHINE_PROFILE", DEFAULT_MACHINE
            ),
            "default_process_profile": _resolve_profile(
                None, "SLICER_PROCESS_PROFILE", DEFAULT_PROCESS
            ),
            "default_filament_profile": _resolve_profile(
                None, "SLICER_FILAMENT_PROFILE", DEFAULT_FILAMENT
            ),
            "builtin_fallback_machine_profile": DEFAULT_MACHINE,
            "builtin_fallback_process_profile": DEFAULT_PROCESS,
            "builtin_fallback_filament_profile": DEFAULT_FILAMENT,
            "printer_model": "h2d",
            "printer_handoff_available": False,
        }

    def inspect_model(self, path_value: str) -> dict[str, Any]:
        path = _resolve_model(path_value)
        result: dict[str, Any] = {
            "path": _repo_relative(path),
            "suffix": path.suffix.lower(),
            "size_bytes": path.stat().st_size,
        }

        try:
            import trimesh
        except ImportError as error:
            raise SlicerServiceError(
                "trimesh is required for slicer_inspect_model; install MCP requirements"
            ) from error

        try:
            loaded = trimesh.load(path, process=False)
            if isinstance(loaded, trimesh.Scene):
                if not loaded.geometry:
                    raise SlicerServiceError("model contains no geometry")
                mesh = trimesh.util.concatenate(tuple(loaded.geometry.values()))
            else:
                mesh = loaded
        except SlicerServiceError:
            raise
        except Exception as error:
            raise SlicerServiceError("unable to inspect model geometry") from error

        bounds = mesh.bounds.tolist() if getattr(mesh, "bounds", None) is not None else None
        extents = mesh.extents.tolist() if getattr(mesh, "extents", None) is not None else None
        result.update(
            {
                "vertices": int(len(mesh.vertices)),
                "faces": int(len(mesh.faces)),
                "watertight": bool(mesh.is_watertight),
                "body_count": int(mesh.body_count),
                "bounds_mm": bounds,
                "dimensions_mm": extents,
            }
        )
        return result

    def list_profiles(
        self,
        profile_type: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        root = _find_profile_root()
        if root is None:
            return {
                "available": False,
                "profile_root": None,
                "profiles": [],
                "message": "Bambu profile root not found; install Bambu Studio first",
            }

        wanted = (profile_type or "").strip().lower()
        if wanted and wanted not in {"machine", "process", "filament"}:
            raise SlicerServiceError(
                "profile_type must be machine, process, filament, or omitted"
            )
        search = (query or "").strip()
        if len(search) > 128:
            raise SlicerServiceError("profile query must be at most 128 characters")
        folded_search = search.casefold()

        profiles: list[dict[str, str]] = []
        for file_path in root.rglob("*.json"):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            kind = str(payload.get("type") or "").strip().lower()
            name = str(payload.get("name") or "").strip()
            if kind not in {"machine", "process", "filament"} or not name:
                continue
            if wanted and kind != wanted:
                continue
            # Machine and process results are scoped to this H2D service. Filament
            # profiles are printer/material choices and may omit the printer name.
            lower_name = name.casefold()
            if "h2d" not in lower_name and kind != "filament":
                continue
            if folded_search and folded_search not in lower_name:
                continue
            profiles.append(
                {
                    "name": name,
                    "type": kind,
                    "source": _repo_relative(file_path),
                }
            )

        profiles.sort(key=lambda item: (item["type"], item["name"].casefold()))
        # Keep an unfiltered all-types request bounded, but do not truncate a
        # caller that explicitly selected a type or search term. This keeps
        # material discovery complete while retaining a safe general listing.
        bounded = not wanted and not folded_search
        returned_profiles = profiles[:250] if bounded else profiles
        return {
            "available": True,
            "profile_root": _repo_relative(root),
            "profiles": returned_profiles,
            "truncated": len(returned_profiles) < len(profiles),
            "query": search or None,
        }

    def slice(
        self,
        path_value: str,
        *,
        machine_profile: str | None = None,
        process_profile: str | None = None,
        filament_profile: str | None = None,
        orient: bool = False,
        bed_type: str | None = None,
    ) -> dict[str, Any]:
        machine_profile = _resolve_profile(
            machine_profile, "SLICER_MACHINE_PROFILE", DEFAULT_MACHINE
        )
        process_profile = _resolve_profile(
            process_profile, "SLICER_PROCESS_PROFILE", DEFAULT_PROCESS
        )
        filament_profile = _resolve_profile(
            filament_profile, "SLICER_FILAMENT_PROFILE", DEFAULT_FILAMENT
        )
        path = _resolve_model(path_value)
        if not SLICE_SCRIPT.is_file():
            raise SlicerServiceError("shared slicer script is missing")

        _cleanup_stale_mcp_outputs()
        safe_stem = _safe_output_stem(path.stem)
        output_dir = _resolve_output(
            OUTPUT_ROOT / f"mcp-{safe_stem}-{uuid.uuid4().hex[:10]}"
        )
        output_dir.mkdir(parents=True, exist_ok=False)

        env = os.environ.copy()
        env["SLICER_MACHINE_PROFILE"] = machine_profile
        env["SLICER_PROCESS_PROFILE"] = process_profile
        env["SLICER_FILAMENT_PROFILE"] = filament_profile
        env["SLICER_ORIENT"] = "1" if orient else "0"
        detected_slicer = _find_slicer()
        if detected_slicer is not None:
            env["BAMBU_STUDIO_BIN"] = str(detected_slicer)
        detected_profile_root = _find_profile_root()
        if detected_profile_root is not None:
            env["BAMBU_PROFILE_ROOT"] = str(detected_profile_root)
        if bed_type:
            env["SLICER_BED_TYPE"] = bed_type

        command = [
            "bash",
            str(SLICE_SCRIPT),
            _repo_relative(path),
            str(output_dir),
        ]
        try:
            completed = _run_process_group(
                command,
                cwd=ROOT,
                env=env,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise SlicerServiceError(
                f"slicing exceeded timeout of {self.timeout_seconds} seconds"
            ) from error

        result_path = output_dir / "result.json"
        if result_path.is_file():
            try:
                result = _validate_result_payload(
                    json.loads(result_path.read_text(encoding="utf-8"))
                )
            except json.JSONDecodeError as error:
                raise SlicerServiceError("slicer produced malformed result.json") from error
        else:
            result = _validate_result_payload(
                {
                    "ok": False,
                    "categories": ["SLICER_ERROR", "MISSING_OUTPUT"],
                    "fatal_categories": ["SLICER_ERROR", "MISSING_OUTPUT"],
                    "slicer_exit": completed.returncode,
                    "artifact": None,
                }
            )

        raw_input = result.get("input")
        if raw_input:
            normalized_input = _resolve_model(str(raw_input))
            if normalized_input != path:
                raise SlicerServiceError("slicer result input does not match request")
            result["input"] = _repo_relative(normalized_input)
        else:
            result["input"] = _repo_relative(path)

        raw_artifact = result.get("artifact")
        if raw_artifact:
            artifact_path = Path(str(raw_artifact))
            if not artifact_path.is_absolute():
                artifact_path = ROOT / artifact_path
            artifact_path = artifact_path.resolve()
            if not _is_relative_to(artifact_path, output_dir):
                raise SlicerServiceError(
                    "slicer result artifact escaped the allocated output directory"
                )
            if artifact_path.suffix.lower() != ".3mf":
                raise SlicerServiceError(
                    "slicer result artifact must be a .3mf file"
                )
            if not artifact_path.is_file() or artifact_path.stat().st_size == 0:
                if result.get("ok"):
                    raise SlicerServiceError(
                        "slicer result artifact is missing or empty"
                    )
                result["expected_artifact"] = _repo_relative(artifact_path)
                result["artifact"] = None
            else:
                result["artifact"] = _repo_relative(artifact_path)
        elif result.get("ok"):
            raise SlicerServiceError(
                "successful slicer result did not include an artifact"
            )

        result.update(
            {
                "provider": self.name,
                "provider_version": os.environ.get(
                    "BAMBU_STUDIO_VERSION", "v02.08.02.61"
                ),
                "machine_profile": machine_profile,
                "process_profile": process_profile,
                "filament_profile": filament_profile,
                "orient": bool(orient),
                "bed_type": bed_type,
                "output_dir": _repo_relative(output_dir),
                "log": _repo_relative(output_dir / "slicer.log"),
            }
        )

        # Never return the captured raw process output through MCP. The shared
        # script writes a bounded artifact log that can be inspected separately.
        if completed.returncode != 0 and result.get("ok"):
            result["ok"] = False
            result.setdefault("categories", []).append("SLICER_ERROR")
            result.setdefault("fatal_categories", []).append("SLICER_ERROR")
        return result


class SlicerService:
    def __init__(
        self,
        provider: BambuStudioProvider | None = None,
        workspace_manager: WorkspaceManager | None = None,
    ):
        self.provider = provider or BambuStudioProvider()
        self.workspace_manager = workspace_manager or WorkspaceManager()

    def _workspace_context(
        self, workspace: str | None, path_value: str
    ) -> dict[str, str]:
        if not workspace:
            return {}
        try:
            resolved = self.workspace_manager.require_generated_model(
                workspace, path_value
            )
        except WorkspaceError as error:
            raise SlicerServiceError(str(error)) from error
        return {
            "repository": resolved.repository,
            "workspace": resolved.workspace_id,
            "commit": resolved.commit,
        }

    def prepare_workspace(self, repository: str, commit: str) -> dict[str, Any]:
        try:
            return self.workspace_manager.prepare(
                repository=repository,
                commit=commit,
            )
        except WorkspaceError as error:
            raise SlicerServiceError(str(error)) from error

    def generate_model(
        self,
        workspace: str,
        model: str,
    ) -> dict[str, Any]:
        try:
            return self.workspace_manager.generate_model(
                workspace_id=workspace,
                model=model,
            )
        except WorkspaceError as error:
            raise SlicerServiceError(str(error)) from error

    def get_model(
        self,
        path: str,
        *,
        include_base64: bool = False,
    ) -> dict[str, Any]:
        model = _resolve_model(path)
        return _file_transfer_payload(
            model,
            include_base64=include_base64,
            allowed_suffixes={".stl", ".3mf"},
            kind="model",
        )

    def get_artifact(
        self,
        path: str,
        *,
        include_base64: bool = False,
    ) -> dict[str, Any]:
        requested = Path(path)
        candidate = requested if requested.is_absolute() else ROOT / requested
        artifact = _resolve_output(candidate)
        return _file_transfer_payload(
            artifact,
            include_base64=include_base64,
            allowed_suffixes={".3mf"},
            kind="artifact",
        )

    def get_diagnostics(
        self,
        log_path: str,
        *,
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        requested = Path(log_path)
        candidate = requested if requested.is_absolute() else ROOT / requested
        log = _resolve_output(candidate)
        if log.suffix.lower() != ".log" or not log.is_file():
            raise SlicerServiceError("diagnostic log does not exist")
        limit = max(1000, min(int(max_chars), 20000))
        text = log.read_text(encoding="utf-8", errors="replace")
        text = re.sub(
            r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
            r"\1[REDACTED]",
            text,
        )
        if len(text) > limit:
            text = text[-limit:]
        return {
            "ok": True,
            "log": _repo_relative(log),
            "truncated": log.stat().st_size > len(text.encode("utf-8")),
            "text": text,
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "service": "windsor-slicer-mcp",
            "providers": [self.provider.capabilities()],
            "allowed_input_roots": [
                _repo_relative(path) for path in ALLOWED_INPUT_ROOTS
            ],
            "output_root": _repo_relative(OUTPUT_ROOT),
            "retention_hours": _retention_hours(),
            "cloud_workspace": {
                "allowed_repositories": sorted(self.workspace_manager.allowed_repositories),
                "supported": True,
                "generated_input_root": "runtime/inputs/",
                "artifact_inline_limit_bytes": _inline_transfer_limit(),
                "model_inline_limit_bytes": _inline_transfer_limit(),
            },
            "cloudflare_remote_endpoint": {
                "configured": False,
                "recommended_path_if_deployed": "/mcp/slicer",
                "note": (
                    "No Cloudflare endpoint is required for stdio/Codespaces. "
                    "A permanent cloud-hosted MCP should use a separate protected "
                    "endpoint rather than /api/control/v1/*."
                ),
            },
        }

    def inspect_model(self, path: str) -> dict[str, Any]:
        return self.provider.inspect_model(path)

    def list_profiles(
        self,
        profile_type: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        return self.provider.list_profiles(profile_type, query)

    def slice_model(
        self,
        *,
        workspace: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        context = self._workspace_context(
            workspace, str(kwargs.get("path_value") or "")
        )
        return {**self.provider.slice(**kwargs), **context}

    def validate_for_print(
        self,
        *,
        workspace: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        context = self._workspace_context(
            workspace, str(kwargs.get("path_value") or "")
        )
        result = self.provider.slice(**kwargs)
        result.update(context)
        return {
            **result,
            "validation_only": True,
            "ready_for_print": bool(result.get("ok") and result.get("artifact")),
        }

    def prepare_print(
        self,
        *,
        workspace: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        context = self._workspace_context(
            workspace, str(kwargs.get("path_value") or "")
        )
        result = self.provider.slice(**kwargs)
        result.update(context)
        ready = bool(result.get("ok") and result.get("artifact"))
        return {
            **result,
            "ready_for_print": ready,
            "print_started": False,
            "printer_handoff_available": False,
            "next_step": (
                "Pass the pre-sliced artifact to the Bambu printer provider"
                if ready
                else "Resolve slicer validation failures before printer handoff"
            ),
        }
