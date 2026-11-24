"""트렌드 보고서 홍보 방향 (11/24 "재미 먼저, 브랜드는 끝에"). 순수 계산만, LLM·파일 없음.

홍보 영상의 목적은 "이 재밌는 영상 만든 채널 어디지? → 회사"로 사람을 끌어오는 것이다.
제품·업종 소개가 목적이 아니다.

- 방향(promo_style): 회사 설정(`tenant.promo()`)이 product면 product.
  의뢰 문장이 제품을 직접 말하면("제품 소개"·"책 소개"·"신간 홍보") 그 의뢰만 product.
  그 밖은 fun_first
- fun_first 추천: 결론은 제품·업종 말로 시작하지 않는다.
  재료는 업종 밖 근거(밈·크리에이터 계정 게시물·요즘 유행·밈)를 적어도 둘 쓰고,
  업종 근거에만 기대는 "업종 연결" 재료는 하나까지
- 업종 밖 근거 종류는 `OFF_INDUSTRY_SOURCES` 목록이다(새 근거가 생기면 한 줄 더한다)
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

FUN_FIRST, PRODUCT = "fun_first", "product"
END_CARD, PROP, CAPTION = "end_card", "prop", "caption"
DEFAULTS = {"promo_style": FUN_FIRST, "brand_exposure": END_CARD}

# 기획 넘김 첫 줄의 한국어. 숫자 없이 쓴다
# (기획 숫자 검사는 facts·제목에 같은 값이 있어야 통과)
EXPOSURE_KO = {
    END_CARD: "마지막에 채널 이름·로고",
    PROP: "화면 속 소품으로 살짝",
    CAPTION: "캡션·프로필에만",
}
STYLE_KO = {FUN_FIRST: "재미 먼저", PRODUCT: "제품·업종 소개"}

# 의뢰 문장이 제품을 직접 말함: (제품 말) + (소개·홍보·리뷰·광고·추천). 띄어쓰기는 보지 않는다
_PRODUCT_NOUNS = ("제품", "상품", "책", "도서", "신간", "굿즈", "신제품")
_PRODUCT_ACTS = ("소개", "홍보", "리뷰", "광고", "추천")
_PRODUCT_REQUEST = re.compile(
    rf"(?:{'|'.join(_PRODUCT_NOUNS)})\s*(?:을|를)?\s*(?:{'|'.join(_PRODUCT_ACTS)})"
)
# 결론이 이 말로 시작하면 제품·업종 이야기로 시작한 것
# (의뢰 제품·회사 이름은 industry_heads가 더한다)
INDUSTRY_HEADS = ("제품", "상품", "신간", "도서", "책", "서점", "출판", "브랜드")
FUN_KINDS = ("meme", "format", "topic", "timing", "other")
LINK_MAX = 1  # 업종 연결 재료(업종 밖 근거가 없는 재료)는 하나까지
OFF_MIN = 2  # 업종 밖 근거를 쓴 재료는 적어도 둘
FEW_OFF = "업종 밖 재미 근거가 적어요"
STARTS_WITH_PRODUCT = (
    "결론이 제품·업종 말로 시작해 버림 (재미 먼저: 대상층이 머무는 형식·소재로 시작)"
)


def asks_for_product(texts: Iterable[str | None]) -> bool:
    """의뢰 문장(들)이 제품을 직접 말하나 ("책 소개 영상", "신간 홍보 릴스")."""
    return any(_PRODUCT_REQUEST.search(" ".join(t.split())) for t in texts if t)


def brief_texts(brief: Any, request_text: str | None = None) -> list[str]:
    """제품 판정에 보는 글: 의뢰 문장 + brief의 제품·목적·형식·결과물 칩·주제."""
    out = [request_text or ""]
    for name in ("product", "goal", "format", "theme"):
        value = getattr(brief, name, None)
        if isinstance(value, str):
            out.append(value)
    out += [p for p in getattr(brief, "format_parts", None) or [] if isinstance(p, str)]
    return [t for t in out if t]


def direction(brief: Any, request_text: str | None,
              company: Mapping[str, str] | None) -> tuple[str, str]:  # fmt: skip
    """(promo_style, brand_exposure).

    회사 설정 product → product. 의뢰가 제품을 직접 말함 → 그 의뢰만 product."""
    company = company or {}
    style = company.get("promo_style")
    style = style if style in STYLE_KO else FUN_FIRST
    exposure = company.get("brand_exposure")
    exposure = exposure if exposure in EXPOSURE_KO else END_CARD
    if style == FUN_FIRST and asks_for_product(brief_texts(brief, request_text)):
        style = PRODUCT
    return style, exposure


def direction_line(style: str, exposure: str) -> str | None:
    """기획 넘김 facts 첫 줄의 값. fun_first만.

    예: "재미 먼저 · 브랜드는 마지막에 채널 이름·로고"."""
    if style != FUN_FIRST:
        return None
    where = EXPOSURE_KO.get(exposure, EXPOSURE_KO[END_CARD])
    return f"{STYLE_KO[FUN_FIRST]} · 브랜드는 {where}"


def industry_heads(brief: Any, company_name: str | None = None) -> list[str]:
    """결론 첫머리로 오면 안 되는 말: 의뢰 제품(낱말까지)·회사 이름·업종 일반 말."""
    words: list[str] = []
    product = getattr(brief, "product", None)
    if isinstance(product, str):
        words += [w for w in re.split(r"[\s·,/]+", product) if w]
    if company_name:
        words.append(company_name.strip())
    words += INDUSTRY_HEADS
    return list(dict.fromkeys(w for w in words if w))


def starts_with_industry(conclusion: str, words: Sequence[str]) -> bool:
    """결론이 제품·업종 말로 시작하나 (앞 따옴표·괄호는 무시)."""
    head = conclusion.lstrip(" '\"‘’“”「」『』《》<>[]()")
    return any(head.startswith(w) for w in words)


# ---- 업종 밖 근거 (근거 id 규칙: 섹션:행 번호, trend_report.evidence_catalog와 같다)
# (이름, 이 보고서에서 그 근거 id들을 고르는 함수).
# radar-audience가 쇼츠 검색·천만 조회 근거를 더하면 같은 모양으로 한 줄 더한다
def _meme_posts(report: Any) -> set[str]:
    posts = report.sections.content.posts
    return {f"content:{n}" for n, p in enumerate(posts, start=1)
            if getattr(p, "source_kind", None) == "meme"}  # fmt: skip


def _trending_memes(report: Any) -> set[str]:
    return {f"meme:{n}" for n in range(1, len(report.sections.content.memes) + 1)}


OFF_INDUSTRY_SOURCES: tuple[tuple[str, Callable[[Any], set[str]]], ...] = (
    ("밈·크리에이터 계정 게시물", _meme_posts),
    ("요즘 유행·밈", _trending_memes),
)


def off_industry_ids(report: Any) -> set[str]:
    out: set[str] = set()
    for _, pick in OFF_INDUSTRY_SOURCES:
        out |= pick(report)
    return out


def is_off_industry(material: Any, ids: set[str]) -> bool:
    return any(e.id in ids for e in material.evidence_items)


def fun_kinds(items: Sequence[Any]) -> tuple[list[Any], list[str]]:
    """LLM 재료 답에서 재미 먼저 종류만 남긴다 (meme·format·topic·timing·업종 연결 other).

    재료 상한(4)보다 먼저 거른다: 쓰지 않는 종류가 자리를 차지하지 않게. (남은 답, 버린 이유)"""
    kept: list[Any] = []
    dropped: list[str] = []
    for m in items:
        kind = str(getattr(m, "kind", "")).strip().lower()
        if kind in FUN_KINDS:
            kept.append(m)
        else:
            dropped.append(f"{kind or '빈 종류'}: 재미 먼저에는 쓰지 않는 종류")
    return kept, dropped


def fun_materials(materials: Sequence[Any],
                  ids: set[str]) -> tuple[list[Any], list[str], list[str]]:  # fmt: skip
    """재미 먼저 재료 규칙 (종류는 fun_kinds가 먼저 거른다). (남은 재료, 버린 이유, 한계).

    - 업종 밖 근거가 없는 재료(업종 연결)는 하나까지
    - 업종 밖 근거를 쓴 재료를 앞으로 둔다
    - 업종 밖 재료가 둘보다 적으면 한계에 "업종 밖 재미 근거가 적어요"
    """
    kept: list[Any] = []
    dropped: list[str] = []
    links = 0
    for m in materials:
        if not is_off_industry(m, ids):
            if links >= LINK_MAX:
                dropped.append(f"{m.kind}: 업종 연결 재료는 하나만")
                continue
            links += 1
        kept.append(m)
    kept.sort(key=lambda m: not is_off_industry(m, ids))  # 안정 정렬: 업종 밖 재료 먼저
    off = sum(1 for m in kept if is_off_industry(m, ids))
    limits = [FEW_OFF] if off < OFF_MIN else []
    return kept, dropped, limits


def fun_prompt(ids: set[str], exposure: str) -> str:
    """재미 먼저일 때 추천 LLM 지시에 더하는 말. 업종 밖 근거 id는 코드가 고른 값이다."""
    listed = ", ".join(sorted(ids)) or "없음"
    where = EXPOSURE_KO.get(exposure, EXPOSURE_KO[END_CARD])
    return (
        "\n- 홍보 방향은 '재미 먼저, 브랜드는 끝에'다."
        " 목적은 '이 재밌는 영상 만든 채널 어디지?'로 사람을 끌어오는 것이고"
        " 제품·업종 소개가 아니다\n"
        "- conclusion: 대상층이 요즘 오래 머무는 형식·소재를 업종과 상관없이 고르고,"
        " 끝에 채널을 기억하게 한다는 방향 한 줄. 제품·업종·회사 이름으로 시작하지 말 것\n"
        f"- materials: 적어도 두 개는 업종 밖 근거(id: {listed})에 기댈 것."
        " kind는 meme·format·topic·timing을 쓰고, 업종과 잇는 재료는 kind other 하나만"
        f" (text 예: '끝에 채널 이름을 남긴다', 브랜드는 {where})\n"
        "- 업종 계정·해시태그 근거는 경쟁사 참고로만 쓸 것"
    )
