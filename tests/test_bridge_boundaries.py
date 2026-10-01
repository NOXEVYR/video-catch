"""Loopback bridge boundary checks using an ephemeral port and no browser profile."""
import json
from pathlib import Path
import sys
import unittest
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Store, start_bridge


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.bridge = start_bridge(Store(), 0)
        self.url = f"http://127.0.0.1:{self.bridge.server_port}"
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.bridge.shutdown()
        self.bridge.server_close()

    def request(self, path="/sync", *, token=None, origin=None, host=None, method="POST"):
        headers = {"Authorization": "Bearer " + (self.bridge.token if token is None else token),
                   "Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        request = Request(self.url + path, data=b'{}' if method == "POST" else None,
                          headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, json.loads(response.read()) if method == "POST" else None

    def test_host_origin_and_token_are_independent_gates(self):
        extension = "chrome-extension://" + "a" * 32
        cases = [
            ({"token": "wrong"}, 401),
            ({"host": f"localhost:{self.bridge.server_port}"}, 401),
            ({"origin": "https://example.com"}, 403),
            ({"origin": "null"}, 403),
            ({"origin": "chrome-extension://bad"}, 403),
            ({"origin": extension, "token": "wrong"}, 401),
            ({"origin": extension}, 400),  # Authenticated extension still needs valid sync input.
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                status, headers, _ = self.request(**arguments)
                self.assertEqual(status, expected)
                self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_route_and_browser_ai_boundaries(self):
        extension = "chrome-extension://" + "a" * 32
        for path in ("/sync/", "/sync?unexpected=1", "/media/", "/api/v1x/state"):
            with self.subTest(path=path):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 404)
                self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        status, _, _ = self.request("/api/v1/state", origin=extension)
        self.assertEqual(status, 403)
        status, _, _ = self.request("/api/v1/state")
        self.assertEqual(status, 503)  # No AI controller is attached in this fixture.

    def test_preflight_never_grants_cors(self):
        status, headers, _ = self.request(method="OPTIONS", origin="https://example.com")
        self.assertEqual(status, 501)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))


if __name__ == "__main__":
    unittest.main()
