"""青柚家电的规则客服。"""

from .prefs import extract_preferences
from .redact import redact
from .store import (
    consent_enabled,
    delete_session_preferences,
    read_preferences,
    read_session_preferences,
    record_turn,
    transaction,
    utc_now,
    validate_identifiers,
    write_preferences,
    write_session_preferences,
)


_STATUS = {"shipped": "已发货", "unshipped": "未发货"}


class InvalidOrderStatus(ValueError):
    """订单状态无法识别。"""


def chat(connection, orders, account, message, written_at=None, session=None):
    validate_identifiers(account, session)
    account_orders = orders.get(account, [])
    for order in account_orders:
        status = order.get("status")
        if not isinstance(status, str) or status not in _STATUS:
            raise InvalidOrderStatus(f'订单 {order["id"]} 的状态无法识别。')

    updates = {
        key: redact(value) if value is not None else None
        for key, value in extract_preferences(message).items()
    }
    one_turn_preferences = {}
    if "one_turn_order_filter" in updates:
        one_turn_preferences["order_filter"] = {"value": updates.pop("one_turn_order_filter")}

    with transaction(connection):
        if written_at is None:
            written_at = utc_now()
        if updates:
            if consent_enabled(connection, account):
                # 保留原来的无会话调用方式。
                if session is None:
                    write_preferences(connection, account, updates, written_at)
                else:
                    write_preferences(connection, account, updates, written_at, session)
                for key in updates:
                    delete_session_preferences(connection, account, written_at, key=key)
            elif session is not None:
                write_session_preferences(connection, account, session, updates, written_at)

        preferences = read_preferences(connection, account, session, written_at)
        if session is not None:
            preferences.update(read_session_preferences(connection, account, session, written_at))
        preferences.update(one_turn_preferences)
        answer = _reply(account_orders, preferences)
        if session is not None:
            record_turn(connection, account, session, message, answer, updates)
        return answer


def _reply(orders, preferences):
    order_filter = preferences.get("order_filter", {}).get("value")
    brevity = preferences.get("brevity", {}).get("value")
    salutation = preferences.get("salutation", {}).get("value")

    if order_filter:
        heading = f"按你的设置，只列{_STATUS[order_filter]}订单"
        orders = [order for order in orders if order["status"] == order_filter]
    else:
        heading = "按默认设置，列出全部订单"

    if salutation:
        heading = f"{salutation}，{heading}"
    if brevity == "short":
        heading += "，使用简短回答"
    elif brevity == "long":
        heading += "，使用详细回答"

    if not orders:
        return f"{heading}\n当前没有符合条件的订单。"
    if brevity == "short":
        return f"{heading}\n" + "、".join(order["id"] for order in orders)

    lines = [heading]
    for order in orders:
        status = _STATUS[order["status"]]
        if brevity == "long":
            lines.append(f'{order["id"]} {order["product"]}（{status}）')
        else:
            lines.append(f'{order["id"]}（{status}）')
    return "\n".join(lines)
