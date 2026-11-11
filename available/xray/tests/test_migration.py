"""Проверки миграции каталогов без Docker."""

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.migration import migrate_layout


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="xray-migration-", dir="/tmp/opencode"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.content = {
            "config/config.json": b'{"security":"reality","uuid":"unchanged"}\n',
            "client/client.json": b'{"client":"unchanged"}\n',
            "client/import-link.txt": b"vless://unchanged\n",
            "client/extra.txt": b"extra\n",
        }
        for relative, content in self.content.items():
            path = self.root / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(content)

    def assert_original(self):
        for relative, content in self.content.items():
            self.assertEqual((self.root / relative).read_bytes(), content)

    def test_move_preserves_all_files_and_sets_permissions(self):
        migrate_layout(self.root, "custom_data")
        data = self.root / "custom_data"
        for relative, content in self.content.items():
            self.assertEqual((data / relative).read_bytes(), content)
        for relative, mode in (
            (".", 0o700),
            ("config", 0o755),
            ("client", 0o700),
            ("config/config.json", 0o644),
            ("client/client.json", 0o600),
            ("client/import-link.txt", 0o600),
        ):
            self.assertEqual(stat.S_IMODE((data / relative).stat().st_mode), mode)
        self.assertFalse((self.root / "config").exists())
        self.assertFalse((self.root / "client").exists())
        with self.assertRaises(RuntimeError):
            migrate_layout(self.root, "custom_data")

    def test_existing_destination_is_not_merged(self):
        (self.root / "xray_data").mkdir()
        with self.assertRaises(RuntimeError):
            migrate_layout(self.root, "xray_data")
        self.assert_original()

    def test_broken_destination_symlink_is_rejected(self):
        (self.root / "xray_data").symlink_to("missing")
        with self.assertRaises(RuntimeError):
            migrate_layout(self.root, "xray_data")
        self.assert_original()

    def test_missing_file_is_rejected_before_move(self):
        (self.root / "client/import-link.txt").unlink()
        with self.assertRaises(FileNotFoundError):
            migrate_layout(self.root, "xray_data")
        self.assertFalse((self.root / "xray_data").exists())
        self.assertTrue((self.root / "config/config.json").is_file())

    def test_source_file_symlink_is_rejected(self):
        path = self.root / "client/client.json"
        path.unlink()
        path.symlink_to("../config/config.json")
        with self.assertRaises(OSError):
            migrate_layout(self.root, "xray_data")
        self.assertFalse((self.root / "xray_data").exists())

    def test_source_directory_symlink_is_rejected(self):
        (self.root / "client").rename(self.root / "elsewhere")
        (self.root / "client").symlink_to("elsewhere", target_is_directory=True)
        with self.assertRaises(OSError):
            migrate_layout(self.root, "xray_data")
        self.assertFalse((self.root / "xray_data").exists())

    def test_interrupted_move_keeps_every_file(self):
        rename = os.rename

        def fail_second(source, target, **kwargs):
            if source == "client":
                raise OSError("Смоделированный отказ")
            return rename(source, target, **kwargs)

        with patch("src.migration.os.rename", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "Миграция прервана"):
                migrate_layout(self.root, "xray_data")
        for relative, content in self.content.items():
            base = (
                self.root / "xray_data" if relative.startswith("config/") else self.root
            )
            self.assertEqual((base / relative).read_bytes(), content)
