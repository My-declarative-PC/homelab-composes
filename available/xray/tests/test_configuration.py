"""Проверки генератора без Docker и сетевых запросов."""

import sys
import unittest
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.configuration import (
    BACKEND_PORT,
    create_client_configuration,
    create_configuration,
    create_server_configuration,
    server_name,
)


class ConfigurationTests(unittest.TestCase):
    def test_server_generation_does_not_create_users(self):
        server = create_server_configuration("example.com")
        self.assertEqual(server["inbounds"][0]["settings"]["clients"], [])

    def test_client_generation_preserves_supplied_credentials(self):
        uid = str(uuid.uuid4())
        client, uri = create_client_configuration(
            "example.com", uid, "/api/test", "phone"
        )
        self.assertEqual(urlsplit(uri).username, uid)
        self.assertEqual(urlsplit(uri).fragment, "phone")
        self.assertEqual(
            client["outbounds"][0]["streamSettings"]["xhttpSettings"]["path"],
            "/api/test",
        )

    def test_tls_client_matches_internal_xhttp_server(self):
        server, client, uri = create_configuration("example.com")
        inbound = server["inbounds"][0]
        outbound = client["outbounds"][0]
        target = outbound["settings"]["vnext"][0]
        uid = inbound["settings"]["clients"][0]["id"]
        self.assertEqual(str(uuid.UUID(uid)), uid)
        self.assertEqual(target["users"][0]["id"], uid)
        self.assertEqual(inbound["port"], BACKEND_PORT)
        self.assertEqual(target["address"], "xray.example.com")
        self.assertEqual(target["port"], 443)
        self.assertEqual(inbound["streamSettings"]["security"], "none")
        stream = outbound["streamSettings"]
        self.assertEqual(stream["security"], "tls")
        self.assertFalse(stream["tlsSettings"]["allowInsecure"])
        self.assertEqual(stream["tlsSettings"]["fingerprint"], "firefox")
        self.assertEqual(stream["tlsSettings"]["serverName"], target["address"])
        path = inbound["streamSettings"]["xhttpSettings"]["path"]
        self.assertRegex(path, r"^/api/[0-9a-f]{24}$")
        self.assertEqual(stream["xhttpSettings"]["path"], path)
        parsed = urlsplit(uri)
        self.assertEqual(parsed.username, uid)
        self.assertEqual(parsed.hostname, target["address"])
        self.assertEqual(parsed.port, target["port"])
        query = parse_qs(parsed.query)
        for key, value in (
            ("security", "tls"),
            ("fp", "firefox"),
            ("type", "xhttp"),
            ("sni", target["address"]),
            ("host", target["address"]),
            ("path", path),
            ("mode", stream["xhttpSettings"]["mode"]),
        ):
            self.assertEqual(query[key], [value])
        self.assertEqual(query["alpn"], ["h2,http/1.1"])
        self.assertNotIn("realitySettings", str(server) + str(client))
        self.assertNotIn("pbk", query)
        self.assertNotIn("sid", query)

    def test_repeated_generation_creates_new_credentials(self):
        first = create_configuration("example.com")[0]["inbounds"][0]
        second = create_configuration("example.com")[0]["inbounds"][0]
        self.assertNotEqual(first["settings"], second["settings"])
        self.assertNotEqual(first["streamSettings"], second["streamSettings"])

    def test_domain_validation(self):
        self.assertEqual(server_name("sub.example.com"), "xray.sub.example.com")
        self.assertEqual(
            server_name("xn--e1afmkfd.xn--p1ai"), "xray.xn--e1afmkfd.xn--p1ai"
        )
        for domain in (
            "",
            "localhost",
            "example.com.",
            "-bad.example",
            "bad_.example",
            "https://example.com",
            "*.example.com",
            "example.com:443",
            "203.0.113.10",
            "пример.рф",
            "a" * 64 + ".com",
            ".".join(["a" * 62] * 4),
        ):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                create_configuration(domain)


if __name__ == "__main__":
    unittest.main()
