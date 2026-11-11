"""Проверки структуры и реальной точки входа без Docker и сети."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProjectTests(unittest.TestCase):
    def test_single_root_entrypoint_and_no_bash_files(self):
        self.assertEqual(sorted(path.name for path in ROOT.glob("*.py")), ["xray.py"])
        self.assertEqual(list(ROOT.glob("*.sh")), [])
        for folder in ("src", "tests"):
            self.assertEqual(list((ROOT / folder).rglob("*.sh")), [])
        self.assertTrue(os.access(ROOT / "xray.py", os.X_OK))

    def test_entrypoint_help_from_other_directory_without_docker_or_settings(self):
        with tempfile.TemporaryDirectory(
            prefix="xray-help-", dir="/tmp/opencode"
        ) as work:
            for command in (
                None,
                "setup",
                "check",
            ):
                with self.subTest(command=command):
                    args = [sys.executable, "-B", str(ROOT / "xray.py")]
                    if command:
                        args.append(command)
                    result = subprocess.run(
                        [*args, "--help"],
                        cwd=work,
                        env={"PATH": "", "PYTHONUTF8": "1"},
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage:", result.stdout)
                    self.assertEqual(list(Path(work).iterdir()), [])

    def test_entrypoint_reports_missing_docker_without_traceback(self):
        with tempfile.TemporaryDirectory(
            prefix="xray-cli-", dir="/tmp/opencode"
        ) as work:
            result = subprocess.run(
                [sys.executable, "-B", str(ROOT / "xray.py"), "check"],
                cwd=work,
                env={"PATH": "", "PYTHONUTF8": "1"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("Необходима команда: docker", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
