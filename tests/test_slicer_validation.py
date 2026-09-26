#!/usr/bin/env python3
"""Regression tests for cloud slicer diagnostic classification."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLASSIFIER = ROOT / "scripts/slicer/classify-slicer-log.py"


class SlicerClassifierTests(unittest.TestCase):
    def classify(self, log_text: str, slicer_exit: int = 0):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log = tmp_path / "slicer.log"
            artifact = tmp_path / "part.sliced.3mf"
            result = tmp_path / "result.json"
            log.write_text(log_text, encoding="utf-8")
            artifact.write_bytes(b"valid-test-artifact")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLASSIFIER),
                    "--log",
                    str(log),
                    "--output",
                    str(result),
                    "--slicer-exit",
                    str(slicer_exit),
                    "--artifact",
                    str(artifact),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            payload = json.loads(result.read_text(encoding="utf-8"))
            return completed, payload

    def test_floating_cantilever_is_fatal_even_when_slicer_exits_zero(self):
        completed, payload = self.classify(
            "[warning] It seems object enclosure has floating cantilever\n"
        )

        self.assertEqual(completed.returncode, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("FLOATING_REGION", payload["fatal_categories"])

    def test_bambu_nonfatal_error_level_chatter_does_not_fail_clean_slice(self):
        completed, payload = self.classify(
            "[error] Invalid T command (T1001).\n"
            "[warning] CLI mode\n"
        )

        self.assertEqual(completed.returncode, 0)
        self.assertTrue(payload["ok"])
        self.assertNotIn("SLICER_ERROR", payload["categories"])

    def test_nonzero_slicer_exit_is_fatal(self):
        completed, payload = self.classify("run stopped\n", slicer_exit=13)

        self.assertEqual(completed.returncode, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("SLICER_ERROR", payload["fatal_categories"])

    def test_explicit_negative_return_marker_is_fatal(self):
        completed, payload = self.classify("run found error, return -13, exit...\n")

        self.assertEqual(completed.returncode, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("SLICER_ERROR", payload["fatal_categories"])


if __name__ == "__main__":
    unittest.main()
