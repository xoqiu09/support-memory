"""给会话保留带预算的最近对话和滚动摘要。"""

import json

from .redact import redact


MAX_TURNS = 6
MAX_CHARACTERS = 400 * 2


def encode_window(window):
    return json.dumps(window, ensure_ascii=False, separators=(",", ":"))


def _over_budget(window):
    return len(encode_window(window)) > MAX_CHARACTERS


def append_turn(window, message, answer, updates):
    turns = window["turns"]
    summary = window["summary"]
    preferences = {key: redact(value) if value is not None else None for key, value in updates.items()}
    turns.append({"user": redact(message), "reply": redact(answer), "preferences": preferences})

    while turns and (len(turns) > MAX_TURNS or _over_budget(window)):
        oldest = turns.pop(0)
        summary.append({"user": redact(oldest["user"][:20]), "preferences": oldest["preferences"]})
        # 若只保留最新摘要仍放不下，先继续折叠，避免过早丢掉旧摘要。
        if len(turns) <= MAX_TURNS and not _over_budget({"turns": turns, "summary": summary[-1:]}):
            break

    while len(summary) > 1 and _over_budget(window):
        summary.pop(0)

    if _over_budget(window):
        # 固定字段和 20 字预览都能装下，只有自填称呼可能单独用完整个预算。
        preferences = summary[-1]["preferences"]
        salutation = preferences["salutation"]
        low, high = 0, len(salutation) - 1
        while low < high:
            middle = (low + high + 1) // 2
            preferences["salutation"] = redact(salutation[:middle]) + "…"
            if _over_budget(window):
                high = middle - 1
            else:
                low = middle
        preferences["salutation"] = redact(salutation[:low]) + "…"
