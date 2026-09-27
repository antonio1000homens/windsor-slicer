"""Immutable, repository-independent consumer workspaces for slicer jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from mcp_servers.slicer.activity import command_error, command_request, command_response


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
WORKSPACE_ID_RE = re.compile(r"^[0-9a-f]{20}$")
MODEL_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
OUTPUT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MANIFEST_NAME = ".windsor-slicer.yaml"


class WorkspaceError(RuntimeError):
    """Expected, audit-safe workspace failure."""


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    repository: str
    commit: str
    path: Path


class WorkspaceManager:
    def __init__(
        self,
        *,
        root: Path = ROOT,
        runtime_root: Path | None = None,
        timeout_seconds: int = 120,
    ):
        self.root = root.resolve()
        configured = os.environ.get("SLICER_RUNTIME_ROOT", "").strip()
        self.runtime_root = Path(configured).expanduser().resolve() if configured else (runtime_root or self.root / "runtime").resolve()
        self.repo_root = self.runtime_root / "repos"
        self.workspace_root = self.runtime_root / "workspaces"
        self.input_root = self.runtime_root / "inputs"
        self.timeout_seconds = timeout_seconds

    @property
    def allowed_repositories(self) -> set[str]:
        raw = os.environ.get("SLICER_ALLOWED_REPOSITORIES", "")
        allowed = {item.strip() for item in raw.split(",") if item.strip()}
        if any(not REPOSITORY_RE.fullmatch(item) for item in allowed):
            raise WorkspaceError("SLICER_ALLOWED_REPOSITORIES contains an invalid repository")
        return allowed

    def _run(self, args: list[str], *, cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
        started = command_request(args, cwd=cwd)
        try:
            completed = subprocess.run(
                args,
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout or self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            command_error(args, error, started)
            raise WorkspaceError("workspace command timed out") from error
        except Exception as error:
            command_error(args, error, started)
            raise
        command_response(
            args,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            started=started,
        )
        return completed

    def _git(self, *args: str, cwd: Path | None = None, timeout: int | None = None) -> str:
        completed = self._run(["git", *args], cwd=cwd, timeout=timeout)
        if completed.returncode != 0:
            raise WorkspaceError("git workspace operation failed")
        return completed.stdout.strip()

    def _validate_request(self, repository: str, commit: str) -> tuple[str, str]:
        repository = repository.strip()
        commit = commit.strip().lower()
        if not REPOSITORY_RE.fullmatch(repository):
            raise WorkspaceError("repository must be an exact owner/repo name")
        if repository not in self.allowed_repositories:
            raise WorkspaceError("repository is not in SLICER_ALLOWED_REPOSITORIES")
        if not COMMIT_RE.fullmatch(commit):
            raise WorkspaceError("commit must be a full 40-character SHA")
        return repository, commit

    def _cache_path(self, repository: str) -> Path:
        name = hashlib.sha256(repository.encode()).hexdigest()[:24] + ".git"
        return self.repo_root / name

    @staticmethod
    def _workspace_id(repository: str, commit: str) -> str:
        return hashlib.sha256(f"{repository}@{commit}".encode()).hexdigest()[:20]

    def _workspace_is_clean(self, path: Path) -> bool:
        status = self._git(
            "-C",
            str(path),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        dirty = [
            line
            for line in status.splitlines()
            if line and line != "?? .windsor-slicer-workspace.json"
        ]
        return not dirty

    def _ensure_commit(self, repository: str, commit: str) -> Path:
        cache = self._cache_path(repository)
        self.repo_root.mkdir(parents=True, exist_ok=True)
        if not cache.exists():
            initialized = self._run(["git", "init", "--bare", str(cache)])
            if initialized.returncode != 0:
                raise WorkspaceError("unable to initialize repository cache")
            self._git("--git-dir", str(cache), "remote", "add", "origin", f"https://github.com/{repository}.git")
        remote = self._git("--git-dir", str(cache), "remote", "get-url", "origin")
        if remote != f"https://github.com/{repository}.git":
            raise WorkspaceError("repository cache origin does not match requested repository")
        probe = self._run(["git", "--git-dir", str(cache), "cat-file", "-e", f"{commit}^{{commit}}"])
        if probe.returncode:
            fetched = self._run(["git", "--git-dir", str(cache), "fetch", "--depth=1", "--no-tags", "origin", commit], timeout=max(self.timeout_seconds, 300))
            if fetched.returncode:
                raise WorkspaceError("requested commit is not available from repository")
        resolved = self._git("--git-dir", str(cache), "rev-parse", f"{commit}^{{commit}}").lower()
        if resolved != commit:
            raise WorkspaceError("resolved commit does not match request")
        return cache

    def cleanup_expired_workspaces(self, *, now: float | None = None) -> int:
        try:
            retention_hours = int(os.environ.get("SLICER_WORKSPACE_RETENTION_HOURS", "168"))
        except ValueError as error:
            raise WorkspaceError("SLICER_WORKSPACE_RETENTION_HOURS must be an integer") from error
        if retention_hours < 1 or retention_hours > 24 * 90:
            raise WorkspaceError("SLICER_WORKSPACE_RETENTION_HOURS must be between 1 and 2160")
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.input_root.mkdir(parents=True, exist_ok=True)
        cutoff = (time.time() if now is None else now) - retention_hours * 3600
        removed = 0
        for candidate in self.workspace_root.iterdir():
            if candidate.is_symlink() or not candidate.is_dir() or not WORKSPACE_ID_RE.fullmatch(candidate.name):
                continue
            metadata_path = candidate / ".windsor-slicer-workspace.json"
            try:
                if metadata_path.stat().st_mtime >= cutoff:
                    continue
                meta = json.loads(metadata_path.read_text(encoding="utf-8"))
                repository, commit = self._validate_request(str(meta.get("repository", "")), str(meta.get("commit", "")))
                if self._workspace_id(repository, commit) != candidate.name:
                    continue
                cache = self._cache_path(repository)
                self._run(["git", "--git-dir", str(cache), "worktree", "remove", "--force", str(candidate)])
                shutil.rmtree(candidate, ignore_errors=True)
                staged = self.input_root / candidate.name
                if staged.is_dir() and not staged.is_symlink() and staged.resolve().parent == self.input_root.resolve():
                    shutil.rmtree(staged, ignore_errors=True)
                removed += 1
            except (OSError, ValueError, WorkspaceError):
                continue
        return removed

    def prepare(self, *, repository: str, commit: str) -> dict[str, Any]:
        repository, commit = self._validate_request(repository, commit)
        cache = self._ensure_commit(repository, commit)
        workspace_id = self._workspace_id(repository, commit)
        self.cleanup_expired_workspaces()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        path = (self.workspace_root / workspace_id).resolve()
        if path.parent != self.workspace_root.resolve():
            raise WorkspaceError("invalid workspace path")
        metadata_path = path / ".windsor-slicer-workspace.json"
        reused = False
        if path.exists():
            try:
                meta = json.loads(metadata_path.read_text(encoding="utf-8"))
                actual = self._git("-C", str(path), "rev-parse", "HEAD").lower()
                reused = (
                    meta == {"repository": repository, "commit": commit}
                    and actual == commit
                    and self._workspace_is_clean(path)
                )
            except (OSError, ValueError, WorkspaceError):
                reused = False
            if not reused:
                self._run(["git", "--git-dir", str(cache), "worktree", "remove", "--force", str(path)])
                shutil.rmtree(path, ignore_errors=True)
        if not reused:
            added = self._run(["git", "--git-dir", str(cache), "worktree", "add", "--detach", str(path), commit], timeout=max(self.timeout_seconds, 180))
            if added.returncode:
                raise WorkspaceError("unable to create isolated git worktree")
            metadata_path.write_text(json.dumps({"repository": repository, "commit": commit}), encoding="utf-8")
        else:
            os.utime(metadata_path, None)
        return {"ok": True, "repository": repository, "commit": commit, "workspace": workspace_id, "reused": reused}

    def resolve(self, workspace_id: str) -> Workspace:
        normalized = workspace_id.strip().lower()
        if not WORKSPACE_ID_RE.fullmatch(normalized):
            raise WorkspaceError("workspace must be a 20-character repository-aware identifier")
        path = (self.workspace_root / normalized).resolve()
        if path.parent != self.workspace_root.resolve() or not path.is_dir():
            raise WorkspaceError("workspace does not exist")
        try:
            meta = json.loads((path / ".windsor-slicer-workspace.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise WorkspaceError("workspace metadata is invalid") from error
        repository, commit = meta.get("repository"), meta.get("commit")
        repository, commit = self._validate_request(str(repository or ""), str(commit or ""))
        actual = self._git("-C", str(path), "rev-parse", "HEAD").lower()
        expected_id = self._workspace_id(repository, commit)
        if actual != commit or normalized != expected_id:
            raise WorkspaceError("workspace repository and commit identity is invalid")
        if not self._workspace_is_clean(path):
            raise WorkspaceError("workspace differs from its immutable commit")
        return Workspace(normalized, repository, commit, path)

    def _model_spec(self, workspace: Workspace, model: str) -> dict[str, str]:
        if not MODEL_KEY_RE.fullmatch(model):
            raise WorkspaceError("model key is invalid")
        manifest_path = (workspace.path / MANIFEST_NAME).resolve()
        try:
            manifest_path.relative_to(workspace.path)
        except ValueError as error:
            raise WorkspaceError("manifest escapes the prepared worktree") from error
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise WorkspaceError("consumer manifest is missing or invalid YAML") from error
        if not isinstance(manifest, dict) or manifest.get("version") != 1 or set(manifest) != {"version", "models"}:
            raise WorkspaceError("manifest must contain only version 1 and models")
        models = manifest.get("models")
        if not isinstance(models, dict) or model not in models:
            raise WorkspaceError("model key does not exist in manifest")
        spec = models[model]
        if not isinstance(spec, dict) or set(spec) != {"source", "generator", "output"}:
            raise WorkspaceError("model entry must contain only source, generator and output")
        if not all(isinstance(spec.get(key), str) and spec[key].strip() for key in ("source", "generator", "output")):
            raise WorkspaceError("model source, generator and output must be non-empty strings")
        if spec["generator"] not in {"openscad", "copy"}:
            raise WorkspaceError("unsupported model generator")
        output = spec["output"]
        if not OUTPUT_RE.fullmatch(output) or output in {".", ".."}:
            raise WorkspaceError("output must be a simple filename")
        source_value = spec["source"]
        source_relative = Path(source_value)
        if source_relative.is_absolute() or ".." in source_relative.parts or "\\" in source_value:
            raise WorkspaceError("source must be a repository-relative path without traversal")
        source = (workspace.path / source_relative).resolve()
        try:
            source.relative_to(workspace.path)
        except ValueError as error:
            raise WorkspaceError("source resolves outside prepared worktree") from error
        if not source.is_file():
            raise WorkspaceError("model source does not exist")
        expected_suffix = ".scad" if spec["generator"] == "openscad" else None
        if expected_suffix and (source.suffix.lower() != expected_suffix or not output.lower().endswith(".stl")):
            raise WorkspaceError("openscad requires a .scad source and .stl output")
        if spec["generator"] == "copy" and (source.suffix.lower() not in {".stl", ".3mf"} or source.suffix.lower() != Path(output).suffix.lower()):
            raise WorkspaceError("copy requires matching .stl or .3mf source and output")
        return {"source": source_value, "generator": spec["generator"], "output": output, "absolute_source": str(source)}

    def generate_model(self, *, workspace_id: str, model: str) -> dict[str, Any]:
        workspace = self.resolve(workspace_id)
        spec = self._model_spec(workspace, model)
        output_dir = (self.input_root / workspace.workspace_id).resolve()
        if output_dir.parent != self.input_root.resolve():
            raise WorkspaceError("staged model path escapes input root")
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / spec["output"]
        started = time.monotonic()
        if spec["generator"] == "openscad":
            executable = shutil.which("openscad")
            if not executable:
                raise WorkspaceError("OpenSCAD is not installed")
            completed = self._run([executable, "-o", str(output), spec["absolute_source"]], cwd=workspace.path, timeout=int(os.environ.get("SLICER_GENERATE_TIMEOUT_SECONDS", "300")))
            if completed.returncode:
                raise WorkspaceError("OpenSCAD generation failed")
        else:
            shutil.copyfile(spec["absolute_source"], output)
        elapsed = time.monotonic() - started
        if not output.is_file() or output.stat().st_size == 0:
            raise WorkspaceError("model generator did not produce a non-empty file")
        return {"ok": True, "repository": workspace.repository, "commit": workspace.commit, "workspace": workspace.workspace_id, "model": model, "source": spec["source"], "path": str(output.relative_to(self.root)), "size_bytes": output.stat().st_size, "generation_seconds": round(elapsed, 3)}

    def require_generated_model(self, workspace_id: str, path_value: str) -> Workspace:
        workspace = self.resolve(workspace_id)
        requested = Path(path_value)
        candidate = requested if requested.is_absolute() else self.root / requested
        path = candidate.resolve()
        expected = (self.input_root / workspace.workspace_id).resolve()
        try:
            path.relative_to(expected)
        except ValueError as error:
            raise WorkspaceError("workspace slicing requires a staged model from that workspace") from error
        if not path.is_file() or path.stat().st_size == 0:
            raise WorkspaceError("staged model is missing or empty")
        return workspace
