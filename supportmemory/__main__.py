"""青柚家电客服演示的命令行。"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

from .bot import chat
from .prefs import PREFERENCE_KEYS
from .store import InvalidAccount, forget, initialize, read_preferences


_ORDERS_PATH = Path("data/orders.json")
_DATABASE_PATH = Path("memory.sqlite3")
_FORGOT_ALL = "已清除该账户的全部偏好。"

_DEMO = (
    {"command": "chat", "account": "A", "session": "a1", "message": "以后只看未发货的"},
    {"command": "chat", "account": "A", "session": "a2", "message": "列出订单"},
    {"command": "chat", "account": "B", "session": "b1", "message": "列出订单"},
    {"command": "forget", "account": "A"},
    {"command": "chat", "account": "A", "session": "a3", "message": "列出订单"},
    {"command": "chat", "account": "A", "session": "a3", "message": "叫我王先生13812345678"},
)


def _same_output_file(resolved_path, protected_path):
    if resolved_path == protected_path.resolve():
        return True
    try:
        return resolved_path.samefile(protected_path)
    except FileNotFoundError:
        return False


def _export(orders, path):
    steps = []
    with closing(sqlite3.connect(":memory:")) as connection:
        initialize(connection)
        for index, step in enumerate(_DEMO):
            account = step["account"]
            if step["command"] == "chat":
                written_at = f"2026-01-01T00:00:{index:02d}Z"
                answer = chat(connection, orders, account, step["message"], written_at)
            else:
                forget(connection, account, None)
                answer = _FORGOT_ALL
            steps.append(
                {"input": step, "answer": answer, "preferences": read_preferences(connection, account)}
            )
    content = json.dumps({"steps": steps}, ensure_ascii=False, indent=2)
    temporary = NamedTemporaryFile(dir=path.parent, delete=False)
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            temporary.write((content + "\n").encode("utf-8"))
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="青柚家电客服偏好演示")
    commands = parser.add_subparsers(dest="command", required=True)

    chat_parser = commands.add_parser("chat", help="发送消息")
    chat_parser.add_argument("--account", required=True)
    chat_parser.add_argument("--session", required=True)
    chat_parser.add_argument("message")

    forget_parser = commands.add_parser("forget", help="清除账户偏好")
    forget_parser.add_argument("--account", required=True)
    forget_parser.add_argument("--key", choices=PREFERENCE_KEYS)

    show_parser = commands.add_parser("show", help="查看账户偏好和写入时间")
    show_parser.add_argument("--account", required=True)

    export_parser = commands.add_parser("export", help="导出内置演示")
    export_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.command in {"chat", "export"}:
        try:
            orders = json.loads(_ORDERS_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            parser.exit(1, "缺少 data/orders.json。请按 HANDOFF 的缺数据恢复步骤复制样例订单。\n")

    if args.command == "export":
        path = args.out.resolve()
        if any(_same_output_file(path, protected) for protected in (_DATABASE_PATH, _ORDERS_PATH)):
            parser.error("导出目标不能覆盖记忆数据库或样例订单文件。")
        _export(orders, path)
        print("已导出演示 JSON。")
        return

    try:
        with closing(sqlite3.connect(_DATABASE_PATH)) as connection:
            initialize(connection)
            if args.command == "chat":
                written_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
                print(chat(connection, orders, args.account, args.message, written_at))
            elif args.command == "forget":
                forget(connection, args.account, args.key)
                if args.key is None:
                    print(_FORGOT_ALL)
                else:
                    print(f"已清除该账户的 {args.key} 偏好。")
            else:
                preferences = read_preferences(connection, args.account)
                print(json.dumps(preferences, ensure_ascii=False, indent=2))
    except InvalidAccount as error:
        parser.error(str(error))
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            parser.exit(1, "记忆数据库被锁定。请关闭占用数据库的程序，再重试。\n")
        raise


if __name__ == "__main__":
    main()
