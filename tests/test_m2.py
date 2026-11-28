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
from unittest.mock import patch

from supportmemory import bot, store
from supportmemory import __main__ as cli

from .test_acceptance import A_DEFAULT, A_FILTERED, B_DEFAULT, PROJECT


class M2Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workdir = Path(temporary.name)
        self.database = self.workdir / "memory.sqlite3"
        self.activity = self.workdir / "memory-m2.sqlite3"
        shutil.copytree(PROJECT / "data", self.workdir / "data")

    def run_cli(self, *arguments, workdir=None):
        return subprocess.run(
            [sys.executable, "-m", "supportmemory", *arguments],
            cwd=workdir or self.workdir,
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

    def create_unversioned_m2_pair(self, message):
        # M2 最初的两份库没有配对表，也没有设置 user_version。
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("""
                CREATE TABLE preferences (
                    account TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    written_at TEXT NOT NULL, PRIMARY KEY (account, key)
                )
            """)
        with closing(sqlite3.connect(self.activity)) as connection, connection:
            connection.executescript("""
                CREATE TABLE consent (account TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
                CREATE TABLE session_preferences (
                    account TEXT NOT NULL, session TEXT NOT NULL, key TEXT NOT NULL,
                    value TEXT, PRIMARY KEY (account, session, key)
                );
                CREATE TABLE history (
                    account TEXT NOT NULL, session TEXT NOT NULL, window TEXT NOT NULL,
                    PRIMARY KEY (account, session)
                );
                CREATE TABLE audit (
                    id INTEGER PRIMARY KEY, account TEXT NOT NULL, session TEXT,
                    key TEXT NOT NULL, action TEXT NOT NULL, time TEXT NOT NULL
                );
                INSERT INTO consent VALUES ('A', 0);
            """)
            window = {"summary": [], "turns": [{"user": message, "reply": "好", "preferences": {}}]}
            connection.execute("INSERT INTO history VALUES ('A', 'a1', ?)", (json.dumps(window),))
    def test_unknown_order_status_is_reported_before_filtering(self):
        orders_path = self.workdir / "data/orders.json"
        orders = json.loads(orders_path.read_text(encoding="utf-8"))
        orders["A"][0]["status"] = "shiped"
        orders_path.write_text(json.dumps(orders, ensure_ascii=False), encoding="utf-8")

        result = self.run_cli("chat", "--account", "A", "--session", "a1", "只看已发货的")

        self.assertEqual(result.returncode, 1, result.stdout or result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "订单 A-101 的状态无法识别。\n")
        self.assertEqual(self.preference_rows(), [])
        self.assertEqual(self.history(), {"summary": [], "turns": []})
        self.assertEqual(self.audit(), [])
        self.assertEqual(self.chat("列出订单", account="B"), B_DEFAULT)

    def test_duplicate_order_account_is_reported_without_adding_history(self):
        self.chat("已有对话")
        before = self.history()
        orders_path = self.workdir / "data/orders.json"
        orders_path.write_text(
            '{"A": [{"id": "A-101", "status": "shipped"}], '
            '"A": [{"id": "A-102", "status": "unshipped"}]}',
            encoding="utf-8",
        )

        result = self.run_cli("chat", "--account", "A", "--session", "a1", "列出订单")

        self.assertEqual(result.returncode, 1, result.stdout or result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "订单文件中的账户 A 重复。\n")
        self.assertEqual(self.history(), before)

    def test_duplicate_sensitive_order_account_is_hidden_when_querying_another_account(self):
        phone = "13800000000"
        orders_path = self.workdir / "data/orders.json"
        orders_path.write_text(
            f'{{"A": [{{"id": "A-101", "status": "shipped"}}], "{phone}": [], "{phone}": []}}',
            encoding="utf-8",
        )

        result = self.run_cli("chat", "--account", "A", "--session", "a1", "列出订单")

        self.assertEqual(result.returncode, 1, result.stdout or result.stderr)
        self.assertNotIn(phone, result.stdout)
        self.assertNotIn(phone, result.stderr)
        self.assertEqual(result.stderr, "订单文件中有重复的账户标识。\n")

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

    def test_missing_activity_database_does_not_reenable_consent(self):
        self.chat("以后只看未发货的")
        self.cli("consent", "--account", "A", "off")
        saved_activity = self.activity.rename(self.workdir / "saved-m2.sqlite3")
        before = self.preference_rows()

        result = self.run_cli("chat", "--account", "A", "--session", "a1", "回答简短点")

        self.assertEqual(self.preference_rows(), before)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("恢复 memory-m2.sqlite3", result.stderr)
        self.assertFalse(self.activity.exists())
        saved_activity.rename(self.activity)
        self.chat("回答简短点")
        self.assertEqual(self.preference_rows(), before)

    def test_audit_rejects_a_paired_database_missing_the_audit_table(self):
        self.chat("叫我小柚")
        self.assertEqual([event["action"] for event in self.audit()], ["write", "read"])
        with closing(sqlite3.connect(self.activity)) as connection:
            connection.execute("DROP TABLE audit")
        before = (self.database.read_bytes(), self.activity.read_bytes())

        result = self.run_cli("audit", "--account", "A")

        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("缺少操作记录表", result.stderr)
        self.assertIn("恢复", result.stderr)
        with closing(sqlite3.connect(self.activity)) as connection:
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'audit'"
            ).fetchone())
        self.assertEqual((self.database.read_bytes(), self.activity.read_bytes()), before)

    def test_history_rejects_a_mismatched_restored_database_pair(self):
        self.chat("甲演示报修", session="s1")
        self.assertEqual(self.history(session="s1")["turns"][0]["user"], "甲演示报修")
        other = self.workdir / "other"
        shutil.copytree(PROJECT / "data", other / "data")
        result = self.run_cli("chat", "--account", "A", "--session", "s1", "乙演示报修", workdir=other)
        self.assertEqual(result.returncode, 0, result.stderr)
        original = self.activity.read_bytes()
        shutil.copyfile(other / self.activity.name, self.activity)
        before = (self.database.read_bytes(), self.activity.read_bytes())

        for options in ((), ("--confirm-legacy-pair",)):
            with self.subTest(options=options):
                result = self.run_cli(*options, "history", "--account", "A", "--session", "s1")
                self.assertEqual((result.returncode, result.stdout), (1, ""))
                self.assertIn("两份记忆数据库不配套", result.stderr)
                self.assertNotIn("乙演示报修", result.stderr)
        self.assertEqual((self.database.read_bytes(), self.activity.read_bytes()), before)

        self.activity.write_bytes(original)
        self.assertEqual(self.history(session="s1")["turns"][0]["user"], "甲演示报修")

    def test_legacy_database_pair_requires_confirmation_before_migration(self):
        self.chat("旧版演示报修")
        for path in (self.database, self.activity):
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("DROP TABLE IF EXISTS database_pair")
        before = (self.database.read_bytes(), self.activity.read_bytes())
        arguments = ("history", "--account", "A", "--session", "a1")

        result = self.run_cli(*arguments)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("--confirm-legacy-pair", result.stderr)
        self.assertEqual((self.database.read_bytes(), self.activity.read_bytes()), before)

        confirmed = json.loads(self.cli("--confirm-legacy-pair", *arguments))
        self.assertEqual(confirmed["turns"][0]["user"], "旧版演示报修")
        self.assertEqual(self.history(), confirmed)

    def test_unversioned_m2_pair_requires_confirmation_before_migration(self):
        self.create_unversioned_m2_pair("最初版演示报修")
        before = (self.database.read_bytes(), self.activity.read_bytes())
        arguments = ("history", "--account", "A", "--session", "a1")

        result = self.run_cli(*arguments)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("--confirm-legacy-pair", result.stderr)
        self.assertEqual((self.database.read_bytes(), self.activity.read_bytes()), before)

        confirmed = json.loads(self.cli("--confirm-legacy-pair", *arguments))
        self.assertEqual(confirmed["turns"][0]["user"], "最初版演示报修")
        self.assertEqual(self.history(), confirmed)
        self.chat("回答简短点")
        self.assertEqual(self.preference_rows(), [])

    def test_reversed_m2_database_files_are_rejected_without_writes(self):
        for numbered in (False, True):
            with self.subTest(numbered=numbered):
                if numbered:
                    self.chat("演示报修")
                    self.cli("consent", "--account", "A", "off")
                else:
                    self.create_unversioned_m2_pair("演示报修")

                preferences = self.database.read_bytes()
                activity = self.activity.read_bytes()
                self.database.write_bytes(activity)
                self.activity.write_bytes(preferences)
                try:
                    for arguments in (
                        ("history", "--account", "A", "--session", "a1"),
                        ("--confirm-legacy-pair", "chat", "--account", "A", "--session", "a1", "回答简短点"),
                    ):
                        result = self.run_cli(*arguments)
                        self.assertEqual((result.returncode, result.stdout), (1, ""))
                        self.assertIn("两份记忆数据库不配套", result.stderr)
                        self.assertEqual((self.database.read_bytes(), self.activity.read_bytes()),
                                         (activity, preferences))
                finally:
                    self.database.unlink()
                    self.activity.unlink()

    def test_reading_existing_m2_database_marks_it_paired_before_a_partial_restore(self):
        self.chat("以后只看未发货的")
        self.cli("consent", "--account", "A", "off")
        for command, arguments in (
            ("history", ("--account", "A", "--session", "a1")),
            ("audit", ("--account", "A")),
        ):
            with self.subTest(command=command):
                # 模拟已配对主库丢失版本号，查看时应补回。
                with closing(sqlite3.connect(self.database)) as connection:
                    connection.execute("PRAGMA user_version = 0")
                self.cli(command, *arguments)
                with closing(sqlite3.connect(self.database)) as connection:
                    version = connection.execute("PRAGMA user_version").fetchone()[0]

                before = self.preference_rows()
                saved_activity = self.activity.rename(self.workdir / "saved-m2.sqlite3")
                try:
                    result = self.run_cli("chat", "--account", "A", "--session", "a1", "回答简短点")
                    self.assertEqual(result.returncode, 1, f"{command} 后主库版本为 {version}")
                    self.assertGreater(version, 0)
                    self.assertIn("恢复 memory-m2.sqlite3", result.stderr)
                    self.assertEqual(self.preference_rows(), before)
                    self.assertFalse(self.activity.exists())
                finally:
                    saved_activity.replace(self.activity)

    def test_reported_repair_advice_does_not_change_salutation(self):
        self.assertEqual(self.chat("叫我小柚"), f"小柚，{A_DEFAULT}")
        reported_answer = self.chat("维修师傅叫我先拔掉电源。")
        next_answer = self.chat("列出订单", session="a2")
        self.assertEqual((reported_answer, next_answer), (f"小柚，{A_DEFAULT}", f"小柚，{A_DEFAULT}"))
        self.assertEqual(self.chat("以后叫我小青", session="a2"), f"小青，{A_DEFAULT}")
        self.assertEqual(self.chat("列出订单", session="a3"), f"小青，{A_DEFAULT}")
        self.assertEqual(self.chat("以后叫我小叶，客服叫我先重启。"), f"小叶，{A_DEFAULT}")

    def test_repair_advice_with_a_time_reference_does_not_change_salutation(self):
        self.chat("叫我小柚")
        self.chat("维修师傅刚才叫我先拔掉电源")
        self.assertEqual(self.chat("列出订单", session="a2"), f"小柚，{A_DEFAULT}")

    def test_polite_salutation_request_to_customer_service_changes_salutation(self):
        self.chat("叫我小柚")
        self.chat("以后请客服叫我小青")
        self.assertEqual(self.chat("列出订单", session="a2"), f"小青，{A_DEFAULT}")

    def test_quoted_examples_in_meaning_questions_do_not_set_preferences(self):
        answer = self.chat("帮助里写的‘只看未发货的’是什么意思？")
        shown = json.loads(self.cli("show", "--account", "A"))
        next_answer = self.chat("列出订单", session="a2")
        self.assertEqual((answer, shown, next_answer), (A_DEFAULT, {}, A_DEFAULT))

        brief_answer = self.chat('请回答简短点，帮助里 "详细一点" 是什么意思？')
        self.assertEqual(brief_answer, "按默认设置，列出全部订单，使用简短回答\nA-101、A-102")
        self.assertEqual(self.chat("列出订单", session="a2"), brief_answer)

    def test_meaning_question_skips_all_joined_quoted_examples(self):
        answer = self.chat("帮助里写的“只看未发货的”和“回答简短点”是什么意思？")
        shown = json.loads(self.cli("show", "--account", "A"))
        next_answer = self.chat("列出订单", session="a2")
        self.assertEqual((answer, shown, next_answer), (A_DEFAULT, {}, A_DEFAULT))

    def test_meaning_question_keeps_preference_between_ascii_quote_pairs(self):
        answer = self.chat('页面上有"帮助"，请回答简短点。"怎么理解退款进度"是什么意思？')
        shown = json.loads(self.cli("show", "--account", "A"))
        next_answer = self.chat("列出订单", session="a2")
        brief = "按默认设置，列出全部订单，使用简短回答\nA-101、A-102"
        self.assertEqual((answer, next_answer), (brief, brief))
        self.assertEqual(set(shown), {"brevity"})
        self.assertEqual(shown["brevity"]["value"], "short")

    def test_meaning_question_with_a_pause_keeps_the_existing_filter(self):
        for punctuation in ("，", ", ", "："):
            with self.subTest(punctuation=punctuation):
                self.chat("以后只看未发货的")
                before = self.preference_rows()
                question = f"帮助里写的‘看全部订单’{punctuation}是什么意思？"
                answer = self.chat(question)
                next_answer = self.chat("列出订单", session="a2")
                self.assertEqual((answer, next_answer, self.preference_rows()),
                                 (A_FILTERED, A_FILTERED, before))

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

    def test_account_preference_update_clears_only_the_same_key_from_old_sessions(self):
        self.cli("consent", "--account", "A", "off")
        self.chat("只看未发货的，叫我小青")
        self.cli("consent", "--account", "A", "on")

        shipped = "按你的设置，只列已发货订单\nA-101（已发货）"
        self.assertEqual(self.chat("以后只看已发货的", session="a2"), shipped)
        self.assertEqual(self.chat("列出订单"), f"小青，{shipped}")

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

    def test_forget_removes_legacy_sensitive_account_preferences(self):
        self.create_unversioned_m2_pair("旧版演示报修")
        account = "青柚花园3栋2单元602室"
        other_account = "青柚花园3栋2单元603室"
        mask = "[地址已遮盖]"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executemany(
                "INSERT INTO preferences VALUES (?, ?, ?, '2026-01-01T00:00:00Z')",
                [(account, "brevity", "short"), (account, "salutation", "小柚"),
                 (other_account, "brevity", "short"), ("B", "brevity", "short")],
            )
        with closing(sqlite3.connect(self.activity)) as connection, connection:
            connection.executemany(
                "INSERT INTO session_preferences VALUES (?, 'a1', 'brevity', 'short')",
                [(account,), ("B",)],
            )

        self.assertEqual(self.cli("--confirm-legacy-pair", "forget", "--account", account, "--key", "brevity"),
                         "已清除该账户的 brevity 偏好。")
        self.assertEqual([(row[0], row[1]) for row in self.preference_rows()],
                         [("B", "brevity"), (account, "salutation"), (other_account, "brevity")])
        self.chat("回答简短点", account=mask)
        self.assertEqual(self.cli("forget", "--account", account), "已清除该账户的全部偏好。")
        self.assertEqual(self.cli("forget", "--account", other_account), "已清除该账户的全部偏好。")
        self.assertEqual([(row[0], row[1]) for row in self.preference_rows()],
                         [("B", "brevity"), (mask, "brevity")])
        self.assertEqual([event["action"] for event in self.audit(account=mask)], ["write", "read"])
        with closing(sqlite3.connect(self.activity)) as connection:
            self.assertEqual(connection.execute("SELECT account FROM session_preferences").fetchall(), [("B",)])
            stored_accounts = connection.execute("SELECT DISTINCT account FROM audit").fetchall()
            self.assertEqual(len(stored_accounts), 3)
            self.assertNotIn((account,), stored_accounts)
            self.assertNotIn((other_account,), stored_accounts)
        with closing(sqlite3.connect(self.database)) as connection:
            store.initialize(connection, read_table="audit")
            self.assertEqual([tuple(event.values())[:4] for event in store.read_audit(connection, account)], [
                (mask, None, "brevity", "delete"),
                (mask, "a1", "brevity", "delete"),
                (mask, None, "salutation", "delete"),
            ])
            self.assertEqual([tuple(event.values())[:4] for event in store.read_audit(connection, other_account)],
                             [(mask, None, "brevity", "delete")])

    def test_forget_removes_a_legacy_account_with_only_a_session_preference(self):
        self.create_unversioned_m2_pair("旧版演示报修")
        account = "青柚花园3栋2单元602室"
        with closing(sqlite3.connect(self.activity)) as connection, connection:
            connection.execute("INSERT INTO consent VALUES (?, 0)", (account,))
            connection.execute("INSERT INTO session_preferences VALUES (?, 'a1', 'brevity', 'short')", (account,))
        self.assertEqual(self.preference_rows(), [])

        self.assertEqual(self.cli("--confirm-legacy-pair", "forget", "--account", account),
                         "已清除该账户的全部偏好。")
        with closing(sqlite3.connect(self.activity)) as connection:
            self.assertEqual(connection.execute("SELECT * FROM session_preferences").fetchall(), [])

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

    def test_building_address_is_redacted_in_history_and_summary(self):
        address = "3栋2单元602室"
        mask = "[地址已遮盖]"
        for index, (message, expected) in enumerate((
            (f"收货地址是青柚花园{address}", f"收货地址是{mask}"),
            (f"收货地址是青柚小区{address}", f"收货地址是{mask}"),
            (f"订单A-101 {address}", f"订单A-101 {mask}"),
            (f"请先修洗衣机\n{address}", f"请先修洗衣机\n{mask}"),
            (f"手机号138 1234 5678 {address}", f"手机号[手机号已遮盖] {mask}"),
        )):
            with self.subTest(message=message):
                session = f"address-{index}"
                self.chat(message, session=session)
                first_turn = self.history(session=session)["turns"][0]["user"]
                for _ in range(6):
                    self.chat("好", session=session)
                first_summary = self.history(session=session)["summary"][0]["user"]

                self.assertEqual((first_turn, first_summary), (expected, expected))

    def test_delivery_request_survives_address_redaction_in_history_and_summary(self):
        mask = "[地址已遮盖]"
        cases = (
            ("请把替换的电饭煲送到青柚路8号", f"请把替换的电饭煲送到{mask}"),
            ("请把A-101电饭煲送到青柚路8号", f"请把A-101电饭煲送到{mask}"),
            ("从青柚路2号送到青柚路8号", f"{mask}送到{mask}"),
            ("请把替换的电饭煲送到青柚花园3栋2单元602室", f"请把替换的电饭煲送到{mask}"),
            ("别送到门店请把电饭煲送到青柚路8号", f"别送到门店请把电饭煲送到{mask}"),
        )
        for index, (message, expected) in enumerate(cases):
            with self.subTest(message=message):
                session = f"delivery-{index}"
                self.chat(message, session=session)
                first_turn = self.history(session=session)["turns"][0]["user"]
                for _ in range(6):
                    self.chat("好", session=session)
                first_summary = self.history(session=session)["summary"][0]["user"]

                self.assertEqual((first_turn, first_summary), (expected, expected))

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

    def test_history_keeps_latest_complete_turn_when_old_summary_fills_budget(self):
        self.chat("叫我" + "甲" * 1000)
        message = "叫我小柚"
        reply = self.chat(message)
        window = self.history()
        self.assertEqual(window["turns"], [
            {"user": message, "reply": reply, "preferences": {"salutation": "小柚"}},
        ])
        self.assertLessEqual(len(json.dumps(window, ensure_ascii=False, separators=(",", ":"))), 800)

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

    def test_chat_timestamps_follow_write_order_when_messages_interleave(self):
        first_started = datetime(2026, 1, 1, tzinfo=timezone.utc)
        second_written = first_started + timedelta(seconds=1)
        first_written = first_started + timedelta(seconds=2)

        def finish_second_chat_before_first_write(connection):
            clock.now.return_value = second_written
            with (
                patch.object(bot, "transaction", store.transaction),
                patch.object(sys, "argv", ["supportmemory", "chat", "--account", "A", "--session", "a2", "叫我小乙"]),
            ):
                cli.main()
            clock.now.return_value = first_written
            return store.transaction(connection)

        with (
            patch.object(cli, "_ORDERS_PATH", self.workdir / "data/orders.json"),
            patch.object(cli, "_DATABASE_PATH", self.database),
            patch.object(sys, "argv", ["supportmemory", "chat", "--account", "A", "--session", "a1", "叫我小甲"]),
            patch.object(store, "datetime", wraps=datetime) as clock,
            patch.object(bot, "transaction", side_effect=finish_second_chat_before_first_write),
            patch("builtins.print"),
        ):
            clock.now.return_value = first_started
            cli.main()

        events = self.audit()
        writes = [event for event in events if event["action"] == "write"]
        self.assertEqual([event["session"] for event in writes], ["a2", "a1"])
        self.assertLess(writes[0]["time"], writes[1]["time"])
        self.assertEqual([event["time"] for event in events], [writes[0]["time"]] * 2 + [writes[1]["time"]] * 2)
        preferences = json.loads(self.cli("show", "--account", "A"))
        self.assertEqual(preferences["salutation"], {"value": "小甲", "written_at": writes[1]["time"]})

    def test_history_and_audit_can_read_while_another_connection_is_ready_to_write(self):
        message = "以后只看未发货的"
        self.chat(message)
        expected_history = self.history()
        expected_audit = self.audit()
        self.assertEqual([turn["user"] for turn in expected_history["turns"]], [message])
        self.assertEqual(
            [(event["key"], event["action"]) for event in expected_audit],
            [("order_filter", "write"), ("order_filter", "read")],
        )

        for legacy in (False, True):
            with closing(sqlite3.connect(self.database)) as writer:
                if legacy:
                    writer.execute("PRAGMA user_version = 0")
                writer.execute("ATTACH DATABASE ? AS activity", (str(self.activity),))
                writer.execute("BEGIN IMMEDIATE")
                for command, arguments, expected in (
                    ("history", ("--account", "A", "--session", "a1"), expected_history),
                    ("audit", ("--account", "A"), expected_audit),
                ):
                    with self.subTest(command=command, legacy=legacy):
                        result = self.run_cli(command, *arguments)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(result.stdout), expected)

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

    def test_export_restores_first_output_when_second_replace_fails(self):
        first = self.workdir / "demo.json"
        second = (self.workdir / "demo.m2.json").resolve()
        old_second = b'{"version": "old M2"}\n'
        original_replace = Path.replace

        def deny_second_replace(source, destination):
            if destination == second:
                raise PermissionError("M2 output denied")
            return original_replace(source, destination)

        for old_first in (b'{"version": "old M1"}\n', None):
            with self.subTest(first_existed=old_first is not None):
                if old_first is None:
                    first.unlink(missing_ok=True)
                else:
                    first.write_bytes(old_first)
                second.write_bytes(old_second)
                with (
                    patch.object(cli, "_ORDERS_PATH", self.workdir / "data" / "orders.json"),
                    patch.object(sys, "argv", ["supportmemory", "export", "--out", str(first)]),
                    patch.object(Path, "replace", autospec=True, side_effect=deny_second_replace),
                    self.assertRaisesRegex(PermissionError, "M2 output denied"),
                ):
                    cli.main()
                first_bytes = first.read_bytes() if first.exists() else None
                self.assertEqual((first_bytes, second.read_bytes()), (old_first, old_second))

    def test_export_keeps_the_original_file_when_rollback_fails(self):
        first = (self.workdir / "demo.json").resolve()
        second = first.with_name("demo.m2.json")
        old_first = b'{"version": "old M1"}\n'
        old_second = b'{"version": "old M2"}\n'
        first.write_bytes(old_first)
        second.write_bytes(old_second)
        original_inode = first.stat().st_ino
        original_replace = Path.replace
        second_failed = False

        def fail_second_replace_and_rollback(source, destination):
            nonlocal second_failed
            if destination == second:
                second_failed = True
                raise PermissionError("M2 output denied")
            if destination == first and second_failed:
                raise PermissionError("M1 rollback denied")
            return original_replace(source, destination)

        with (
            patch.object(cli, "_ORDERS_PATH", self.workdir / "data" / "orders.json"),
            patch.object(sys, "argv", ["supportmemory", "export", "--out", str(first)]),
            patch.object(Path, "replace", autospec=True, side_effect=fail_second_replace_and_rollback),
            self.assertRaises(OSError) as failure,
        ):
            cli.main()

        backups = [path for path in first.parent.iterdir() if path.is_file() and path not in (first, second)]
        self.assertEqual(len(backups), 1, "回滚失败后应保留第一份原件")
        backup = backups[0]
        self.assertEqual(backup.read_bytes(), old_first)
        self.assertEqual(backup.stat().st_ino, original_inode)
        self.assertEqual(second.read_bytes(), old_second)
        self.assertIn(str(backup), str(failure.exception))
        self.assertIn("M2 output denied", str(failure.exception))
        self.assertIn("M1 rollback denied", str(failure.exception))

    def test_export_can_replace_an_existing_output_it_cannot_read(self):
        first = (self.workdir / "demo.json").resolve()
        second = first.with_name("demo.m2.json")
        first.write_text("old M1", encoding="utf-8")
        second.write_text("old M2", encoding="utf-8")
        original_open = open

        def deny_reading_old_output(file, mode="r", *args, **kwargs):
            if file in (first, str(first)) and "r" in mode:
                raise PermissionError("old M1 cannot be read")
            return original_open(file, mode, *args, **kwargs)

        with (
            patch.object(cli, "_ORDERS_PATH", self.workdir / "data" / "orders.json"),
            patch.object(sys, "argv", ["supportmemory", "export", "--out", str(first)]),
            patch("builtins.open", side_effect=deny_reading_old_output),
            patch("io.open", side_effect=deny_reading_old_output),
        ):
            cli.main()

        self.assertIn("steps", json.loads(first.read_text(encoding="utf-8")))
        self.assertIn("consent", json.loads(second.read_text(encoding="utf-8")))
        self.assertEqual(set(first.parent.iterdir()), {first, second, first.parent / "data"})

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
