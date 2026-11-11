"""Проверки записи и блокировки без Docker и сети."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.storage import (
    check_new_configuration,
    locked_root,
    read_json,
    write_configuration,
    write_json,
)


class StorageTests(unittest.TestCase):
    def test_atomic_json_update_and_exclusive_creation(self):
        with locked_root(self.root) as parent:
            write_json(parent, "state.json", {"old": True})
            with self.assertRaises(FileExistsError):
                write_json(parent, "state.json", {})
            self.assertEqual(read_json(parent, "state.json"), {"old": True})
            write_json(parent, "state.json", {"new": True}, replace=True)
            self.assertEqual(read_json(parent, "state.json"), {"new": True})
        self.assertEqual((self.root / "state.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.root.glob(".xray-*")), [])

    def test_json_operations_reject_symlinks(self):
        (self.root / "state.json").symlink_to("missing")
        with locked_root(self.root) as parent:
            with self.assertRaises(OSError):
                read_json(parent, "state.json")
            with self.assertRaises(RuntimeError):
                write_json(parent, "state.json", {}, replace=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="xray-storage-", dir="/tmp/opencode"
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_lock_excludes_concurrent_operation_and_can_be_reused(self):
        with (
            locked_root(self.root),
            self.assertRaisesRegex(RuntimeError, "уже выполняется"),
            locked_root(self.root),
        ):
            self.fail("Получена вторая блокировка")
        with locked_root(self.root):
            pass

    def test_symlink_directories_are_rejected_without_touching_target(self):
        for relative in ("xray_data", "xray_data/config", "xray_data/client"):
            with (
                self.subTest(relative=relative),
                tempfile.TemporaryDirectory(
                    prefix="xray-link-", dir="/tmp/opencode"
                ) as root,
            ):
                root = Path(root)
                outside = root / "outside"
                outside.mkdir()
                link = root / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside, target_is_directory=True)
                with locked_root(root) as parent, self.assertRaises(OSError):
                    check_new_configuration(parent, "xray_data")
                self.assertEqual(list(outside.iterdir()), [])

    def test_exclusive_write_preserves_partial_configuration(self):
        with locked_root(self.root) as parent:
            write_configuration(
                parent, "xray_data", {"original": True}, {}, "vless://secret"
            )
            original = (self.root / "xray_data/config/config.json").read_bytes()
            with self.assertRaises(FileExistsError):
                write_configuration(parent, "xray_data", {}, {}, "different")
            self.assertEqual(
                (self.root / "xray_data/config/config.json").read_bytes(), original
            )

    def test_symlink_lock_is_rejected(self):
        (self.root / ".xray.lock").symlink_to("missing")
        with self.assertRaises(OSError), locked_root(self.root):
            self.fail("Принята символьная ссылка")
