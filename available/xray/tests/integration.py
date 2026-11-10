"""Opt-in isolated Docker test. Never uses the deployment .env, networks or data.

Requires docker, openssl, curl; pulls images if absent. No Cloudflare test here.
Temporary test credentials/configs stay under /tmp/opencode (mode 700).
"""

import http.cookiejar
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

from test_compose import ROOT, compose_config


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def wait(check, timeout=60):
    deadline = time.monotonic() + timeout
    last_error = None
    while True:
        try:
            value = check()
            if value:
                return value
        except (
            OSError,
            ValueError,
            subprocess.CalledProcessError,
            AssertionError,
        ) as exc:
            last_error = getattr(exc, "output", str(exc))
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Readiness/functional check failed: {last_error}")
        time.sleep(1)


class Panel:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"
        self.http = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.csrf = ""

    def api(self, path, data=None, form=False):
        headers = {"X-CSRF-Token": self.csrf}
        if data is not None:
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded" if form else "application/json"
            )
            data = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        with self.http.open(req, timeout=10) as response:
            result = json.load(response)
        assert result["success"], result.get("msg")
        return result.get("obj")

    def login(self, user, password):
        self.csrf = self.api("/csrf-token")
        self.api("/login", {"username": user, "password": password}, form=True)
        self.csrf = self.api("/panel/csrf-token")


def main():
    config = compose_config()
    image = config["services"]["app"]["image"]
    prefix = "xui-test-" + uuid.uuid4().hex[:10]
    containers = []
    work = Path(tempfile.mkdtemp(prefix=prefix + "-", dir="/tmp/opencode"))
    network = prefix

    def start(suffix, *args):
        name = prefix + "-" + suffix
        containers.append(name)
        run("docker", "run", "-d", "--name", name, "--network", network, *args)
        return name

    def port(name, target):
        return run("docker", "port", name, str(target)).rsplit(":", 1)[1]

    try:
        run("docker", "network", "create", network)
        (work / "db").mkdir(mode=0o700)
        run(
            "docker",
            "run",
            "--rm",
            "-v",
            f"{work}/db:/etc/x-ui",
            "--entrypoint",
            "sh",
            image,
            "-c",
            "chown 0:0 /etc/x-ui; chmod 700 /etc/x-ui",
        )
        # Same image, storage and restrictions as deployment; random loopback ports.
        app = start(
            "app",
            "--network-alias",
            "panel",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "-e",
            "XUI_ENABLE_FAIL2BAN=false",
            "-e",
            "XUI_PORT=2053",
            "-p",
            "127.0.0.1::2053",
            "-v",
            f"{work}/db:/etc/x-ui",
            image,
        )
        panel = Panel(port(app, 2053))
        wait(lambda: panel.api("/csrf-token"))
        logs = run("docker", "logs", app)
        match = re.search(r"Username: (\S+)\s+Password: (\S+)", logs)
        assert match, "First-start random credentials missing"
        user, password = match.groups()
        panel.login(user, password)
        inbound = panel.api(
            "/panel/api/inbounds/add",
            {
                "remark": "test-xhttp",
                "enable": True,
                "listen": "0.0.0.0",
                "port": 8080,
                "protocol": "vless",
                "settings": json.dumps({"clients": [], "decryption": "none"}),
                "streamSettings": json.dumps(
                    {
                        "network": "xhttp",
                        "security": "none",
                        "xhttpSettings": {
                            "path": "/api/test/",
                            "mode": "packet-up",
                            "host": "xray.test.internal",
                        },
                    }
                ),
                "sniffing": json.dumps({"enabled": False}),
            },
        )
        iid = inbound["id"]
        clients = [
            {
                "id": str(uuid.uuid4()),
                "email": f"user{i}",
                "enable": True,
                "flow": "",
                "totalGB": 0,
                "expiryTime": 0,
                "subId": uuid.uuid4().hex,
            }
            for i in (1, 2)
        ]
        assert clients[0]["id"] != clients[1]["id"]
        for client in clients:
            panel.api("/panel/api/clients/add", {"client": client, "inboundIds": [iid]})
        panel.api(
            "/panel/api/hosts/add",
            {
                "inboundIds": [iid],
                "hosts": ["xray.test.internal"],
                "remark": "CDN TLS",
                "port": 443,
                "security": "tls",
                "sni": "xray.test.internal",
                "hostHeader": "xray.test.internal",
                "path": "/api/test/",
                "alpn": ["h2", "http/1.1"],
                "fingerprint": "chrome",
                "allowInsecure": False,
            },
        )
        # Read links generated by 3x-ui, not by a second user/config generator.
        for client in clients:
            links = panel.api("/panel/api/clients/links/" + client["email"])
            # ClientQrModal fetches subLinks/<subId>, not links/<email>.
            qr_links = panel.api("/panel/api/clients/subLinks/" + client["subId"])
            assert links and qr_links
            for link in links + qr_links:
                uri = urllib.parse.urlsplit(link)
                query = urllib.parse.parse_qs(uri.query)
                assert (uri.scheme, uri.username, uri.hostname, uri.port) == (
                    "vless",
                    client["id"],
                    "xray.test.internal",
                    443,
                )
                for key, value in {
                    "security": "tls",
                    "sni": "xray.test.internal",
                    "type": "xhttp",
                    "mode": "packet-up",
                    "host": "xray.test.internal",
                    "path": "/api/test/",
                    "encryption": "none",
                }.items():
                    assert query[key] == [value], (key, query)
                assert not {"pbk", "sid"} & query.keys()
                assert query.get("allowInsecure", ["false"]) not in (["true"], ["1"])
        print("PASS: first login, two UUIDs, inbound, Hosts and external TLS links")

        # Local TLS fixture: dedicated certificate trusted by the test client, no allowInsecure.
        run(
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(work / "key.pem"),
            "-out",
            str(work / "cert.pem"),
            "-subj",
            "/CN=xray.test.internal",
            "-addext",
            "subjectAltName=DNS:xray.test.internal",
        )
        dynamic = {
            "tls": {
                "certificates": [
                    {"certFile": "/test/cert.pem", "keyFile": "/test/key.pem"}
                ]
            },
            "http": {
                "routers": {
                    "xray": {
                        "rule": "Host(`xray.test.internal`) && PathPrefix(`/api/`)",
                        "priority": 100,
                        "entryPoints": ["websecure"],
                        "tls": {},
                        "service": "xray",
                    },
                    "site": {
                        "rule": "Host(`xray.test.internal`)",
                        "priority": 10,
                        "entryPoints": ["websecure"],
                        "tls": {},
                        "service": "site",
                    },
                },
                "services": {
                    "xray": {
                        "loadBalancer": {
                            "servers": [{"url": "h2c://panel:8080"}],
                            "responseForwarding": {"flushInterval": "-1ms"},
                        }
                    },
                    "site": {
                        "loadBalancer": {"servers": [{"url": "http://site:8080"}]}
                    },
                },
            },
        }
        # JSON is a YAML subset; Traefik file provider requires a YAML extension.
        (work / "dynamic.yaml").write_text(json.dumps(dynamic))
        start(
            "site",
            "--network-alias",
            "site",
            "--cap-drop",
            "ALL",
            "--read-only",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp",
            "-v",
            f"{ROOT}/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro",
            "-v",
            f"{ROOT}/site:/usr/share/nginx/html:ro",
            "--entrypoint",
            "nginx",
            config["services"]["site"]["image"],
            "-g",
            "daemon off;",
        )
        proxy = start(
            "traefik",
            "--network-alias",
            "xray.test.internal",
            "-p",
            "127.0.0.1::443",
            "-v",
            f"{work}:/test:ro",
            "traefik:v3",
            "--entrypoints.websecure.address=:443",
            "--providers.file.filename=/test/dynamic.yaml",
            "--accesslog=true",
            "--accesslog.format=json",
        )
        p = port(proxy, 443)
        html = wait(
            lambda: run(
                "curl",
                "--silent",
                "--show-error",
                "--fail",
                "--noproxy",
                "*",
                "--cacert",
                str(work / "cert.pem"),
                "--resolve",
                f"xray.test.internal:{p}:127.0.0.1",
                f"https://xray.test.internal:{p}/",
            )
        )
        assert html == (ROOT / "site/index.html").read_text().strip()
        # Only temporary CLIENT JSON is constructed here; server config is always panel-owned.
        for i, client in enumerate(clients):
            client_config = {
                "log": {"loglevel": "debug"},
                "inbounds": [
                    {
                        "listen": "0.0.0.0",
                        "port": 1080,
                        "protocol": "socks",
                        "settings": {"auth": "noauth"},
                    }
                ],
                "outbounds": [
                    {
                        "protocol": "vless",
                        "settings": {
                            "vnext": [
                                {
                                    "address": "xray.test.internal",
                                    "port": 443,
                                    "users": [
                                        {"id": client["id"], "encryption": "none"}
                                    ],
                                }
                            ]
                        },
                        "streamSettings": {
                            "network": "xhttp",
                            "security": "tls",
                            "tlsSettings": {
                                "serverName": "xray.test.internal",
                                "alpn": ["h2"],
                                "allowInsecure": False,
                                "certificates": [
                                    {
                                        "certificateFile": "/test/cert.pem",
                                        "usage": "verify",
                                    }
                                ],
                            },
                            "xhttpSettings": {
                                "path": "/api/test/",
                                "mode": "packet-up",
                                "host": "xray.test.internal",
                            },
                        },
                    }
                ],
            }
            (work / f"client{i}.json").write_text(json.dumps(client_config))
            start(
                f"client{i}",
                "-e",
                "SSL_CERT_FILE=/test/cert.pem",
                "-v",
                f"{work}:/test:ro",
                "--entrypoint",
                "/app/bin/xray-linux-amd64",
                image,
                "run",
                "-c",
                f"/test/client{i}.json",
            )

        def transfer(i):
            return run(
                "docker",
                "exec",
                f"{prefix}-client{i}",
                "curl",
                "--silent",
                "--show-error",
                "--fail",
                "--max-time",
                "15",
                "--proxy",
                "socks5h://127.0.0.1:1080",
                "https://example.com/",
            )

        for i in (0, 1):
            assert "Example Domain" in wait(lambda: transfer(i))

        # The first stats poll establishes a baseline, not billable traffic.
        # Keep moving bytes after it, then allow the 5s collector to persist deltas.
        def counted(i):
            if (
                panel.api("/panel/api/clients/traffic/" + clients[i]["email"]) or {}
            ).get("down", 0) > 0:
                return True
            assert "Example Domain" in transfer(i)
            return False

        for i in (0, 1):
            wait(lambda: counted(i))
        print(
            "PASS: site and both users over TLS/h2 -> Traefik -> h2c -> Xray -> Internet"
        )
        panel.api("/panel/api/clients/update/user1", {**clients[0], "enable": False})

        def disabled():
            try:
                transfer(0)
            except subprocess.CalledProcessError:
                return True
            return False

        wait(disabled)
        assert "Example Domain" in transfer(1)
        wait(
            lambda: (
                (panel.api("/panel/api/clients/traffic/user2") or {}).get("down", 0) > 0
            )
        )
        print("PASS: disabling user1 does not block user2; traffic counters increase")
        # Recreate, not just restart: /app/bin/config.json is intentionally not persistent.
        snapshot = work / "snapshot"
        snapshot.mkdir()
        (work / "repo").mkdir()
        backup_env = config["services"]["backup_restic__xray"]["environment"]
        # Actual backup script and deployment stop/check/start hooks, disposable app only.
        backup_output = run(
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "-v",
            "/var/run/docker.sock:/var/run/docker.sock:ro",
            "-v",
            f"{work}/db:/backup_data/db:ro",
            "-v",
            f"{work}/repo:/repo",
            "-v",
            f"{snapshot}:/restore",
            "-e",
            "RESTIC_REPOSITORY=/repo",
            "-e",
            "RESTIC_PASSWORD=" + uuid.uuid4().hex,
            "-e",
            "RESTIC_BACKUP_SOURCES=/backup_data",
            "-e",
            "RESTIC_BACKUP_ARGS=" + backup_env["RESTIC_BACKUP_ARGS"],
            "-e",
            "PRE_COMMANDS=" + backup_env["PRE_COMMANDS"].replace("xray-test_main", app),
            "-e",
            "POST_COMMANDS_EXIT="
            + backup_env["POST_COMMANDS_EXIT"].replace("xray-test_main", app),
            "--entrypoint",
            "/bin/bash",
            "mazzolino/restic",
            "-ec",
            "restic init\nbackup\nrestic check\nrestic restore latest --target /restore",
        )
        (work / "restic.log").write_text(backup_output)
        assert run("docker", "inspect", "-f", "{{.State.Running}}", app) == "true"
        # A failed stop must abort BEFORE opening/copying the SQLite backup source.
        failed = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                "/var/run/docker.sock:/var/run/docker.sock:ro",
                "-e",
                "PRE_COMMANDS="
                + backup_env["PRE_COMMANDS"].replace(
                    "xray-test_main", prefix + "-nonexistent"
                ),
                "-e",
                "POST_COMMANDS_EXIT=true",
                "--entrypoint",
                "/usr/local/bin/backup",
                "mazzolino/restic",
            ],
            capture_output=True,
            text=True,
        )
        (work / "restic-failed-stop.log").write_text(failed.stdout + failed.stderr)
        assert failed.returncode != 0
        assert "Starting Backup" not in failed.stdout
        run("docker", "stop", app)
        run("docker", "rm", app)
        start(
            "app",
            "--network-alias",
            "panel",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "-e",
            "XUI_ENABLE_FAIL2BAN=false",
            "-e",
            "XUI_PORT=2053",
            "-p",
            "127.0.0.1::2053",
            "-v",
            f"{snapshot}/backup_data/db:/etc/x-ui",
            image,
        )
        panel = Panel(port(app, 2053))
        wait(lambda: panel.api("/csrf-token"))
        panel.login(user, password)
        for client in clients:
            rec = panel.api("/panel/api/clients/get/" + client["email"])["client"]
            assert rec["uuid"] == client["id"], rec
        assert panel.api("/panel/api/hosts/list")
        # Traefik's static file backend may cache the old container IP: restart only test proxy.
        run("docker", "restart", proxy)
        assert "Example Domain" in wait(lambda: transfer(1))
        wait(disabled)
        # Rotate only user1. Server state is changed only through the panel API.
        rotated = {**clients[0], "id": str(uuid.uuid4()), "enable": True}
        panel.api("/panel/api/clients/update/user1", rotated)
        wait(disabled)
        client_file = work / "client0.json"
        profile = json.loads(client_file.read_text())
        profile["outbounds"][0]["settings"]["vnext"][0]["users"][0]["id"] = rotated[
            "id"
        ]
        client_file.write_text(json.dumps(profile))
        run("docker", "restart", prefix + "-client0")
        assert "Example Domain" in wait(lambda: transfer(0))
        panel.api("/panel/api/clients/del/user1", {})
        wait(disabled)
        assert "Example Domain" in transfer(1)
        access = run("docker", "logs", proxy)
        assert '"RequestProtocol":"HTTP/2.0"' in access
        assert "h2c://panel:8080" in access
        print(
            "PASS: restore/recreate preserves UUIDs, disabled state, Hosts, admin and inbound; rotate/delete"
        )
        print(
            "PASS: Restic stop/backup/start, repository check and start from restored SQLite"
        )
        print("NOT TESTED: Cloudflare, QR rendering, long transfers, Apprise delivery")
    except Exception as error:
        if isinstance(error, subprocess.CalledProcessError):
            (work / "command-error.log").write_text(error.output or "")
        try:
            (work / "traffic.json").write_text(
                json.dumps(
                    {
                        c["email"]: panel.api(
                            "/panel/api/clients/traffic/" + c["email"]
                        )
                        for c in clients
                    },
                    indent=2,
                )
            )
        except Exception:
            pass
        stats = subprocess.run(
            [
                "docker",
                "exec",
                prefix + "-app",
                "/app/bin/xray-linux-amd64",
                "api",
                "statsquery",
                "--server=127.0.0.1:62789",
            ],
            capture_output=True,
            text=True,
        )
        (work / "core-stats.json").write_text(stats.stdout + stats.stderr)
        result = subprocess.run(
            ["docker", "exec", prefix + "-app", "cat", "/app/bin/config.json"],
            capture_output=True,
            text=True,
        )
        (work / "generated-server.json").write_text(result.stdout)
        for name in dict.fromkeys(containers):
            result = subprocess.run(
                ["docker", "logs", name], capture_output=True, text=True
            )
            (work / (name + ".log")).write_text(result.stdout + result.stderr)
        raise
    finally:
        for name in reversed(list(dict.fromkeys(containers))):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)
        # Private evidence retained for diagnosis; contains test UUIDs and SQLite, never in Git.
        print(f"Private test artifacts: {work}")


if __name__ == "__main__":
    main()
