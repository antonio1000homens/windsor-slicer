from __future__ import annotations

import os
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_servers.slicer.workspace import Workspace, WorkspaceError, WorkspaceManager


class WorkspaceContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.repo = (self.runtime / "workspaces" / "fixture").resolve()
        self.repo.mkdir(parents=True)
        (self.repo / "model.stl").write_bytes(b"stl")
        self.manager = WorkspaceManager(root=self.root, runtime_root=self.runtime)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "tests@example.test"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Slicer tests"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "model.stl"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "fixture"], check=True)
        self.commit = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip()
        self.repository = "example/model-fixture"
        self.workspace_id = self.manager._workspace_id(self.repository, self.commit)
        destination = self.manager.workspace_root / self.workspace_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.repo.rename(destination)
        self.repo = destination.resolve()
        (self.repo / ".windsor-slicer-workspace.json").write_text(json.dumps({"repository": self.repository, "commit": self.commit}), encoding="utf-8")
        self.workspace = Workspace(self.workspace_id, self.repository, self.commit, self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def test_allowlist_requires_exact_owner_repo_and_commit(self):
        with patch.dict(os.environ, {"SLICER_ALLOWED_REPOSITORIES": "example/model-fixture"}):
            self.assertEqual(self.manager._validate_request("example/model-fixture", "a" * 40), ("example/model-fixture", "a" * 40))
            for repo, commit in (("https://github.com/example/model-fixture", "a" * 40), ("example/model-fixture", "a" * 39), ("example/other", "a" * 40)):
                with self.assertRaises(WorkspaceError):
                    self.manager._validate_request(repo, commit)

    def test_same_commit_in_different_repositories_has_distinct_workspace_id(self):
        self.assertNotEqual(self.manager._workspace_id("example/one", "a" * 40), self.manager._workspace_id("example/two", "a" * 40))

    def test_expired_workspace_and_its_staged_inputs_are_removed(self):
        staged = self.manager.input_root / self.workspace_id
        staged.mkdir(parents=True)
        (staged / "old.stl").write_bytes(b"stale")
        metadata = self.repo / ".windsor-slicer-workspace.json"
        os.utime(metadata, (time.time() - 2 * 3600, time.time() - 2 * 3600))
        with patch.dict(os.environ, {"SLICER_ALLOWED_REPOSITORIES": "example/model-fixture", "SLICER_WORKSPACE_RETENTION_HOURS": "1"}):
            removed = self.manager.cleanup_expired_workspaces()
        self.assertEqual(removed, 1)
        self.assertFalse(self.repo.exists())
        self.assertFalse(staged.exists())

    def test_copy_manifest_stages_only_committed_mesh_to_runtime_input_root(self):
        (self.repo / ".windsor-slicer.yaml").write_text(
            "version: 1\nmodels:\n  fixture:\n    source: model.stl\n    generator: copy\n    output: staged.stl\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"SLICER_ALLOWED_REPOSITORIES": "example/model-fixture"}):
            result = self.manager.generate_model(workspace_id=self.workspace_id, model="fixture")
        staged = self.root / result["path"]
        self.assertEqual(staged.read_bytes(), b"stl")
        self.assertEqual(result["repository"], "example/model-fixture")
        self.assertEqual(result["commit"], self.commit)
        self.assertEqual(result["size_bytes"], 3)

    def test_manifest_rejects_escape_and_unknown_execution_fields(self):
        manifest = self.repo / ".windsor-slicer.yaml"
        for extra in ("    command: whoami\n",):
            manifest.write_text("version: 1\nmodels:\n  fixture:\n    source: model.stl\n    generator: copy\n    output: staged.stl\n" + extra, encoding="utf-8")
            with self.assertRaisesRegex(WorkspaceError, "only source, generator and output"):
                self.manager._model_spec(self.workspace, "fixture")
        manifest.write_text("version: 1\nmodels:\n  fixture:\n    source: ../outside.stl\n    generator: copy\n    output: staged.stl\n", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "without traversal"):
            self.manager._model_spec(self.workspace, "fixture")

    def test_manifest_rejects_unknown_generator_and_non_simple_output(self):
        manifest = self.repo / ".windsor-slicer.yaml"
        for generator, output in (("shell", "staged.stl"), ("copy", "../staged.stl")):
            manifest.write_text(f"version: 1\nmodels:\n  fixture:\n    source: model.stl\n    generator: {generator}\n    output: {output}\n", encoding="utf-8")
            with self.assertRaises(WorkspaceError):
                self.manager._model_spec(self.workspace, "fixture")


if __name__ == "__main__":
    unittest.main()
