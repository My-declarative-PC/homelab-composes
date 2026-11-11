"""Проверки без запуска контейнеров; разбор Compose выполняет настоящий Docker CLI."""

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
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
        for name in ("xray.py", "compose.yaml"):
            shutil.copy2(SOURCE / name, self.work / name)
        for name in ("src", "nginx", "site"):
            shutil.copytree(SOURCE / name, self.work / name)
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
    sys.exit(int(os.environ.get('MOCK_PULL_EXIT', '0')))
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
            [sys.executable, str(self.work / "xray.py"), "setup"],
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
            "Host(`xray.override.example`) && PathPrefix(`/api/`)",
        )
        site = json.loads(config.stdout)["services"]["site"]
        self.assertEqual(site["container_name"], "override_site")
        self.assertEqual(
            site["labels"]["traefik.http.routers.xray-site.rule"],
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
        self.assertTrue((self.work / "xray_data/client/import-link.txt").is_file())

    def test_failed_pull_does_not_create_credentials(self):
        result = self.run_setup(success=False, MOCK_PULL_EXIT="7")
        self.assertEqual(result.returncode, 7)
        self.assertFalse((self.work / "xray_data").exists())

    def test_setup_from_another_working_directory(self):
        result = subprocess.run(
            [sys.executable, str(self.work / "xray.py"), "setup"],
            cwd=self.temp.name,
            env=self.env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.work / "xray_data/config/config.json").is_file())

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

    def test_site_and_proxy_have_independent_routes_and_read_only_mounts(self):
        result = subprocess.run(
            [DOCKER, "compose", "config", "--format", "json"],
            cwd=self.work,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        services = json.loads(result.stdout)["services"]
        app, site = services["app"], services["site"]
        proxy_labels, site_labels = app["labels"], site["labels"]
        self.assertEqual(
            proxy_labels["traefik.http.routers.xray.rule"],
            "Host(`xray.example.com`) && PathPrefix(`/api/`)",
        )
        self.assertEqual(
            site_labels["traefik.http.routers.xray-site.rule"],
            "Host(`xray.example.com`)",
        )
        self.assertGreater(
            int(proxy_labels["traefik.http.routers.xray.priority"]),
            int(site_labels["traefik.http.routers.xray-site.priority"]),
        )
        for name, service in (("xray", app), ("xray-site", site)):
            with self.subTest(service=name):
                labels = service["labels"]
                router = "traefik.http.routers." + name
                self.assertEqual(labels[router + ".entrypoints"], "websecure")
                self.assertEqual(labels[router + ".tls"], "true")
                self.assertEqual(labels[router + ".tls.certresolver"], "letsencrypt")
                self.assertEqual(labels[router + ".service"], name)
                self.assertNotIn(router + ".middlewares", labels)
                self.assertNotIn("ports", service)
                self.assertNotIn("depends_on", service)
                self.assertTrue(service["read_only"])
                self.assertIn("ALL", service["cap_drop"])
                self.assertIn("no-new-privileges:true", service["security_opt"])
                self.assertIn("/tmp:size=16m,mode=1777", service["tmpfs"])
                self.assertEqual(set(service["networks"]), {"traefik"})
                self.assertEqual(labels["traefik.docker.network"], "traefik")
        self.assertEqual(site["expose"], ["8080"])
        self.assertEqual(site["entrypoint"], ["nginx"])
        self.assertEqual(site["command"], ["-g", "daemon off;"])
        self.assertEqual(
            site_labels["traefik.http.services.xray-site.loadbalancer.server.port"],
            "8080",
        )
        self.assertEqual(
            site_labels["traefik.http.services.xray-site.loadbalancer.server.scheme"],
            "http",
        )
        self.assertEqual(site_labels["com.centurylinklabs.watchtower.enable"], "false")
        mounts = {volume["target"]: volume for volume in site["volumes"]}
        expected = {
            "/etc/nginx/conf.d/default.conf": "nginx/default.conf",
            "/usr/share/nginx/html": "site",
        }
        self.assertEqual(set(mounts), set(expected))
        for target, source in expected.items():
            volume = mounts[target]
            self.assertEqual(volume["source"], str(self.work / source))
            self.assertTrue(volume["read_only"])
            self.assertFalse(volume["bind"]["create_host_path"])
            self.assertTrue(Path(volume["source"]).exists())

    def test_documented_legacy_directory_move_preserves_files(self):
        self.run_setup()
        data = self.work / "xray_data"
        original = {
            str(path.relative_to(data)): path.read_bytes()
            for path in data.rglob("*")
            if path.is_file()
        }
        for name in ("config", "client"):
            (data / name).rename(self.work / name)
        data.rmdir()
        match = re.search(
            r"```bash\n(   \(\n.*?\n   \))\n   ```",
            (SOURCE / "README.md").read_text(),
            re.S,
        )
        self.assertIsNotNone(
            match, "Не найден документированный блок переноса каталогов"
        )
        script = textwrap.dedent(match.group(1))
        subprocess.run(["bash", "-c", script], cwd=self.work, env=self.env, check=True)
        for relative, content in original.items():
            self.assertEqual((data / relative).read_bytes(), content)
        self.assertEqual(stat.S_IMODE(data.stat().st_mode), 0o700)
        repeated = subprocess.run(["bash", "-c", script], cwd=self.work, env=self.env)
        self.assertNotEqual(repeated.returncode, 0)
        for relative, content in original.items():
            self.assertEqual((data / relative).read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
