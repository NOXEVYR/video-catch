"""Updater tests use only synthetic releases and in-memory HTTP responses."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from updater import (API, ASSET_BASE, MAX_AUTOMATIC_BYTES, Release, UpdateError,
                     Updater, select_release, verify_package)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def synthetic_archive(path, version="0.6.0", *, extra=None):
    files = {"VideoCatch.exe": b"new desktop", "VideoCatchAI.exe": b"new client",
             "version.py": f'VERSION = "{version}"\n'.encode()}
    files.update(extra or {})
    manifest = {"version": version, "files": {name: {"bytes": len(data), "sha256": digest(data)}
                                              for name, data in files.items()}}
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr("VideoCatch/" + name, data)
        archive.writestr("VideoCatch/runtime-manifest.json", json.dumps(manifest))
    name = f"VideoCatch-v{version}-Windows-x64.zip"
    return Release(version, "v" + version, name, f"{ASSET_BASE}/v{version}/{name}", path.stat().st_size,
                   digest(path.read_bytes()), "GitHub asset digest")


def metadata(release, *, include_digest=True):
    return {"tag_name": release.tag, "draft": False, "prerelease": False,
            "assets": [{"name": release.asset_name, "browser_download_url": release.asset_url,
                        "size": release.bytes, "digest": "sha256:" + release.sha256 if include_digest else None}]}


class Response(io.BytesIO):
    status = 200
    def __init__(self, body):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / "fixture.zip"
        self.release = synthetic_archive(self.archive)

    def test_fixed_official_windows_stable_asset_and_trusted_digest(self):
        selected = select_release(metadata(self.release))
        self.assertEqual(selected.sha256, self.release.sha256)
        for change in ({"prerelease": True}, {"tag_name": "v0.6.0-rc.1"},
                       {"assets": [dict(metadata(self.release)["assets"][0], browser_download_url="https://other.test/a.zip")]},
                       {"assets": [dict(metadata(self.release, include_digest=False)["assets"][0])] } ):
            value = metadata(self.release)
            value.update(change)
            with self.subTest(change=change), self.assertRaises(UpdateError):
                select_release(value)

    def test_check_throttle_backoff_manual_and_current_version(self):
        calls = []
        def opener(request, timeout):
            self.assertEqual(request.full_url, API)
            calls.append(1)
            return Response(json.dumps(metadata(self.release)).encode())
        clock = [1000.0]
        updater = Updater(self.root / "state", "0.5.1-rc.2", self.root / "app", opener=opener, clock=lambda: clock[0])
        self.assertEqual(updater.check()["status"], "available")
        self.assertEqual(updater.check()["status"], "throttled")
        restarted = Updater(self.root / "state", "0.5.1-rc.2", self.root / "app", opener=opener, clock=lambda: clock[0])
        self.assertEqual(restarted.check()["status"], "throttled")
        self.assertEqual(len(calls), 1)
        self.assertEqual(updater.check(manual=True)["status"], "available")
        self.assertEqual(len(calls), 2)
        newer = Updater(self.root / "state2", "0.7.0", self.root / "app", opener=opener, clock=lambda: clock[0])
        self.assertEqual(newer.check()["status"], "current")
        broken = Updater(self.root / "error", "0.5.0", self.root / "app", opener=lambda *_args, **_kw: (_ for _ in ()).throw(OSError("offline")), clock=lambda: clock[0])
        first = broken.check()
        self.assertEqual(first["next_check"], 1000 + 3600)
        second = broken.check(manual=True)
        self.assertEqual(second["next_check"], 1000 + 7200)

    def test_complete_download_and_manifest_verification(self):
        archive = self.archive.read_bytes()
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            return Response(archive)
        updater = Updater(self.root / "state", "0.5.0", self.root / "app", opener=opener)
        package = updater.stage(self.release)
        self.assertEqual(package.files["VideoCatch.exe"]["sha256"], digest(b"new desktop"))
        self.assertEqual(calls, [self.release.asset_url])
        self.assertEqual(updater.stage(self.release).path, package.path)
        self.assertEqual(len(calls), 1)

    def test_large_archive_requires_explicit_choice_before_network(self):
        big = Release(self.release.version, self.release.tag, self.release.asset_name, self.release.asset_url,
                      MAX_AUTOMATIC_BYTES + 1, self.release.sha256, self.release.digest_source)
        updater = Updater(self.root / "state", "0.5.0", self.root / "app",
                          opener=lambda *_args, **_kw: self.fail("download started"))
        with self.assertRaisesRegex(UpdateError, "50 MiB"):
            updater.stage(big)

    def test_other_platform_never_checks_or_downloads_windows_package(self):
        updater = Updater(self.root / "state", "0.5.0", self.root / "app",
                          opener=lambda *_args, **_kw: self.fail("network started"))
        with patch("updater.platform.machine", return_value="ARM64"):
            self.assertEqual(updater.check()["status"], "unsupported")
            with self.assertRaisesRegex(UpdateError, "Windows x64"):
                updater.stage(self.release)

    def test_official_checksum_fallback_is_bound_to_github_asset_digest(self):
        checksum_name = f"checksums-v{self.release.version}.json"
        checksum_url = f"{ASSET_BASE}/{self.release.tag}/{checksum_name}"
        checksum = json.dumps({"file": self.release.asset_name, "bytes": self.release.bytes,
                               "sha256": self.release.sha256, "root": "VideoCatch"}).encode()
        value = metadata(self.release, include_digest=False)
        value["assets"].append({"name": checksum_name, "browser_download_url": checksum_url,
                                "digest": "sha256:" + digest(checksum), "size": len(checksum)})
        selected = select_release(value, opener=lambda request, timeout: Response(checksum))
        self.assertEqual(selected.sha256, self.release.sha256)
        self.assertIn("checksums", selected.digest_source)

    def test_partial_http_response_is_refused_without_retry(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            response = Response(self.archive.read_bytes())
            response.headers["Content-Range"] = "bytes 0-1/999"
            return response
        updater = Updater(self.root / "state", "0.5.0", self.root / "app", opener=opener)
        with self.assertRaisesRegex(UpdateError, "完整程序包"):
            updater.stage(self.release)
        self.assertEqual(calls, [self.release.asset_url])
        self.assertFalse((self.root / "state" / "updates" / (self.release.asset_name + ".part")).exists())

    def test_unwritable_check_state_returns_actionable_error(self):
        updater = Updater(self.root / "state", "0.5.0", self.root / "app",
                          opener=lambda *_args, **_kw: Response(json.dumps(metadata(self.release)).encode()))
        with patch.object(updater, "_write_state", side_effect=OSError("disk full")):
            result = updater.check()
        self.assertEqual(result["status"], "error")
        self.assertIn("无法保存", result["error"])

    def test_archive_rejects_extra_and_traversal_files_despite_outer_digest(self):
        for extra in ({"../escape": b"bad"}, {"private.txt": b"extra"}):
            with self.subTest(extra=extra):
                path = self.root / "bad.zip"
                release = synthetic_archive(path)
                with zipfile.ZipFile(path, "a") as archive:
                    for name, data in extra.items():
                        archive.writestr("VideoCatch/" + name, data)
                release = Release(release.version, release.tag, release.asset_name, release.asset_url,
                                  path.stat().st_size, digest(path.read_bytes()), release.digest_source)
                with self.assertRaises(UpdateError):
                    verify_package(path, release)


if __name__ == "__main__":
    unittest.main()
