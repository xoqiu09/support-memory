"""遮盖常见的中文详细地址、身份证号和手机号格式。"""

import re


_DIGIT = r"[0-9０-９]"
_ADDRESS_DIGIT = r"[0-9０-９一二三四五六七八九十零〇]"
_ADDRESS_NUMBER = rf"[A-Za-z]?{_ADDRESS_DIGIT}+(?:-{_DIGIT}+)?"
_ADDRESS_UNIT = r"(?:号院|号楼|号|栋|幢|座|楼|室|单元)"
_PLACE_NAME = r"[\u4e00-\u9fffA-Za-z0-9·-]"
_NAMED_ADDRESS = (
    rf"{_PLACE_NAME}{{1,64}}(?:大道|街道|路|街|巷|弄|小区|村|镇|乡|区|县)"
    rf"(?:\s*{_ADDRESS_NUMBER}\s*{_ADDRESS_UNIT})+"
)
_BUILDING_ADDRESS = (
    rf"{_ADDRESS_NUMBER}\s*(?:栋|幢)\s*{_ADDRESS_NUMBER}\s*单元\s*{_ADDRESS_NUMBER}\s*室"
)
_ADDRESS = re.compile(
    r"(?P<label>[\u4e00-\u9fff]{0,64}?地址(?:是|为|[：:])\s*)"
    rf"(?:{_NAMED_ADDRESS}|{_PLACE_NAME}{{0,64}}?\s*{_BUILDING_ADDRESS})"
    rf"|(?P<delivery>{_PLACE_NAME}{{0,64}}送到\s*)?"
    rf"(?:{_NAMED_ADDRESS}|(?:{_PLACE_NAME}{{1,64}}花园\s*)?(?<!{_ADDRESS_DIGIT}){_BUILDING_ADDRESS})"
)
_ID_NUMBER = re.compile(rf"(?<!{_DIGIT})(?:{_DIGIT}{{17}}[0-9０-９XxＸｘ]|{_DIGIT}{{15}})(?!{_DIGIT})")
_PHONE_NUMBER = re.compile(
    rf"(?<!{_DIGIT})(?:[+＋]?[8８][6６][ \u3000-]?)?[1１][3-9３-９](?:[ \u3000-]?{_DIGIT}){{9}}(?!{_DIGIT})"
)


def contains_personal_number(text):
    return bool(_ID_NUMBER.search(text) or _PHONE_NUMBER.search(text))


def contains_sensitive_data(text):
    return bool(_ADDRESS.search(text)) or contains_personal_number(text)


def _mask_address(match):
    prefix = match["label"] or ""
    if match["delivery"]:
        prefix = _ADDRESS.sub(_mask_address, match["delivery"])
    return prefix + "[地址已遮盖]"


def redact(text):
    text = _ID_NUMBER.sub("[身份证号已遮盖]", text)
    text = _PHONE_NUMBER.sub("[手机号已遮盖]", text)
    return _ADDRESS.sub(_mask_address, text)
