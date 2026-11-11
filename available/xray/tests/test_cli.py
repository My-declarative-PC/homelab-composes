"""Проверки интерфейса и вызовов Docker без установленного Docker."""

import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.cli import main
from src.compose import Compose, DockerError


class CliTests(unittest.TestCase):
    def setUp(self):
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.root = Path("/tmp/opencode/unused-xray")
        self.factory = patch("src.cli.Compose")
        self.compose_class = self.factory.start()
        self.addCleanup(self.factory.stop)
        self.compose = self.compose_class.return_value
        self.compose.run.return_value = "Состояние сервисов\n"
        self.compose.config.return_value = {
            "x-xray-setup": {"container_name": "xray", "domain": "example.com"},
            "services": {"app": {"image": "xray:test"}},
        }

    def run_cli(self, *args):
        with redirect_stdout(self.out), redirect_stderr(self.err):
            return main(self.root, list(args))

    def test_lifecycle_commands(self):
        for args, expected in (
            (("up",), call("up", "-d")),
            (("up", "app", "site"), call("up", "-d", "app", "site")),
            (("down",), call("down")),
            (("status",), call("ps")),
        ):
            with self.subTest(args=args):
                self.compose.reset_mock()
                self.assertEqual(self.run_cli(*args), 0)
                self.compose_class.assert_called_with(self.root)
                self.assertEqual(self.compose.run.call_args_list, [expected])
                self.compose.config.assert_not_called()

    def test_check_validates_both_backends_without_generation(self):
        self.assertEqual(self.run_cli("check"), 0)
        self.compose.config.assert_called_once_with()
        self.assertEqual(
            self.compose.run.call_args_list,
            [
                call(
                    "run",
                    "--rm",
                    "--no-deps",
                    "app",
                    "run",
                    "-test",
                    "-c",
                    "/etc/xray/config.json",
                ),
                call("run", "--rm", "--no-deps", "site", "-t"),
            ],
        )
        self.compose.docker_run.assert_not_called()
        self.assertIn("пройдены", self.out.getvalue())

    def test_check_failure_stops_sequence(self):
        self.compose.run.side_effect = DockerError("Проверка не пройдена", 7)
        self.assertEqual(self.run_cli("check"), 7)
        self.assertEqual(self.compose.run.call_count, 1)
        self.assertNotIn("пройдены", self.out.getvalue())

    @patch("src.cli.migrate_layout")
    def test_migration_uses_compose_settings_without_starting_containers(self, migrate):
        self.compose.config.return_value["x-xray-setup"]["container_name"] = "custom"
        self.assertEqual(self.run_cli("migrate-layout"), 0)
        migrate.assert_called_once_with(self.root, "custom_data")
        self.compose.run.assert_not_called()
        self.compose.docker_run.assert_not_called()

    def test_logs_stream_only_on_explicit_request(self):
        self.assertEqual(self.run_cli("logs", "--tail", "12", "--follow", "app"), 0)
        self.compose.run.assert_called_once_with(
            "logs", "--tail", "12", "--follow", "app", stream=True
        )

    def test_logs_default_tail(self):
        self.assertEqual(self.run_cli("logs"), 0)
        self.compose.run.assert_called_once_with("logs", "--tail", "50", stream=True)

    def test_invalid_arguments_never_call_docker(self):
        for args in (
            ("down", "--volumes"),
            ("up", "unknown"),
            ("logs", "--tail", "-1"),
        ):
            with self.subTest(args=args), self.assertRaises(SystemExit) as raised:
                self.run_cli(*args)
            self.assertEqual(raised.exception.code, 2)
        self.compose_class.assert_not_called()

    def test_help_does_not_require_docker(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli("--help")
        self.assertEqual(raised.exception.code, 0)
        self.compose_class.assert_not_called()


class ComposeTests(unittest.TestCase):
    @patch("src.compose.shutil.which", return_value=None)
    def test_missing_docker(self, which):
        with self.assertRaisesRegex(DockerError, "Необходима команда: docker"):
            Compose(Path("/tmp/opencode"))

    @patch("src.compose.shutil.which", return_value="/fake/docker")
    @patch("src.compose.subprocess.run")
    def test_compose_uses_project_directory(self, run, which):
        run.return_value = subprocess.CompletedProcess([], 0, stdout="{}")
        root = Path("/tmp/opencode/project")
        self.assertEqual(Compose(root).config(), {})
        run.assert_called_once_with(
            ["/fake/docker", "compose", "config", "--format", "json"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

    @patch("src.compose.shutil.which", return_value="/fake/docker")
    @patch("src.compose.subprocess.run")
    def test_compose_errors_do_not_expose_output(self, run, which):
        run.return_value = subprocess.CompletedProcess(
            [], 17, stdout="secret UUID", stderr="secret password"
        )
        with self.assertRaises(DockerError) as raised:
            Compose(Path("/tmp/opencode")).config()
        self.assertEqual(raised.exception.returncode, 17)
        self.assertNotIn("secret", str(raised.exception))

    @patch("src.compose.shutil.which", return_value="/fake/docker")
    @patch("src.compose.subprocess.run")
    def test_invalid_json_is_reported_safely(self, run, which):
        for output in ("secret", "[]", "null"):
            run.return_value = subprocess.CompletedProcess([], 0, stdout=output)
            with self.assertRaisesRegex(DockerError, "некорректный JSON"):
                Compose(Path("/tmp/opencode")).config()
