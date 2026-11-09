"""보정셋 정의: 질의 만드는 규칙과 글 모양. 네트워크 코드를 import하지 않는다(테스트가 본다).

정답은 이 규칙(만든 방식)에서만 나온다. 유사도 그래프를 보고 정하지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

# 관련 질의 = 상품 이름 핵심어 + 꼬리말 (정답: 그 상품 하나)
NAME_TEMPLATES: tuple[str, ...] = ("{} 추천", "{} 리뷰")
# 관련 질의 = 카테고리 이름 + 꼬리말 (정답: 그 카테고리 상품 전부)
CATEGORY_TEMPLATES: tuple[str, ...] = ("{} 추천", "{} 신상")
# 무관 소재 = 지금 인기 검색어 앞에서 TRENDING_MAX개 + YouTube 인기 태그 영상 수 상위 YOUTUBE_MAX개
TRENDING_MAX = 20
YOUTUBE_MAX = 12
YOUTUBE_MIN_VIDEOS = 2
# rich 모양에 붙이는 근거 글 최대 수 (YouTube 제목·인기 검색어 뉴스 제목)
MAX_EVIDENCE = 3
# 핵심어에서 빼는 낱말: 숫자가 든 낱말(3권·12색·3종)과 묶음 표시
DROP_WORDS = frozenset({"세트"})


def name_core(name: str) -> str:
    """상품 이름 → 핵심어. 숫자가 든 낱말과 `DROP_WORDS`를 뺀다.

    예: "무지 노트 3권 세트" → "무지 노트"
    """
    words = [w for w in name.split() if not re.search(r"\d", w) and w not in DROP_WORDS]
    return " ".join(words) or name


def _norm(text: str) -> str:
    return "".join(text.lower().split())


def catalog_words(catalog: Sequence[Mapping[str, object]]) -> set[str]:
    """상품 이름 핵심어의 낱말·카테고리 이름 (2글자 이상, 공백 없앤 소문자)."""
    words: set[str] = set()
    for p in catalog:
        for w in name_core(str(p["name"])).split():
            if len(_norm(w)) >= 2:
                words.add(_norm(w))
        label = p.get("category_name")
        if label and len(_norm(str(label))) >= 2:
            words.add(_norm(str(label)))
    return words


def overlaps_catalog(name: str, catalog: Sequence[Mapping[str, object]]) -> bool:
    """무관 소재에서 뺄 것.

    상품 낱말·카테고리 이름이 소재 이름에 들었거나, 소재 이름이 상품 이름에 들었다.
    """
    key = _norm(name)
    if len(key) < 2:
        return True
    if any(w in key for w in catalog_words(catalog)):
        return True
    return any(key in _norm(str(p["name"])) for p in catalog)


def basic_product_text(product: Mapping[str, object]) -> str:
    """지금 content_recommend가 쓰는 상품 글: "이름 (카테고리)"."""
    label = product.get("category_name")
    return f"{product['name']} ({label})" if label else str(product["name"])


def rich_product_text(product: Mapping[str, object]) -> str:
    """rich 상품 글: 이름 + 카테고리 + 설명(있으면)."""
    description = product.get("description")
    base = basic_product_text(product)
    return f"{base}\n{description}" if description else base


def rich_topic_text(name: str, evidence: Sequence[str]) -> str:
    """rich 소재 글: 이름 + 근거 글 최대 `MAX_EVIDENCE`개 (근거가 없으면 이름만)."""
    return "\n".join([name, *[e for e in evidence if e][:MAX_EVIDENCE]])
