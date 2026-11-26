"""青柚家电的规则客服。"""

from .prefs import extract_preferences
from .redact import redact
from .store import read_preferences, write_preferences


_STATUS = {"shipped": "已发货", "unshipped": "未发货"}


def chat(connection, orders, account, message, written_at):
    updates = extract_preferences(message)
    if updates:
        updates = {
            key: redact(value) if value is not None else None for key, value in updates.items()
        }
        write_preferences(connection, account, updates, written_at)

    preferences = read_preferences(connection, account)
    return _reply(orders.get(account, []), preferences)


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
