from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mcp_servers.slicer import server
from mcp_servers.slicer import service as service_module
from mcp_servers.slicer.service import (
    ROOT,
    BambuStudioProvider,
    SlicerService,
    SlicerServiceError,
)
from mcp_servers.slicer.workspace import Workspace


TEST_MODEL = (
    ROOT
    / "runtime/inputs/test-fixture"
    / "sample.stl"
)


class SlicerServiceTests(unittest.TestCase):
    def setUp(self):
        TEST_MODEL.parent.mkdir(parents=True, exist_ok=True)
        TEST_MODEL.write_text(
            "solid sample\n"
            "facet normal 0 0 1\nouter loop\n"
            "vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
            "endloop\nendfacet\nendsolid sample\n",
            encoding="utf-8",
        )
        self.provider = BambuStudioProvider(timeout_seconds=5)
        self.service = SlicerService(self.provider)

    def tearDown(self):
        shutil.rmtree(TEST_MODEL.parent, ignore_errors=True)

    def _successful_process(self, command, **kwargs):
        requested = (ROOT / command[-2]).resolve()
        output_dir = Path(command[-1])
        artifact = output_dir / f"{requested.stem}.sliced.3mf"
        artifact.write_bytes(b"test-3mf")
        (output_dir / "slicer.log").write_text("clean slice\n", encoding="utf-8")
        (output_dir / "result.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "categories": [],
                    "fatal_categories": [],
                    "slicer_exit": 0,
                    "input": str(requested),
                    "artifact": str(artifact.resolve()),
                    "slice_seconds": 0.42,
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_capabilities_keep_cloudflare_endpoint_separate(self):
        payload = self.service.capabilities()

        self.assertEqual(payload["service"], "windsor-slicer-mcp")
        self.assertFalse(payload["cloudflare_remote_endpoint"]["configured"])
        self.assertEqual(
            payload["cloudflare_remote_endpoint"]["recommended_path_if_deployed"],
            "/mcp/slicer",
        )

    def test_capabilities_report_environment_profiles_and_builtin_fallbacks(self):
        configured = {
            "SLICER_MACHINE_PROFILE": "runtime machine",
            "SLICER_PROCESS_PROFILE": "runtime process",
            "SLICER_FILAMENT_PROFILE": "runtime filament",
        }
        with patch.dict(os.environ, configured, clear=False):
            payload = self.provider.capabilities()

        self.assertEqual(payload["default_machine_profile"], "runtime machine")
        self.assertEqual(payload["default_process_profile"], "runtime process")
        self.assertEqual(payload["default_filament_profile"], "runtime filament")
        self.assertEqual(
            payload["builtin_fallback_filament_profile"],
            "Bambu PLA Basic @BBL H2D",
        )

    def test_profile_precedence_for_machine_process_and_filament(self):
        env_names = {
            "machine": "SLICER_MACHINE_PROFILE",
            "process": "SLICER_PROCESS_PROFILE",
            "filament": "SLICER_FILAMENT_PROFILE",
        }
        fallbacks = {
            "machine": "Bambu Lab H2D 0.4 nozzle",
            "process": "0.20mm Standard @BBL H2D",
            "filament": "Bambu PLA Basic @BBL H2D",
        }
        for profile_type, env_name in env_names.items():
            with self.subTest(profile_type=profile_type):
                override = f"request {profile_type}"
                configured = f"environment {profile_type}"
                kwargs = {f"{profile_type}_profile": override}
                with (
                    patch.dict(os.environ, {env_name: configured}, clear=False),
                    patch(
                        "mcp_servers.slicer.service._run_process_group",
                        side_effect=self._successful_process,
                    ) as mocked,
                ):
                    requested = self.provider.slice(
                        str(TEST_MODEL.relative_to(ROOT)), **kwargs
                    )
                self.assertEqual(requested[f"{profile_type}_profile"], override)
                self.assertEqual(mocked.call_args.kwargs["env"][env_name], override)

                with (
                    patch.dict(os.environ, {env_name: configured}, clear=False),
                    patch(
                        "mcp_servers.slicer.service._run_process_group",
                        side_effect=self._successful_process,
                    ) as mocked,
                ):
                    environment_default = self.provider.slice(
                        str(TEST_MODEL.relative_to(ROOT))
                    )
                self.assertEqual(
                    environment_default[f"{profile_type}_profile"], configured
                )
                self.assertEqual(mocked.call_args.kwargs["env"][env_name], configured)

                with (
                    patch.dict(os.environ, {}, clear=True),
                    patch(
                        "mcp_servers.slicer.service._run_process_group",
                        side_effect=self._successful_process,
                    ) as mocked,
                ):
                    fallback = self.provider.slice(
                        str(TEST_MODEL.relative_to(ROOT))
                    )
                self.assertEqual(
                    fallback[f"{profile_type}_profile"], fallbacks[profile_type]
                )
                self.assertEqual(
                    mocked.call_args.kwargs["env"][env_name], fallbacks[profile_type]
                )

    def test_filament_profile_discovery_includes_names_without_printer_suffix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_dir = root / "filament"
            profile_dir.mkdir()
            (profile_dir / "petg.json").write_text(
                json.dumps({"type": "filament", "name": "Generic PETG"}),
                encoding="utf-8",
            )
            with patch(
                "mcp_servers.slicer.service._find_profile_root", return_value=root
            ):
                result = self.provider.list_profiles("filament")

        self.assertEqual([item["name"] for item in result["profiles"]], ["Generic PETG"])

    def test_filament_profile_discovery_is_not_truncated_before_petg(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_dir = root / "filament"
            profile_dir.mkdir()
            for index in range(260):
                (profile_dir / f"aaa-{index:03d}.json").write_text(
                    json.dumps(
                        {
                            "type": "filament",
                            "name": f"AAA Filament {index:03d}",
                        }
                    ),
                    encoding="utf-8",
                )
            (profile_dir / "petg.json").write_text(
                json.dumps({"type": "filament", "name": "Generic PETG"}),
                encoding="utf-8",
            )
            with patch(
                "mcp_servers.slicer.service._find_profile_root", return_value=root
            ):
                result = self.provider.list_profiles("filament")
                filtered = self.provider.list_profiles("filament", "petg")

        names = [item["name"] for item in result["profiles"]]
        self.assertIn("Generic PETG", names)
        self.assertFalse(result["truncated"])
        self.assertEqual(
            [item["name"] for item in filtered["profiles"]],
            ["Generic PETG"],
        )
        self.assertEqual(filtered["query"], "petg")

    def test_rejects_path_outside_allowlist(self):
        with self.assertRaises(SlicerServiceError):
            self.provider.inspect_model("/etc/passwd")

    def test_inspects_tracked_stl(self):
        payload = self.provider.inspect_model(str(TEST_MODEL.relative_to(ROOT)))

        self.assertGreater(payload["size_bytes"], 0)
        self.assertGreater(payload["vertices"], 0)
        self.assertGreater(payload["faces"], 0)
        self.assertEqual(payload["suffix"], ".stl")
        self.assertEqual(len(payload["dimensions_mm"]), 3)

    def test_shared_script_accepts_slicer_input_root(self):
        input_dir = ROOT / "runtime/inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        model = input_dir / "mcp-allowlist-test.stl"
        model.write_text("solid test\nendsolid test\n", encoding="utf-8")
        generated_output = ROOT / "artifacts/slicer/mcp-allowlist-test"

        env = os.environ.copy()
        env["SLICER_AUTO_INSTALL"] = "0"
        env["BAMBU_STUDIO_BIN"] = "/definitely/not/a/slicer"

        try:
            completed = subprocess.run(
                [
                    "bash",
                    str(service_module.SLICE_SCRIPT),
                    str(model.relative_to(ROOT)),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            model.unlink(missing_ok=True)
            shutil.rmtree(generated_output, ignore_errors=True)

        self.assertEqual(completed.returncode, 3)
        self.assertIn("Bambu Studio is not installed", completed.stderr)
        self.assertNotIn("input must be under", completed.stderr)

    def test_slice_uses_shared_script_and_normalizes_paths(self):
        with patch(
            "mcp_servers.slicer.service._run_process_group",
            side_effect=self._successful_process,
        ) as mocked:
            payload = self.provider.slice(
                str(TEST_MODEL.relative_to(ROOT)),
                orient=False,
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "bambustudio")
        self.assertFalse(payload["orient"])
        self.assertEqual(payload["input"], str(TEST_MODEL.relative_to(ROOT)))
        self.assertTrue(payload["artifact"].startswith("artifacts/slicer/"))
        self.assertFalse(Path(payload["artifact"]).is_absolute())
        self.assertFalse(Path(payload["output_dir"]).is_absolute())
        self.assertFalse(Path(payload["log"]).is_absolute())

        command = mocked.call_args.args[0]
        self.assertEqual(command[0], "bash")
        self.assertTrue(command[1].endswith("scripts/slicer/slice-stl.sh"))
        env = mocked.call_args.kwargs["env"]
        self.assertEqual(env["SLICER_ORIENT"], "0")
        self.assertEqual(
            env["SLICER_MACHINE_PROFILE"],
            "Bambu Lab H2D 0.4 nozzle",
        )

    def test_detected_slicer_binary_is_forwarded_to_shared_script(self):
        custom = Path("/tmp/custom-bambu-studio")
        with (
            patch(
                "mcp_servers.slicer.service._find_slicer",
                return_value=custom,
            ),
            patch(
                "mcp_servers.slicer.service._run_process_group",
                side_effect=self._successful_process,
            ) as mocked,
        ):
            self.provider.slice(str(TEST_MODEL.relative_to(ROOT)))

        self.assertEqual(
            mocked.call_args.kwargs["env"]["BAMBU_STUDIO_BIN"],
            str(custom),
        )


    def test_detected_profile_root_is_forwarded_to_shared_script(self):
        profile_root = Path("/tmp/bambu-profiles/BBL")
        with (
            patch(
                "mcp_servers.slicer.service._find_profile_root",
                return_value=profile_root,
            ),
            patch(
                "mcp_servers.slicer.service._run_process_group",
                side_effect=self._successful_process,
            ) as mocked,
        ):
            self.provider.slice(str(TEST_MODEL.relative_to(ROOT)))

        self.assertEqual(
            mocked.call_args.kwargs["env"]["BAMBU_PROFILE_ROOT"],
            str(profile_root),
        )

    def test_rejects_non_object_result_json(self):
        def fake_process(command, **kwargs):
            output_dir = Path(command[-1])
            (output_dir / "result.json").write_text(
                json.dumps(["not", "an", "object"]),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch(
            "mcp_servers.slicer.service._run_process_group",
            side_effect=fake_process,
        ):
            with self.assertRaisesRegex(
                SlicerServiceError,
                "must contain a JSON object",
            ):
                self.provider.slice(str(TEST_MODEL.relative_to(ROOT)))

    def test_success_result_requires_real_nonempty_3mf(self):
        def fake_process(command, **kwargs):
            output_dir = Path(command[-1])
            missing = output_dir / "missing.sliced.3mf"
            (output_dir / "result.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "categories": [],
                        "fatal_categories": [],
                        "slicer_exit": 0,
                        "input": str(TEST_MODEL.resolve()),
                        "artifact": str(missing),
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch(
            "mcp_servers.slicer.service._run_process_group",
            side_effect=fake_process,
        ):
            with self.assertRaisesRegex(
                SlicerServiceError,
                "missing or empty",
            ):
                self.provider.slice(str(TEST_MODEL.relative_to(ROOT)))

    def test_failed_result_preserves_missing_artifact_diagnostics(self):
        def fake_process(command, **kwargs):
            output_dir = Path(command[-1])
            missing = output_dir / "missing.sliced.3mf"
            (output_dir / "slicer.log").write_text(
                "profile mismatch\n",
                encoding="utf-8",
            )
            (output_dir / "result.json").write_text(
                json.dumps(
                    {
                        "ok": False,
                        "categories": ["MISSING_OUTPUT", "PROFILE_MISMATCH"],
                        "fatal_categories": ["MISSING_OUTPUT", "PROFILE_MISMATCH"],
                        "slicer_exit": 2,
                        "input": str(TEST_MODEL.resolve()),
                        "artifact": str(missing),
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 2, "", "")

        with patch(
            "mcp_servers.slicer.service._run_process_group",
            side_effect=fake_process,
        ):
            result = self.provider.slice(str(TEST_MODEL.relative_to(ROOT)))

        self.assertFalse(result["ok"])
        self.assertIsNone(result["artifact"])
        self.assertTrue(result["expected_artifact"].endswith("missing.sliced.3mf"))
        self.assertIn("PROFILE_MISMATCH", result["fatal_categories"])
        self.assertTrue(result["log"].endswith("slicer.log"))

    def test_output_directory_stem_is_sanitized(self):
        input_dir = ROOT / "runtime/inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        model = input_dir / "hinge weird @ name!.stl"
        model.write_text("solid test\nendsolid test\n", encoding="utf-8")

        try:
            with patch(
                "mcp_servers.slicer.service._run_process_group",
                side_effect=self._successful_process,
            ) as mocked:
                payload = self.provider.slice(str(model.relative_to(ROOT)))
        finally:
            model.unlink(missing_ok=True)

        output_name = Path(mocked.call_args.args[0][-1]).name
        self.assertRegex(output_name, r"^mcp-hinge-weird-name-[0-9a-f]{10}$")
        self.assertNotIn("@", output_name)
        self.assertNotIn("!", output_name)
        self.assertTrue(payload["ok"])

    def test_cleanup_removes_only_stale_mcp_directories(self):
        temporary_output_root = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_output_root.cleanup)
        output_root = (Path(temporary_output_root.name) / "slicer-output").resolve()
        with patch.object(service_module, "OUTPUT_ROOT", output_root):
            self._assert_cleanup_removes_only_stale_mcp_directories(output_root)

    def _assert_cleanup_removes_only_stale_mcp_directories(self, output_root):
        output_root.mkdir(parents=True, exist_ok=True)
        stale = output_root / "mcp-stale-test"
        fresh = output_root / "mcp-fresh-test"
        unrelated = output_root / "manual-keep-test"
        now = service_module.time.time()

        for directory in (stale, fresh, unrelated):
            directory.mkdir(exist_ok=True)
        os.utime(stale, (now - 7200, now - 7200))
        os.utime(fresh, (now - 60, now - 60))
        os.utime(unrelated, (now - 7200, now - 7200))

        try:
            with patch.dict(
                os.environ,
                {"SLICER_MCP_RETENTION_HOURS": "1"},
                clear=False,
            ):
                removed = service_module._cleanup_stale_mcp_outputs(now=now)

            self.assertEqual(removed, 1)
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(unrelated.exists())
        finally:
            shutil.rmtree(stale, ignore_errors=True)
            shutil.rmtree(fresh, ignore_errors=True)
            shutil.rmtree(unrelated, ignore_errors=True)

    def test_rejects_artifact_path_outside_allocated_output(self):
        def fake_process(command, **kwargs):
            output_dir = Path(command[-1])
            (output_dir / "result.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "input": str(TEST_MODEL.resolve()),
                        "artifact": "/tmp/escaped.sliced.3mf",
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch(
            "mcp_servers.slicer.service._run_process_group",
            side_effect=fake_process,
        ):
            with self.assertRaisesRegex(
                SlicerServiceError,
                "artifact escaped",
            ):
                self.provider.slice(str(TEST_MODEL.relative_to(ROOT)))

    def test_generated_model_metadata_and_bounded_inline_transfer(self):
        data = TEST_MODEL.read_bytes()
        payload = self.service.get_model(
            str(TEST_MODEL.relative_to(ROOT)),
            include_base64=True,
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["path"], str(TEST_MODEL.relative_to(ROOT)))
        self.assertEqual(payload["filename"], TEST_MODEL.name)
        self.assertEqual(payload["size_bytes"], len(data))
        self.assertEqual(payload["sha256"], hashlib.sha256(data).hexdigest())
        self.assertTrue(payload["base64"])

    def test_generated_3mf_model_can_be_retrieved(self):
        model = TEST_MODEL.with_suffix(".3mf")
        data = b"generated-3mf"
        model.write_bytes(data)
        try:
            payload = self.service.get_model(
                str(model.relative_to(ROOT)),
                include_base64=True,
            )
        finally:
            model.unlink(missing_ok=True)

        self.assertEqual(payload["filename"], model.name)
        self.assertEqual(payload["size_bytes"], len(data))
        self.assertEqual(payload["sha256"], hashlib.sha256(data).hexdigest())

    def test_generated_model_inline_transfer_is_size_bounded(self):
        with patch.dict(
            os.environ,
            {"SLICER_MCP_MAX_ARTIFACT_BYTES": "4"},
            clear=False,
        ):
            metadata = self.service.get_model(
                str(TEST_MODEL.relative_to(ROOT)),
                include_base64=False,
            )
            with self.assertRaisesRegex(
                SlicerServiceError,
                "model is too large for inline MCP transfer",
            ):
                self.service.get_model(
                    str(TEST_MODEL.relative_to(ROOT)),
                    include_base64=True,
                )

        self.assertGreater(metadata["size_bytes"], 4)
        self.assertNotIn("base64", metadata)

    def test_generated_model_rejects_paths_and_suffixes_outside_contract(self):
        with self.assertRaisesRegex(
            SlicerServiceError,
            "must be under runtime/inputs",
        ):
            self.service.get_model("/etc/passwd")

        invalid = TEST_MODEL.parent / "not-a-model.txt"
        invalid.write_text("not a model", encoding="utf-8")
        try:
            with self.assertRaisesRegex(
                SlicerServiceError,
                "must end in .stl or .3mf",
            ):
                self.service.get_model(str(invalid.relative_to(ROOT)))
        finally:
            invalid.unlink(missing_ok=True)

    @unittest.skipIf(os.name == "nt", "symlink contract is POSIX-specific")
    def test_file_retrieval_rejects_symlink_escape_from_both_roots(self):
        outside_model = ROOT / "outside-model.stl"
        outside_artifact = ROOT / "outside-artifact.3mf"
        outside_model.write_bytes(b"outside-model")
        outside_artifact.write_bytes(b"outside-artifact")

        model_link = TEST_MODEL.parent / "escaped.stl"
        artifact_dir = service_module.OUTPUT_ROOT / "mcp-symlink-contract-test"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_link = artifact_dir / "escaped.3mf"
        model_link.symlink_to(outside_model)
        artifact_link.symlink_to(outside_artifact)

        try:
            with self.assertRaisesRegex(
                SlicerServiceError,
                "must be under runtime/inputs",
            ):
                self.service.get_model(str(model_link.relative_to(ROOT)))
            with self.assertRaisesRegex(
                SlicerServiceError,
                "must remain under artifacts/slicer",
            ):
                self.service.get_artifact(str(artifact_link.relative_to(ROOT)))
        finally:
            model_link.unlink(missing_ok=True)
            artifact_link.unlink(missing_ok=True)
            outside_model.unlink(missing_ok=True)
            outside_artifact.unlink(missing_ok=True)
            shutil.rmtree(artifact_dir, ignore_errors=True)

    def test_artifact_metadata_and_bounded_inline_transfer(self):
        output_dir = service_module.OUTPUT_ROOT / "mcp-artifact-contract-test"
        output_dir.mkdir(parents=True, exist_ok=True)
        artifact = output_dir / "part.sliced.3mf"
        artifact.write_bytes(b"print-ready")
        try:
            payload = self.service.get_artifact(
                str(artifact.relative_to(ROOT)),
                include_base64=True,
            )
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["filename"], "part.sliced.3mf")
        self.assertEqual(payload["size_bytes"], 11)
        self.assertEqual(payload["base64"], "cHJpbnQtcmVhZHk=")
        self.assertEqual(
            payload["sha256"],
            hashlib.sha256(b"print-ready").hexdigest(),
        )

    def test_artifact_retrieval_remains_restricted_to_sliced_3mf_outputs(self):
        with self.assertRaisesRegex(
            SlicerServiceError,
            "must remain under artifacts/slicer",
        ):
            self.service.get_artifact(str(TEST_MODEL.relative_to(ROOT)))

        output_dir = service_module.OUTPUT_ROOT / "mcp-artifact-suffix-test"
        output_dir.mkdir(parents=True, exist_ok=True)
        invalid = output_dir / "part.stl"
        invalid.write_bytes(b"not-a-sliced-artifact")
        try:
            with self.assertRaisesRegex(
                SlicerServiceError,
                "artifact must be a .3mf file",
            ):
                self.service.get_artifact(str(invalid.relative_to(ROOT)))
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_workspace_slice_is_bound_to_generated_model(self):
        manager = Mock()
        manager.require_generated_model.return_value = Workspace(
            "0123456789abcdef0123",
            "example/consumer",
            "0123456789abcdef0123456789abcdef01234567",
            Path("/tmp/workspace"),
        )
        service = SlicerService(self.provider, manager)
        with patch.object(
            self.provider,
            "slice",
            return_value={"ok": True, "artifact": "artifacts/slicer/x/part.sliced.3mf"},
        ):
            payload = service.slice_model(
                workspace="0123456789abcdef0123",
                path_value="runtime/inputs/0123456789abcdef0123/part.stl",
            )

        self.assertEqual(payload["workspace"], "0123456789abcdef0123")
        self.assertEqual(payload["repository"], "example/consumer")
        self.assertEqual(
            payload["commit"],
            "0123456789abcdef0123456789abcdef01234567",
        )

    def test_prepare_print_never_marks_print_started(self):
        with patch.object(
            self.provider,
            "slice",
            return_value={
                "ok": True,
                "artifact": "artifacts/slicer/test/part.sliced.3mf",
                "categories": [],
            },
        ):
            payload = self.service.prepare_print(
                path_value=str(TEST_MODEL.relative_to(ROOT))
            )

        self.assertTrue(payload["ready_for_print"])
        self.assertFalse(payload["print_started"])
        self.assertFalse(payload["printer_handoff_available"])

    def test_timeout_is_a_safe_error(self):
        with patch(
            "mcp_servers.slicer.service._run_process_group",
            side_effect=subprocess.TimeoutExpired(["bash"], 5),
        ):
            with self.assertRaisesRegex(SlicerServiceError, "exceeded timeout"):
                self.provider.slice(str(TEST_MODEL.relative_to(ROOT)))

    def test_timeout_terminates_posix_process_group(self):
        process = Mock()
        process.pid = 4321
        process.returncode = -signal.SIGTERM
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["bash", "slice-stl.sh"], 1),
            ("partial stdout", "partial stderr"),
        ]

        with (
            patch(
                "mcp_servers.slicer.service.subprocess.Popen",
                return_value=process,
            ) as popen,
            patch("mcp_servers.slicer.service.os.killpg") as killpg,
            patch.object(service_module.os, "name", "posix"),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                service_module._run_process_group(
                    ["bash", "slice-stl.sh"],
                    cwd=ROOT,
                    env={},
                    timeout=1,
                )

        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once_with(4321, signal.SIGTERM)
        self.assertEqual(process.communicate.call_count, 2)


class SlicerMcpContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_expected_tools_are_registered(self):
        tools = await server.mcp.list_tools()
        names = {tool.name for tool in tools}

        self.assertEqual(
            names,
            {
                "slicer_prepare_workspace",
                "slicer_generate_model",
                "slicer_capabilities",
                "slicer_inspect_model",
                "slicer_list_profiles",
                "slicer_slice",
                "slicer_validate_for_print",
                "slicer_prepare_print",
                "slicer_get_model",
                "slicer_get_artifact",
                "slicer_get_diagnostics",
            },
        )

    async def test_profile_overrides_are_optional_in_tool_schemas(self):
        tools = {tool.name: tool for tool in await server.mcp.list_tools()}
        for tool_name in (
            "slicer_slice",
            "slicer_validate_for_print",
            "slicer_prepare_print",
        ):
            with self.subTest(tool=tool_name):
                schema = tools[tool_name].input_schema
                self.assertNotIn("machine_profile", schema.get("required", []))
                self.assertNotIn("process_profile", schema.get("required", []))
                self.assertNotIn("filament_profile", schema.get("required", []))

    async def test_profile_discovery_query_is_optional(self):
        tools = {tool.name: tool for tool in await server.mcp.list_tools()}
        schema = tools["slicer_list_profiles"].input_schema
        self.assertIn("query", schema.get("properties", {}))
        self.assertNotIn("query", schema.get("required", []))

    def test_stdio_transport_uses_default_sdk_run(self):
        with (
            patch.dict(
                os.environ,
                {"SLICER_MCP_TRANSPORT": "stdio"},
                clear=False,
            ),
            patch.object(server.mcp, "run") as run,
        ):
            server.main()

        run.assert_called_once_with()

    def test_streamable_http_transport_serves_authenticated_asgi_app(self):
        app = Mock()
        with (
            patch.dict(
                os.environ,
                {
                    "SLICER_MCP_TRANSPORT": "streamable-http",
                    "SLICER_MCP_HOST": "127.0.0.1",
                    "SLICER_MCP_PORT": "8123",
                    "SLICER_MCP_PATH": "/mcp-test",
                },
                clear=False,
            ),
            patch.object(server, "build_http_app", return_value=app) as build,
            patch.object(server.uvicorn, "run") as run,
        ):
            server.main()

        build.assert_called_once_with("/mcp-test")
        run.assert_called_once_with(
            app,
            host="127.0.0.1",
            port=8123,
            log_level="info",
        )


if __name__ == "__main__":
    unittest.main()
