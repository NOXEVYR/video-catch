"""Owned-file-only, recoverable Windows updater handoff.

The GUI must close voluntarily after its queue/recording guards pass. This
helper waits for one exact process identity and never terminates a process.
Successful replacement is not reported as installed until the restarted app
confirms UI readiness with confirm_started().
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

from updater import (Release, UpdateError, VerifiedPackage, _manifest, file_sha256,
                     safe_name, verify_package, version_key)


@dataclass(frozen=True)
class Operation:
    name: str
    previous_sha256: str | None
    target_sha256: str | None


@dataclass(frozen=True)
class Plan:
    package: VerifiedPackage
    install_root: Path
    state_dir: Path
    previous_version: str
    operations: tuple[Operation, ...]
    reused: int
    manifest_sha256: str


def _read_manifest(root: Path) -> tuple[str, dict[str, dict]]:
    manifest_path = root / "runtime-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise UpdateError("当前安装缺少可信的运行时清单")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = data["version"]
        version_key(version)
        files = _manifest(data, version)
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise UpdateError("当前安装的运行时清单无效") from exc
    return version, files


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _target(root: Path, name: str) -> Path:
    safe_name(name)
    if _is_link(root):
        raise UpdateError("安装目录不能是符号链接")
    target = root
    for part in name.split("/"):
        target = target / part
        if _is_link(target):
            raise UpdateError(f"安装路径包含符号链接：{name}")
    return target


def prepare_update(package: VerifiedPackage, install_root: Path, state_dir: Path) -> Plan:
    """Reject local edits/collisions before UI asks for an exit."""
    supplied_root = Path(install_root)
    if _is_link(supplied_root):
        raise UpdateError("安装目录不能是符号链接")
    root = supplied_root.resolve()
    if not root.is_dir():
        raise UpdateError("安装目录无效")
    package = verify_package(package.path, package.release)
    previous_version, old = _read_manifest(root)
    if version_key(package.release.version) <= version_key(previous_version):
        raise UpdateError("目标不是比当前安装更新的稳定版本")
    new = package.files
    operations = []
    reused = 0
    # A case-only rename can alias the same Windows path; refuse ambiguity.
    old_fold = {name.casefold(): name for name in old}
    new_fold = {name.casefold(): name for name in new}
    if any(old_fold[key] != new_fold[key] for key in old_fold.keys() & new_fold.keys()):
        raise UpdateError("新旧清单存在大小写路径冲突")
    for name in sorted(set(old) | set(new)):
        dest = _target(root, name)
        if dest.exists() and not dest.is_file():
            raise UpdateError(f"程序文件路径被目录占用：{name}")
        prior = old.get(name)
        target = new.get(name)
        if prior:
            if dest.is_file() and (dest.stat().st_size != prior["bytes"] or file_sha256(dest) != prior["sha256"].lower()):
                raise UpdateError(f"本机已登记程序文件被修改，拒绝覆盖：{name}")
        elif dest.exists():
            raise UpdateError(f"目标路径已有未登记文件，拒绝覆盖：{name}")
        if prior and target and prior == target and dest.is_file():
            reused += 1
            continue
        if not dest.exists() and target is None:
            continue
        operations.append(Operation(name, prior["sha256"].lower() if dest.exists() and prior else None,
                                    target["sha256"].lower() if target else None))
    operations.append(Operation("runtime-manifest.json", file_sha256(root / "runtime-manifest.json"),
                                None))  # The trusted ZIP's manifest is handled as the final file.
    with zipfile.ZipFile(package.path) as archive:
        manifest_sha256 = hashlib.sha256(archive.read("VideoCatch/runtime-manifest.json")).hexdigest()
    return Plan(package, root, Path(state_dir), previous_version, tuple(operations), reused, manifest_sha256)


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def process_creation_time(pid: int) -> int:
    """Return Windows FILETIME ticks, independent of a reused PID."""
    if sys.platform != "win32":
        raise UpdateError("进程身份校验仅支持 Windows")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise UpdateError(f"无法读取进程 {pid} 的身份")
    try:
        values = (ctypes.c_ulonglong * 4)()
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(values, i * 8) for i in range(4))):
            raise UpdateError("无法读取进程创建时间")
        return int(values[0])
    finally:
        kernel.CloseHandle(handle)


def wait_for_exact_exit(pid: int, creation_time: int, timeout: float = 120) -> bool:
    """Wait for the named process only; never kill by name or PID."""
    if sys.platform != "win32":
        raise UpdateError("独立安装器仅支持 Windows")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x101000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
    if not handle:
        if ctypes.get_last_error() == 87:  # ERROR_INVALID_PARAMETER: PID has exited.
            return True
        raise UpdateError("无法等待指定进程退出")
    try:
        values = (ctypes.c_ulonglong * 4)()
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        kernel.GetProcessTimes.restype = wintypes.BOOL
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(values, i * 8) for i in range(4))):
            raise UpdateError("无法核对指定进程的创建时间")
        if int(values[0]) != creation_time:
            return True  # The original PID exited; this is another process.
        result = kernel.WaitForSingleObject(handle, max(0, min(int(timeout * 1000), 0xFFFFFFFE)))
        if result == 0:
            return True
        if result == 0x102:
            return False
        raise UpdateError("等待指定进程退出失败")
    finally:
        kernel.CloseHandle(handle)


def _replace_from_stream(root: Path, name: str, source, transaction: str, expected: str) -> None:
    dest = _target(root, name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + f".update-{transaction}.tmp")
    if temporary.exists():
        raise UpdateError(f"存在未处理的程序临时文件：{name}")
    import hashlib
    digest = hashlib.sha256()
    try:
        with temporary.open("xb") as output:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                output.write(block)
                digest.update(block)
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest() != expected:
            raise UpdateError(f"替换前文件摘要不匹配：{name}")
        os.replace(temporary, dest)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _journal_path(state_dir: Path) -> Path:
    return state_dir / "updates" / "transaction.json"


def _rollback(journal: dict, state_dir: Path, install_root: Path) -> None:
    backup = state_dir / "updates" / "backups" / journal["id"]
    root = Path(install_root)
    for op in reversed(journal["operations"]):
        name = op["name"]
        dest = _target(root, name)
        old_sha = op["previous_sha256"]
        new_sha = op["target_sha256"]
        if dest.exists():
            current = file_sha256(dest)
            if current not in {old_sha, new_sha}:
                raise UpdateError(f"回滚时文件已被其他操作改动：{name}")
        if old_sha:
            saved = _target(backup, name)
            if not saved.is_file() or file_sha256(saved) != old_sha:
                raise UpdateError(f"回滚备份损坏：{name}")
            if not dest.exists() or file_sha256(dest) != old_sha:
                with saved.open("rb") as source:
                    _replace_from_stream(root, name, source, journal["id"] + "-rollback", old_sha)
        elif dest.exists():
            dest.unlink()
    journal["status"] = "rolled_back"
    _atomic_json(_journal_path(state_dir), journal)


def recover_pending(state_dir: Path, install_root: Path, *, child_exited=None) -> dict:
    """Rollback an interrupted transaction only after any restarted app exits."""
    path = _journal_path(Path(state_dir))
    if not path.exists():
        return {"status": "none"}
    journal = json.loads(path.read_text(encoding="utf-8"))
    if journal.get("status") in {"committed", "rolled_back"}:
        return {"status": journal["status"]}
    if journal.get("status") == "backing_up":
        journal["status"] = "rolled_back"  # No program file has changed yet.
        _atomic_json(path, journal)
        return {"status": "rolled_back"}
    marker = Path(state_dir) / "updates" / f"confirmed-{journal['id']}.json"
    if journal.get("status") in {"restarting", "awaiting_confirmation"} and marker.is_file():
        try:
            confirmation = json.loads(marker.read_text(encoding="utf-8"))
            pid_matches = journal.get("child_pid") is None or confirmation.get("pid") == journal["child_pid"]
            if (isinstance(confirmation.get("pid"), int) and confirmation["pid"] > 0 and pid_matches and
                    confirmation.get("id") == journal["id"] and
                    confirmation.get("version") == journal["target_version"]):
                journal["status"] = "committed"
                _atomic_json(path, journal)
                return {"status": "committed"}
        except (OSError, ValueError):
            pass
    pid = journal.get("child_pid")
    started = journal.get("child_created")
    if journal.get("status") in {"restarting", "awaiting_confirmation"} and (not pid or not started):
        return {"status": "unknown_child_identity"}
    if pid and started:
        exited = child_exited(pid, started) if child_exited else wait_for_exact_exit(pid, started, 0)
        if not exited:
            return {"status": "awaiting_running_app"}
    _rollback(journal, Path(state_dir), Path(install_root))
    return {"status": "rolled_back"}


def confirm_started(state_dir: Path, transaction_id: str, version: str) -> None:
    """Call on the restarted app's UI thread once startup is actually ready."""
    journal = json.loads(_journal_path(Path(state_dir)).read_text(encoding="utf-8"))
    if (journal.get("id") != transaction_id or journal.get("target_version") != version or
            journal.get("status") not in {"restarting", "awaiting_confirmation"} or
            (journal.get("child_pid") is not None and journal["child_pid"] != os.getpid())):
        raise UpdateError("更新重启确认与安装事务不匹配")
    _atomic_json(Path(state_dir) / "updates" / f"confirmed-{transaction_id}.json",
                 {"id": transaction_id, "version": version, "pid": os.getpid()})
    if journal["status"] == "awaiting_confirmation":
        journal["status"] = "committed"
        _atomic_json(_journal_path(Path(state_dir)), journal)


def _launch_app(plan: Plan, transaction: str):
    return subprocess.Popen([str(plan.install_root / "VideoCatch.exe"), "--update-transaction", transaction],
                            cwd=plan.install_root, close_fds=True)


def _confirm_app(process, transaction: str, plan: Plan, timeout: float = 30) -> bool | None:
    marker = plan.state_dir / "updates" / f"confirmed-{transaction}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.is_file():
            try:
                data = json.loads(marker.read_text(encoding="utf-8"))
                if data == {"id": transaction, "version": plan.package.release.version, "pid": process.pid}:
                    return True
            except (OSError, ValueError):
                pass
        if process.poll() is not None:
            return False
        time.sleep(.2)
    return None if process.poll() is None else False


def install_prepared(plan: Plan, *, host_pid: int, host_created: int, wait_fn=wait_for_exact_exit,
                     launch_fn=None, verify_fn=None, wait_timeout: float = 120) -> dict:
    """Install after exact host exit; False verify rolls back, None stays pending."""
    if host_pid <= 0 or host_created <= 0:
        raise UpdateError("缺少精确宿主进程身份")
    if not wait_fn(host_pid, host_created, wait_timeout):
        raise UpdateError("拾影仍在运行；安装未开始，未强制退出")
    fresh = prepare_update(plan.package, plan.install_root, plan.state_dir)
    if (fresh.operations != plan.operations or fresh.previous_version != plan.previous_version or
            fresh.manifest_sha256 != plan.manifest_sha256):
        raise UpdateError("退出后安装目录已变化，请重新检查更新")
    state_dir = plan.state_dir
    journal_file = _journal_path(state_dir)
    if journal_file.exists():
        previous = json.loads(journal_file.read_text(encoding="utf-8"))
        if previous.get("status") not in {"committed", "rolled_back"}:
            raise UpdateError("存在未解决的安装事务，请先恢复")
    transaction = uuid.uuid4().hex
    backup = state_dir / "updates" / "backups" / transaction
    backup.mkdir(parents=True, exist_ok=False)
    operations = [asdict(op) for op in plan.operations]
    journal = {"id": transaction, "status": "backing_up", "old_version": plan.previous_version,
               "target_version": plan.package.release.version, "archive_sha256": plan.package.release.sha256,
               "operations": operations, "child_pid": None, "child_created": None}
    _atomic_json(journal_file, journal)
    for op in plan.operations:
        if op.previous_sha256:
            source = _target(plan.install_root, op.name)
            saved = _target(backup, op.name)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, saved)
            if file_sha256(saved) != op.previous_sha256:
                raise UpdateError(f"程序文件备份失败：{op.name}")
    journal["status"] = "applying"
    _atomic_json(journal_file, journal)
    process = None
    try:
        with zipfile.ZipFile(plan.package.path) as archive:
            for op in plan.operations:
                dest = _target(plan.install_root, op.name)
                if op.target_sha256 is None and op.name != "runtime-manifest.json":
                    if dest.exists():
                        dest.unlink()
                    continue
                member = "VideoCatch/" + op.name
                expected = op.target_sha256
                if op.name == "runtime-manifest.json":
                    expected = plan.manifest_sha256
                    journal["operations"][-1]["target_sha256"] = expected
                    _atomic_json(journal_file, journal)
                with archive.open(member) as source:
                    _replace_from_stream(plan.install_root, op.name, source, transaction, expected)
        journal["status"] = "restarting"
        _atomic_json(journal_file, journal)
        process = (launch_fn or _launch_app)(plan, transaction)
        journal["child_pid"] = process.pid
        try:
            journal["child_created"] = process_creation_time(process.pid)
        except UpdateError:
            # Tests and non-Windows synthesis may supply a fake process. Real
            # Windows launches must identify the process before confirmation.
            if launch_fn is None:
                raise
        _atomic_json(journal_file, journal)
        confirmation = (verify_fn or _confirm_app)(process, transaction, plan)
        if confirmation is None:
            journal["status"] = "awaiting_confirmation"
            _atomic_json(journal_file, journal)
            return {"status": "restart_unconfirmed", "transaction": transaction, "reused": plan.reused}
        if not confirmation:
            raise UpdateError("新版本未完成启动确认")
        journal["status"] = "committed"
        _atomic_json(journal_file, journal)
        return {"status": "installed", "transaction": transaction, "reused": plan.reused,
                "changed": len(plan.operations) - 1}
    except Exception as exc:
        if process is not None and process.poll() is None:
            journal["status"] = "awaiting_confirmation"
            _atomic_json(journal_file, journal)
            raise UpdateError("新程序仍在运行但未确认就绪；保留事务，等待其退出后再恢复") from exc
        try:
            _rollback(journal, state_dir, plan.install_root)
        except Exception as rollback_error:
            journal["status"] = "rollback_failed"
            _atomic_json(journal_file, journal)
            raise UpdateError(f"安装失败且回滚未完成：{rollback_error}") from exc
        raise UpdateError(f"安装失败，已回滚：{exc}") from exc


def write_handoff(plan: Plan, *, host_pid: int, host_created: int) -> Path:
    """Serialize only public package identity and local paths for a helper EXE."""
    if host_pid <= 0 or host_created <= 0:
        raise UpdateError("缺少精确宿主进程身份")
    path = plan.state_dir / "updates" / f"handoff-{uuid.uuid4().hex}.json"
    _atomic_json(path, {"package": str(plan.package.path), "release": asdict(plan.package.release),
                        "install_root": str(plan.install_root), "state_dir": str(plan.state_dir),
                        "host_pid": host_pid, "host_created": host_created})
    return path


def launch_helper(helper_path: Path, handoff: Path, state_dir: Path):
    """Run an isolated copy so the installed helper executable can be updated."""
    source = Path(helper_path)
    if source.name != "VideoCatchUpdater.exe" or _is_link(source) or not source.is_file():
        raise UpdateError("独立更新助手不存在或路径无效")
    folder = Path(state_dir) / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / f"VideoCatchUpdater-{uuid.uuid4().hex}.exe"
    shutil.copy2(source, copy)
    if file_sha256(copy) != file_sha256(source):
        copy.unlink(missing_ok=True)
        raise UpdateError("更新助手复制后摘要不一致")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen([str(copy), "--handoff", str(handoff)], cwd=folder, creationflags=flags,
                            close_fds=True)


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "--handoff":
        raise SystemExit("用法: VideoCatchUpdater.exe --handoff <path>")
    handoff = Path(args[1])
    data = json.loads(handoff.read_text(encoding="utf-8"))
    state_dir = Path(data["state_dir"])
    try:
        release = Release(**data["release"])
        package = verify_package(Path(data["package"]), release)
        plan = prepare_update(package, Path(data["install_root"]), state_dir)
        result = install_prepared(plan, host_pid=int(data["host_pid"]), host_created=int(data["host_created"]))
    except (UpdateError, OSError, ValueError, KeyError, TypeError) as exc:
        result = {"status": "error", "error": str(exc)}
    _atomic_json(state_dir / "updates" / "last-result.json", result)
    return 0 if result["status"] == "installed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
