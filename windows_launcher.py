"""Windowed entry point for the installed Wordbook executable."""

import ctypes
import http.client
import json
import os
from pathlib import Path
import sys
import time
import traceback

import wordbook


LOG_PATH = Path(wordbook.PORT_FILE).with_name("startup.log")
INSTANCE_READY_TIMEOUT = 30


def open_browser(url):
    try:
        os.startfile(url)
    except OSError:
        message = ("无法打开默认浏览器，请在浏览器中访问下方地址。\n"
                   "Could not open the default browser. Open this address manually.\n\n"
                   f"{url}")
        ctypes.windll.user32.MessageBoxW(None, message, "Wordbook · 生词本", 0x30)


def reopen_running_instance():
    deadline = time.monotonic() + INSTANCE_READY_TIMEOUT
    while time.monotonic() < deadline:
        try:
            port = int(Path(wordbook.PORT_FILE).read_text(encoding="utf-8"))
            if not 1 <= port <= 65535:
                raise ValueError("Invalid Wordbook port")
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.4)
            try:
                connection.request("GET", "/api/ping")
                response = connection.getresponse()
                info = json.loads(response.read(1024)) if response.status == 200 else {}
            finally:
                connection.close()
        except (OSError, ValueError, http.client.HTTPException):
            info = {}
        if isinstance(info, dict) and info.get("app") == "wordbook" and info.get("ok") is True:
            open_browser(f"http://127.0.0.1:{port}/")
            return
        time.sleep(0.15)
    raise RuntimeError("找不到正在运行的生词本，请退出旧版窗口后重试。")


def run():
    try:
        wordbook.main(on_existing=reopen_running_instance, open_browser=open_browser)
    except (Exception, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code in (0, None):
            return 0
        logged = False
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            LOG_PATH.write_text(traceback.format_exc(), encoding="utf-8")
            logged = True
        except OSError:
            pass
        message = f"生词本启动失败。\nWordbook could not start.\n\n{exc}"
        if logged:
            message += f"\n\n是否打开日志？\nOpen the log?\n{LOG_PATH}"
        choice = ctypes.windll.user32.MessageBoxW(None, message, "Wordbook · 生词本",
                                                 0x14 if logged else 0x10)
        if logged and choice == 6:
            try:
                os.startfile(str(LOG_PATH))
            except OSError:
                pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
