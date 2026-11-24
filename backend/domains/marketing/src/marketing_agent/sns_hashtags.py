"""기획안 SNS 초안의 해시태그 고르기(결정적 화이트리스트 규칙, LLM 없음).

브랜드 태그는 늘 붙이고, 기획안(`CampaignBriefInput`)에 이미 있는 값에서 주제 태그를
`sns_hashtags.toml` 표로 고른다. 순서: 키워드(title·idea·products·trend_refs) -> content_category
-> content_format. 표에 없으면 붙이지 않고, 임의 문자열을 태그로 만들지 않는다.
다른 부서 설정·DB는 읽지 않는다(기획안은 마케팅이 받아 둔 것이다). 골라진 태그와 근거 필드는
`select_hashtags`가 돌려주는 메타로 초안 `caption_source`에 남긴다.
설정을 못 읽거나 어긋나면 브랜드 태그만 쓴다(하위 호환, 게시를 막지 않는다).

브랜드 태그(11/24, 캣북스 고정을 없앴다)는 요청 회사 설정에서 읽는다(`brand_tags`):
tenant.toml `[brand] hashtags` → 없으면 "#" + 회사 이름(공백·태그에 못 쓰는 글자 뺌) → 회사 이름을
모르면 없음. 표 파일에 `brand`를 적으면(설치마다 따로 준 파일) 그것이 먼저다.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

CONFIG_ENV = "MARKETING_SNS_HASHTAG_CONFIG"
DEFAULT_CONFIG = Path(__file__).with_name("sns_hashtags.toml")
TAG_PATTERN = re.compile(r"#[0-9A-Za-z가-힣_]+")
_NOT_TAG = re.compile(r"[^0-9A-Za-z가-힣_]")
TagCheck = Callable[[str], str | None]  # 쓸 수 없는 이유, 쓸 수 있으면 None
_log = logging.getLogger(__name__)


class HashtagConfigError(ValueError):
    pass


def _pattern_only(tag: str) -> str | None:
    return None if TAG_PATTERN.fullmatch(tag) else "invalid_chars"


def brand_tags(check: TagCheck = _pattern_only) -> tuple[str, ...]:
    """요청 회사의 브랜드 태그. `[brand] hashtags` → "#" + 회사 이름 → 없음.

    `[brand] hashtags`에 쓸 수 없는 태그가 있으면 그 목록은 버리고 회사 이름으로 만든다
    (경고 로그)."""
    from ax_agent_template import tenant
    from marketing_agent import company_scope

    given = tenant.brand_section().get("hashtags")
    if given is not None:
        try:
            return _tags(given, "[brand] hashtags", check)
        except HashtagConfigError as error:
            _log.warning("company brand hashtags rejected: %s", error)
    name = company_scope.company_name()
    if name == company_scope.UNKNOWN_COMPANY_NAME:
        return ()
    tag = "#" + _NOT_TAG.sub("", name)
    return (tag,) if tag != "#" and check(tag) is None else ()


@dataclass(frozen=True)
class HashtagTable:
    brand: tuple[str, ...]  # 표 파일의 브랜드 태그. 비면 요청 회사의 브랜드 태그(`brand_tags`)
    max_topic: int
    keywords: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = ()
    category: dict[str, tuple[str, ...]] = field(default_factory=dict)
    format: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _tags(value: Any, where: str, check: TagCheck) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise HashtagConfigError(f"{where}: 태그 목록이 비었다")
    out: list[str] = []
    for tag in value:
        problem = "type" if not isinstance(tag, str) else check(tag)
        if problem:
            raise HashtagConfigError(f"{where}: 쓸 수 없는 태그({problem})")
        if tag not in out:
            out.append(tag)
    return tuple(out)


def parse_table(raw: dict[str, Any], check: TagCheck, max_total: int) -> HashtagTable:
    # 브랜드 태그는 회사 설정에서 읽는다. 표 파일에 적었으면(설치마다 따로 준 파일) 그것이 먼저
    brand = _tags(raw.get("brand"), "brand", check) if "brand" in raw else ()
    max_topic = raw.get("max_topic_tags", 4)
    if (
        not isinstance(max_topic, int)
        or isinstance(max_topic, bool)
        or not 0 <= max_topic <= max_total - len(brand)
    ):
        raise HashtagConfigError("max_topic_tags가 0~(전체 상한-브랜드 수) 정수가 아니다")
    keywords = []
    for i, item in enumerate(raw.get("keyword", [])):
        words = item.get("words")
        if (
            not isinstance(words, list)
            or not words
            or not all(isinstance(w, str) and w.strip() for w in words)
        ):
            raise HashtagConfigError(f"keyword[{i}].words가 비었다")
        keywords.append((tuple(words), _tags(item.get("tags"), f"keyword[{i}]", check)))
    category = {
        str(k): _tags(v, f"category.{k}", check) for k, v in raw.get("category", {}).items()
    }
    fmt = {str(k): _tags(v, f"format.{k}", check) for k, v in raw.get("format", {}).items()}
    return HashtagTable(brand, max_topic, tuple(keywords), category, fmt)


def load_table(path: Path, check: TagCheck, max_total: int) -> HashtagTable:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise HashtagConfigError(f"해시태그 설정을 읽을 수 없다: {type(error).__name__}") from error
    return parse_table(raw, check, max_total)


@lru_cache(maxsize=8)
def _cached(path: str, check: TagCheck, max_total: int) -> HashtagTable | None:
    try:
        return load_table(Path(path), check, max_total)
    except (HashtagConfigError, TypeError, AttributeError) as error:
        # 구조가 어긋난 설정(예: [[keyword]]가 아닌 단일 표)도 게시를 막지 않고 브랜드 태그만 쓴다
        _log.warning("sns hashtag config rejected: %s", error)
        return None


def reset_cache() -> None:
    _cached.cache_clear()


def table(check: TagCheck, max_total: int) -> HashtagTable | None:
    """현재 설정 표. 못 읽거나 검증에 실패하면 None(브랜드 태그만 쓴다)."""
    return _cached(os.environ.get(CONFIG_ENV) or str(DEFAULT_CONFIG), check, max_total)


def select_hashtags(
    brief: Any,
    tbl: HashtagTable | None,
    max_total: int,
    company_tags: tuple[str, ...] | None = None,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    """(전체 태그, 근거 메타). 브랜드 태그가 맨 앞이다.

    브랜드 태그: 표 파일의 `brand` → company_tags(요청 회사, 없으면 `brand_tags()`).
    근거는 주제 태그마다 `{tag, from}`(고른 필드 이름)이고 값 전문은 넣지 않는다.
    """
    mine = brand_tags() if company_tags is None else company_tags
    if tbl is None:
        return mine, {"brand": list(mine), "topic": [], "table": "unavailable"}
    brand = tbl.brand or mine
    limit = max(0, min(tbl.max_topic, max_total - len(brand)))
    chosen: dict[str, dict[str, str]] = {}

    def add(tags: tuple[str, ...], source: str) -> None:
        for tag in tags:
            if tag in brand or tag in chosen or len(chosen) >= limit:
                continue
            chosen[tag] = {"tag": tag, "from": source}

    sources = (
        ("title", brief.title),
        ("idea", brief.idea),
        ("products", "\n".join(brief.products)),
        ("trend_refs", "\n".join(brief.trend_refs)),
    )
    for words, tags in tbl.keywords:
        for source, text in sources:
            if any(w in text for w in words):
                add(tags, source)
                break
    category = (brief.content_category or "").strip()
    if category in tbl.category:
        add(tbl.category[category], "content_category")
    if brief.content_format is not None and brief.content_format in tbl.format:
        add(tbl.format[brief.content_format], "content_format")
    return (*brand, *chosen), {"brand": list(brand), "topic": list(chosen.values())}
