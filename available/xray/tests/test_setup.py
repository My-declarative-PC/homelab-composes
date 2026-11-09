"""Проверки без запуска контейнеров; разбор Compose выполняет настоящий Docker CLI."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit


SOURCE = Path(__file__).resolve().parents[1]
DOCKER = shutil.which("docker")


@unittest.skipUnless(DOCKER, "Требуется Docker CLI с Compose")
class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="xray-test-", dir="/tmp/opencode"
        )
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.work = root / "available/xray"
        self.work.mkdir(parents=True)
        for name in ("setup.sh", "compose.yaml"):
            shutil.copy2(SOURCE / name, self.work / name)
        shutil.copytree(SOURCE.parent / "restic", root / "available/restic")
        self.bin = root / "bin"
        self.bin.mkdir()
        self.calls = root / "calls.jsonl"
        mock = self.bin / "docker"
        mock.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ['compose', 'config']:
    os.execv(os.environ['REAL_DOCKER'], [os.environ['REAL_DOCKER'], *args])
with open(os.environ['DOCKER_CALLS'], 'a') as file:
    file.write(json.dumps(args) + '\\n')
if args[:1] == ['pull']:
    sys.exit(0)
if args[:1] == ['run'] and args[-1:] == ['x25519']:
    print(os.environ.get('MOCK_KEYS', 'PrivateKey: test-private\\nPassword (PublicKey): test-public'))
    sys.exit(0)
if args == ['compose', 'run', '--rm', '--no-deps', 'app', 'run', '-test', '-c', '/etc/xray/config.json']:
    sys.exit(int(os.environ.get('MOCK_TEST_EXIT', '0')))
sys.exit('Неожиданная команда Docker: ' + repr(args))
""")
        mock.chmod(0o755)
        curl = self.bin / "curl"
        curl.write_text('#!/bin/sh\nprintf "%s" "${MOCK_IP:-203.0.113.20}"\n')
        curl.chmod(0o755)
        self.env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in (
                "CONTAINER_NAME",
                "XRAY_PORT",
                "TARGET_HOST",
                "SERVER_IP",
                "RESTIC_PASSWORD",
                "COMPOSE_FILE",
                "COMPOSE_ENV_FILES",
                "COMPOSE_PROJECT_NAME",
                "COMPOSE_DISABLE_ENV_FILE",
            )
        }
        self.env.update(
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            REAL_DOCKER=DOCKER,
            DOCKER_CALLS=str(self.calls),
        )
        self.write_env()

    def write_env(self, **overrides):
        settings = dict(
            CONTAINER_NAME="xray",
            XRAY_PORT="443",
            TARGET_HOST="www.bing.com",
            SERVER_IP="203.0.113.10",
            RESTIC_PASSWORD="test-only-password",
        )
        settings.update(overrides)
        (self.work / ".env").write_text(
            "".join(f"{key}={value}\n" for key, value in settings.items())
        )

    def run_setup(self, success=True, **env):
        result = subprocess.run(
            ["bash", "setup.sh"],
            cwd=self.work,
            env={**self.env, **env},
            capture_output=True,
            text=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def read_json(self, path):
        return json.loads((self.work / path).read_text())

    def test_generation_and_permissions(self):
        self.run_setup()
        server = self.read_json("xray_data/config/config.json")
        client = self.read_json("xray_data/client/client.json")
        inbound = server["inbounds"][0]
        outbound = client["outbounds"][0]
        user = outbound["settings"]["vnext"][0]["users"][0]
        self.assertEqual(inbound["settings"]["clients"][0]["id"], user["id"])
        self.assertEqual(inbound["port"], 443)
        self.assertEqual(
            inbound["streamSettings"]["xhttpSettings"],
            outbound["streamSettings"]["xhttpSettings"],
        )
        reality = inbound["streamSettings"]["realitySettings"]
        self.assertEqual(reality["privateKey"], "test-private")
        self.assertEqual(
            reality["shortIds"][0],
            outbound["streamSettings"]["realitySettings"]["shortId"],
        )
        uri = urlsplit(
            (self.work / "xray_data/client/import-link.txt").read_text().strip()
        )
        self.assertEqual(uri.username, user["id"])
        self.assertEqual(uri.port, 443)
        self.assertEqual(
            parse_qs(uri.query)["path"],
            [inbound["streamSettings"]["xhttpSettings"]["path"]],
        )
        for path, mode in (
            ("xray_data", 0o700),
            ("xray_data/config", 0o755),
            ("xray_data/config/config.json", 0o644),
            ("xray_data/client", 0o700),
            ("xray_data/client/client.json", 0o600),
            ("xray_data/client/import-link.txt", 0o600),
        ):
            self.assertEqual(stat.S_IMODE((self.work / path).stat().st_mode), mode)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(calls[-1][4], "app")

    def test_environment_overrides_dotenv_and_matches_compose(self):
        self.write_env(CONTAINER_NAME="from_file", XRAY_PORT="8443")
        env = dict(
            CONTAINER_NAME="override", XRAY_PORT="9443", TARGET_HOST="example.com"
        )
        self.run_setup(**env)
        client = self.read_json("override_data/client/client.json")
        self.assertEqual(client["outbounds"][0]["settings"]["vnext"][0]["port"], 9443)
        self.assertEqual(
            client["outbounds"][0]["streamSettings"]["realitySettings"]["serverName"],
            "example.com",
        )
        uri = urlsplit(
            (self.work / "override_data/client/import-link.txt").read_text().strip()
        )
        self.assertEqual(uri.port, 9443)
        config = subprocess.run(
            [DOCKER, "compose", "config", "--format", "json"],
            cwd=self.work,
            env={**self.env, **env},
            capture_output=True,
            text=True,
            check=True,
        )
        app = json.loads(config.stdout)["services"]["app"]
        self.assertEqual(app["container_name"], "override_main")
        self.assertEqual(int(app["ports"][0]["published"]), 9443)
        self.assertEqual(
            app["volumes"][0]["source"],
            str(self.work / "override_data/config/config.json"),
        )

    def test_dotenv_is_not_executed(self):
        with (self.work / ".env").open("a") as file:
            file.write("UNUSED='$(touch executed)'\n")
        self.run_setup()
        self.assertFalse((self.work / "executed").exists())

    def test_auto_detect_ip(self):
        self.write_env(SERVER_IP="")
        self.run_setup()
        client = self.read_json("xray_data/client/client.json")
        self.assertEqual(
            client["outbounds"][0]["settings"]["vnext"][0]["address"], "203.0.113.20"
        )

    def test_defaults_and_required_container_name(self):
        (self.work / ".env").write_text(
            "CONTAINER_NAME=xray\nRESTIC_PASSWORD=test-only-password\n"
        )
        self.run_setup()
        client = self.read_json("xray_data/client/client.json")
        self.assertEqual(client["outbounds"][0]["settings"]["vnext"][0]["port"], 443)
        self.assertEqual(
            client["outbounds"][0]["streamSettings"]["realitySettings"]["serverName"],
            "www.bing.com",
        )
        self.calls.unlink()
        (self.work / ".env").write_text("")
        self.run_setup(success=False)
        self.assertFalse(self.calls.exists())

    def test_repeat_preserves_keys(self):
        self.run_setup()
        path = self.work / "xray_data/config/config.json"
        original = path.read_bytes()
        self.calls.unlink()
        self.run_setup(success=False)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(self.calls.exists())

    def test_existing_files_and_legacy_config_block_generation(self):
        for relative in (
            "config/config.json",
            "xray_data/config/config.json",
            "xray_data/client/client.json",
            "xray_data/client/import-link.txt",
        ):
            with self.subTest(path=relative):
                path = self.work / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("не изменять")
                self.run_setup(success=False)
                self.assertEqual(path.read_text(), "не изменять")
                self.assertFalse(self.calls.exists())
                path.unlink()

    def test_broken_symlink_is_not_overwritten(self):
        path = self.work / "xray_data/config/config.json"
        path.parent.mkdir(parents=True)
        path.symlink_to("missing.json")
        self.run_setup(success=False)
        self.assertTrue(path.is_symlink())
        self.assertFalse(self.calls.exists())

    def test_invalid_settings_fail_before_pull(self):
        for key, value in (
            ("CONTAINER_NAME", "../escape"),
            ("CONTAINER_NAME", "with.dot"),
            ("XRAY_PORT", "0"),
            ("XRAY_PORT", "65536"),
            ("XRAY_PORT", "abc"),
            ("TARGET_HOST", "-invalid.example"),
            ("TARGET_HOST", "https://example.com"),
            ("SERVER_IP", "not-an-ip"),
            ("SERVER_IP", "::1"),
        ):
            with self.subTest(key=key, value=value):
                self.run_setup(success=False, **{key: value})
                self.assertFalse(self.calls.exists())
                self.assertFalse((self.work / "xray_data").exists())

    def test_invalid_detected_ip_fails_before_pull(self):
        self.write_env(SERVER_IP="")
        self.run_setup(success=False, MOCK_IP="not-an-ip")
        self.assertFalse(self.calls.exists())

    def test_old_key_output_is_supported(self):
        self.run_setup(MOCK_KEYS="Private key: test-private\nPublic key: test-public")
        client = self.read_json("xray_data/client/client.json")
        self.assertEqual(
            client["outbounds"][0]["streamSettings"]["realitySettings"]["password"],
            "test-public",
        )

    def test_unknown_key_output_does_not_leak_keys(self):
        result = self.run_setup(success=False, MOCK_KEYS="unknown-secret-key")
        self.assertNotIn("unknown-secret-key", result.stderr)
        self.assertFalse((self.work / "xray_data").exists())

    def test_failed_config_test_does_not_report_success(self):
        result = self.run_setup(success=False, MOCK_TEST_EXIT="1")
        self.assertNotIn("Проверка конфигурации пройдена", result.stdout)

    def test_restic_configuration(self):
        result = subprocess.run(
            [DOCKER, "compose", "config", "--format", "json"],
            cwd=self.work,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        config = json.loads(result.stdout)
        services = config["services"]
        backup = services["backup_restic__xray"]
        sources = [
            volume for volume in backup["volumes"] if volume["target"] == "/backup_data"
        ]
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["source"], str(self.work / "xray_data"))
        self.assertTrue(sources[0]["read_only"])
        self.assertEqual(
            backup["environment"]["PRE_COMMANDS"].strip(), "docker stop xray_main"
        )
        self.assertEqual(
            backup["environment"]["POST_COMMANDS_EXIT"].strip(),
            "docker start xray_main",
        )
        for name in ("backup_restic__xray", "prune_restic__xray", "check_restic__xray"):
            service = services[name]
            self.assertEqual(
                service["environment"]["RESTIC_PASSWORD"], "test-only-password"
            )
            repositories = [
                v["source"] for v in service["volumes"] if v["target"] == "/mnt/restic"
            ]
            self.assertEqual(repositories, ["/backup/ssd/restic/xray_backups"])
        self.assertIn("apprise_notification", backup["networks"])
        self.assertTrue(config["networks"]["apprise_notification"]["external"])
        self.assertNotIn("traefik", services["app"]["networks"])

    def test_restic_password_is_required(self):
        self.write_env(RESTIC_PASSWORD="")
        self.run_setup(success=False)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
