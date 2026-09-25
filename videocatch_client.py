"""Standard-library client. Pairing stays in local settings, never in stdout."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError


def settings_path():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VideoCatch" / "ai-client.json"


def main():
    parser = argparse.ArgumentParser(description="拾影 AI 客户端；先在拾影启用接口并复制配对码，然后运行 --pair。")
    parser.add_argument("action", nargs="?", choices=["capabilities", "state", "watch", "import", "download", "clip", "cancel"])
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
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps({"token": token}), encoding="utf-8")
            print('{"ok": true, "message": "已保存本次本机配对设置"}')
            return
        if not args.action:
            parser.error("需要 action 或 --pair")
        token = os.environ.get("VIDEOCATCH_TOKEN") or json.loads(config.read_text(encoding="utf-8"))["token"]
        data = json.loads(Path(args.input).read_text(encoding="utf-8-sig") if args.input else args.json)
        if not isinstance(data, dict):
            raise ValueError("参数必须是 JSON 对象")
        request = Request("http://127.0.0.1:18796/api/v1/" + args.action,
                          data=json.dumps(data).encode("utf-8"),
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with build_opener(ProxyHandler({})).open(request, timeout=8) as response:
            result = json.load(response)
        print(json.dumps(result, ensure_ascii=False))
    except HTTPError as exc:
        print(json.dumps({"ok": False, "status": exc.code, "error": json.loads(exc.read()).get("error", "请求失败")}, ensure_ascii=False))
        sys.exit(1)
    except (OSError, ValueError, KeyError, URLError):
        print('{"ok": false, "error": "请检查拾影已启动、接口已启用、配对设置和 JSON 参数正确；重启拾影后需重新配对"}')
        sys.exit(1)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
