"""업종별 낱말: 회사 업종(회사 목록의 `industry`)에 맞춰 "상품"·"주문"을 부르는 말을 고른다.

코드에 한 회사(서점)의 말을 박지 않으려고 둔 표다. 업종은 운영자가 연결 키를 발급할 때 고른 값이고,
화면의 선택지(`frontend/src/lib/industryTerms.ts`의 `INDUSTRIES`)와 같은 이름을 쓴다.
모르는 업종·빈 값은 기본 낱말("상품"·"주문")이다.
예전에 자유롭게 적은 업종도 낱말이 들어 있으면 맞춘다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Terms:
    item: str  # 파는 것 하나를 부르는 말
    order: str  # 고객이 사는 일을 부르는 말


DEFAULT = Terms(item="상품", order="주문")

# (업종 이름, 자유 입력에서 찾을 낱말, 낱말). 위에서부터 먼저 맞는 줄을 쓴다
_TABLE: tuple[tuple[str, tuple[str, ...], Terms], ...] = (
    ("서점·출판", ("서점", "출판", "도서", "책"), Terms(item="책", order="주문")),
    ("여행사", ("여행", "투어", "숙박", "항공"), Terms(item="여행 상품", order="예약")),
    ("교육", ("교육", "강의", "학원", "에듀"), Terms(item="강의", order="수강 신청")),
    ("카페·음식", ("카페", "음식", "식당", "베이커리"), Terms(item="메뉴", order="주문")),
    ("병원", ("병원", "의원", "클리닉"), Terms(item="진료", order="예약")),
    ("온라인 쇼핑몰", ("쇼핑몰", "이커머스", "커머스"), DEFAULT),
    ("오프라인 유통", ("유통", "마트", "매장"), DEFAULT),
    ("제조", ("제조", "공장"), DEFAULT),
)

# 화면 선택지와 같은 순서 (마지막은 "기타")
INDUSTRIES: tuple[str, ...] = (*(name for name, _, _ in _TABLE), "기타")


def terms_for(industry: str | None) -> Terms:
    """업종에 맞는 낱말. 선택지 이름이 같으면 그 줄, 아니면 낱말이 들어 있는 첫 줄, 없으면 기본."""
    text = " ".join((industry or "").split())
    if not text:
        return DEFAULT
    for name, _, terms in _TABLE:
        if text == name:
            return terms
    for _, words, terms in _TABLE:
        if any(word in text for word in words):
            return terms
    return DEFAULT
