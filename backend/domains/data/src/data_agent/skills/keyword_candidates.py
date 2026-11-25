"""급상승 검색어 후보 고르기·판정 다시 쓰기·줄 꾸미기 (v3, 순수 계산 + 저장소). LLM 없음.

예전: 풀(검색량 큰 순) 앞 30개만 판정해 늘 많이 찾는 큰 단어만 봤다. 이제 `rising_check`(30)
자리를 출처를 섞어 고른다(상한은 `[search]`):
- new: 최근 4일 새 키워드(검색량 큰 순) ≤ `cand_new`(12)
- related_up: 직전 풀 날짜보다 total이 `related_up_ratio`(1.3)배 이상 는 것(증가율 큰 순)
  ≤ `cand_related_up`(10)
- youtube·google: trend_candidates 최신 저장분의 후보 이름(다시 계산하지 않음) ≤ `cand_external`(8)
- company: 회사 trend_scan 그룹 이름 ≤ `cand_company`(6)
- pool: 남은 자리는 풀 검색량 큰 순
같은 말(compact)은 하나로 합치고 출처를 모두 적는다. 그날 처음 고른 30개는 저장해 그날 그대로 쓴다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from data_agent.keyword_pool import PoolRow, SearchConfig, compact

SOURCES = ("new", "related_up", "youtube", "google", "company", "pool")
STALE_DAYS = 3  # 한도로 오늘 못 본 키워드는 이만큼 전까지의 판정을 다시 쓴다
LEVEL_ORDER = {"rising": 0, "warming": 1, "normal": 2, "unknown": 3}
WHY_MAX = 160
NO_EXTERNAL = "trend_candidates 저장분이 없어 YouTube·인기 검색어 후보는 0개"


@dataclass
class Candidate:
    keyword: str
    sources: list[str] = field(default_factory=list)
    video_count: int | None = None  # youtube 출처면 trend_candidates의 인기 영상 수


def external_from(stored: Any) -> tuple[list[tuple[str, str, int | None]], bool]:
    """trend_candidates 저장분([결과, 출력, 근거]) → ([(이름, youtube|google, 영상 수)], 있었는지).

    YouTube 후보(영상 수 많은 순, 저장 순서 그대로) 다음 인기 검색어 후보."""
    output = stored[1] if isinstance(stored, list | tuple) and len(stored) > 1 else None
    if not isinstance(output, dict):
        return [], False
    out: list[tuple[str, str, int | None]] = []
    for c in output.get("candidates") or []:
        if isinstance(c, dict) and c.get("keyword"):
            count = (c.get("youtube") or {}).get("video_count")
            out.append((str(c["keyword"]), "youtube", count if isinstance(count, int) else None))
    for c in output.get("trending_candidates") or []:
        if isinstance(c, dict) and c.get("keyword"):
            out.append((str(c["keyword"]), "google", None))
    return out, True


def related_up(today: Sequence[PoolRow], before: Sequence[PoolRow], ratio: float) -> list[str]:
    """직전 풀보다 total이 `ratio`배 이상 는 키워드 (증가율 큰 순)."""
    prev = {compact(r.keyword): r.total for r in before}
    grown: list[tuple[float, str]] = []
    for r in today:
        old = prev.get(compact(r.keyword))
        if r.total and old and old > 0 and r.total >= old * ratio:
            grown.append((r.total / old, r.keyword))
    grown.sort(key=lambda t: (-t[0], t[1]))
    return [k for _, k in grown]


def pick(*, new: Iterable[str], up: Iterable[str],
         external: Iterable[tuple[str, str, int | None]], company: Iterable[str],
         pool: Sequence[PoolRow], config: SearchConfig) -> list[Candidate]:  # fmt: skip
    """출처별 상한으로 `rising_check`개를 섞어 고른다. 같은 말은 합치고 출처를 모두 적는다."""
    limit = config.rising_check
    by_key: dict[str, Candidate] = {}
    order: list[str] = []

    def add(keyword: str, source: str, *, take: bool, video: int | None = None) -> bool:
        key = compact(keyword)
        if not key:
            return False
        if key in by_key:
            c = by_key[key]
            if source not in c.sources:
                c.sources.append(source)
            if video is not None and c.video_count is None:
                c.video_count = video
            return False
        if not take or len(order) >= limit:
            return False
        by_key[key] = Candidate(keyword, [source], video)
        order.append(key)
        return True

    def fill(items: Iterable[tuple[str, int | None]], source: str, cap: int) -> None:
        used = 0
        for keyword, video in items:
            if add(keyword, source, take=used < cap, video=video):
                used += 1

    fill(((k, None) for k in new), "new", config.cand_new)
    fill(((k, None) for k in up), "related_up", config.cand_related_up)
    used = 0
    for keyword, source, video in external:
        if add(keyword, source, take=used < config.cand_external, video=video):
            used += 1
    fill(((k, None) for k in company), "company", config.cand_company)
    for row in pool:  # 남은 자리: 풀 검색량 큰 순 (이미 들어간 말이면 출처만 더한다)
        add(row.keyword, "pool", take=True)
    return [by_key[k] for k in order]


def counts(cands: Sequence[Candidate]) -> dict[str, int]:
    """출처별 몇 개가 들어갔는지 (첫 출처 기준이 아니라 출처마다)."""
    out = {s: 0 for s in SOURCES}
    for c in cands:
        for s in c.sources:
            out[s] = out.get(s, 0) + 1
    return out


def level(status: str, ratio: float | None, warming: float) -> str:
    if status == "rising":
        return "rising"
    if status == "normal":
        return "warming" if ratio is not None and ratio >= warming else "normal"
    return "unknown"


def _clip(text: str) -> str:
    return text if len(text) <= WHY_MAX else text[: WHY_MAX - 1] + "…"


def why_line(news: Sequence[Any], video_count: int | None, together: Sequence[str]) -> str | None:
    """코드 문장 한 줄: ① 뉴스 첫 줄 ② YouTube 인기 차트 영상 수 ③ 같이 오른 연관어. 없으면 None.

    외부 글(뉴스 제목)은 자르기만 한다."""
    if news:
        n = news[0]
        tail = " · ".join(p for p in (n.source, n.published_at.strftime("%m-%d")
                                      if n.published_at else None) if p)  # fmt: skip
        return _clip(f'"{n.title}"' + (f" · {tail}" if tail else ""))
    if video_count:
        return _clip(f"YouTube 인기 차트 영상 {video_count}개")
    if together:
        return _clip(f'연관어 "{together[0]}"도 같이 오름')
    return None


def sort_key(r: Any) -> tuple[int, int, float, int]:
    """level(rising → warming → normal → unknown) → 끝난 급상승(faded)은 같은 칸 뒤로 → 배수 큰 순
    → 월 검색량 큰 순."""
    faded = 1 if getattr(r, "faded", False) else 0
    return (LEVEL_ORDER.get(r.level, 3), faded, -(r.ratio or 0.0), -(r.monthly_volume or 0))


def volume_chunks(keywords: Sequence[str], volumes: Mapping[str, int | None],
                  size: int = 5) -> list[list[str]]:  # fmt: skip
    """월 검색량이 비슷한 것끼리 5개씩 (작은 말이 큰 말 곁에서 0 근처로 뭉개지지 않게)."""
    ordered = sorted(keywords, key=lambda k: (-(volumes.get(k) or 0), k))
    return [ordered[i : i + size] for i in range(0, len(ordered), size)]


def latest_before(rows: Sequence[Mapping[str, Any]], keyword: str,
                  today: date) -> Mapping[str, Any] | None:  # fmt: skip
    """오늘 전 STALE_DAYS일 안의 가장 최근 판정 (unknown은 빼고)."""
    key = compact(keyword)
    for r in rows:
        day = date.fromisoformat(r["day"])
        if (compact(r["keyword"]) == key and day < today and (today - day).days <= STALE_DAYS
                and r["status"] != "unknown"):  # fmt: skip
            return r
    return None
