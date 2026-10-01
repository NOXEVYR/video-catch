"""Private local-state tests use only temporary folders and synthetic tasks."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_state import DEFAULT_SETTINGS, LocalState, default_data_dir


class LocalStateTests(unittest.TestCase):
    def test_legacy_settings_migrate_and_manual_name_survives(self):
        self.root.mkdir(parents=True, exist_ok=True)
        old = dict(DEFAULT_SETTINGS, schema=1)
        old.pop('ai_on_start')
        (self.root/'settings.json').write_text(json.dumps(old), encoding='utf-8')
        self.assertTrue(self.state.load_settings()['ai_on_start'])
        self.state.save_receipts([{'id':'manual', 'status':'已保存', 'path':str(self.root/'source.mp4'), 'display_name':'剪辑素材 A'}])
        self.assertEqual(self.state.load_receipts()[0]['title'], '剪辑素材 A')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.state = LocalState(self.root)

    def test_default_paths_and_disabled_mode_never_touch_disk(self):
        with patch.dict(os.environ, {"VIDEOCATCH_DATA_DIR": str(self.root)}):
            self.assertEqual(default_data_dir(), self.root)
        with patch.dict(os.environ, {"VIDEOCATCH_DATA_DIR": "", "LOCALAPPDATA": str(self.root)}, clear=True), patch(
                "local_state.sys.platform", "win32"):
            self.assertEqual(default_data_dir(), self.root / "VideoCatch")
        with patch.dict(os.environ, {}, clear=True), patch("local_state.sys.platform", "darwin"), patch(
                "pathlib.Path.home", return_value=Path("/fixture")):
            self.assertEqual(default_data_dir(), Path("/fixture/Library/Application Support/VideoCatch"))
        disabled = LocalState(self.root, enabled=False)
        self.assertEqual(disabled.load_settings(), DEFAULT_SETTINGS)
        self.assertTrue(disabled.save_settings({"diagnostics": True}))
        self.assertTrue(disabled.save_receipts([{"id": "one", "status": "下载中"}]))
        self.assertEqual(disabled.load_receipts()[0]["status"], "已中断")
        self.assertFalse(disabled.log("startup", count=1))
        self.assertFalse(self.root.exists())

    def test_settings_schema_remember_choice_and_trust_limit(self):
        self.assertEqual(self.state.load_settings(), DEFAULT_SETTINGS)
        self.assertTrue(self.state.save_settings({"folder": str(self.root / "videos")}))
        self.assertEqual(self.state.load_settings()["folder"], "")
        self.assertTrue(self.state.save_settings({"remember_folder": True, "folder": str(self.root / "videos")}))
        fresh = LocalState(self.root)
        self.assertEqual(fresh.load_settings()["folder"], str(self.root / "videos"))
        self.assertFalse(fresh.save_settings({"url": "https://example.test/?token=secret"}))
        self.assertFalse(fresh.save_settings({"trust_until": 10**15}))
        self.assertFalse(fresh.save_settings({"diagnostics": "yes"}))
        self.assertNotIn("secret", (self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(set(json.loads((self.root / "settings.json").read_text(encoding="utf-8"))),
                         set(DEFAULT_SETTINGS) | {"schema"})

    def test_corrupt_settings_backup_before_new_save(self):
        self.root.mkdir()
        target = self.root / "settings.json"
        target.write_bytes(b"{broken private original")
        self.assertEqual(self.state.load_settings(), DEFAULT_SETTINGS)
        backups = list(self.root.glob("settings.corrupt-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"{broken private original")
        self.assertFalse(target.exists())
        self.assertTrue(self.state.warnings)
        self.assertNotIn("private original", " ".join(self.state.warnings))
        self.assertTrue(self.state.save_settings({"diagnostics": True}))
        self.assertEqual(backups[0].read_bytes(), b"{broken private original")
        self.assertTrue(target.exists())

    def test_oversized_settings_are_bounded_and_backed_up(self):
        self.root.mkdir()
        target = self.root / "settings.json"
        target.write_bytes(b"private" * 4000)
        self.assertEqual(self.state.load_settings(), DEFAULT_SETTINGS)
        backups = list(self.root.glob("settings.corrupt-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_size, 28000)
        self.assertFalse(target.exists())

    def test_failed_corrupt_backup_blocks_overwrite(self):
        self.root.mkdir()
        target = self.root / "settings.json"
        target.write_bytes(b"{private corrupt content")
        with patch("local_state.os.replace", side_effect=PermissionError("private path")):
            self.assertEqual(self.state.load_settings(), DEFAULT_SETTINGS)
            self.assertFalse(self.state.save_settings({"diagnostics": True}))
        self.assertEqual(target.read_bytes(), b"{private corrupt content")
        self.assertEqual(self.state.last_error, "StateFormatError")
        self.assertNotIn("private path", " ".join(self.state.warnings))

    def test_failed_atomic_replace_keeps_previous_settings(self):
        self.assertTrue(self.state.save_settings({"diagnostics": False}))
        target = self.root / "settings.json"
        original = target.read_bytes()
        with patch("local_state.os.replace", side_effect=PermissionError("private path")):
            self.assertFalse(self.state.save_settings({"diagnostics": True}))
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.state.last_error, "PermissionError")
        self.assertEqual(list(self.root.glob(".settings-*.tmp")), [])

    def test_receipts_strip_private_fields_and_reconcile_busy_status(self):
        secret = "https://cdn.example.test/video?token=private-secret"
        items = {
            "discovery": {"id": "discovery", "status": "待保存", "url": secret},
            "busy": {"id": "busy", "status": "下载中", "url": secret, "headers": {"Cookie": "private-secret"},
                     "title": "private-secret", "source_path": "private-secret", "error": "private-secret", "elapsed": 12.9},
            "failed": {"id": "failed", "operation": "clip", "status": "失败", "error": "private-secret"},
            "saved": {"id": "saved", "operation": "record", "status": "已保存", "path": str(self.root / "capture.mp4")},
        }
        self.assertTrue(self.state.save_receipts(items))
        raw = (self.root / "receipts.json").read_text(encoding="utf-8")
        self.assertNotIn("private-secret", raw)
        self.assertNotIn("https://", raw)
        self.assertNotIn("discovery", raw)
        self.assertEqual(len(json.loads(raw)["items"]), 3)
        restored = {item["id"]: item for item in LocalState(self.root).load_receipts()}
        self.assertEqual(set(restored), {"busy", "failed", "saved"})
        self.assertEqual(restored["busy"]["status"], "已中断")
        self.assertEqual(restored["busy"]["elapsed"], 12)
        self.assertEqual(restored["saved"]["path"], str(self.root / "capture.mp4"))
        self.assertEqual(restored["failed"]["status"], "失败")
        for item in restored.values():
            self.assertTrue(item["history"])
            self.assertEqual(item["url"], "")
            self.assertEqual(item["headers"], {})
            self.assertEqual(item["error"], "")
            self.assertTrue({"id", "operation", "status", "path", "elapsed", "title", "kind",
                             "source", "host", "size", "progress", "history"} <= set(item))

    def test_receipts_keep_latest_200_and_corruption_is_preserved(self):
        items = [{"id": f"job_{index}", "status": "失败"} for index in range(205)]
        self.assertTrue(self.state.save_receipts(items))
        restored = self.state.load_receipts()
        self.assertEqual(len(restored), 200)
        self.assertEqual(restored[0]["id"], "job_5")
        target = self.root / "receipts.json"
        target.write_text(json.dumps({"schema": 1, "items": [{"id": "x", "url": "private"}]}), encoding="utf-8")
        self.assertEqual(self.state.load_receipts(), [])
        backups = list(self.root.glob("receipts.corrupt-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertIn("private", backups[0].read_text(encoding="utf-8"))
        self.assertNotIn("private", " ".join(self.state.warnings))

    def test_diagnostics_allowlist_default_off_and_bounded_rotation(self):
        secret = "https://example.test/?token=private-secret"
        self.assertFalse(self.state.log("task", status="失败", path=secret))
        self.assertFalse(self.root.exists())
        self.assertTrue(self.state.save_settings({"diagnostics": True}))
        self.assertTrue(self.state.log("task", status="失败", operation="download", reason="timeout",
                                       error_class="PermissionError", path=secret, url=secret,
                                       token="private-secret", headers={"Cookie": "private-secret"},
                                       error="private-secret", title="private-secret", source_path=secret))
        self.assertFalse(self.state.log("private-secret", path=secret))
        first = (self.root / "diagnostics.log").read_text(encoding="utf-8")
        self.assertNotIn("private-secret", first)
        self.assertNotIn("path", first)
        self.assertEqual(json.loads(first)["error_class"], "PermissionError")
        with patch("local_state.MAX_LOG_BYTES", 300):
            for _ in range(40):
                self.assertTrue(self.state.log("task", count=1))
        logs = list(self.root.glob("diagnostics.log*"))
        self.assertLessEqual(len(logs), 4)
        self.assertGreaterEqual(len(logs), 2)
        self.assertTrue(all(path.stat().st_size <= 300 for path in logs))
        self.assertTrue(all("private-secret" not in path.read_text(encoding="utf-8") for path in logs))


if __name__ == "__main__":
    unittest.main()
