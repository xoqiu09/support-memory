"""遮盖常见的中文详细地址、身份证号和手机号格式。"""

import re


_DIGIT = r"[0-9０-９]"
_ADDRESS_NUMBER = rf"[A-Za-z]?[0-9０-９一二三四五六七八九十零〇]+(?:-{_DIGIT}+)?"
_ADDRESS_UNIT = r"(?:号院|号楼|号|栋|幢|座|楼|室|单元)"
_ADDRESS = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9·-]{1,64}(?:大道|街道|路|街|巷|弄|小区|村|镇|乡|区|县)"
    rf"(?:\s*{_ADDRESS_NUMBER}\s*{_ADDRESS_UNIT})+"
)
_ID_NUMBER = re.compile(rf"(?<!{_DIGIT})(?:{_DIGIT}{{17}}[0-9０-９XxＸｘ]|{_DIGIT}{{15}})(?!{_DIGIT})")
_PHONE_NUMBER = re.compile(
    rf"(?<!{_DIGIT})(?:[+＋]?[8８][6６][ -]?)?[1１][3-9３-９](?:[ -]?{_DIGIT}){{9}}(?!{_DIGIT})"
)


def contains_personal_number(text):
    return bool(_ID_NUMBER.search(text) or _PHONE_NUMBER.search(text))


def contains_sensitive_data(text):
    return bool(_ADDRESS.search(text)) or contains_personal_number(text)


def redact(text):
    text = _ADDRESS.sub("[地址已遮盖]", text)
    text = _ID_NUMBER.sub("[身份证号已遮盖]", text)
    return _PHONE_NUMBER.sub("[手机号已遮盖]", text)
