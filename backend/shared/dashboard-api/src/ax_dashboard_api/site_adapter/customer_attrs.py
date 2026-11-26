"""고객 속성: 고객사 주문 원본에서 성별·연령대를 꺼내 shop-api 고객 속성 code로 (11/26).

고객사마다 회원 API 모양이 달라 짝 파일 `orders.fields`의 선택 칸으로 정한다.
- `attr.gender`: 성별 값이 있는 곳 (예: 누비투어 예약 `travelers.0.gender`)
- `attr.birth`: 생년(또는 생년월일, 앞 4자리가 연도)이 있는 곳 (예: `travelers.0.birth_date`)

짝에 그 칸이 없으면 같은 규칙(이름 목록)으로 주문 원본에서 찾는다: 맨 위 칸, 회원 묶음
(`member`·`customer`·`user`·`buyer`), 사람 목록(`travelers`·`guests`·`passengers`)의 첫 어른.
없으면 속성을 비운다(지어내지 않는다). 개인 식별 값(이름·연락처)은 읽지 않는다.

code는 데이터 AI 공통 카탈로그(`catalogs/common.toml` `[[sales.attributes]]`)와 같다.
- gender: female · male
- age_band: 10s · 20s · 30s · 40s · 50s_plus (한국 시간 올해 − 생년, 10살 미만은 비움)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from ax_dashboard_api.site_adapter.mapping import MissingField, ResourceMap, pick

KST = timezone(timedelta(hours=9))
GENDER_FIELD = "attr.gender"
BIRTH_FIELD = "attr.birth"

_GENDER_NAMES = ("gender", "sex")
_BIRTH_NAMES = ("birth_date", "birth_year", "birthday", "birthdate", "birth", "dob")
_MEMBER_KEYS = ("member", "customer", "user", "buyer")
_PEOPLE_KEYS = ("travelers", "travellers", "guests", "passengers", "people")
_ADULT = ("adult", "adt", "a")

_FEMALE = {"f", "female", "w", "woman", "women", "여", "여성", "여자"}
_MALE = {"m", "male", "man", "men", "남", "남성", "남자"}
_YEAR = re.compile(r"(19|20)\d{2}")


def gender_code(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in _FEMALE:
        return "female"
    if text in _MALE:
        return "male"
    return None


def age_band(value: Any, now: datetime) -> str | None:
    """생년(또는 생년월일 글자) → 연령대 code. 연도를 못 읽거나 10살 미만이면 None."""
    found = _YEAR.match(str(value or "").strip())
    if not found:
        return None
    age = now.astimezone(KST).year - int(found.group(0))
    if age < 10 or age > 120:
        return None
    if age >= 50:
        return "50s_plus"
    return f"{age // 10 * 10}s"


def _by_name(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    return None


def _person(row: dict[str, Any]) -> dict[str, Any] | None:
    """속성을 볼 사람 한 명: 회원 묶음 → 사람 목록의 첫 어른(없으면 첫 사람)."""
    for key in _MEMBER_KEYS:
        if isinstance(row.get(key), dict):
            return row[key]
    for key in _PEOPLE_KEYS:
        people = [p for p in row.get(key) or [] if isinstance(p, dict)]
        if people:
            adults = [p for p in people if str(p.get("type", "")).lower() in _ADULT]
            return (adults or people)[0]
    return None


def _value(row: dict[str, Any], m: ResourceMap, field: str, names: tuple[str, ...]) -> Any:
    expr = m.fields.get(field)
    if expr:
        try:
            return pick(row, expr)
        except MissingField:
            return None
    found = _by_name(row, names)
    if found is None:
        person = _person(row)
        found = _by_name(person, names) if person else None
    return found


def attributes(row: dict[str, Any], m: ResourceMap, now: datetime | None = None) -> dict[str, str]:
    """주문 원본 한 건 → 고객 속성 {key: code}. 못 읽은 속성은 넣지 않는다."""
    moment = now or datetime.now(UTC)
    out: dict[str, str] = {}
    gender = gender_code(_value(row, m, GENDER_FIELD, _GENDER_NAMES))
    if gender:
        out["gender"] = gender
    band = age_band(_value(row, m, BIRTH_FIELD, _BIRTH_NAMES), moment)
    if band:
        out["age_band"] = band
    return out
