import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from supportmemory import store

from .test_acceptance import A_DEFAULT, A_FILTERED, B_DEFAULT, PROJECT


class M2Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workdir = Path(temporary.name)
        self.database = self.workdir / "memory.sqlite3"
        self.activity = self.workdir / "memory-m2.sqlite3"
        shutil.copytree(PROJECT / "data", self.workdir / "data")

    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "supportmemory", *arguments],
            cwd=self.workdir,
            env={**os.environ, "PYTHONPATH": str(PROJECT)},
            capture_output=True,
            encoding="utf-8",
            timeout=15,
        )

    def cli(self, *arguments):
        result = self.run_cli(*arguments)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.rstrip("\n")

    def chat(self, message, session="a1", account="A"):
        return self.cli("chat", "--account", account, "--session", session, message)

    def history(self, session="a1", account="A"):
        return json.loads(self.cli("history", "--account", account, "--session", session))

    def audit(self, account="A"):
        return json.loads(self.cli("audit", "--account", account))

    def preference_rows(self):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute("SELECT * FROM preferences ORDER BY account, key").fetchall()

    def test_consent_off_keeps_new_preferences_in_one_session(self):
        self.cli("consent", "--account", "A", "off")
        self.assertEqual(self.chat("以后只看未发货的"), A_FILTERED)
        self.assertEqual(self.preference_rows(), [])
        self.assertEqual(self.chat("列出订单"), A_FILTERED)
        self.assertEqual(self.chat("列出订单", session="a2"), A_DEFAULT)
        self.assertEqual(json.loads(self.cli("show", "--account", "A")), {})
        self.assertEqual(self.preference_rows(), [])
        self.assertEqual([(event["session"], event["action"]) for event in self.audit()], [
            ("a1", "write"), ("a1", "read"), ("a1", "read"),
        ])

        self.chat("回答简短点", account="B")
        self.assertEqual(self.chat("列出订单", account="B", session="a2"),
                         "按默认设置，列出全部订单，使用简短回答\nB-201、B-202")

    def test_consent_toggle_preserves_old_preferences_and_forget_clears_them(self):
        self.chat("以后只看未发货的")
        before = self.preference_rows()
        self.assertEqual(len(before), 1)
        self.assertIn("已有偏好仍然保留", self.cli("consent", "--account", "A", "off"))
        self.assertEqual(self.preference_rows(), before)
        self.assertEqual(self.chat("看全部订单"), A_DEFAULT)
        self.assertEqual(self.preference_rows(), before)
        self.assertEqual(self.chat("列出订单", session="a2"), A_FILTERED)
        self.assertEqual(self.chat("只看已发货的"), "按你的设置，只列已发货订单\nA-101（已发货）")
        self.cli("forget", "--account", "A", "--key", "order_filter")
        self.assertEqual(self.preference_rows(), [])
        self.assertEqual(self.chat("列出订单"), A_DEFAULT)
        self.assertEqual(self.chat("列出订单", session="a2"), A_DEFAULT)

        self.assertEqual(self.chat("以后只看未发货的"), A_FILTERED)
        self.cli("forget", "--account", "A")
        self.assertEqual(self.chat("列出订单"), A_DEFAULT)
        self.cli("consent", "--account", "A", "on")
        self.chat("以后只看未发货的")
        self.assertEqual(self.chat("列出订单", session="a2"), A_FILTERED)

    def test_sensitive_account_and_session_identifiers_are_rejected(self):
        paths = (self.database, self.activity)
        with self.subTest(command="首次 forget"):
            result = self.run_cli("forget", "--account", "13812345678", "--key", "brevity")
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(any(path.exists() for path in paths))
        self.cli("consent", "--account", "A", "off")
        before = [path.read_bytes() for path in paths]
        for sensitive in ("13812345678", "00000020000101001X", "杭州市文一西路 969 号"):
            cases = (
                ("会话标识", ("chat", "--account", "A", "--session", sensitive, f"回答简短点 {sensitive}")),
                ("账户标识", ("chat", "--account", sensitive, "--session", "a1", "列出订单")),
                ("账户标识", ("forget", "--account", sensitive, "--key", "brevity")),
                ("账户标识", ("forget", "--account", sensitive)),
                ("账户标识", ("consent", "--account", sensitive, "off")),
            )
            for label, arguments in cases:
                with self.subTest(label=label, command=arguments[0], sensitive=sensitive):
                    result = self.run_cli(*arguments)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn(label, result.stderr)
                    self.assertNotIn(sensitive, result.stderr + result.stdout)
        for path, original in zip(paths, before, strict=True):
            self.assertTrue(path.read_bytes() == original, f"{path.name} 被修改")

    def test_forget_does_not_copy_an_old_sensitive_session_identifier_into_audit(self):
        self.cli("consent", "--account", "A", "off")
        phone = "13812345678"
        with closing(sqlite3.connect(self.activity)) as connection, connection:
            connection.execute("INSERT INTO session_preferences VALUES ('A', ?, 'brevity', 'short')", (phone,))

        self.cli("forget", "--account", "A")
        with closing(sqlite3.connect(self.activity)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM session_preferences").fetchall(), [])
        self.assertEqual([(event["session"], event["key"], event["action"]) for event in self.audit()], [
            ("[手机号已遮盖]", "brevity", "delete"),
        ])

    def test_history_keeps_six_turns_and_preserves_prefix_and_preferences(self):
        first = "请帮我检查订单什么时候发货以及在哪里查看物流状态，以后只看未发货的"
        self.chat(first)
        self.chat("看全部订单")
        recent_messages = [f"第{index}轮" for index in range(3, 9)]
        for message in recent_messages:
            self.chat(message)

        window = self.history()
        self.assertEqual([turn["user"] for turn in window["turns"]], recent_messages)
        self.assertEqual(window["summary"], [
            {"user": first[:20], "preferences": {"order_filter": "unshipped"}},
            {"user": "看全部订单", "preferences": {"order_filter": None}},
        ])
        self.assertEqual(window, self.history())
        self.assertEqual(self.history(session="a2"), {"summary": [], "turns": []})

    def test_history_budget_holds_after_every_one_of_30_turns(self):
        for index in range(1, 31):
            message = f"第{index:02d}轮" + "核对订单状态" * 47
            self.chat(message)
            window = self.history()
            with self.subTest(turn=index):
                size = len(json.dumps(window, ensure_ascii=False, separators=(",", ":")))
                self.assertLessEqual(size / 2, 400)
                self.assertLessEqual(len(window["turns"]), 2)
                self.assertEqual(window["turns"][-1]["user"], message)
        self.assertTrue(window["summary"])
        self.assertIn("第28轮", window["summary"][-1]["user"])

    def test_oversized_turn_preserves_earlier_summaries_that_fit(self):
        self.chat("回答简短点")
        self.chat("普通问句")
        self.chat("甲" * 1000)
        self.assertEqual(self.history(), {"turns": [], "summary": [
            {"user": "回答简短点", "preferences": {"brevity": "short"}},
            {"user": "普通问句", "preferences": {}},
            {"user": "甲" * 20, "preferences": {}},
        ]})

    def test_oversized_preference_still_leaves_a_bounded_summary(self):
        message = "叫我" + "甲" * 1000
        self.chat(message)
        window = self.history()
        self.assertLessEqual(len(json.dumps(window, ensure_ascii=False, separators=(",", ":"))), 800)
        self.assertEqual(window["turns"], [])
        self.assertEqual(window["summary"][0]["user"], message[:20])
        salutation = window["summary"][0]["preferences"]["salutation"]
        self.assertTrue(salutation.startswith("甲") and salutation.endswith("…"))
        preferences = json.loads(self.cli("show", "--account", "A"))
        self.assertEqual(preferences["salutation"]["value"], "甲" * 1000)

    def test_first_turn_phone_is_absent_in_every_row_before_and_after_folding(self):
        phone = "13812345678"
        mask = "[手机号已遮盖]"
        first_message = f"叫我王先生 {phone}"
        self.chat(first_message)
        for phase in ("turns", "summary"):
            with self.subTest(phase=phase):
                window = self.history()
                self.assertEqual(window[phase][0]["user"], f"叫我王先生 {mask}")
                for path in (self.database, self.activity):
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertNotIn(phone, "\n".join(connection.iterdump()))
                    self.assertNotIn(phone.encode("ascii"), path.read_bytes())
            if phase == "turns":
                for _ in range(6):
                    self.chat("列出订单")

    def test_reply_and_summary_preview_are_redacted_before_storage(self):
        user_phone = "13812345678"
        reply_phone = "13912345678"
        salutation_phone = "13712345678"
        preview_phone = "13612345678"
        preview_message = f"请核对订单备注号 {preview_phone}9"
        with closing(sqlite3.connect(self.database)) as connection:
            store.initialize(connection)
            # 截断前的 12 位备注号合法，只拦截截出来的 11 位片段。
            connection.create_function("has_unredacted_phone", 1, lambda window: any(
                text in window for text in (user_phone, reply_phone, salutation_phone, f'{preview_phone}"')
            ))
            for action in ("INSERT", "UPDATE"):
                connection.execute(f"""
                    CREATE TEMP TRIGGER check_history_{action} BEFORE {action} ON activity.history
                    WHEN has_unredacted_phone(NEW.window)
                    BEGIN
                        SELECT RAISE(ABORT, '历史写入含未遮盖的号码');
                    END
                """)
            with store.transaction(connection):
                store.record_turn(connection, "A", "a1", f"电话 {user_phone}", f"号码是 {reply_phone}",
                                  {"salutation": salutation_phone})
            turn = store.read_history(connection, "A", "a1")["turns"][0]
            self.assertEqual(turn["user"], "电话 [手机号已遮盖]")
            self.assertEqual(turn["reply"], "号码是 [手机号已遮盖]")
            self.assertEqual(turn["preferences"], {"salutation": "[手机号已遮盖]"})
            with store.transaction(connection):
                store.record_turn(connection, "A", "a1", preview_message, "好", {})
                for _ in range(6):
                    store.record_turn(connection, "A", "a1", "继续", "好", {})
            self.assertEqual(store.read_history(connection, "A", "a1")["summary"], [
                {"user": "电话 [手机号已遮盖]", "preferences": {"salutation": "[手机号已遮盖]"}},
                {"user": "请核对订单备注号 [手机号已遮盖]", "preferences": {}},
            ])

    def test_audit_counts_only_deleted_rows_for_keyed_forget(self):
        self.cli("consent", "--account", "A", "off")
        self.assertEqual(self.chat("只看未发货的"), A_FILTERED)
        before = self.audit()
        self.cli("forget", "--account", "A", "--key", "order_filter")
        after = self.audit()
        self.assertEqual(after[:len(before)], before)
        self.assertEqual([(event["session"], event["key"], event["action"]) for event in after[len(before):]],
                         [("a1", "order_filter", "delete")])
        self.cli("forget", "--account", "A", "--key", "order_filter")
        self.assertEqual(self.audit(), after)

    def test_audit_distinguishes_clearing_a_missing_preference_from_writing_a_session_override(self):
        self.assertEqual(self.chat("看全部订单"), A_DEFAULT)
        self.assertEqual(self.audit(), [])
        self.chat("以后只看未发货的")
        self.cli("consent", "--account", "A", "off")
        before = len(self.audit())
        self.assertEqual(self.chat("看全部订单"), A_DEFAULT)
        self.assertEqual([(event["session"], event["key"], event["action"]) for event in self.audit()[before:]], [
            ("a1", "order_filter", "write"), ("a1", "order_filter", "read"), ("a1", "order_filter", "read"),
        ])

    def test_audit_records_reads_writes_and_deletes_without_values(self):
        value = "测试专用称呼"
        command_times = []
        for arguments, count in (
            (("chat", "--account", "A", "--session", "a1", f"叫我{value}"), 2),
            (("chat", "--account", "A", "--session", "a2", "列出订单"), 1),
            (("show", "--account", "A"), 1),
            (("forget", "--account", "A", "--key", "salutation"), 1),
            (("chat", "--account", "A", "--session", "a1", "以后只看未发货的，回答简短点"), 4),
            (("forget", "--account", "A"), 2),
        ):
            started = datetime.now(timezone.utc)
            self.cli(*arguments)
            finished = datetime.now(timezone.utc)
            command_times.extend([(started, finished)] * count)
        with closing(sqlite3.connect(self.activity)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(audit)")}
            dump = "\n".join(connection.iterdump())
        self.assertEqual(columns, {"id", "account", "session", "key", "action", "time"})
        audit_rows = [line for line in dump.splitlines() if line.startswith('INSERT INTO "audit"')]
        self.assertNotIn(value, "\n".join(audit_rows))

        events = self.audit()
        self.assertEqual([(event["session"], event["key"], event["action"]) for event in events], [
            ("a1", "salutation", "write"), ("a1", "salutation", "read"),
            ("a2", "salutation", "read"), (None, "salutation", "read"),
            (None, "salutation", "delete"), ("a1", "order_filter", "write"),
            ("a1", "brevity", "write"), ("a1", "brevity", "read"),
            ("a1", "order_filter", "read"), (None, "brevity", "delete"),
            (None, "order_filter", "delete"),
        ])
        for event, (started, finished) in zip(events, command_times, strict=True):
            self.assertEqual(set(event), {"account", "session", "key", "action", "time"})
            self.assertEqual(event["account"], "A")
            recorded = datetime.fromisoformat(event["time"])
            self.assertEqual(recorded.utcoffset(), timedelta(0))
            self.assertLessEqual(started, recorded)
            self.assertLessEqual(recorded, finished)
        self.assertNotIn(value, json.dumps(events, ensure_ascii=False))

    def test_history_and_audit_are_isolated_by_account(self):
        self.chat("以后只看未发货的")
        self.assertEqual(self.chat("列出订单", account="B"), B_DEFAULT)
        self.assertEqual(self.history(account="A")["turns"][0]["user"], "以后只看未发货的")
        self.assertEqual(self.history(account="B")["turns"][0]["user"], "列出订单")
        self.assertEqual(self.history(account="C"), {"summary": [], "turns": []})
        self.assertEqual(self.audit("B"), [])
        self.chat("回答简短点", account="B")
        self.assertEqual({event["account"] for event in self.audit("A")}, {"A"})
        self.assertEqual({event["account"] for event in self.audit("B")}, {"B"})

    def test_export_m2_is_repeatable_and_uses_separate_demo_data(self):
        self.chat("回答简短点")
        before = self.activity.read_bytes()
        self.cli("export", "--out", "demo.json")
        self.cli("export", "--out", "repeat.json")
        content = (self.workdir / "demo.m2.json").read_bytes()
        self.assertEqual(content, (self.workdir / "repeat.m2.json").read_bytes())
        self.assertEqual(before, self.activity.read_bytes())
        self.assertNotIn(b"13812345678", content)
        demo = json.loads(content)
        self.assertIs(demo["consent"]["enabled"], False)
        self.assertEqual(demo["preferences"], {})
        self.assertEqual(demo["same_session_answer"], A_FILTERED)
        self.assertEqual(demo["new_session_answer"], A_DEFAULT)
        self.assertEqual(len(demo["history"]["turns"]), 6)
        self.assertEqual(demo["history"]["summary"][0]["preferences"], {"order_filter": "unshipped"})
        self.assertEqual({event["action"] for event in demo["audit"]}, {"read", "write", "delete"})

        (self.workdir / "protected.m2.json").symlink_to(self.activity)
        for path in (self.activity.name, "protected.json"):
            self.assertNotEqual(self.run_cli("export", "--out", path).returncode, 0)
        self.assertFalse((self.workdir / "protected.json").exists())
        self.assertEqual(before, self.activity.read_bytes())

    def test_existing_m1_database_opens_with_preferences_and_default_consent(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("""
                CREATE TABLE preferences (
                    account TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    written_at TEXT NOT NULL, PRIMARY KEY (account, key)
                )
            """)
            connection.execute("INSERT INTO preferences VALUES ('A', 'order_filter', 'unshipped', 'old')")
        self.assertFalse(self.activity.exists())
        self.assertEqual(self.chat("列出订单"), A_FILTERED)
        self.assertEqual(self.preference_rows(), [("A", "order_filter", "unshipped", "old")])
        self.chat("回答简短点")
        self.assertEqual({row[1] for row in self.preference_rows()}, {"order_filter", "brevity"})
