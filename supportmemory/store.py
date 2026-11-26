"""按账户存取偏好。"""

from .redact import contains_personal_number


class InvalidAccount(ValueError):
    """账户标识含有个人号码。"""


def initialize(connection):
    with connection:
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


def read_preferences(connection, account):
    rows = connection.execute(
        """
        SELECT key, value, written_at
        FROM preferences
        WHERE account = ?
        ORDER BY key
        """,
        (account,),
    )
    return {
        key: {"value": value, "written_at": written_at}
        for key, value, written_at in rows
    }


def write_preferences(connection, account, updates, written_at):
    if any(value is not None for value in updates.values()) and contains_personal_number(account):
        raise InvalidAccount("账户标识不能包含手机号或身份证号。")

    with connection:
        for key, value in updates.items():
            if value is None:
                connection.execute(
                    "DELETE FROM preferences WHERE account = ? AND key = ?",
                    (account, key),
                )
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


def forget(connection, account, key):
    if key is not None:
        write_preferences(connection, account, {key: None}, None)
        return

    with connection:
        connection.execute("DELETE FROM preferences WHERE account = ?", (account,))
