from concurrent.futures import ThreadPoolExecutor
import functools
from http.client import HTTPConnection
from http.server import SimpleHTTPRequestHandler
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_api import Api
from core import Store, start_bridge
from scripts.verify_macos import LoopbackFixtureServer


class NoReverseDnsBridgeTests(unittest.TestCase):
    def test_numeric_bind_keeps_real_http_auth_and_ai_dispatch_without_dns(self):
        with patch("socket.getfqdn", side_effect=AssertionError("Loopback must not perform reverse DNS")) as lookup:
            store = Store()
            bridge = start_bridge(store, 0)
            try:
                self.assertEqual(bridge.server_name, "127.0.0.1")
                self.assertEqual(bridge.server_address, bridge.socket.getsockname())
                self.assertGreater(bridge.server_port, 0)
                api = bridge.ai = Api(None)

                def post(path, data, token=None, origin=None, host=None):
                    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + (token or bridge.token)}
                    if origin is not None:
                        headers["Origin"] = origin
                    if host is not None:
                        headers["Host"] = host
                    connection = HTTPConnection("127.0.0.1", bridge.server_port, timeout=3)
                    try:
                        connection.request("POST", path, json.dumps(data).encode(), headers)
                        response = connection.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        connection.close()

                self.assertEqual(post("/sync", {}, token="wrong")[0], 401)
                self.assertEqual(post("/sync", {}, host=f"localhost:{bridge.server_port}")[0], 401)
                self.assertEqual(post("/sync", {}, origin="https://example.com")[0], 403)
                status, result = post("/sync", {"client": "fixture-123", "tabs": [{"id": 1, "url": "https://example.com"}]},
                                      origin="chrome-extension://" + "a" * 32)
                self.assertEqual(status, 200)
                self.assertTrue(result["ok"])
                self.assertIn("fixture-123:1", store.tabs)
                self.assertEqual(post("/api/v1/capabilities", {})[0], 403)
                api.enabled.set()
                self.assertEqual(post("/api/v1/capabilities", {}, origin="chrome-extension://" + "a" * 32)[0], 403)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    response = executor.submit(post, "/api/v1/capabilities", {})
                    deadline = time.monotonic() + 3
                    while not response.done() and time.monotonic() < deadline:
                        api.pump()  # Actual API dispatch remains on the caller/UI thread.
                        time.sleep(.005)
                    status, result = response.result(timeout=.2)
                self.assertEqual(status, 200)
                self.assertTrue(result["ok"])
                self.assertIn("record_start", result["actions"])
                lookup.assert_not_called()
            finally:
                bridge.shutdown()
                bridge.server_close()


class NoReverseDnsFixtureTests(unittest.TestCase):
    def test_fixture_server_serves_exact_bytes_without_reverse_dns(self):
        class Quiet(SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass

        with tempfile.TemporaryDirectory() as directory, \
                patch("socket.getfqdn", side_effect=AssertionError("Fixture must not perform reverse DNS")) as lookup:
            payload = b"VideoCatch generated fixture\x00\x01\xff"
            (Path(directory) / "source.mp4").write_bytes(payload)
            server = LoopbackFixtureServer(("127.0.0.1", 0), functools.partial(Quiet, directory=directory))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                self.assertEqual(server.server_name, "127.0.0.1")
                connection.request("GET", "/source.mp4")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), payload)
                lookup.assert_not_called()
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=1)

    def test_fixture_server_rejects_non_loopback_bind(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            LoopbackFixtureServer(("0.0.0.0", 0), SimpleHTTPRequestHandler)


if __name__ == "__main__":
    unittest.main()
