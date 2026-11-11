"""Операции с постоянными пользователями без Docker и сети."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.configuration import create_configuration, create_server_configuration
from src.storage import directory, locked_root, write_configuration, write_json
from src.users import user_store


class UserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="xray-users-", dir="/tmp/opencode"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "xray_data"
        with (
            locked_root(self.root) as parent,
            directory(parent, "xray_data", create=True) as data,
            directory(data, "config", create=True, mode=0o755) as config,
        ):
            write_json(
                config,
                "config.json",
                create_server_configuration("example.com"),
                mode=0o644,
            )

    def store(self, domain="example.com"):
        return user_store(self.root, "xray", domain)

    def validate(self, path):
        self.assertEqual(path.name, ".users-candidate.json")
        json.loads(path.read_text())

    def server(self):
        return json.loads((self.data / "config/config.json").read_text())

    def snapshot(self):
        return {
            str(path.relative_to(self.data)): path.read_bytes()
            for path in self.data.rglob("*")
            if path.is_file()
        }

    def test_complete_lifecycle_preserves_uuid_and_other_users(self):
        with self.store() as store:
            self.assertEqual(store.list(), [])
            store.change("create", "phone", self.validate)
            first, client, uri = store.show("phone")
            self.assertIn(first["client"]["id"], uri)
            self.assertEqual(
                client["outbounds"][0]["settings"]["vnext"][0]["users"][0]["id"],
                first["client"]["id"],
            )
            store.change("create", "laptop", self.validate)
            second = store.show("laptop")[0]
            self.assertNotEqual(first["client"]["id"], second["client"]["id"])
            self.assertEqual(
                len(self.server()["inbounds"][0]["settings"]["clients"]), 2
            )
            store.change("suspend", "phone", self.validate)
            self.assertFalse(store.show("phone")[0]["enabled"])
            self.assertEqual(
                self.server()["inbounds"][0]["settings"]["clients"], [second["client"]]
            )
            store.change("resume", "phone", self.validate)
            self.assertEqual(store.show("phone")[0], first)
            store.change("delete", "phone", self.validate)
            with self.assertRaisesRegex(ValueError, "не найден"):
                store.show("phone")
            self.assertEqual(store.list(), [second])
        self.assertEqual((self.data / "users.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            (self.data / "config/config.json").stat().st_mode & 0o777, 0o644
        )

    def test_invalid_names_duplicates_and_unknown_users_do_not_write(self):
        with self.store() as store:
            store.change("create", "phone", self.validate)
            before = self.snapshot()
            for action, name in (
                ("create", "phone"),
                ("create", "../escape"),
                ("create", ""),
                ("create", "a" * 65),
                ("delete", "missing"),
                ("suspend", "missing"),
                ("resume", "missing"),
            ):
                with (
                    self.subTest(action=action, name=name),
                    self.assertRaises(ValueError),
                ):
                    store.change(action, name, self.validate)
                self.assertEqual(self.snapshot(), before)

    def test_validation_failure_preserves_all_files(self):
        with self.store() as store:
            store.change("create", "phone", self.validate)
            before = self.snapshot()

            def fail(path):
                raise RuntimeError("Ошибка теста Xray")

            with self.assertRaisesRegex(RuntimeError, "Ошибка теста"):
                store.change("suspend", "phone", fail)
            self.assertEqual(self.snapshot(), before)

    def test_partial_commit_is_rolled_back_on_next_command(self):
        with self.store() as store:
            store.change("create", "phone", self.validate)
            before = self.snapshot()
            real_write = write_json

            def fail_registry(parent, name, *args, **kwargs):
                if name == "users.json":
                    raise OSError("Заполнен диск")
                return real_write(parent, name, *args, **kwargs)

            with (
                patch("src.users.write_json", side_effect=fail_registry),
                self.assertRaisesRegex(RuntimeError, "Не перезапускайте"),
            ):
                store.change("delete", "phone", self.validate)
        with self.assertRaisesRegex(RuntimeError, "восстановлено"), self.store():
            pass
        self.assertEqual(self.snapshot(), before)
        with self.store() as store:
            self.assertEqual(len(store.list()), 1)

    def test_manual_client_edits_and_domain_drift_are_rejected(self):
        with self.store() as store:
            store.change("create", "phone", self.validate)
        with (
            self.store("other.example") as store,
            self.assertRaisesRegex(ValueError, "DOMAIN"),
        ):
            store.list()
        server = self.server()
        server["inbounds"][0]["settings"]["clients"] = []
        (self.data / "config/config.json").write_text(json.dumps(server))
        with (
            self.store() as store,
            self.assertRaisesRegex(RuntimeError, "вне инструмента"),
        ):
            store.list()

    def test_registry_symlink_is_rejected(self):
        (self.data / "users.json").symlink_to("missing")
        with self.store() as store, self.assertRaises(OSError):
            store.list()

    def test_corrupted_registry_does_not_expose_secrets(self):
        (self.data / "users.json").write_text('{"secret": "secret-value"}')
        with self.store() as store, self.assertRaises(ValueError) as error:
            store.list()
        self.assertNotIn("secret-value", str(error.exception))

    def legacy(self):
        server, client, uri = create_configuration("example.com")
        server["inbounds"][0]["settings"]["clients"][0]["email"] = "legacy@example.com"
        (self.data / "config/config.json").unlink()
        with locked_root(self.root) as parent:
            write_configuration(parent, "xray_data", server, client, uri)
        return server, client, uri

    def test_explicit_import_preserves_credentials_and_legacy_files(self):
        original, _, _ = self.legacy()
        old_files = {
            path.name: path.read_bytes() for path in (self.data / "client").iterdir()
        }
        with self.store() as store:
            with self.assertRaisesRegex(RuntimeError, "users import"):
                store.change("create", "phone", self.validate)
            store.change("import", "legacy", self.validate)
            self.assertEqual(self.server(), original)
            self.assertEqual(
                store.show("legacy")[0]["client"],
                original["inbounds"][0]["settings"]["clients"][0],
            )
            with self.assertRaisesRegex(RuntimeError, "уже существует"):
                store.change("import", "again", self.validate)
        self.assertEqual(
            {path.name: path.read_bytes() for path in (self.data / "client").iterdir()},
            old_files,
        )

    def test_import_rejects_wrong_domain(self):
        self.legacy()
        before = self.snapshot()
        with (
            self.store("other.example") as store,
            self.assertRaisesRegex(ValueError, "DOMAIN"),
        ):
            store.change("import", "legacy", self.validate)
        self.assertEqual(self.snapshot(), before)

    def test_stale_candidate_is_removed_after_interrupted_validation(self):
        (self.data / "config/.users-candidate.json").write_text("{}")
        with self.store() as store:
            store.change("create", "phone", self.validate)
        self.assertFalse((self.data / "config/.users-candidate.json").exists())
