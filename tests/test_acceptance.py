import getpass
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from supportmemory import bot, store


PROJECT = Path(__file__).resolve().parents[1]
A_FILTERED = "按你的设置，只列未发货订单\nA-102（未发货）"
A_DEFAULT = "按默认设置，列出全部订单\nA-101（已发货）\nA-102（未发货）"
B_DEFAULT = "按默认设置，列出全部订单\nB-201（已发货）\nB-202（未发货）"


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workdir = Path(temporary.name)
        self.database = self.workdir / "memory.sqlite3"
        shutil.copytree(PROJECT / "supportmemory", self.workdir / "supportmemory")
        shutil.copytree(PROJECT / "data", self.workdir / "data")

    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "supportmemory", *arguments],
            cwd=self.workdir,
            capture_output=True,
            encoding="utf-8",
            timeout=15,
        )

    def cli(self, *arguments):
        result = self.run_cli(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.rstrip("\n")

    def chat(self, account, session, message):
        return self.cli("chat", "--account", account, "--session", session, message)

    def show(self, account):
        return json.loads(self.cli("show", "--account", account))

    def test_01_same_account_keeps_filter_across_sessions_and_processes(self):
        first_start = datetime.now(timezone.utc)
        self.chat("A", "a1", "以后只看未发货的")
        first_end = datetime.now(timezone.utc)
        self.assertEqual(self.chat("A", "a2", "列出订单"), A_FILTERED)
        preferences = self.show("A")
        self.assertEqual(set(preferences), {"order_filter"})
        self.assertEqual(set(preferences["order_filter"]), {"value", "written_at"})
        self.assertEqual(preferences["order_filter"]["value"], "unshipped")
        written_at = datetime.fromisoformat(preferences["order_filter"]["written_at"])
        self.assertEqual(written_at.utcoffset(), timedelta(0))
        self.assertLessEqual(first_start, written_at)
        self.assertLessEqual(written_at, first_end)

        second_start = datetime.now(timezone.utc)
        self.chat("A", "a2", "只看已发货的")
        second_end = datetime.now(timezone.utc)
        updated = self.show("A")["order_filter"]
        self.assertEqual(updated["value"], "shipped")
        updated_at = datetime.fromisoformat(updated["written_at"])
        self.assertEqual(updated_at.utcoffset(), timedelta(0))
        self.assertLess(written_at, updated_at)
        self.assertLessEqual(second_start, updated_at)
        self.assertLessEqual(updated_at, second_end)

    def test_02_other_account_has_its_own_orders_and_no_borrowed_preferences(self):
        self.chat("A", "a1", "以后只看未发货的")
        self.assertEqual(self.chat("B", "b1", "列出订单"), B_DEFAULT)
        self.assertEqual(self.show("B"), {})

    def test_03_forget_clears_one_or_all_preferences_for_the_account(self):
        answer = self.chat("A", "a1", "叫我王先生，回答简短点，以后只看未发货的")
        self.assertEqual(answer, "王先生，按你的设置，只列未发货订单，使用简短回答\nA-102")
        self.chat("B", "b1", "只看已发货的")
        other_preferences = self.show("B")
        self.assertEqual(other_preferences["order_filter"]["value"], "shipped")

        self.cli("forget", "--account", "A", "--key", "order_filter")
        self.assertEqual(set(self.show("A")), {"brevity", "salutation"})
        self.assertEqual(self.show("B"), other_preferences)
        self.assertEqual(
            self.chat("A", "a2", "列出订单"),
            "王先生，按默认设置，列出全部订单，使用简短回答\nA-101、A-102",
        )

        self.assertEqual(
            self.chat("A", "a2", "只看已发货的，详细一点"),
            "王先生，按你的设置，只列已发货订单，使用详细回答\nA-101 洗衣机（已发货）",
        )
        self.chat("A", "a2", "看全部订单")
        self.assertEqual(set(self.show("A")), {"brevity", "salutation"})

        self.chat("A", "a2", "以后只看未发货的")
        self.cli("forget", "--account", "A")
        self.assertEqual(self.show("A"), {})
        self.assertEqual(self.chat("A", "a3", "列出订单"), A_DEFAULT)
        self.assertEqual(self.show("B"), other_preferences)

    def test_04_sensitive_values_are_redacted_before_storage_and_reply(self):
        examples = (
            ("电话", "13812345678", "[手机号已遮盖]"),
            ("全角电话", "１３８１２３４５６７８", "[手机号已遮盖]"),
            ("全角空格分组的电话", "１３８　１２３４　５６７８", "[手机号已遮盖]"),
            ("身份证", "00000020000101001X", "[身份证号已遮盖]"),
            ("地址", "青柚市云杉区示例大街27号3号楼2单元602室", "[地址已遮盖]"),
            ("带空格的地址", "青柚路 27 号", "[地址已遮盖]"),
        )
        orders = json.loads((self.workdir / "data/orders.json").read_text(encoding="utf-8"))
        writes = []

        def record_write(connection, account, updates, written_at):
            writes.append((account, updates.copy()))
            store.write_preferences(connection, account, updates, written_at)

        for label, sensitive, mask in examples:
            with self.subTest(kind=label):
                writes.clear()
                expected = f"王先生 {mask}"
                with closing(sqlite3.connect(self.database)) as connection:
                    store.initialize(connection)
                    with patch.object(bot, "write_preferences", side_effect=record_write):
                        answer = bot.chat(
                            connection, orders, "A", f"叫我王先生 {sensitive}", "2026-01-01T00:00:00Z"
                        )
                    dump = "\n".join(connection.iterdump())
                self.assertEqual(writes, [("A", {"salutation": expected})])
                self.assertNotIn(sensitive, dump, f"SQLite 转储含有未遮盖的{label}")
                self.assertNotIn(sensitive.encode("utf-8"), self.database.read_bytes())
                self.assertEqual(answer, f"{expected}，{A_DEFAULT}")
                self.assertEqual(self.show("A")["salutation"]["value"], expected)

        for clause in ("我的洗衣机漏水了", "电话 13812345678"):
            with self.subTest(clause=clause):
                self.assertEqual(self.chat("A", "a1", f"叫我王先生，{clause}"), f"王先生，{A_DEFAULT}")
                self.assertEqual(self.show("A")["salutation"]["value"], "王先生")
                with closing(sqlite3.connect(self.database)) as connection:
                    dump = "\n".join(connection.iterdump())
                self.assertNotIn(clause, dump)
                self.assertNotIn("13812345678", dump)

        before = self.database.read_bytes()
        for account in ("13812345678", "１３８１２３４５６７８", "00000020000101001X"):
            with self.subTest(account=account):
                result = self.run_cli(
                    "chat", "--account", account, "--session", "a1", "回答简短点"
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("账户标识", result.stderr)
                self.assertNotIn(account, result.stderr)
                self.assertEqual(self.database.read_bytes(), before)

    def test_05_unrecognized_message_leaves_database_unchanged(self):
        self.chat("A", "a1", "以后只看未发货的")
        preferences = self.show("A")
        with closing(sqlite3.connect(self.database)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM preferences").fetchone()[0]
        before = self.database.read_bytes()

        for message in (
            "我想了解一下", "不要只看未发货的", "不要只看已发货的", "不用看全部订单",
            "别回答简短点", "不要详细一点", "别叫我李先生",
        ):
            with self.subTest(message=message):
                self.assertEqual(self.chat("A", "a2", message), A_FILTERED)
                self.assertEqual(self.show("A"), preferences)

        with closing(sqlite3.connect(self.database)) as connection:
            after = connection.execute("SELECT COUNT(*) FROM preferences").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(after, count)
        self.assertEqual(self.database.read_bytes(), before)

    def test_not_wanting_a_filter_preserves_existing_preference(self):
        self.chat("A", "a1", "只看已发货的")
        preferences = self.show("A")

        self.assertEqual(
            self.chat("A", "a2", "我不想只看未发货的"),
            "按你的设置，只列已发货订单\nA-101（已发货）",
        )
        self.assertEqual(self.show("A"), preferences)

    def test_failed_reply_does_not_save_preferences(self):
        self.assertEqual(self.show("A"), {})
        orders_path = self.workdir / "data/orders.json"
        orders = json.loads(orders_path.read_text(encoding="utf-8"))
        del orders["A"][0]["product"]
        orders_path.write_text(json.dumps(orders, ensure_ascii=False), encoding="utf-8")

        result = self.run_cli("chat", "--account", "A", "--session", "a1", "详细一点")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("product", result.stderr)
        self.assertEqual(self.show("A"), {})

    def test_blank_accounts_are_rejected_by_all_account_commands(self):
        self.assertEqual(self.show("A"), {})
        before = self.database.read_bytes()
        for account in ("", " \u3000\t"):
            for command, options in (
                ("chat", ("--session", "a1", "叫我小柚")),
                ("show", ()),
                ("forget", ()),
            ):
                with self.subTest(account=account, command=command):
                    result = self.run_cli(command, "--account", account, *options)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("账户不能为空", result.stderr)
                    self.assertEqual(self.database.read_bytes(), before)

    def test_period_with_space_ends_salutation(self):
        self.chat("A", "a1", "叫我小柚. 我的洗衣机漏水了。")

        self.assertEqual(self.show("A")["salutation"]["value"], "小柚")
        self.assertEqual(self.chat("A", "a2", "列出订单"), f"小柚，{A_DEFAULT}")

    def test_06_export_is_repeatable_and_has_no_machine_identifiers(self):
        self.chat("A", "a1", "只看已发货的")
        before = self.database.read_bytes()
        orders = self.workdir / "data/orders.json"
        original_orders = orders.read_bytes()

        for target in (self.database, orders):
            link = self.workdir / f"{target.name}.link"
            link.symlink_to(target)
            hard_link = self.workdir / f"{target.name}.hardlink"
            hard_link.hardlink_to(target)
            for path in (target.relative_to(self.workdir), target, link, hard_link):
                with self.subTest(protected_output=path):
                    result = self.run_cli("export", "--out", str(path))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("覆盖", result.stderr)
                    self.assertEqual(self.database.read_bytes(), before)
                    self.assertEqual(orders.read_bytes(), original_orders)

        self.cli("export", "--out", str(self.workdir / "first.json"))
        self.cli("export", "--out", str(self.workdir / "second.json"))

        first = (self.workdir / "first.json").read_bytes()
        self.assertEqual(first, (self.workdir / "second.json").read_bytes())
        self.assertEqual(before, self.database.read_bytes())
        content = first.decode("utf-8")
        self.assertNotRegex(content, r'(?<!\w)(?:/|[A-Za-z]:[\\/]|\\\\)[^\s"]+')
        self.assertNotIn(socket.gethostname(), content)
        self.assertNotIn(getpass.getuser(), content)

        def chat_input(account, session, message):
            return {"command": "chat", "account": account, "session": session, "message": message}

        filtered = {"order_filter": {"value": "unshipped", "written_at": "2026-01-01T00:00:00Z"}}
        salutation = "王先生[手机号已遮盖]"
        redacted = {"salutation": {"value": salutation, "written_at": "2026-01-01T00:00:05Z"}}
        expected = (
            (chat_input("A", "a1", "以后只看未发货的"), A_FILTERED, filtered),
            (chat_input("A", "a2", "列出订单"), A_FILTERED, filtered),
            (chat_input("B", "b1", "列出订单"), B_DEFAULT, {}),
            ({"command": "forget", "account": "A"}, "已清除该账户的全部偏好。", {}),
            (chat_input("A", "a3", "列出订单"), A_DEFAULT, {}),
            (chat_input("A", "a3", "叫我王先生13812345678"), f"{salutation}，{A_DEFAULT}", redacted),
        )
        self.assertEqual(json.loads(content), {
            "steps": [
                {"input": step_input, "answer": answer, "preferences": preferences}
                for step_input, answer, preferences in expected
            ],
        })

    def test_07_smoke_recovers_missing_orders_from_an_empty_directory(self):
        with tempfile.TemporaryDirectory() as empty:
            result = subprocess.run(
                ["bash", str(PROJECT / "scripts" / "smoke.sh")],
                cwd=empty,
                capture_output=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(list(Path(empty).iterdir()), [])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("缺少 data/orders.json", result.stdout)
        self.assertIn(A_FILTERED + "\n" + A_FILTERED, result.stdout)
        self.assertIn(B_DEFAULT, result.stdout)
        self.assertIn(A_DEFAULT, result.stdout)
        self.assertIn("冒烟验证通过。", result.stdout)


if __name__ == "__main__":
    unittest.main()
