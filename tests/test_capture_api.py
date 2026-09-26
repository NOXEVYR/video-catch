import json
import gc
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App


class FakeRecorder:
    def __init__(self):
        self.data = {"id": "", "status": "", "path": "", "error": "", "elapsed": 0, "options": {}}

    @property
    def is_busy(self):
        return self.data["status"] in {"录制中", "录制暂停", "正在保存"}

    def snapshot(self):
        return dict(self.data)

    def start(self, options, folder):
        self.data.update(id="fixture-recording", status="录制中", options=options)
        return self.snapshot()

    def pause(self):
        self.data["status"] = "录制暂停"
        return self.snapshot()

    def resume(self):
        self.data["status"] = "录制中"
        return self.snapshot()

    def stop(self):
        self.data["status"] = "正在保存"
        return self.snapshot()


class CaptureApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        with patch("recorder.Recorder", FakeRecorder):
            self.app = App(self.root, smoke=True)
        self.app.folder.set(self.directory.name)
        self.app.ai.enabled.set()
        self.toolbar = patch("recording_ui.RecordingToolbar", return_value=Mock())
        self.toolbar.start()

    def tearDown(self):
        self.app.recorder.data["status"] = "已保存"
        self.app.screenshot_pending = False
        self.app.exit_after_capture = False
        self.app.close()
        self.toolbar.stop()
        self.directory.cleanup()
        # The patched toolbar's call history can retain App through a Mock cycle.
        # Finalize those Tk variables/images here, never in a later HTTP worker.
        self.app = self.root = self.toolbar = None
        gc.collect()

    def wait(self, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail("capture API timed out")

    def post(self, action, data=None, token=None):
        results = queue.Queue()
        def send():
            request = Request(f"http://127.0.0.1:{self.app.bridge.server_port}/api/v1/{action}",
                data=json.dumps(data or {}).encode(), headers={"Content-Type": "application/json",
                "Authorization": "Bearer " + (token or self.app.bridge.token)})
            try:
                with build_opener(ProxyHandler({})).open(request, timeout=6) as response:
                    results.put((response.status, json.load(response)))
            except HTTPError as error:
                results.put((error.code, json.loads(error.read())))
        worker = threading.Thread(target=send)
        worker.start()
        self.wait(lambda: not results.empty())
        worker.join()
        return results.get()

    def test_recording_control_and_task_identity(self):
        status, job = self.post("record_start", {"mode": "screen", "audio": "none"})
        self.assertEqual(status, 200)
        ident = job["id"]
        self.assertEqual(self.post("record_start")[0], 409)
        self.assertEqual(self.post("record_stop", {"id": "stale-recording"})[0], 404)
        self.assertEqual(self.post("record_pause", {"id": ident})[1]["status"], "录制暂停")
        self.assertEqual(self.post("record_resume", {"id": ident})[1]["status"], "录制中")
        self.assertEqual(self.post("record_stop", {"id": ident})[1]["status"], "正在保存")
        self.app.recorder.data.update(status="已保存", path=str(Path(self.directory.name) / "record.mp4"))
        self.app._refresh_capture()
        state = self.post("state")[1]
        self.assertEqual(state["items"][0]["operation"], "record")
        self.assertEqual(state["recording"]["status"], "已保存")
        self.assertEqual(self.post("download", {"id": ident})[0], 400)

    def test_capture_requires_current_authorization(self):
        self.assertEqual(self.post("record_start", token="invalid")[0], 401)
        self.app.ai.enabled.clear()
        self.assertEqual(self.post("record_start")[0], 403)
        self.assertEqual(self.post("screenshot")[0], 403)
        self.assertEqual(self.app.store.items, {})

    def test_bad_options_do_not_create_recording(self):
        for data in ({"mode": "camera"}, {"fps": True}, {"mode": "region", "region": {}}, {"camera": 'bad"name'}, {"extra": 1}):
            self.assertEqual(self.post("record_start", data)[0], 400, data)
        self.assertEqual(self.app.store.items, {})

    def test_screenshot_async_identity_and_error(self):
        self.assertEqual(self.post("screenshot", {"mode": "camera"})[0], 400)
        def fake_capture(options, folder):
            path = Path(folder) / "fixture.png"
            path.write_bytes(b"synthetic screenshot marker")
            return {"id": "engine-id", "status": "已保存", "path": str(path), "error": ""}
        with patch("recorder.screenshot", fake_capture):
            status, result = self.post("screenshot")
            self.assertEqual(status, 200)
            self.wait(lambda: not self.app.screenshot_pending)
        ident = result["id"]
        self.assertNotIn("engine-id", self.app.store.items)
        self.assertEqual(self.app.store.items[ident]["status"], "已保存")
        with patch("recorder.screenshot", side_effect=RuntimeError("test capture failure")):
            result = self.post("screenshot")[1]
            self.wait(lambda: not self.app.screenshot_pending)
        self.assertEqual(self.app.store.items[result["id"]]["error"], "test capture failure")

    def test_source_discovery_is_async_and_uses_cache(self):
        with patch("recorder.device_inventory", return_value={"cameras": [{"name": "synthetic"}], "microphones": [], "systems": []}) as inventory, \
             patch("capture_native.list_windows", return_value=[]), patch("capture_native.list_monitors", return_value=[]), \
             patch("capture_native.desktop_bounds", return_value={"x": 0, "y": 0, "width": 640, "height": 480}):
            self.assertEqual(self.post("capture_sources")[0], 200)
            self.wait(lambda: self.app.capture_inventory["loaded"])
            result = self.post("capture_sources")[1]
            self.assertEqual(result["devices"]["cameras"], [{"name": "synthetic"}])
            self.assertEqual(inventory.call_count, 1)

    def test_selector_and_exit_guards(self):
        self.app.recording_panel = Mock(selector=Mock(active=True))
        self.assertEqual(self.post("record_start")[0], 409)
        self.assertEqual(self.post("screenshot")[0], 409)
        self.app.recording_panel = None
        self.post("record_start")
        with patch("app.messagebox.askyesno", return_value=True):
            self.app.close()
        self.assertTrue(self.app.exit_after_capture)
        self.assertFalse(self.app.closing)
        self.assertEqual(self.app.recorder.snapshot()["status"], "正在保存")


if __name__ == "__main__":
    unittest.main()
