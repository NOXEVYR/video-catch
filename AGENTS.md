# VideoCatch

- Keep source, browser extension, documentation, and release packages in this folder.
- Run `.build/venv/Scripts/python.exe -m unittest discover -s tests -v` for backend and download integration changes.
- Run `node --test tests/extension.test.cjs` for extension changes if Node is available.
- Build the portable Windows app with `.build/venv/Scripts/python.exe build.py`.
- Never bundle captured URLs, cookies, pairing tokens, downloaded videos, or local settings.
- AI routes use the authenticated loopback bridge and reject browser Origin headers. Per the user's 2026-09-30 request, startup enables AI by default and rotates the local pairing token; users can persistently disable auto-enable in settings or revoke collaboration. Dispatch Tk and job queue operations only on the UI thread.
- Run `.build/venv/Scripts/python.exe -m unittest discover -s tests -v` after AI/clip changes; `tests/test_ai_api.py` covers HTTP auth, capture/download/clip flow, cancellation and invalid ranges.
- AI client settings in LOCALAPPDATA are private; never add them to release inputs. Clips use unique names and never overwrite source files.
- Test with generated local video fixtures; do not change a user's browser profile, proxy, or certificate store.

- Set `VIDEOCATCH_BUILD_DIR` to keep build intermediates outside the deliverable source directory. The build reuses the hash-verified Deno binary from that directory.
- UI layouts and theme are in `ui.py`; queue and action callbacks stay in `app.py`. Verify actual empty/populated windows and minimum-size layout after visual changes.
- Use `QUALITY-CHECKLIST.md` for regression scope. Check affected lifecycle combinations and failure recovery, not only the new feature's happy path. Report physical-device and installed-package checks separately from synthetic tests.
