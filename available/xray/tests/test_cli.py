"""Проверки интерфейса и вызовов Docker без установленного Docker."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.cli import main
from src.compose import Compose, DockerError
from src.configuration import create_configuration, create_server_configuration
from src.storage import locked_root, write_configuration, write_server_configuration


class CliTests(unittest.TestCase):
    def setUp(self):
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.temp = tempfile.TemporaryDirectory(prefix="xray-cli-", dir="/tmp/opencode")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with locked_root(self.root) as parent:
            write_server_configuration(
                parent,
                "xray_data",
                create_server_configuration("example.com"),
                "example.com",
            )
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

    def test_hctl_commands_are_rejected_before_docker(self):
        for command in (
            "up",
            "down",
            "status",
            "logs",
            "pull",
            "restart",
            "ps",
            "stop",
            "start",
        ):
            with self.subTest(command=command), self.assertRaises(SystemExit) as raised:
                self.run_cli(command)
            self.assertEqual(raised.exception.code, 2)
        self.compose_class.assert_not_called()

    def test_check_validates_both_backends_without_generation(self):
        self.assertEqual(self.run_cli("check"), 0)
        self.compose.config.assert_called_once_with()
        self.assertEqual(
            self.compose.run.call_args_list,
            [
                call(
                    "run",
                    "--pull",
                    "never",
                    "--rm",
                    "--no-deps",
                    "app",
                    "run",
                    "-test",
                    "-c",
                    "/etc/xray/config.json",
                ),
                call("run", "--pull", "never", "--rm", "--no-deps", "site", "-t"),
            ],
        )
        self.assertIn("пройдены", self.out.getvalue())

    def test_check_failure_stops_sequence(self):
        self.compose.run.side_effect = DockerError("Проверка не пройдена", 7)
        self.assertEqual(self.run_cli("check"), 7)
        self.assertEqual(self.compose.run.call_count, 1)
        self.assertNotIn("пройдены", self.out.getvalue())

    def test_removed_migration_command_is_rejected_before_docker(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_cli("migrate-layout")
        self.assertEqual(raised.exception.code, 2)
        self.compose_class.assert_not_called()

    def test_invalid_arguments_never_call_docker(self):
        for args in (
            ("setup", "--pull"),
            ("check", "app"),
            ("setup", "--force"),
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

    def test_setup_creates_server_without_user_or_client_files(self):
        with tempfile.TemporaryDirectory(
            prefix="xray-new-", dir="/tmp/opencode"
        ) as root:
            self.root = Path(root)
            self.assertEqual(self.run_cli("setup"), 0)

            data = self.root / "xray_data"
            server = json.loads((data / "config/config.json").read_text())
            registry = json.loads((data / "users.json").read_text())
            self.assertEqual(server["inbounds"][0]["settings"]["clients"], [])
            self.assertEqual(registry["users"], [])
            self.assertFalse((data / "client").exists())
            self.assertEqual(self.run_cli("setup"), 1)

    def test_user_commands_only_validate_candidate_and_never_restart(self):
        self.assertEqual(self.run_cli("users", "create", "phone"), 0)
        self.assertIn("../../hctl restart xray", self.out.getvalue())
        self.compose.run.assert_called_once_with(
            "run",
            "--pull",
            "never",
            "--rm",
            "--no-deps",
            "--volume",
            f"{self.root}/xray_data/config/.users-candidate.json:/etc/xray/config.json:ro",
            "app",
            "run",
            "-test",
            "-c",
            "/etc/xray/config.json",
        )
        self.compose.run.reset_mock()
        self.out = io.StringIO()
        self.assertEqual(self.run_cli("users", "show", "phone", "--format", "json"), 0)
        profile = json.loads(self.out.getvalue())
        uid = profile["outbounds"][0]["settings"]["vnext"][0]["users"][0]["id"]
        self.out = io.StringIO()
        self.assertEqual(self.run_cli("users", "list"), 0)
        self.assertNotIn(uid, self.out.getvalue())
        self.assertIn("phone\tактивен", self.out.getvalue())
        self.compose.run.assert_not_called()
        self.assertEqual(self.run_cli("users", "suspend", "phone"), 0)
        self.assertEqual(self.run_cli("users", "resume", "phone"), 0)
        self.out = io.StringIO()
        self.assertEqual(self.run_cli("users", "show", "phone", "--format", "link"), 0)
        self.assertTrue(self.out.getvalue().startswith(f"vless://{uid}@"))
        self.assertEqual(self.run_cli("users", "delete", "phone", "--yes"), 0)
        self.assertEqual(self.run_cli("users", "show", "phone"), 1)

    def test_delete_confirmation_defaults_to_cancel(self):
        self.assertEqual(self.run_cli("users", "create", "phone"), 0)
        self.compose.run.reset_mock()
        with patch("builtins.input", return_value="нет"):
            self.assertEqual(self.run_cli("users", "delete", "phone"), 1)
        with patch("builtins.input", side_effect=EOFError):
            self.assertEqual(self.run_cli("users", "delete", "phone"), 1)
        self.compose.run.assert_not_called()
        with patch("builtins.input", return_value="да"):
            self.assertEqual(self.run_cli("users", "delete", "phone"), 0)

    def test_failed_user_validation_preserves_configuration(self):
        before = (self.root / "xray_data/config/config.json").read_bytes()
        self.compose.run.side_effect = DockerError("Не прошла проверка", 7)
        self.assertEqual(self.run_cli("users", "create", "phone"), 7)
        self.assertEqual(
            (self.root / "xray_data/config/config.json").read_bytes(), before
        )
        self.assertNotIn("Изменения сохранены", self.out.getvalue())

    def test_check_rejects_registry_mismatch_before_docker_run(self):
        self.assertEqual(self.run_cli("users", "create", "phone"), 0)
        (self.root / "xray_data/users.json").write_text(
            '{"version":1,"domain":"example.com","users":[]}'
        )
        self.compose.run.reset_mock()
        self.assertEqual(self.run_cli("check"), 1)
        self.compose.run.assert_not_called()

    def test_import_command_preserves_existing_user(self):
        with tempfile.TemporaryDirectory(
            prefix="xray-import-", dir="/tmp/opencode"
        ) as root:
            self.root = Path(root)
            server, client, uri = create_configuration("example.com")
            with locked_root(self.root) as parent:
                write_configuration(parent, "xray_data", server, client, uri)
            self.assertEqual(self.run_cli("users", "import", "legacy"), 0)
            self.out = io.StringIO()
            self.assertEqual(
                self.run_cli("users", "show", "legacy", "--format", "link"), 0
            )
            self.assertIn(
                server["inbounds"][0]["settings"]["clients"][0]["id"],
                self.out.getvalue(),
            )

    def test_user_help_and_invalid_arguments_do_not_require_docker(self):
        for action in (
            "create",
            "list",
            "show",
            "suspend",
            "resume",
            "delete",
            "import",
        ):
            with self.subTest(action=action), self.assertRaises(SystemExit) as error:
                self.run_cli("users", action, "--help")
            self.assertEqual(error.exception.code, 0)
        for args in (
            ("users",),
            ("users", "create"),
            ("users", "show", "phone", "--format", "invalid"),
        ):
            with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                self.run_cli(*args)
            self.assertEqual(error.exception.code, 2)
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
