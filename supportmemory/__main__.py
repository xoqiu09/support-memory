"""青柚家电客服演示的命令行。"""

import argparse
from contextlib import ExitStack, closing
import json
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile

from .bot import chat
from .prefs import PREFERENCE_KEYS
from .store import (
    ACTIVITY_FILENAME,
    InvalidIdentifier,
    forget,
    initialize,
    read_audit,
    read_history,
    read_preferences,
    set_consent,
    transaction,
    utc_now,
    validate_identifiers,
)


_ORDERS_PATH = Path("data/orders.json")
_DATABASE_PATH = Path("memory.sqlite3")
_FORGOT_ALL = "已清除该账户的全部偏好。"
_CONSENT_OFF = "已关闭跨会话偏好保存。已有偏好仍然保留，可用 forget 清除。"

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


def _m2_demo(orders):
    with closing(sqlite3.connect(":memory:")) as connection:
        initialize(connection)
        with transaction(connection):
            set_consent(connection, "A", False)
        first = chat(
            connection, orders, "A", "电话13812345678，以后只看未发货的",
            "2026-01-01T00:00:00Z", "a1",
        )
        for index in range(1, 7):
            same_session = chat(
                connection, orders, "A", "列出订单", f"2026-01-01T00:00:{index:02d}Z", "a1"
            )
        new_session = chat(connection, orders, "A", "列出订单", "2026-01-01T00:00:07Z", "a2")
        preferences = read_preferences(connection, "A", time="2026-01-01T00:00:08Z")
        forget(connection, "A", None, "2026-01-01T00:00:09Z")
        return {
            "consent": {"account": "A", "enabled": False, "answer": _CONSENT_OFF},
            "first_answer": first,
            "same_session_answer": same_session,
            "new_session_answer": new_session,
            "preferences": preferences,
            "history": {"account": "A", "session": "a1", **read_history(connection, "A", "a1")},
            "audit": read_audit(connection, "A"),
        }


def _export(orders, path, m2_path):
    steps = []
    with closing(sqlite3.connect(":memory:")) as connection:
        initialize(connection)
        for index, step in enumerate(_DEMO):
            account = step["account"]
            written_at = f"2026-01-01T00:00:{index:02d}Z"
            if step["command"] == "chat":
                answer = chat(connection, orders, account, step["message"], written_at, step["session"])
            else:
                forget(connection, account, None, written_at)
                answer = _FORGOT_ALL
            steps.append(
                {
                    "input": step,
                    "answer": answer,
                    "preferences": read_preferences(connection, account, time=written_at),
                }
            )

    with ExitStack() as cleanup:
        staged = []
        for destination, data in ((path, {"steps": steps}), (m2_path, _m2_demo(orders))):
            content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            with NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                cleanup.callback(temporary_path.unlink, missing_ok=True)
                temporary.write(content.encode("utf-8"))
            staged.append((temporary_path, destination))
        for temporary_path, destination in staged:
            temporary_path.replace(destination)


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

    consent_parser = commands.add_parser("consent", help="设置跨会话偏好保存")
    consent_parser.add_argument("--account", required=True)
    consent_parser.add_argument("state", choices=("on", "off"))

    history_parser = commands.add_parser("history", help="查看会话窗口和摘要")
    history_parser.add_argument("--account", required=True)
    history_parser.add_argument("--session", required=True)

    audit_parser = commands.add_parser("audit", help="查看偏好操作日志")
    audit_parser.add_argument("--account", required=True)

    export_parser = commands.add_parser("export", help="导出内置演示")
    export_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.command != "export" and not args.account.strip():
        parser.error("账户不能为空。")

    if args.command in {"chat", "export"}:
        try:
            orders = json.loads(_ORDERS_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            parser.exit(1, "缺少 data/orders.json。请按 HANDOFF 的缺数据恢复步骤复制样例订单。\n")

    if args.command == "export":
        path = args.out.resolve()
        if path.is_dir():
            parser.error("导出目标必须是文件。")
        m2_path = path.with_name(f"{path.stem}.m2{path.suffix}")
        protected_paths = (_DATABASE_PATH, Path(ACTIVITY_FILENAME), _ORDERS_PATH)
        for destination in (path, m2_path):
            if any(_same_output_file(destination.resolve(), protected) for protected in protected_paths):
                parser.error("导出目标不能覆盖记忆数据库或样例订单文件。")
        if m2_path.is_dir() or _same_output_file(m2_path.resolve(), path):
            parser.error("M2 导出目标必须是另一个文件。")
        _export(orders, path, m2_path)
        print("已导出演示 JSON。")
        return

    try:
        validate_identifiers(args.account, getattr(args, "session", None))
        with closing(sqlite3.connect(_DATABASE_PATH)) as connection:
            initialize(connection)
            if args.command == "chat":
                print(chat(connection, orders, args.account, args.message, utc_now(), args.session))
            elif args.command == "forget":
                forget(connection, args.account, args.key)
                if args.key is None:
                    print(_FORGOT_ALL)
                else:
                    print(f"已清除该账户的 {args.key} 偏好。")
            elif args.command == "consent":
                enabled = args.state == "on"
                with transaction(connection):
                    set_consent(connection, args.account, enabled)
                print("已开启跨会话偏好保存。" if enabled else _CONSENT_OFF)
            elif args.command == "history":
                print(json.dumps(read_history(connection, args.account, args.session), ensure_ascii=False, indent=2))
            elif args.command == "audit":
                print(json.dumps(read_audit(connection, args.account), ensure_ascii=False, indent=2))
            else:
                preferences = read_preferences(connection, args.account)
                print(json.dumps(preferences, ensure_ascii=False, indent=2))
    except InvalidIdentifier as error:
        parser.error(str(error))
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            parser.exit(1, "记忆数据库被锁定。请关闭占用数据库的程序，再重试。\n")
        raise


if __name__ == "__main__":
    main()
