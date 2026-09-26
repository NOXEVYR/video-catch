"""Standard-library client. Pairing stays in local settings, never in stdout."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
from collaboration import settings_path, save_pairing


def fail(code, message, status=None):
    result = {"ok": False, "code": code, "error": message}
    if status is not None:
        result["status"] = status
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description="拾影 AI 客户端；推荐先在拾影点击「开启协作并复制」，再调用 state。")
    parser.add_argument("action", nargs="?", choices=["capabilities", "state", "watch", "import", "download", "clip", "cancel",
        "capture_sources", "record_start", "record_pause", "record_resume", "record_stop", "record_state", "screenshot"])
    parser.add_argument("--pair", action="store_true", help="从剪贴板读取本次配对码并保存在本机设置中，不输出配对码")
    parser.add_argument("--json", default="{}", help="JSON 参数；也可用 --input 避免 shell 转义")
    parser.add_argument("--input", help="UTF-8 JSON 参数文件")
    args = parser.parse_args()
    try:
        config = settings_path()
        if args.pair:
            import re
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            try:
                token = root.clipboard_get().strip()
            finally:
                root.destroy()
            if not re.fullmatch(r"[A-Za-z0-9_-]{32}", token):
                raise ValueError("请先在拾影点击复制配对码")
            save_pairing(token)
            print('{"ok": true, "message": "已保存本次本机配对设置"}')
            return
        if not args.action:
            parser.error("需要 action 或 --pair")
        try:
            settings = {} if os.environ.get("VIDEOCATCH_TOKEN") else json.loads(config.read_text(encoding="utf-8"))
            token = os.environ.get("VIDEOCATCH_TOKEN") or settings["token"]
            port = settings.get("port", 18796)
            if not isinstance(token, str) or not token or type(port) is not int or not 1 <= port <= 65535:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            fail("pairing_required", "请在拾影点击「开启协作并复制」，完成本机配对后重试。")
        try:
            data = json.loads(Path(args.input).read_text(encoding="utf-8-sig") if args.input else args.json)
        except (OSError, ValueError):
            fail("invalid_input", "请检查 --input 文件存在且为 UTF-8 JSON，或检查 --json 参数。")
        if not isinstance(data, dict):
            fail("invalid_input", "参数必须是 JSON 对象。")
        request = Request(f"http://127.0.0.1:{port}/api/v1/" + args.action,
                          data=json.dumps(data).encode("utf-8"),
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with build_opener(ProxyHandler({})).open(request, timeout=8) as response:
            result = json.load(response)
        print(json.dumps(result, ensure_ascii=False))
    except HTTPError as exc:
        errors = {401: ("pairing_expired", "配对已过期。请在拾影再次点击「开启协作并复制」。"),
                  403: ("collaboration_disabled", "AI 协作已关闭。请在拾影点击「开启协作并复制」。"),
                  503: ("busy", "拾影界面忙碌，请先查询 state 再决定是否重试。")}
        try:
            detail = json.loads(exc.read()).get("error", "请求失败")
        except (ValueError, AttributeError):
            detail = "请求失败"
        code, message = errors.get(exc.code, ("request_rejected", detail))
        fail(code, message, exc.code)
    except (URLError, TimeoutError):
        fail("connection_failed", "无法连接拾影或请求超时。请确认拾影已打开并已开启协作；恢复后先查 state，避免重复创建裁剪任务。")
    except (OSError, ValueError, KeyError, URLError):
        fail("client_error", "本机设置或响应无效；请在拾影重新点击「开启协作并复制」，并检查请求参数。")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
