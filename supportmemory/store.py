"""按账户存取偏好、会话和审计记录。"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path

from .history import append_turn, encode_window
from .redact import contains_sensitive_data, redact


ACTIVITY_FILENAME = "memory-m2.sqlite3"


class InvalidIdentifier(ValueError):
    """账户或会话标识含有隐私信息。"""


class InvalidAccount(InvalidIdentifier):
    """账户标识含有隐私信息。"""


def initialize(connection):
    databases = {name: path for _, name, path in connection.execute("PRAGMA database_list")}
    if "activity" not in databases:
        main_path = databases["main"]
        activity_path = str(Path(main_path).with_name(ACTIVITY_FILENAME)) if main_path else ":memory:"
        connection.execute("ATTACH DATABASE ? AS activity", (activity_path,))
    with transaction(connection):
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


@contextmanager
def transaction(connection):
    if connection.in_transaction:
        yield
        return
    with connection:
        connection.execute("BEGIN IMMEDIATE")
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


def consent_enabled(connection, account):
    row = connection.execute(
        "SELECT enabled FROM activity.consent WHERE account = ?", (account,)
    ).fetchone()
    return row is None or bool(row[0])


def set_consent(connection, account, enabled):
    validate_account(account)
    connection.execute(
        """
        INSERT INTO activity.consent (account, enabled) VALUES (?, ?)
        ON CONFLICT (account) DO UPDATE SET enabled = excluded.enabled
        """,
        (account, enabled),
    )


def _record_audit(connection, account, session, keys, action, time):
    validate_identifiers(account, session)
    connection.executemany(
        """
        INSERT INTO activity.audit (account, session, key, action, time) VALUES (?, ?, ?, ?, ?)
        """,
        ((account, session, key, action, time) for key in keys),
    )


def read_audit(connection, account):
    rows = connection.execute(
        "SELECT account, session, key, action, time FROM activity.audit WHERE account = ? ORDER BY id",
        (account,),
    )
    fields = ("account", "session", "key", "action", "time")
    return [dict(zip(fields, row)) for row in rows]


def read_preferences(connection, account, session=None, time=None):
    with transaction(connection):
        rows = connection.execute(
            "SELECT key, value, written_at FROM preferences WHERE account = ? ORDER BY key",
            (account,),
        )
        preferences = {
            key: {"value": value, "written_at": written_at} for key, value, written_at in rows
        }
        _record_audit(connection, account, session, preferences, "read", time or utc_now())
        return preferences


def write_preferences(connection, account, updates, written_at, session=None):
    validate_identifiers(account, session)

    with transaction(connection):
        for key, value in updates.items():
            if value is None:
                deleted = connection.execute(
                    "DELETE FROM preferences WHERE account = ? AND key = ?",
                    (account, key),
                ).rowcount
                if not deleted:
                    continue
                action = "delete"
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
                action = "write"
            _record_audit(connection, account, session, (key,), action, written_at or utc_now())


def read_session_preferences(connection, account, session, time):
    rows = connection.execute(
        """
        SELECT key, value FROM activity.session_preferences
        WHERE account = ? AND session = ? ORDER BY key
        """,
        (account, session),
    )
    preferences = {key: {"value": value} for key, value in rows}
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
    validate_account(account)
    time = time or utc_now()
    with transaction(connection):
        if key is None:
            rows = connection.execute(
                "SELECT key FROM preferences WHERE account = ? ORDER BY key", (account,)
            )
            keys = [row[0] for row in rows]
        else:
            keys = [key]
        write_preferences(connection, account, dict.fromkeys(keys), time)
        delete_session_preferences(connection, account, time, key=key)


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
