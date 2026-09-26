import functools
import gc
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from engine import ffmpeg_path
from clipper import clip_worker
from collaboration import collaboration_prompt, save_pairing, settings_path


class AiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, smoke=True)
        self.app.folder.set(str(self.folder / "output"))
        self.app.ai.enabled.set()

    def tearDown(self):
        for ident in list(self.app.jobs):
            self.app.kill_job(ident)
        self.app.pending.clear()
        self.app.close()
        self.temp.cleanup()
        # Closed widgets and callback/Mock cycles still own Tcl objects. Release
        # the fixture and collect on Tk's thread before another worker can do it.
        self.app = self.root = None
        gc.collect()

    def wait(self, predicate, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail("timed out waiting for UI / worker")

    def post(self, action, data=None, origin=None, token=None):
        results = queue.Queue()
        def send():
            headers = {"Authorization": "Bearer " + (token or self.app.bridge.token), "Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            req = Request(f"http://127.0.0.1:{self.app.bridge.server_port}/api/v1/{action}", data=json.dumps(data or {}).encode(), headers=headers)
            try:
                with build_opener(ProxyHandler({})).open(req, timeout=7) as response:
                    results.put((response.status, json.load(response)))
            except HTTPError as exc:
                results.put((exc.code, json.loads(exc.read())))
        thread = threading.Thread(target=send)
        thread.start()
        self.wait(lambda: not results.empty())
        thread.join()
        return results.get()

    def fixture(self):
        path = self.folder / "source.mp4"
        subprocess.run([ffmpeg_path(), "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=20", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, capture_output=True)
        return path

    def completed(self, ident):
        self.wait(lambda: self.app.store.items[ident]["status"] in {"已保存", "失败"} and ident not in self.app.jobs)
        return self.app.store.items[ident]

    def test_security_and_validation(self):
        self.assertEqual(self.post("state", token="wrong")[0], 401)
        self.assertEqual(self.post("state", origin="https://example.com")[0], 403)
        self.assertEqual(self.post("state", origin="chrome-extension://" + "a" * 32)[0], 403)
        self.app.ai.enabled.clear()
        self.assertEqual(self.post("state")[0], 403)
        self.app.ai.enabled.set()
        self.assertEqual(self.post("missing")[0], 404)
        self.assertEqual(self.post("watch", {"keys": "bad", "enabled": True})[0], 400)
        self.assertEqual(self.post("download", {"id": "missing"})[0], 404)
        self.assertEqual(self.post("capabilities")[0], 200)

    def test_one_click_pairs_client_and_disable_revokes_access(self):
        self.app.ai.enabled.clear()
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.folder)}), \
                patch.object(self.root, "clipboard_clear"), \
                patch.object(self.root, "clipboard_append") as clipboard:
            self.app.collaboration_button.invoke()
            prompt = clipboard.call_args.args[0]
            self.assertNotIn(self.app.bridge.token, prompt)
            self.assertIn(str(Path(sys.executable)), prompt)
            self.assertIn("--input", prompt)
            self.assertTrue(self.app.ai_enabled.get())
            self.assertTrue(self.app.ai.enabled.is_set())
            self.assertEqual(json.loads(settings_path().read_text())["port"], self.app.bridge.server_port)
            self.assertEqual(list(settings_path().parent.glob(".pair-*")), [])
            client = Path(__file__).resolve().parents[1] / "videocatch_client.py"
            env = dict(os.environ)
            env.pop("VIDEOCATCH_TOKEN", None)
            def call(action):
                process = subprocess.Popen([sys.executable, str(client), action], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.wait(lambda: process.poll() is not None)
                out, err = process.communicate()
                self.assertEqual(err, b"")
                return process.returncode, json.loads(out.decode("utf-8"))
            status, result = call("capabilities")
            self.assertEqual(status, 0, result)
            self.assertIn("clip", result["parameters"])
            self.app.ai_enabled.set(False)
            self.app.toggle_ai()
            status, result = call("state")
            self.assertEqual(status, 1)
            self.assertEqual(result["code"], "collaboration_disabled")
            self.app.ai.enabled.set()
            save_pairing("x" * 32, self.app.bridge.server_port)
            status, result = call("state")
            self.assertEqual(result["code"], "pairing_expired")
            self.app.collaboration_button.invoke()
            self.assertEqual(call("state")[0], 0)

    def test_handoff_failure_does_not_enable_interface(self):
        self.app.ai.enabled.clear()
        with patch("app.save_pairing", side_effect=OSError("settings unavailable")):
            self.app.copy_ai_collaboration()
        self.assertFalse(self.app.ai.enabled.is_set())
        self.assertFalse(self.app.ai_enabled.get())
        self.assertIn("未复制成功", self.app.notice.get())

    def test_frozen_handoff_quotes_paths_and_missing_client(self):
        base = self.folder / "拾影 O'Brien"
        base.mkdir()
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(base / "VideoCatch.exe")):
            with self.assertRaises(FileNotFoundError):
                collaboration_prompt()
            (base / "VideoCatchAI.exe").touch()
            prompt = collaboration_prompt()
        self.assertIn("O''Brien", prompt)
        self.assertIn("VideoCatchAI.exe' state", prompt)

    def test_client_reports_missing_pairing_and_bad_json(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.folder)}):
            env = dict(os.environ)
            env.pop("VIDEOCATCH_TOKEN", None)
            client = Path(__file__).resolve().parents[1] / "videocatch_client.py"
            def call(*args):
                result = subprocess.run([sys.executable, str(client), *args], env=env, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, b"")
                return json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(call("state")["code"], "pairing_required")
            save_pairing(self.app.bridge.token, self.app.bridge.server_port)
            self.assertEqual(call("clip", "--json", "bad")["code"], "invalid_input")
            self.assertEqual(call("clip", "--json", "[]")["code"], "invalid_input")

    def test_extension_capture_api_download_and_clip_end_to_end(self):
        source = self.fixture()
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(self.folder)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            self.app.store.sync("browser-123", "Fixture", [{"id": 1, "url": "https://example.com/video"}])
            self.assertEqual(self.post("watch", {"keys": ["browser-123:1"], "enabled": True})[0], 200)
            media = self.app.store.add({"client": "browser-123", "tabId": 1, "url": f"http://127.0.0.1:{server.server_port}/source.mp4?signature=private"})
            ident = media["id"]
            self.assertNotIn("signature=private", json.dumps(self.post("state")[1]))
            self.assertTrue(self.post("download", {"id": ident, "proxy": ""})[1]["queued"])
            downloaded = self.completed(ident)
            self.assertEqual(downloaded["status"], "已保存", downloaded)
            self.assertEqual(hashlib.sha256(Path(downloaded["path"]).read_bytes()).hexdigest(), original)
            self.assertFalse(self.post("download", {"id": ident})[1]["queued"])
            status, job = self.post("clip", {"source": downloaded["path"], "start": 1, "end": 3.25})
            self.assertEqual(status, 200, job)
            clipped = self.completed(job["id"])
            self.assertEqual(clipped["status"], "已保存", clipped)
            decoded = subprocess.run([ffmpeg_path(), "-v", "error", "-i", clipped["path"], "-map", "0:v:0", "-map", "0:a:0", "-progress", "pipe:1", "-f", "null", "-"], capture_output=True)
            self.assertEqual(decoded.returncode, 0, decoded.stderr)
            times = [int(line.split("=")[1]) / 1e6 for line in decoded.stdout.decode().splitlines() if line.startswith("out_time_us=")]
            self.assertAlmostEqual(times[-1], 2.25, delta=.12)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)
        finally:
            server.shutdown()
            server.server_close()

    def test_clip_rejects_invalid_ranges_and_out_of_duration(self):
        source = self.fixture()
        for start, end in [(0, 0), (-1, 2), (3, 1), (float("nan"), 2), (0, float("inf")), (True, 2)]:
            self.assertEqual(self.post("clip", {"source": str(source), "start": start, "end": end})[0], 400)
        status, job = self.post("clip", {"source": str(source), "start": 0, "end": 99})
        self.assertEqual(status, 200)
        self.assertEqual(self.completed(job["id"])["status"], "失败")

    def test_cancel_queued_clip_and_timeout_does_not_run_later(self):
        source = self.fixture()
        job = self.app.ai.enqueue_clip({"source": str(source), "start": 0, "end": 1})
        self.app.ai.dispatch("cancel", {"id": job["id"]})
        self.assertEqual(self.app.store.items[job["id"]]["status"], "已取消")
        self.assertEqual(self.app.pending, [])
        cancelled = threading.Event()
        cancelled.set()
        self.app.ai.requests.put(("import", {"url": "https://example.com/should-not-run.mp4"}, queue.Queue(), cancelled))
        before = len(self.app.store.items)
        self.app.ai.pump()
        self.assertEqual(len(self.app.store.items), before)

    def test_clip_does_not_overwrite_existing_output(self):
        source = self.fixture()
        output = self.folder / "clip-existing.mp4"
        output.write_bytes(b"existing user file")
        events = queue.Queue()
        clip_worker({"id": "existing", "source_path": str(source), "start": 0, "end": 1}, str(self.folder), events)
        messages = []
        while not events.empty():
            messages.append(events.get()[1])
        self.assertEqual(messages[-1]["status"], "失败")
        self.assertEqual(output.read_bytes(), b"existing user file")


if __name__ == "__main__":
    unittest.main()
