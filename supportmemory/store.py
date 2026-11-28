"""按账户存取偏好、会话和审计记录。"""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .history import append_turn, encode_window
from .redact import contains_sensitive_data, redact


ACTIVITY_FILENAME = "memory-m2.sqlite3"
_PAIRED_DATABASE_VERSION = 2


class InvalidIdentifier(ValueError):
    """账户或会话标识含有隐私信息。"""


class InvalidAccount(InvalidIdentifier):
    """账户标识含有隐私信息。"""


class MissingActivityDatabase(RuntimeError):
    """已配对的记忆库缺少授权数据。"""


class InvalidConsentValue(RuntimeError):
    """记忆库中的授权值无效。"""


class MissingAuditTable(RuntimeError):
    """已配对的记忆库缺少操作记录表。"""


class MismatchedDatabasePair(RuntimeError):
    """两份记忆库的配对编号无法匹配。"""


class UnconfirmedLegacyPair(RuntimeError):
    """旧版 M2 库没有配对编号，需要确认来源。"""


def _check_database_pair(connection, confirm_legacy_pair):
    schemas = ("main", "activity")
    main_tables, activity_tables = (
        {name for (name,) in connection.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table'")}
        for schema in schemas
    )
    if {"consent", "session_preferences", "history", "audit"} & main_tables or "preferences" in activity_tables:
        raise MismatchedDatabasePair

    if "consent" not in activity_tables:
        version = connection.execute("PRAGMA main.user_version").fetchone()[0]
        if version >= _PAIRED_DATABASE_VERSION:
            raise MissingActivityDatabase

    if "database_pair" in main_tables or "database_pair" in activity_tables:
        if "database_pair" not in main_tables or "database_pair" not in activity_tables:
            raise MismatchedDatabasePair
        main_id, activity_id = (
            connection.execute(f"SELECT id FROM {schema}.database_pair").fetchall()
            for schema in schemas
        )
        if len(main_id) != 1 or not main_id[0][0] or main_id != activity_id:
            raise MismatchedDatabasePair
        if "audit" not in activity_tables:
            raise MissingAuditTable
        return True, activity_tables

    if "consent" in activity_tables:
        if "preferences" not in main_tables:
            raise MismatchedDatabasePair
        if not confirm_legacy_pair:
            raise UnconfirmedLegacyPair
    return False, activity_tables


def initialize(connection, *, read_table=None, confirm_legacy_pair=False):
    paired = connection.execute("PRAGMA main.user_version").fetchone()[0] >= _PAIRED_DATABASE_VERSION
    databases = {name: path for _, name, path in connection.execute("PRAGMA database_list")}
    if "activity" not in databases:
        main_path = databases["main"]
        activity_path = Path(main_path).with_name(ACTIVITY_FILENAME) if main_path else None
        if paired and activity_path is not None and not activity_path.exists():
            raise MissingActivityDatabase
        connection.execute(
            "ATTACH DATABASE ? AS activity", (str(activity_path) if activity_path else ":memory:",)
        )
    if read_table is not None:
        with transaction(connection, immediate=False):
            has_pair_id, tables = _check_database_pair(connection, confirm_legacy_pair)
        if has_pair_id and read_table in tables:
            if not paired:
                _mark_pair_on_read(connection)
            return

    with transaction(connection):
        has_pair_id, _ = _check_database_pair(connection, confirm_legacy_pair)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS preferences (
                account TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                written_at TEXT NOT NULL,
                PRIMARY KEY (account, key)
            )
            """
        )

        for definition in (
            "consent (account TEXT PRIMARY KEY, enabled INTEGER NOT NULL)",
            """session_preferences (
                account TEXT NOT NULL,
                session TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                PRIMARY KEY (account, session, key)
            )""",
            """history (
                account TEXT NOT NULL,
                session TEXT NOT NULL,
                window TEXT NOT NULL,
                PRIMARY KEY (account, session)
            )""",
            """audit (
                id INTEGER PRIMARY KEY,
                account TEXT NOT NULL,
                session TEXT,
                key TEXT NOT NULL,
                action TEXT NOT NULL,
                time TEXT NOT NULL
            )""",
        ):
            connection.execute(f"CREATE TABLE IF NOT EXISTS activity.{definition}")
        if not has_pair_id:
            pair_id = uuid4().hex
            for schema in ("main", "activity"):
                connection.execute(f"CREATE TABLE {schema}.database_pair (id TEXT NOT NULL PRIMARY KEY)")
                connection.execute(f"INSERT INTO {schema}.database_pair (id) VALUES (?)", (pair_id,))
        if not paired:
            connection.execute(f"PRAGMA main.user_version = {_PAIRED_DATABASE_VERSION}")


def _mark_pair_on_read(connection):
    # 查看命令遇到写锁时应立即继续读取。
    timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    connection.execute("PRAGMA busy_timeout = 0")
    try:
        connection.execute(f"PRAGMA main.user_version = {_PAIRED_DATABASE_VERSION}")
    except sqlite3.OperationalError as error:
        message = str(error).lower()
        if "locked" not in message and "readonly" not in message:
            raise
    finally:
        connection.execute(f"PRAGMA busy_timeout = {timeout}")


@contextmanager
def transaction(connection, *, immediate=True):
    if connection.in_transaction:
        yield
        return
    with connection:
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def validate_account(account):
    if contains_sensitive_data(account):
        raise InvalidAccount("账户标识不能包含手机号、身份证号或详细地址。")


def validate_identifiers(account, session=None):
    validate_account(account)
    if session is not None and contains_sensitive_data(session):
        raise InvalidIdentifier("会话标识不能包含手机号、身份证号或详细地址。")


def has_legacy_preferences(database_path, account):
    # 校验未通过时先只读查找旧偏好，避免为陌生的敏感标识创建数据库。
    database_path = database_path.resolve()
    if not database_path.is_file():
        return False
    for path, table in (
        (database_path, "preferences"),
        (database_path.with_name(ACTIVITY_FILENAME), "session_preferences"),
    ):
        if not path.is_file():
            continue
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            if table_exists and connection.execute(
                f"SELECT 1 FROM {table} WHERE account = ? LIMIT 1", (account,)
            ).fetchone():
                return True
    return False


def consent_enabled(connection, account):
    row = connection.execute(
        "SELECT enabled FROM activity.consent WHERE account = ?", (account,)
    ).fetchone()
    if row is None:
        return True
    enabled = row[0]
    if type(enabled) is not int or enabled not in (0, 1):
        raise InvalidConsentValue
    return enabled == 1


def set_consent(connection, account, enabled):
    validate_account(account)
    connection.execute(
        """
        INSERT INTO activity.consent (account, enabled) VALUES (?, ?)
        ON CONFLICT (account) DO UPDATE SET enabled = excluded.enabled
        """,
        (account, enabled),
    )


def _audit_account_key(account):
    if contains_sensitive_data(account):
        # SQLite 将摘要存为 BLOB，与普通账户使用的 TEXT 分开。
        return sha256(account.encode("utf-8")).digest()
    return account


def _record_audit(connection, account, session, keys, action, time):
    validate_identifiers(redact(account) if action == "delete" else account, session)
    account_key = _audit_account_key(account)
    connection.executemany(
        """
        INSERT INTO activity.audit (account, session, key, action, time) VALUES (?, ?, ?, ?, ?)
        """,
        ((account_key, session, key, action, time) for key in keys),
    )


def read_audit(connection, account):
    rows = connection.execute(
        "SELECT session, key, action, time FROM activity.audit WHERE account = ? ORDER BY id",
        (_audit_account_key(account),),
    )
    display_account = redact(account)
    fields = ("account", "session", "key", "action", "time")
    return [dict(zip(fields, (display_account, *row))) for row in rows]


def read_preferences(connection, account, session=None, time=None):
    with transaction(connection):
        rows = connection.execute(
            "SELECT key, value, written_at FROM preferences WHERE account = ? ORDER BY key",
            (account,),
        )
        preferences = {
            key: {"value": redact(value) if key == "salutation" else value, "written_at": written_at}
            for key, value, written_at in rows
        }
        _record_audit(connection, account, session, preferences, "read", time or utc_now())
        return preferences


def _delete_preference(connection, account, key, time, session=None):
    deleted = connection.execute(
        "DELETE FROM preferences WHERE account = ? AND key = ?", (account, key)
    ).rowcount
    if deleted:
        _record_audit(connection, account, session, (key,), "delete", time or utc_now())


def write_preferences(connection, account, updates, written_at, session=None):
    validate_identifiers(account, session)

    with transaction(connection):
        for key, value in updates.items():
            if value is None:
                _delete_preference(connection, account, key, written_at, session)
            else:
                connection.execute(
                    """
                    INSERT INTO preferences (account, key, value, written_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (account, key) DO UPDATE SET
                        value = excluded.value,
                        written_at = excluded.written_at
                    """,
                    (account, key, value, written_at),
                )
                _record_audit(connection, account, session, (key,), "write", written_at or utc_now())


def read_session_preferences(connection, account, session, time):
    rows = connection.execute(
        """
        SELECT key, value FROM activity.session_preferences
        WHERE account = ? AND session = ? ORDER BY key
        """,
        (account, session),
    )
    preferences = {
        key: {"value": redact(value) if key == "salutation" and value is not None else value}
        for key, value in rows
    }
    _record_audit(connection, account, session, preferences, "read", time)
    return preferences


def write_session_preferences(connection, account, session, updates, time):
    validate_identifiers(account, session)
    connection.executemany(
        """
        INSERT INTO activity.session_preferences (account, session, key, value) VALUES (?, ?, ?, ?)
        ON CONFLICT (account, session, key) DO UPDATE SET value = excluded.value
        """,
        ((account, session, key, value) for key, value in updates.items()),
    )
    _record_audit(connection, account, session, updates, "write", time)


def delete_session_preferences(connection, account, time, session=None, key=None):
    validate_identifiers(account, session)
    _delete_session_preferences(connection, account, time, session, key)


def _delete_session_preferences(connection, account, time, session=None, key=None):
    rows = connection.execute(
        """
        SELECT session, key FROM activity.session_preferences
        WHERE account = ? AND (? IS NULL OR session = ?) AND (? IS NULL OR key = ?)
        ORDER BY session, key
        """,
        (account, session, session, key, key),
    ).fetchall()
    for row_session, row_key in rows:
        deleted = connection.execute(
            "DELETE FROM activity.session_preferences WHERE account = ? AND session = ? AND key = ?",
            (account, row_session, row_key),
        ).rowcount
        if deleted:
            _record_audit(connection, account, redact(row_session), (row_key,), "delete", time)


def forget(connection, account, key, time=None):
    time = time or utc_now()
    with transaction(connection):
        if key is None:
            rows = connection.execute(
                "SELECT key FROM preferences WHERE account = ? ORDER BY key", (account,)
            )
            keys = [row[0] for row in rows]
        else:
            keys = [key]
        for preference_key in keys:
            _delete_preference(connection, account, preference_key, time)
        _delete_session_preferences(connection, account, time, key=key)


def read_history(connection, account, session):
    row = connection.execute(
        "SELECT window FROM activity.history WHERE account = ? AND session = ?", (account, session)
    ).fetchone()
    return json.loads(row[0]) if row else {"summary": [], "turns": []}


def record_turn(connection, account, session, message, answer, updates):
    validate_identifiers(account, session)
    window = read_history(connection, account, session)
    append_turn(window, message, answer, updates)
    connection.execute(
        """
        INSERT INTO activity.history (account, session, window) VALUES (?, ?, ?)
        ON CONFLICT (account, session) DO UPDATE SET window = excluded.window
        """,
        (account, session, encode_window(window)),
    )
