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
        for name in ("setup.sh", "compose.yaml", "configuration.py"):
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
if args == ['compose', 'run', '--rm', '--no-deps', 'app', 'run', '-test', '-c', '/etc/xray/config.json']:
    sys.exit(int(os.environ.get('MOCK_TEST_EXIT', '0')))
sys.exit('Неожиданная команда Docker: ' + repr(args))
""")
        mock.chmod(0o755)
        self.env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in (
                "CONTAINER_NAME",
                "DOMAIN",
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
            DOMAIN="example.com",
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
        self.assertEqual(inbound["port"], 8080)
        self.assertEqual(inbound["streamSettings"]["security"], "none")
        self.assertEqual(outbound["streamSettings"]["security"], "tls")
        self.assertEqual(
            inbound["streamSettings"]["xhttpSettings"]["path"],
            outbound["streamSettings"]["xhttpSettings"]["path"],
        )
        uri = urlsplit(
            (self.work / "xray_data/client/import-link.txt").read_text().strip()
        )
        self.assertEqual(uri.username, user["id"])
        self.assertEqual(uri.port, 443)
        self.assertEqual(uri.hostname, "xray.example.com")
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
        self.assertEqual(len(calls), 2)  # Только pull и проверка; без x25519.

    def test_environment_overrides_dotenv_and_matches_compose(self):
        self.write_env(CONTAINER_NAME="from_file", DOMAIN="from-file.example")
        env = dict(CONTAINER_NAME="override", DOMAIN="override.example")
        self.run_setup(**env)
        client = self.read_json("override_data/client/client.json")
        self.assertEqual(client["outbounds"][0]["settings"]["vnext"][0]["port"], 443)
        self.assertEqual(
            client["outbounds"][0]["streamSettings"]["tlsSettings"]["serverName"],
            "xray.override.example",
        )
        uri = urlsplit(
            (self.work / "override_data/client/import-link.txt").read_text().strip()
        )
        self.assertEqual(uri.port, 443)
        self.assertEqual(uri.hostname, "xray.override.example")
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
        self.assertNotIn("ports", app)
        self.assertEqual(
            app["labels"]["traefik.http.routers.xray.rule"],
            "Host(`xray.override.example`)",
        )
        self.assertEqual(
            app["volumes"][0]["source"],
            str(self.work / "override_data/config/config.json"),
        )

    def test_dotenv_is_not_executed(self):
        with (self.work / ".env").open("a") as file:
            file.write("UNUSED='$(touch executed)'\n")
        self.run_setup()
        self.assertFalse((self.work / "executed").exists())

    def test_required_settings(self):
        for key in ("CONTAINER_NAME", "DOMAIN", "RESTIC_PASSWORD"):
            with self.subTest(key=key):
                self.write_env(**{key: ""})
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
            ("DOMAIN", "-invalid.example"),
            ("DOMAIN", "https://example.com"),
            ("DOMAIN", "example.com:443"),
            ("DOMAIN", "example.com/path"),
            ("DOMAIN", "203.0.113.10"),
            ("DOMAIN", "example.com` || Host(`other.example"),
        ):
            with self.subTest(key=key, value=value):
                self.run_setup(success=False, **{key: value})
                self.assertFalse(self.calls.exists())
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
        self.assertIn("traefik", services["app"]["networks"])

    def test_restic_password_is_required(self):
        self.write_env(RESTIC_PASSWORD="")
        self.run_setup(success=False)
        self.assertFalse(self.calls.exists())

    def test_existing_reality_config_requires_explicit_migration(self):
        path = self.work / "xray_data/config/config.json"
        path.parent.mkdir(parents=True)
        original = json.dumps(
            {
                "inbounds": [
                    {
                        "streamSettings": {
                            "security": "reality",
                            "realitySettings": {"privateKey": "old-private-key"},
                        }
                    }
                ]
            }
        )
        path.write_text(original)
        result = self.run_setup(success=False)
        self.assertIn("переход с REALITY", result.stderr)
        self.assertNotIn("old-private-key", result.stderr)
        self.assertEqual(path.read_text(), original)
        self.assertFalse(self.calls.exists())

    def test_traefik_configuration_matches_generated_backend(self):
        self.run_setup()
        result = subprocess.run(
            [DOCKER, "compose", "config", "--format", "json"],
            cwd=self.work,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        config = json.loads(result.stdout)
        app = config["services"]["app"]
        inbound = self.read_json("xray_data/config/config.json")["inbounds"][0]
        labels = app["labels"]
        self.assertNotIn("ports", app)
        self.assertEqual(app["expose"], [str(inbound["port"])])
        self.assertEqual(
            labels["traefik.http.services.xray.loadbalancer.server.port"],
            str(inbound["port"]),
        )
        self.assertEqual(
            labels["traefik.http.services.xray.loadbalancer.server.scheme"], "h2c"
        )
        self.assertEqual(labels["traefik.http.routers.xray.entrypoints"], "websecure")
        self.assertEqual(labels["traefik.http.routers.xray.tls"], "true")
        self.assertEqual(
            labels["traefik.http.routers.xray.tls.certresolver"], "letsencrypt"
        )
        self.assertEqual(labels["traefik.docker.network"], "traefik")
        self.assertEqual(labels["com.centurylinklabs.watchtower.enable"], "false")
        self.assertTrue(config["networks"]["traefik"]["external"])
        self.assertTrue(app["read_only"])
        self.assertIn("ALL", app["cap_drop"])
        self.assertFalse(app["volumes"][0]["bind"]["create_host_path"])


if __name__ == "__main__":
    unittest.main()
