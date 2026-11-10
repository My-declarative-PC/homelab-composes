"""Contract tests: real Compose parser, no deployment and no real .env."""

import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


def compose_config():
    env = {
        **os.environ,
        "CONTAINER_NAME": "xray-test",
        "DOMAIN": "test.invalid",
        "RESTIC_PASSWORD": "test-only-not-a-secret",
        "TZ": "Europe/Moscow",
    }
    return json.loads(
        subprocess.check_output(
            [
                "docker",
                "compose",
                "--env-file",
                "/dev/null",
                "-f",
                str(ROOT / "compose.yaml"),
                "config",
                "--format",
                "json",
            ],
            env=env,
        )
    )


class ComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = compose_config()
        cls.services = cls.config["services"]

    def test_single_source_and_storage(self):
        app = self.services["app"]
        self.assertEqual(app["image"], "ghcr.io/mhsanaei/3x-ui:v3.11.0")
        self.assertIsNone(app.get("command"))
        self.assertNotIn("network_mode", app)
        self.assertEqual(app["container_name"], "xray-test_main")
        self.assertEqual(app["restart"], "unless-stopped")
        self.assertEqual(len(app["volumes"]), 1)
        self.assertEqual(app["volumes"][0]["target"], "/etc/x-ui")
        self.assertTrue(app["volumes"][0]["source"].endswith("xray-test_data/db"))
        self.assertEqual(app["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", app["security_opt"])
        self.assertEqual(app["environment"]["XUI_ENABLE_FAIL2BAN"], "false")
        self.assertEqual(app["environment"]["XUI_PORT"], "2053")
        self.assertEqual(
            app["labels"]["com.centurylinklabs.watchtower.enable"], "false"
        )
        for old in ("configuration.py", "setup.sh"):
            self.assertFalse((ROOT / old).exists())

    def test_admin_is_loopback_only(self):
        app = self.services["app"]
        self.assertEqual(len(app["ports"]), 1)
        self.assertEqual(app["ports"][0]["host_ip"], "127.0.0.1")
        self.assertEqual(app["ports"][0]["target"], 2053)
        self.assertEqual(str(app["ports"][0]["published"]), "2053")
        self.assertNotIn("ports", self.services["site"])

    def test_routing(self):
        app = self.services["app"]["labels"]
        site = self.services["site"]["labels"]
        self.assertEqual(
            app["traefik.http.routers.xray.rule"],
            "Host(`xray.test.invalid`) && PathPrefix(`/api/`)",
        )
        self.assertEqual(
            site["traefik.http.routers.xray-site.rule"], "Host(`xray.test.invalid`)"
        )
        self.assertEqual(app["traefik.http.routers.xray.priority"], "100")
        self.assertEqual(site["traefik.http.routers.xray-site.priority"], "10")
        self.assertEqual(
            app["traefik.http.services.xray.loadbalancer.server.scheme"], "h2c"
        )
        self.assertEqual(
            app["traefik.http.services.xray.loadbalancer.server.port"], "8080"
        )
        self.assertFalse(any("middlewares" in key for key in app))
        for service, router in ((app, "xray"), (site, "xray-site")):
            self.assertEqual(
                service[f"traefik.http.routers.{router}.entrypoints"], "websecure"
            )
            self.assertEqual(
                service[f"traefik.http.routers.{router}.tls.certresolver"],
                "letsencrypt",
            )
            self.assertEqual(service["traefik.docker.network"], "traefik")
        self.assertTrue(self.config["networks"]["traefik"]["external"])

    def test_backup(self):
        backup = self.services["backup_restic__xray"]
        env = backup["environment"]
        self.assertIn("docker stop --time 30 xray-test_main", env["PRE_COMMANDS"])
        self.assertIn(".State.Running", env["PRE_COMMANDS"])
        self.assertIn("set -e", env["PRE_COMMANDS"])
        self.assertEqual(env["POST_COMMANDS_EXIT"], "docker start xray-test_main")
        self.assertIn("apprise_notification__bed:5000", env["POST_COMMANDS_FAILURE"])
        self.assertIn(
            "--exclude /backup_data/backup_restore", env["RESTIC_BACKUP_ARGS"]
        )
        sources = {v["target"]: v["source"] for v in backup["volumes"]}
        self.assertTrue(sources["/backup_data"].endswith("xray-test_data"))
        self.assertEqual(sources["/var/run/docker.sock"], "/var/run/docker.sock")
        for name in ("app", "site"):
            self.assertFalse(
                any(
                    "docker.sock" in v["source"] for v in self.services[name]["volumes"]
                )
            )

    def test_site_and_logs(self):
        site = self.services["site"]
        self.assertTrue(site["read_only"])
        self.assertTrue(all(v["read_only"] for v in site["volumes"]))
        for name in self.services:
            self.assertEqual(
                self.services[name]["logging"]["options"]["max-size"], "10m"
            )
            self.assertEqual(self.services[name]["logging"]["options"]["max-file"], "3")


if __name__ == "__main__":
    unittest.main()
