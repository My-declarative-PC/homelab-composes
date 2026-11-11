"""Проверки записи и блокировки без Docker и сети."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.storage import check_new_configuration, locked_root, write_configuration


class StorageTests(unittest.TestCase):
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
        for relative in ("config", "xray_data", "xray_data/config", "xray_data/client"):
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
