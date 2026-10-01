"""Small private local state store. No browser capture data is persisted here.

All public I/O methods contain filesystem errors and expose only safe error
class names in ``last_error``/``warnings``. ``enabled=False`` uses memory only.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid


SCHEMA = 1
MAX_RECEIPTS = 200
MAX_LOG_BYTES = 256 * 1024
LOG_BACKUPS = 3
MAX_SETTINGS_BYTES = 16 * 1024
MAX_RECEIPTS_BYTES = 2 * 1024 * 1024
DEFAULT_SETTINGS = {"remember_folder": False, "folder": "", "diagnostics": False, "trust_until": 0, "ai_on_start": True}
SETTING_KEYS = frozenset(DEFAULT_SETTINGS)
OPERATIONS = frozenset({"download", "clip", "record", "screenshot"})
BUSY = frozenset({"排队中", "解析中", "下载中", "整理文件", "裁剪中", "准备录制", "录制中", "录制暂停", "正在保存", "截图中"})
TERMINAL = frozenset({"已保存", "失败", "已取消", "已中断", "未知"})
STATUS_VALUES = BUSY | TERMINAL
KIND = {"download": "视频下载", "clip": "本地裁剪", "record": "屏幕录制", "screenshot": "屏幕截图"}
LOG_EVENTS = frozenset({"startup", "shutdown", "state_io_error", "download", "clip", "record", "screenshot", "bridge", "task", "trust_granted", "trust_revoked", "refresh_error"})
LOG_REASONS = frozenset({"user", "completed", "cancelled", "interrupted", "timeout", "unavailable", "invalid_state"})
LOG_ERRORS = frozenset({"OSError", "PermissionError", "FileNotFoundError", "TimeoutError", "StateFormatError", "JSONDecodeError", "RuntimeError", "ValueError", "TypeError", "AttributeError", "TclError"})
RECEIPT_KEYS = frozenset({"id", "operation", "status", "path", "elapsed", "display_name"})


class StateFormatError(ValueError):
    """A state file is malformed or uses an unsupported schema."""


def default_data_dir():
    override = os.environ.get("VIDEOCATCH_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VideoCatch"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "VideoCatch"
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "VideoCatch"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "VideoCatch"


def _settings(value):
    if isinstance(value, dict) and set(value) == (SETTING_KEYS - {"ai_on_start"}) | {"schema"}:
        value = dict(value, ai_on_start=True)
    if not isinstance(value, dict) or set(value) != SETTING_KEYS | {"schema"} or type(value["schema"]) is not int or value["schema"] != SCHEMA:
        raise StateFormatError("settings schema")
    result = {key: value[key] for key in SETTING_KEYS}
    if any(type(result[key]) is not bool for key in ("remember_folder", "diagnostics", "ai_on_start")):
        raise StateFormatError("settings booleans")
    folder = result["folder"]
    if not isinstance(folder, str) or len(folder) > 4096 or "\x00" in folder or "\n" in folder or "\r" in folder:
        raise StateFormatError("settings folder")
    trust = result["trust_until"]
    if type(trust) not in (int, float) or not math.isfinite(trust) or not 0 <= trust <= time.time() + 86400:
        raise StateFormatError("settings trust")
    if not result["remember_folder"]:
        result["folder"] = ""
    return result


def _receipt(value):
    if isinstance(value, dict) and set(value) == RECEIPT_KEYS - {"display_name"}:
        value = dict(value, display_name="")
    if not isinstance(value, dict) or set(value) != RECEIPT_KEYS:
        raise StateFormatError("receipt fields")
    ident, operation, status, path, elapsed = (value[key] for key in ("id", "operation", "status", "path", "elapsed"))
    if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident):
        raise StateFormatError("receipt id")
    if operation not in OPERATIONS or status not in STATUS_VALUES:
        raise StateFormatError("receipt operation/status")
    if not isinstance(path, str) or len(path) > 4096 or any(char in path for char in "\x00\r\n") or "://" in path:
        raise StateFormatError("receipt path")
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or not 0 <= elapsed <= 30 * 86400:
        raise StateFormatError("receipt elapsed")
    name = value["display_name"]
    if not isinstance(name, str) or len(name) > 120 or any(c in name for c in "\r\n\x00"):
        raise StateFormatError("receipt display name")
    return {"id": ident, "operation": operation, "status": status, "path": path, "elapsed": int(elapsed), "display_name": name}


def _receipts(value):
    if not isinstance(value, dict) or set(value) != {"schema", "items"} or type(value["schema"]) is not int or value["schema"] != SCHEMA:
        raise StateFormatError("receipts schema")
    items = value["items"]
    if not isinstance(items, list) or len(items) > MAX_RECEIPTS:
        raise StateFormatError("receipts count")
    records = [_receipt(item) for item in items]
    if len({item["id"] for item in records}) != len(records):
        raise StateFormatError("duplicate receipt id")
    return records


class LocalState:
    """Private settings, safe task receipts, and opt-in bounded diagnostics.

    ``save_*`` returns bool; ``load_*`` returns defaults on error. The most
    recent error class is exposed as ``last_error`` and safe notices accumulate
    in ``warnings``. Corrupt files are renamed to unique backups before a new
    file can be written; if backup fails, writes to that file stay blocked.
    """

    def __init__(self, data_dir=None, enabled=True):
        self.data_dir = Path(data_dir).expanduser() if data_dir is not None else default_data_dir()
        self.enabled = bool(enabled)
        self.warnings = []
        self.last_error = ""
        self._blocked = set()
        self._settings_cache = dict(DEFAULT_SETTINGS)
        self._settings_loaded = False
        self._receipts_cache = []

    def _warn(self, label, error):
        self.last_error = type(error).__name__
        self.warnings.append(f"{label}（{self.last_error}）")
        del self.warnings[:-20]

    def _backup_bad(self, path):
        backup = path.with_name(f"{path.stem}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{path.suffix}")
        try:
            os.replace(path, backup)
        except OSError as exc:
            self._blocked.add(path)
            self._warn("状态文件损坏且无法备份；保留原文件，拒绝覆盖", exc)
            return False
        self._blocked.discard(path)
        self._warn(f"状态文件损坏，已保留备份 {backup.name}", StateFormatError())
        return True

    def _read(self, path, validator):
        try:
            limit = MAX_SETTINGS_BYTES if path.name == "settings.json" else MAX_RECEIPTS_BYTES
            with open(path, "rb") as stream:
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise StateFormatError("state file too large")
            value = json.loads(raw.decode("utf-8"))
            validated = validator(value)
            self._blocked.discard(path)
            self.last_error = ""
            return validated
        except FileNotFoundError:
            self._blocked.discard(path)
            return None
        except (json.JSONDecodeError, UnicodeError, StateFormatError, TypeError, ValueError):
            self._backup_bad(path)
            return None
        except OSError as exc:
            self._blocked.add(path)
            self._warn("读取本地状态失败", exc)
            return None

    def _prepare_write(self, path, validator):
        try:
            exists = path.exists()
        except OSError as exc:
            self._warn("检查本地状态失败，未覆盖", exc)
            return False
        if path in self._blocked and exists:
            self._warn("状态文件仍需人工处理，未覆盖", StateFormatError())
            return False
        if not exists:
            self._blocked.discard(path)
        if exists:
            self._read(path, validator)
        return path not in self._blocked

    def _write(self, path, value):
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self.last_error = ""
            return True
        except (OSError, ValueError, TypeError) as exc:
            self._warn("保存本地状态失败", exc)
            return False
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def load_settings(self):
        if not self.enabled:
            return deepcopy(self._settings_cache)
        value = self._read(self.data_dir / "settings.json", _settings)
        self._settings_cache = dict(value if value is not None else DEFAULT_SETTINGS)
        self._settings_loaded = True
        return deepcopy(self._settings_cache)

    def save_settings(self, values):
        if self.enabled and not self._settings_loaded:
            self.load_settings()
        try:
            if not isinstance(values, dict) or set(values) - (SETTING_KEYS | {"schema"}):
                raise StateFormatError("unknown settings")
            if "schema" in values and values["schema"] != SCHEMA:
                raise StateFormatError("settings version")
            merged = {**self._settings_cache, **{key: value for key, value in values.items() if key != "schema"}, "schema": SCHEMA}
            result = _settings(merged)
        except (StateFormatError, TypeError, ValueError) as exc:
            self._warn("配置参数无效，未保存", exc)
            return False
        if self.enabled:
            path = self.data_dir / "settings.json"
            if not self._prepare_write(path, _settings) or not self._write(path, {"schema": SCHEMA, **result}):
                return False
        self._settings_cache = result
        self._settings_loaded = True
        self.last_error = ""
        return True

    def save_receipts(self, items):
        try:
            source = items.values() if isinstance(items, dict) else iter(items)
            records = []
            for item in source:
                if not isinstance(item, dict) or item.get("status") == "待保存":
                    continue
                raw = {"id": item.get("id"), "operation": item.get("operation") or "download",
                       "status": item.get("status") if item.get("status") in STATUS_VALUES else "未知",
                       "path": item.get("path") or "", "elapsed": item.get("elapsed", 0), "display_name": item.get("display_name", "")}
                try:
                    records.append(_receipt(raw))
                except (StateFormatError, TypeError):
                    continue  # Never persist a malformed or sensitive task value.
            unique = {record["id"]: record for record in records}
            records = list(unique.values())[-MAX_RECEIPTS:]
        except (TypeError, ValueError) as exc:
            self._warn("任务回执无效，未保存", exc)
            return False
        if self.enabled:
            path = self.data_dir / "receipts.json"
            if not self._prepare_write(path, _receipts) or not self._write(path, {"schema": SCHEMA, "items": records}):
                return False
        self._receipts_cache = records
        self.last_error = ""
        return True

    def load_receipts(self):
        if self.enabled:
            records = self._read(self.data_dir / "receipts.json", _receipts)
            self._receipts_cache = records if records is not None else []
        result = []
        for record in self._receipts_cache:
            item = dict(record)
            if item["status"] in BUSY:
                item["status"] = "已中断"
            kind = KIND[item["operation"]]
            item.update(title=item.get("display_name") or (Path(item["path"]).name if item["path"] else f"{kind}历史记录"), kind=kind, source="历史记录", host="", url="",
                        headers={}, size="", progress="上次运行已中断；不会自动重试" if item["status"] == "已中断" else "历史记录",
                        error="", history=True)
            result.append(item)
        return result

    def log(self, event, **fields):
        """Write only closed-vocabulary metadata; never strings from a job."""
        if not self.enabled or not self._settings_cache["diagnostics"] or not isinstance(event, str) or event not in LOG_EVENTS:
            return False
        safe = {}
        for name, value in fields.items():
            if name == "status" and isinstance(value, str) and value in STATUS_VALUES:
                safe[name] = value
            elif name == "operation" and isinstance(value, str) and value in OPERATIONS:
                safe[name] = value
            elif name == "reason" and isinstance(value, str) and value in LOG_REASONS:
                safe[name] = value
            elif name == "error_class" and isinstance(value, str) and value in LOG_ERRORS:
                safe[name] = value
            elif name in {"count", "duration_ms"} and type(value) is int and 0 <= value <= (1000000 if name == "count" else 3600000):
                safe[name] = value
        line = (json.dumps({"time": int(time.time()), "event": event, **safe}, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        path = self.data_dir / "diagnostics.log"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                raise OSError("diagnostic log is a link")
            if path.exists() and path.stat().st_size + len(line) > MAX_LOG_BYTES:
                for index in range(LOG_BACKUPS, 1, -1):
                    older = path.with_name(f"diagnostics.log.{index - 1}")
                    if older.exists():
                        os.replace(older, path.with_name(f"diagnostics.log.{index}"))
                os.replace(path, path.with_name("diagnostics.log.1"))
            with open(path, "ab") as stream:
                stream.write(line)
            self.last_error = ""
            return True
        except OSError as exc:
            self._warn("诊断记录失败", exc)
            return False
