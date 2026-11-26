"""用固定词句识别三种偏好。"""

import re


PREFERENCE_KEYS = ("order_filter", "brevity", "salutation")

_RULE = re.compile(
    r"(?P<negated>(?:不要|不用|不必|别|不)\s*)?(?:"
    r"只看\s*(?P<filter>未发货|已发货)"
    r"|(?P<all>看\s*全部订单)"
    r"|(?P<short>(?:回答\s*)?简短点)"
    r"|(?P<long>(?:回答\s*)?详细一点)"
    r"|(?P<salutation>叫我))"
)
_CLAUSE_END = re.compile(r"[，,。！？!?；;\r\n]")


def extract_preferences(message):
    matches = list(_RULE.finditer(message))
    updates = {}
    for index, match in enumerate(matches):
        if match["negated"]:
            continue
        if match.lastgroup == "filter":
            updates["order_filter"] = (
                "unshipped" if match["filter"] == "未发货" else "shipped"
            )
        elif match.lastgroup == "all":
            updates["order_filter"] = None
        elif match.lastgroup == "short":
            updates["brevity"] = "short"
        elif match.lastgroup == "long":
            updates["brevity"] = "long"
        else:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(message)
            salutation = _CLAUSE_END.split(message[match.end():end], maxsplit=1)[0].strip()
            if salutation:
                updates["salutation"] = salutation
    return updates
