"""트렌드 보고서 콘텐츠 섹션: 의뢰 대상층(나이·성별)으로 레이더 재료 고르기 (11/24).

의뢰 대상층은 매번 다르다(20대 남성, 10~30대 여성, 70대 남녀, 전체…). 한 목록의 계정에서 뜬
게시물을 그대로 쓰면 대상층과 안 맞는 재료가 나온다. 그래서 계정 층 태그(`radar_meta`)로 고른다.

- 레이더 저장분(instagram_radar `audience_posts`·`accounts_meta`) 중 industry가 아니고 팔로워 하한에
  걸리지 않은 계정만. 계정 ages가 대상과 겹치고 genders가 대상 성별 또는 all인 계정의 게시물
- 맞는 계정이 5곳 미만이면 전체 계정으로 넓히고 머리에 적는다
- 60plus처럼 맞는 계정이 3곳 미만이면 "인스타 표본 적음" + 검색·유튜브 근거를 앞에 둔다
- 의뢰에 밈·유행·짤 말이 있으면 meme·creator 계정 게시물을 먼저 (#1671 규칙 유지)
- 쇼츠 검색 근거: 대상층이 있거나 밈·유행 의뢰일 때만. trend_memes 저장분(분류 끝난 것) + 대상층
  말을 붙인 추가 검색 최대 2개(쿼터 30% 규칙 안, 실존 인물·사건 분류 적용)
- 조회 천만 이상: trend_memes 저장분 중 대상층에 맞는 인스타 릴스 + 유튜브 쇼츠, 우선 근거
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ax_contracts.schemas.data import (
    InstagramRadarPost,
    MegaViewItem,
    MemeShortsVideo,
    RadarAccountMeta,
    TrendMaterial,
    TrendReportBrief,
)
from data_agent import radar_meta as rm

MAX_SHORTS = 5
MAX_MEGA = 5
EXTRA_SEARCHES = 2  # 대상층 말을 붙인 추가 쇼츠 검색 (키워드 2개까지)
FIRST_KINDS = ("meme", "creator")


@dataclass
class AudiencePick:
    audience: rm.Audience
    posts: list[InstagramRadarPost] = field(default_factory=list)
    matched: int = 0  # 대상층에 맞는 계정 수
    widened: bool = False
    thin: bool = False
    line: str | None = None
    limits: list[str] = field(default_factory=list)
    shorts: list[MemeShortsVideo] = field(default_factory=list)
    mega: list[MegaViewItem] = field(default_factory=list)


def _output(stored: Any) -> dict[str, Any] | None:
    """응답 캐시 저장분([결과, 출력, 근거]) → 출력."""
    output = stored[1] if isinstance(stored, list | tuple) and len(stored) > 1 else None
    return output if isinstance(output, dict) else None


def _valid(model: Any, rows: Any) -> list[Any]:
    out = []
    for row in rows if isinstance(rows, list) else []:
        try:
            out.append(model.model_validate(row))
        except ValueError:
            continue
    return out


def pick(brief: TrendReportBrief, stored_radar: Any, stored_memes: Any,
         meme_words: bool) -> AudiencePick | None:  # fmt: skip
    """대상층으로 고른 레이더 게시물·쇼츠·천만 근거.

    레이더 저장분에 계정 메타가 없으면(옛 결과) None."""
    audience = rm.audience_from_brief(brief.target_ages, brief.target_gender)
    radar = _output(stored_radar)
    metas: list[RadarAccountMeta] = _valid(RadarAccountMeta, (radar or {}).get("accounts_meta"))
    if not metas:
        return None
    out = AudiencePick(audience)
    usable = [m for m in metas if m.kind != "industry" and not m.excluded_reason]
    matched = [m for m in usable if rm.matches(m, audience)]
    out.matched = len(matched)
    use = matched
    if not audience.is_all and len(matched) < rm.WIDEN_BELOW:
        out.widened, use = True, usable
    names = {m.account for m in use}
    posts = [p for p in _valid(InstagramRadarPost, (radar or {}).get("audience_posts"))
             if p.account in names]  # fmt: skip
    if meme_words:  # 안정 정렬: 배수 순 안에서 meme·creator 먼저
        posts.sort(key=lambda p: p.account_kind not in FIRST_KINDS)
    out.posts = posts
    out.thin = "60plus" in audience.ages and len(matched) < rm.THIN_BELOW
    out.line = f"대상층({audience.label})에 맞는 계정 {out.matched}곳 중 뜬 게시물 {len(posts)}개"
    if out.widened:
        out.line += f" · {rm.WIDENED}"
        out.limits.append(f"대상층({audience.label})에 맞는 계정이 {out.matched}곳이라 전체 계정"
                          f" {len(usable)}곳으로 넓힘")  # fmt: skip
    if out.thin:
        out.limits.append(f"인스타 표본 적음 ({audience.label}에 맞는 계정 {out.matched}곳):"
                          " 네이버 검색(60대 이상 칸)·유튜브 근거를 결론 앞에 둠")  # fmt: skip
    memes = _output(stored_memes) or {}
    if not audience.is_all or meme_words:
        shorts = (memes.get("shorts_search") or {}).get("items")
        out.shorts = _valid(MemeShortsVideo, shorts)[:MAX_SHORTS]
    mega = _valid(MegaViewItem, (memes.get("mega_views") or {}).get("items"))
    out.mega = mega_for(audience, mega)
    return out


def mega_for(audience: rm.Audience, items: Sequence[MegaViewItem]) -> list[MegaViewItem]:
    """조회 천만 이상 중 대상층에 맞는 인스타 릴스 + 유튜브 쇼츠 (조회수 순, 5개)."""
    ok = [i for i in items if i.platform == "youtube"
          or rm.tags_match(i.audience_ages, i.audience_genders, audience)]  # fmt: skip
    return sorted(ok, key=lambda i: -i.views)[:MAX_MEGA]


def extra_queries(audience: rm.Audience, keywords: Sequence[str]) -> list[tuple[str, str]]:
    """대상층 말을 붙인 추가 검색어 (키워드 2개까지): ("밈", "밈 20대"). 대상층이 없으면 빈 목록."""
    word = audience.search_word
    if audience.is_all or word is None:
        return []
    return [(k, f"{k} {word}") for k in list(dict.fromkeys(keywords))[:EXTRA_SEARCHES]]


async def add_extra_shorts(out: AudiencePick, env: Mapping[str, str], connector: Any,
                           keywords: Sequence[str], now: datetime, llm: Any, *,
                           category_cache: Any = None, context_id: str | None = None,
                           ledger: Any = None) -> int:  # fmt: skip
    """대상층 추가 쇼츠 검색 → 분류 → 쇼츠 근거 앞에. 쓴 YouTube 단위를 돌려준다."""
    from data_agent import meme_shorts as ms

    queries = extra_queries(out.audience, keywords)
    if not queries or connector is None:
        return 0
    found, limits, units = await ms.search_extra(env, connector, queries, now, ledger=ledger)
    out.limits.extend(limits)
    kept, removed = await ms.classify(found, llm, category_cache=category_cache,
                                      context_id=context_id, limit=MAX_SHORTS)  # fmt: skip
    if removed:
        out.limits.append(f"쇼츠 대상층 검색: 실존 인물·사건 분류로 {len(removed)}개 뺌")
    seen = {v.video_id for v in kept}
    out.shorts = [*kept, *[v for v in out.shorts if v.video_id not in seen]][:MAX_SHORTS]
    return units


# ---------------------------------------------------------------- 근거 순서


def first_prefixes(thin: bool) -> tuple[str, ...]:
    """결론 근거를 앞에 둘 id 앞부분: 표본이 적으면 검색·유튜브, 아니면 조회 천만 이상."""
    return ("search:", "shopping:", "shorts:", "mega:") if thin else ("mega:",)


def order_catalog(catalog: Mapping[str, Any], thin: bool) -> dict[str, Any]:
    """근거 목록 순서: 앞에 둘 근거 먼저, 나머지는 원래 순서 (LLM 데이터 블록에 이 순서로)."""
    first = first_prefixes(thin)
    head = {k: v for k, v in catalog.items() if k.startswith(first)}
    return {**head, **{k: v for k, v in catalog.items() if k not in head}}


def order_materials(materials: Sequence[TrendMaterial], thin: bool,
                    meme_ids: set[str]) -> list[TrendMaterial]:  # fmt: skip
    """재료 순서: 앞에 둘 근거를 쓴 재료 → 밈 소스 근거를 쓴 재료 → 나머지 (안정 정렬)."""
    first = first_prefixes(thin)

    def key(m: TrendMaterial) -> tuple[bool, bool]:
        ids = [e.id for e in m.evidence_items]
        return (not any(i.startswith(first) for i in ids), not any(i in meme_ids for i in ids))

    return sorted(materials, key=key)
