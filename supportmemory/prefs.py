"""用固定词句识别三种偏好。"""

import re


PREFERENCE_KEYS = ("order_filter", "brevity", "salutation")

_SALUTATION_SPEAKER = r"(?:维修师傅|师傅|客服|维修人员)"
_RULE = re.compile(
    r"(?P<negated>(?:不要|不用|不必|不想|别|不)\s*)?(?:"
    r"(?:(?P<one_turn>这次\s*(?:(?:请|想)\s*)?)?"
    r"(?:只看\s*(?P<filter>未发货|已发货)|(?P<all>看\s*全部订单)))"
    r"|(?P<short>(?:回答\s*)?简短点)"
    r"|(?P<long>(?:回答\s*)?详细一点)"
    rf"|(?P<reported_salutation>{_SALUTATION_SPEAKER}\s*(?:(?:刚才|刚刚|之前|先前|刚)\s*)?叫我)"
    rf"|(?P<salutation>(?:请\s*{_SALUTATION_SPEAKER}\s*)?叫我))"
)
_CLAUSE_END = re.compile(r"[，,。！？!?；;\r\n]|\.(?=\s)")
_DETAIL_COMPARISON = re.compile(r"\S\s*(?<!性价)比(?![如较例]|方说)\s*\S.*详细一点$")
_POLITE_REQUEST_PREFIX = re.compile(r"^\s*请\s*(?:[你您]\s*)?")
_QUOTED_TEXT = r"""(?:‘[^’]*’|“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"|'[^']*')"""
# Consume complete quote pairs even when they are outside a meaning question.
_QUOTED_EXAMPLES = re.compile(
    rf"{_QUOTED_TEXT}(?:[ \t，,、]*(?:(?:和|或|以及|与|及)[ \t，,、]*)?{_QUOTED_TEXT})*"
    r"(?P<meaning>[\s，,、：:；;]*(?=(?:这句话\s*)?(?:是?(?:什么|啥)意思|怎么理解)))?"
)


def extract_preferences(message):
    message = _QUOTED_EXAMPLES.sub(
        lambda match: "，" if match["meaning"] is not None else match[0], message
    )
    matches = list(_RULE.finditer(message))
    updates = {}
    for index, match in enumerate(matches):
        if match["negated"] or match["reported_salutation"]:
            continue
        if match.lastgroup in ("filter", "all"):
            key = "one_turn_order_filter" if match["one_turn"] else "order_filter"
            updates[key] = {"未发货": "unshipped", "已发货": "shipped"}.get(match["filter"])
        elif match.lastgroup == "short":
            updates["brevity"] = "short"
        elif match.lastgroup == "long":
            clause = _CLAUSE_END.split(message[:match.end()])[-1]
            if not _DETAIL_COMPARISON.search(_POLITE_REQUEST_PREFIX.sub("", clause)):
                updates["brevity"] = "long"
        else:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(message)
            salutation = _CLAUSE_END.split(message[match.end():end], maxsplit=1)[0].strip()
            if salutation:
                updates["salutation"] = salutation
    return updates
