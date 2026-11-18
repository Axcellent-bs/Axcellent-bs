"""기획에 넘기는 영상 카테고리·형식 칸 (`content_category`·`content_format`).

기획이 `ReceiveInsightInput.content_category`·`content_format`으로 옮겨 재무에 제작비를 묻는다.
새 분류기·LLM·외부 호출 없이 이미 있는 숏츠 형식(`shorts_format`)만 쓴다.

- 숏츠 형식이 `SHORTS_FORMATS` 안의 이름이고 기타(`OTHER_FORMAT`)·unknown이 아닐 때만
  content_category = 그 형식 이름, content_format = "shorts". 그 밖에는 둘 다 None
- 데이터는 유튜브 숏츠만 보므로 reels는 채우지 않는다
- PromoConcept: 빌려 온 숏츠 형식 기준. 롱폼 기획안은 숏츠 형식이 아니라 None
- TrendInsight: `shorts_format_source == "topic"`(이 주제 영상에서 고른 형식)일 때만.
  카테고리·전체 단계에서 고른 형식은 그 주제 영상과 상관없어 None
- 응답할 때 계산한다 (저장된 예전 브리프도 같은 규칙, 다시 저장하지 않는다)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ax_contracts.schemas.data import PromoConcept, TrendInsight

SHORTS: Literal["shorts"] = "shorts"
LONG_FORM = "롱폼"  # PromoFormat의 롱폼 (숏츠 형식이 아님)


def content_fields(shorts_format: str | None) -> tuple[str | None, Literal["shorts"] | None]:
    """(content_category, content_format). 목록 안 숏츠 형식만, 기타·unknown은 None."""
    from data_agent.skills.shorts_stats import SHORTS_FORMATS, UNKNOWN
    from data_agent.skills.trend_insight_brief import OTHER_FORMAT

    name = (shorts_format or "").strip()
    if not name or name not in SHORTS_FORMATS or name in (OTHER_FORMAT, UNKNOWN):
        return None, None
    return name, SHORTS


def for_concept(concept: PromoConcept) -> tuple[str | None, Literal["shorts"] | None]:
    if concept.format == LONG_FORM:
        return None, None
    return content_fields(concept.shorts_format)


def for_insight(insight: TrendInsight) -> tuple[str | None, Literal["shorts"] | None]:
    if insight.shorts_format_source != "topic":
        return None, None
    return content_fields(insight.shorts_format)


def fill_concepts(concepts: Sequence[PromoConcept]) -> None:
    for c in concepts:
        c.content_category, c.content_format = for_concept(c)


def fill_insights(insights: Sequence[TrendInsight]) -> None:
    for i in insights:
        i.content_category, i.content_format = for_insight(i)
