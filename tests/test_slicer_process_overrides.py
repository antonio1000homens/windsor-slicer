from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/slicer/apply-process-overrides.py"
SPEC = importlib.util.spec_from_file_location("process_overrides", HELPER)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ProcessOverrideTests(unittest.TestCase):
    def apply(self, mode: str, initial: dict[str, object] | None = None):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "process.json"
            original = initial or {
                "enable_support": "1",
                "support_type": "tree(auto)",
                "support_threshold_angle": "27",
                "layer_height": "0.2",
                "custom_setting": ["preserve", "me"],
            }
            path.write_text(json.dumps(original), encoding="utf-8")
            metadata = MODULE.apply_overrides(path, mode)
            return json.loads(path.read_text(encoding="utf-8")), metadata

    def test_off_disables_support_and_preserves_inherited_type_and_other_settings(self):
        process, metadata = self.apply("off")
        self.assertEqual(process["enable_support"], "0")
        self.assertEqual(process["support_type"], "tree(auto)")
        self.assertEqual(process["support_threshold_angle"], "27")
        self.assertEqual(process["custom_setting"], ["preserve", "me"])
        self.assertEqual(metadata["enabled"], False)

    def test_normal_auto_uses_pinned_bambu_enum(self):
        process, metadata = self.apply("normal-auto")
        self.assertEqual(process["enable_support"], "1")
        self.assertEqual(process["support_type"], "normal(auto)")
        self.assertEqual(metadata["type"], "normal(auto)")

    def test_tree_auto_uses_pinned_bambu_enum(self):
        process, metadata = self.apply("tree-auto")
        self.assertEqual(process["enable_support"], "1")
        self.assertEqual(process["support_type"], "tree(auto)")
        self.assertEqual(metadata["type"], "tree(auto)")

    def test_unknown_mode_is_rejected_without_changing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "process.json"
            original = {"enable_support": "0", "layer_height": "0.2"}
            path.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "support_mode"):
                MODULE.apply_overrides(path, "custom")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_omitted_mode_reports_profile_state_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "process.json"
            original = {
                "enable_support": "1",
                "support_type": "tree(auto)",
                "support_threshold_angle": "30",
                "support_on_build_plate_only": "0",
            }
            serialized = json.dumps(original)
            path.write_text(serialized, encoding="utf-8")
            metadata = MODULE.apply_overrides(path, None)

            self.assertEqual(path.read_text(encoding="utf-8"), serialized)
            self.assertEqual(
                metadata,
                {
                    "mode": None,
                    "enabled": True,
                    "type": "tree(auto)",
                    "threshold_angle": 30,
                    "build_plate_only": False,
                },
            )


if __name__ == "__main__":
    unittest.main()
