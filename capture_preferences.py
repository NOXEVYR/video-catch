"""Private, bounded capture controls. Never stores media paths or device names."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid


SCHEMA = 1
DEFAULTS = {
    "record_mode": "全屏录制", "screenshot_mode": "区域录制", "audio": "不录声音",
    "quality": "均衡", "fps": "30", "duration": "0", "cursor": True,
    "overlay": False, "countdown": "3 秒", "minimize": True, "profile": "日常演示",
}
CHOICES = {
    "record_mode": {"全屏录制", "区域录制", "窗口录制", "仅摄像头"},
    "screenshot_mode": {"全屏录制", "区域录制", "窗口录制"},
    "audio": {"不录声音", "系统声音", "麦克风", "系统 + 麦克风"},
    "quality": {"清晰优先", "均衡", "节省空间"},
    "fps": {"10", "15", "24", "30", "60"},
    "countdown": {"3 秒", "立即开始"},
    "profile": {"日常演示", "流畅动态", "轻量记录"},
}


def _validated(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS) | {"schema"} or type(value["schema"]) is not int or value["schema"] != SCHEMA:
        raise ValueError("capture preferences schema")
    result = {key: value[key] for key in DEFAULTS}
    for key, choices in CHOICES.items():
        if type(result[key]) is not str or result[key] not in choices:
            raise ValueError("capture preferences choice")
    if any(type(result[key]) is not bool for key in ("cursor", "overlay", "minimize")):
        raise ValueError("capture preferences boolean")
    duration = result["duration"]
    if type(duration) is not str or len(duration) > 20:
        raise ValueError("capture preferences duration")
    try:
        seconds = float(duration)
    except ValueError:
        raise ValueError("capture preferences duration") from None
    if not math.isfinite(seconds) or not 0 <= seconds <= 86400:
        raise ValueError("capture preferences duration")
    return result


class CapturePreferences:
    def __init__(self, data_dir, enabled=True):
        self.path = Path(data_dir) / "capture-preferences.json"
        self.enabled = bool(enabled)
        self.cache = dict(DEFAULTS)
        self.last_error = ""
        self.warnings = []
        self._blocked = False

    def _warning(self, message, error):
        self.last_error = type(error).__name__
        self.warnings.append(f"{message}（{self.last_error}）")
        del self.warnings[:-10]

    def _read(self):
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError("capture preferences too large")
            result = _validated(json.loads(raw.decode("utf-8")))
            self._blocked = False
            self.last_error = ""
            return result
        except FileNotFoundError:
            self._blocked = False
            return None
        except (ValueError, UnicodeError, TypeError) as error:
            backup = self.path.with_name(f"{self.path.stem}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{self.path.suffix}")
            try:
                os.replace(self.path, backup)
            except OSError as backup_error:
                self._blocked = True
                self._warning("采集参数损坏且无法备份，拒绝覆盖", backup_error)
                return None
            self._warning(f"采集参数损坏，已备份为 {backup.name}", error)
            return None
        except OSError as error:
            self._blocked = True
            self._warning("无法读取采集参数", error)
            return None

    def load(self):
        if self.enabled:
            value = self._read()
            if value is not None:
                self.cache = value
        return dict(self.cache)

    def save(self, values):
        try:
            value = _validated({"schema": SCHEMA, **values})
        except (ValueError, TypeError) as error:
            self._warning("采集参数无效，未保存", error)
            return False
        if not self.enabled:
            self.cache = value
            return True
        try:
            exists = self.path.exists()
        except OSError as error:
            self._warning("无法检查采集参数文件", error)
            return False
        if exists:
            self._read()
        if self._blocked:
            self._warning("采集参数原文件仍需处理，拒绝覆盖", ValueError())
            return False
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".capture-preferences-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"schema": SCHEMA, **value}, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.cache = value
            self.last_error = ""
            return True
        except OSError as error:
            self._warning("无法保存采集参数", error)
            return False
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
