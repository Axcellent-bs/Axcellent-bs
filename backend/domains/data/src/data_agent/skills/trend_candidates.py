"""trend_candidates: YouTube 인기 영상 태그에서 후보 키워드를 뽑고 네이버 급상승을 붙인다.

읽기 전용(grade read).

- 후보 뽑기는 코드 규칙이다(LLM 안 씀). 최신 YouTube 스냅샷(`youtube_store`, #277)의
  인기 영상 태그를 정리(앞뒤 공백·# 제거, 영문 소문자)하고, `min_videos`개 이상 영상에 나온
  태그를 나온 영상 수 많은 순으로 최대 `max_candidates`개 고른다.
  회사 제외 목록(`exclude_tags`)은 뺀다.
- 별칭 묶기: 태그 정리 다음에 회사 별칭 표(`[candidates.aliases]`, 대표 이름 = [별칭…])로
  별칭을 대표 이름으로 바꾼다(lol → 리그오브레전드). 나온 영상 수는 영상 기준으로 중복 없이
  합친다(한 영상에 lol·리그오브레전드가 같이 있어도 1개). 표에 없는 이름은 묶지 않는다.
- 네이버 판정은 trend_scan과 같은 함수(`trend_scan.judge`)를 그대로 쓴다. 후보마다 한 그룹
  (대표 이름 + 별칭들을 키워드로)으로 한 번씩 부르고, 못 읽은 후보는 그 후보만 unknown이다.
- 별칭이 있으면 대표 이름·별칭을 하나씩 따로도 판정한다(이름마다 네이버 1번). 묶음 그룹은 평범인데
  이름 하나가 급상승이면 급상승으로 올리고, 어느 이름이 올랐는지(`rising_names`)를 남긴다.
- 스냅샷이 없거나 `max_age_hours`보다 오래됐으면 후보를 뽑지 않고 unknown + 이유다.
- YouTube 제목에서 뽑은 구(공백·문장부호로 나눈 2~3어절)도 후보 출처다(`[candidates] titles`).
  `min_videos`개 이상 영상 제목에 나온 구만 쓰고, 더 긴 구와 영상 집합이 같은 짧은 구는 뺀다.
  태그 이름과 공백만 다르면 그 태그 후보에 합친다(`origins`에 youtube_title).
- 자동 제외: 쌓인 스냅샷 전체(`youtube_store.popular_history`)에서 여러 날·여러 카테고리에 고르게
  나오는 태그·구(문서 빈도)를 늘 붙는 말로 보고 뺀다(`[candidates.auto_exclude]` 기준값).
  수동 `exclude_tags`는 그대로 늘 빼고, `keep_tags`는 자동 제외에서 살린다(덮어쓰기용).
  빠진 것은 출력 `auto_excluded`에 이유와 숫자로 남긴다.
- 이름 자동 묶기(`[candidates.auto_group]`): 네이버로 판정한 YouTube 후보끼리, 쌓인 스냅샷에서
  같은 영상에 함께 나온 적이 있고 두 이름의 네이버 일별 값(로그) 상관이 `merge_corr` 이상이면 같은
  대상으로 묶는다. 묶음은 별칭 표와 같은 규칙(#375: 묶음 그룹 + 이름별 판정, 하나라도 급상승이면
  올림)으로 다시 판정한다(묶음마다 네이버 1번 더). `suggest_corr` 이상이면 묶음 제안만 남긴다.
- 인기 검색어(Google Trends 공개 RSS 스냅샷, `trending_store`)도 후보 출처다. 정리·별칭·제외 규칙은
  태그와 같고, YouTube 후보와 이름이 겹치면 그 후보에 근거(`trending`)를 붙인다. 겹치지 않는 것은
  `trending_candidates`(최대 `[trending] max_candidates`개)로 따로 네이버 판정한다(같은 함수).
  인기 검색어 스냅샷이 없거나 오래되면 그 출처만 unknown + 이유다(YouTube 후보는 그대로).
- 설정: 회사 `trends.toml`의 `[candidates]`·`[trending]` (`DATA_TREND_CONFIG`).
  README "trend_candidates".
"""

from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    AutoExcludedTerm,
    NamePairEvidence,
    TrendCandidate,
    TrendCandidatesOutput,
    TrendCandidatesRequest,
    TrendingCandidate,
    TrendingSearchEvidence,
    TrendKeywordGroup,
    TrendScanInput,
    TrendVerdict,
    YouTubeTagEvidence,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import KST, NaverDatalabConnector
from data_agent.skills.trend_scan import (
    DEFAULT_SETTINGS,
    REQUEST_DAYS,
    TREND_CONFIG_ENV,
    Settings,
    judge,
    yesterday_kst,
)
from data_agent.today_board import BOARD
from data_agent.trending_store import DB_ENV as TRENDING_DB_ENV
from data_agent.trending_store import TrendingSnapshot, latest_trending
from data_agent.video_links import links, thumbs_of
from data_agent.youtube_store import (
    DB_ENV,
    DEFAULT_DB,
    HistoryVideo,
    PopularSnapshot,
    latest_popular,
    popular_history,
)

NAVER_FAILED = "네이버 검색어 트렌드를 읽지 못함"  # trend_scan과 같은 문장
MAX_KEYWORDS = 20  # 네이버 그룹 하나에 넣을 수 있는 키워드 수 (TrendKeywordGroup)


@dataclass(frozen=True)
class CandidateConfig:
    """회사 설정 `[candidates]`. 파일·표가 없으면 이 값(제외 목록·별칭 표는 비어 있음)."""

    max_candidates: int = 5
    min_videos: int = 2
    max_age_hours: float = 24.0
    rep_videos: int = 3
    exclude_tags: frozenset[str] = field(default_factory=frozenset)
    # 대표 이름 → 별칭들 (정리한 값, 설정 순서). 별칭 → 대표 이름은 `canonical()`
    aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # 인기 검색어 `[trending]`: 지역, YouTube와 겹치지 않는 후보 상한, 스냅샷 나이 기준
    trending_geo: str = "KR"
    trending_max: int = 5
    trending_max_age_hours: float = 24.0
    # YouTube 제목 구 (`titles`): 2~3어절, 이 수 이상 영상 제목에 나온 것만
    titles: bool = False
    title_min_words: int = 2
    title_max_words: int = 3
    # 자동 제외 `[candidates.auto_exclude]`: 쌓인 인기 영상 중 비율·카테고리 수·날짜 비율이
    # 모두 이 이상이면 늘 붙는 말. 꺼져 있으면(`auto_exclude` false) 수동 목록만 쓴다
    auto_exclude: bool = False
    auto_min_doc_ratio: float = 0.02
    auto_min_categories: int = 3
    auto_min_day_ratio: float = 0.5
    keep_tags: frozenset[str] = field(default_factory=frozenset)  # 자동 제외하지 않을 이름
    # 이름 자동 묶기 `[candidates.auto_group]`: 같은 영상에 함께 나온 영상 수 ≥ `group_min_shared`,
    # 두 이름의 네이버 일별 로그값 상관 ≥ `group_merge_corr`(값이 둘 다 있는 날 ≥ `group_min_days`)
    auto_group: bool = False
    group_merge_corr: float = 0.9
    group_suggest_corr: float = 0.7
    group_min_days: int = 28
    group_min_shared: int = 1

    def canonical(self, tag: str) -> str:
        """정리한 태그의 대표 이름 (별칭이 아니면 그대로)."""
        for name, others in self.aliases.items():
            if tag in others:
                return name
        return tag

    def keywords(self, name: str) -> list[str]:
        """네이버 그룹에 넣을 키워드: 대표 이름 + 별칭들 (최대 20개)."""
        return [name, *self.aliases.get(name, ())][:MAX_KEYWORDS]


def clean_tag(tag: str) -> str:
    """앞뒤 공백·#을 떼고 영문을 소문자로 바꾼다 ("  #T1 " → "t1"). 남는 게 없으면 ""."""
    return tag.strip().strip("#").strip().lower()


def _norm(name: str) -> str:
    """이름 비교용: 태그 정리 뒤 안쪽 공백도 뺀다 ("리그 오브 레전드" = "리그오브레전드")."""
    return clean_tag(name).replace(" ", "")


def _load_aliases(raw: object) -> dict[str, tuple[str, ...]]:
    """`[candidates.aliases]` (대표 이름 = [별칭…]). 이름·별칭은 태그처럼 정리한다.

    별칭이 대표 이름과 같거나, 이미 다른 대표 이름의 별칭이거나, 그 자체가 대표 이름이면 뺀다
    (앞에 적은 것이 이긴다). 틀린 모양이면 `TypeError`.
    """
    if not isinstance(raw, Mapping):
        raise TypeError("aliases는 표여야 한다")
    names = [clean_tag(str(k)) for k in raw]
    taken: set[str] = set(n for n in names if n)
    out: dict[str, tuple[str, ...]] = {}
    for key, values in raw.items():
        name = clean_tag(str(key))
        if not name or not isinstance(values, list):
            raise TypeError("aliases의 값은 별칭 목록이어야 한다")
        mine: list[str] = []
        for value in values:
            other = clean_tag(str(value))
            if other and other not in taken:
                mine.append(other)
                taken.add(other)
        out[name] = tuple(mine[: MAX_KEYWORDS - 1])
    return out


def load_config(env: Mapping[str, str]) -> CandidateConfig:
    """`DATA_TREND_CONFIG`(회사 `trends.toml`)의 `[candidates]`. 없거나 틀리면 기본값."""
    path = env.get(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return CandidateConfig()
    try:
        with Path(path).open("rb") as f:
            whole = tomllib.load(f)
        raw = whole.get("candidates") or {}
        trending = whole.get("trending") or {}
        auto = raw.get("auto_exclude") or {}
        grouping = raw.get("auto_group") or {}
        base = CandidateConfig()
        return CandidateConfig(
            auto_group=bool(grouping.get("enabled", bool(grouping))),
            group_merge_corr=float(grouping.get("merge_corr", base.group_merge_corr)),
            group_suggest_corr=float(grouping.get("suggest_corr", base.group_suggest_corr)),
            group_min_days=int(grouping.get("min_days", base.group_min_days)),
            group_min_shared=int(grouping.get("min_shared_videos", base.group_min_shared)),
            titles=bool(raw.get("titles", base.titles)),
            title_min_words=int(raw.get("title_min_words", base.title_min_words)),
            title_max_words=int(raw.get("title_max_words", base.title_max_words)),
            auto_exclude=bool(auto.get("enabled", bool(auto))),
            auto_min_doc_ratio=float(auto.get("min_doc_ratio", base.auto_min_doc_ratio)),
            auto_min_categories=int(auto.get("min_categories", base.auto_min_categories)),
            auto_min_day_ratio=float(auto.get("min_day_ratio", base.auto_min_day_ratio)),
            keep_tags=frozenset(
                t for t in (clean_tag(str(x)) for x in raw.get("keep_tags", [])) if t
            ),
            trending_geo=str(trending.get("geo") or base.trending_geo).strip().upper(),
            trending_max=int(trending.get("max_candidates", base.trending_max)),
            trending_max_age_hours=float(
                trending.get("max_age_hours", base.trending_max_age_hours)
            ),
            max_candidates=int(raw.get("max_candidates", base.max_candidates)),
            min_videos=int(raw.get("min_videos", base.min_videos)),
            max_age_hours=float(raw.get("max_age_hours", base.max_age_hours)),
            rep_videos=int(raw.get("rep_videos", base.rep_videos)),
            exclude_tags=frozenset(
                t for t in (clean_tag(str(x)) for x in raw.get("exclude_tags", [])) if t
            ),
            aliases=_load_aliases(raw.get("aliases", {})),
        )
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return CandidateConfig()


@dataclass(frozen=True)
class TagCount:
    keyword: str  # 대표 이름
    video_ids: tuple[str, ...]  # 인기 순위 순, 중복 없음
    matched: tuple[str, ...] = ()  # 이 후보로 센 정리된 태그 (처음 나온 순서)
    phrases: tuple[str, ...] = ()  # 이 후보로 센 제목 구
    origins: tuple[str, ...] = ("youtube_tag",)

    @property
    def video_count(self) -> int:
        return len(self.video_ids)


_WORD = re.compile(r"[^\W_]+")


def title_phrases(title: str, min_words: int = 2, max_words: int = 3) -> set[str]:
    """제목 → 공백·문장부호로 나눈 낱말의 이어진 `min_words`~`max_words`어절 구 (영문 소문자).

    숫자로만 된 구는 뺀다. 형태소 분석은 하지 않는다(조사가 붙은 채로 센다).
    """
    words = _WORD.findall(title.lower().replace("'", "").replace("’", ""))
    out: set[str] = set()
    for n in range(max(1, min_words), max_words + 1):
        for i in range(len(words) - n + 1):
            part = words[i : i + n]
            if all(w.isdigit() for w in part):
                continue
            out.add(" ".join(part))
    return out


# ---------------------------------------------------------------- 자동 제외 (늘 붙는 말)


@dataclass(frozen=True)
class CommonTerm:
    """쌓인 인기 영상 전체에서 잰 이름 하나의 퍼짐 (문서 빈도)."""

    name: str
    source: str  # youtube_tag | youtube_title
    videos: int  # 이 이름이 붙은(제목에 나온) 영상 수
    total_videos: int
    categories: int
    days: int
    total_days: int

    @property
    def doc_ratio(self) -> float:
        return self.videos / self.total_videos if self.total_videos else 0.0

    def reason(self) -> str:
        where = "태그" if self.source == "youtube_tag" else "제목"
        return (
            f"늘 붙는 말({where}): 쌓인 인기 영상 {self.total_videos}개 중 {self.videos}개"
            f"({self.doc_ratio:.1%}), 카테고리 {self.categories}개, {self.days}/{self.total_days}일"
        )


def common_terms(history: Sequence[HistoryVideo], config: CandidateConfig) -> dict[str, CommonTerm]:
    """여러 날·여러 카테고리에 고르게 나오는 이름 → 그 숫자. 꺼져 있거나 기록이 없으면 빈 표.

    기준(모두 만족): 영상 비율 ≥ `auto_min_doc_ratio`, 카테고리 수 ≥ `auto_min_categories`,
    나온 날 ÷ 기록이 있는 날 ≥ `auto_min_day_ratio`. 이름은 태그·구를 정리하고 별칭 표로 바꾼 값.
    """
    if not config.auto_exclude or not history:
        return {}
    total = len(history)
    all_days = {d for v in history for d in v.days}
    stats: dict[tuple[str, str], tuple[set[str], set[str | None], set[object]]] = {}
    for video in history:
        names = {("youtube_tag", config.canonical(clean_tag(t))) for t in video.tags}
        if config.titles:
            names |= {
                ("youtube_title", config.canonical(p))
                for p in title_phrases(video.title, config.title_min_words, config.title_max_words)
            }
        for key in names:
            if not key[1]:
                continue
            vids, cats, days = stats.setdefault(key, (set(), set(), set()))
            vids.add(video.video_id)
            cats.add(video.category_id)
            days.update(video.days)
    out: dict[str, CommonTerm] = {}
    for (source, name), (vids, cats, days) in sorted(stats.items()):
        term = CommonTerm(name, source, len(vids), total, len(cats), len(days), len(all_days))
        if (
            term.doc_ratio >= config.auto_min_doc_ratio
            and term.categories >= config.auto_min_categories
            and term.days / max(1, term.total_days) >= config.auto_min_day_ratio
            and name not in config.keep_tags
            and name not in out
        ):
            out[name] = term
    return out


@dataclass(frozen=True)
class Picked:
    candidates: list[TagCount]
    auto_dropped: list[tuple[CommonTerm, int]]  # 자동 제외로 빠진 이름과 이번 스냅샷 영상 수


def pick_candidates(
    snapshot: PopularSnapshot, config: CandidateConfig, limit: int
) -> list[TagCount]:
    """`pick_names`의 후보만 (자동 제외 없이)."""
    return pick_names(snapshot, config, limit).candidates


def pick_names(
    snapshot: PopularSnapshot,
    config: CandidateConfig,
    limit: int,
    common: Mapping[str, CommonTerm] | None = None,
) -> Picked:
    """정리한 태그(와 제목 구)를 대표 이름으로 바꿔 붙은 인기 영상을 세고 후보를 고른다.

    한 영상에 같은 이름이 여러 번(대소문자·# 차이, 별칭 포함) 붙어도 한 번만 센다. 정리한 태그나
    대표 이름이 제외 목록(수동)에 있거나 자동 제외(`common`)면 뺀다. 순서: 나온 영상 수 많은 순 →
    그 이름 영상 중 가장 높은 인기 순위 → 대표 이름.
    """
    common = common or {}
    rank_of = {v.video_id: v.rank for v in snapshot.videos}
    videos: dict[str, list[str]] = {}
    matched: dict[str, list[str]] = {}
    best_rank: dict[str, int] = {}
    dropped: dict[str, set[str]] = {}

    def manual(tag: str, name: str) -> bool:
        return tag in config.exclude_tags or name in config.exclude_tags

    def auto(tag: str, name: str, vid: str) -> bool:
        hit = name if name in common else tag if tag in common else None
        if hit is None or name in config.keep_tags:
            return False
        dropped.setdefault(hit, set()).add(vid)
        return True

    phrase_videos: dict[str, list[str]] = {}
    for video in sorted(snapshot.videos, key=lambda v: v.rank):
        names: dict[str, list[str]] = {}  # 이 영상에서 대표 이름 → 붙은 태그
        for tag in sorted({clean_tag(t) for t in video.tags}):
            name = config.canonical(tag)
            # 자동 제외를 먼저 본다: 수동 목록에도 있는 이름도 자동으로 잡혔는지 남긴다
            if not tag or (auto(tag, name, video.video_id) | manual(tag, name)):
                continue
            names.setdefault(name, []).append(tag)
        for name, tags in names.items():
            videos.setdefault(name, []).append(video.video_id)
            best_rank.setdefault(name, video.rank)
            seen = matched.setdefault(name, [])
            seen.extend(t for t in tags if t not in seen)
        if config.titles:
            words = (config.title_min_words, config.title_max_words)
            for phrase in sorted(title_phrases(video.title, *words)):
                name = config.canonical(phrase)
                if auto(phrase, name, video.video_id) | manual(phrase, name):
                    continue
                phrase_videos.setdefault(name, []).append(video.video_id)

    origins: dict[str, list[str]] = {n: ["youtube_tag"] for n in videos}
    phrases: dict[str, list[str]] = {}
    enough = {p: ids for p, ids in phrase_videos.items() if len(set(ids)) >= config.min_videos}
    for phrase, ids in sorted(enough.items(), key=lambda kv: (-len(kv[0]), kv[0])):
        same = set(ids)
        # 더 긴 구가 같은 영상들에만 나오면 짧은 구는 그 긴 구의 조각이다
        if any(
            f" {phrase} " in f" {longer} " and set(other) == same
            for longer, other in enough.items()
            if len(longer) > len(phrase)
        ):
            continue
        tag_name = next((n for n in videos if _norm(n) == _norm(phrase)), phrase)
        mine = videos.setdefault(tag_name, [])
        mine.extend(v for v in ids if v not in mine)
        mine.sort(key=lambda v: rank_of.get(v, 10**9))
        best_rank[tag_name] = rank_of.get(mine[0], 10**9)
        matched.setdefault(tag_name, [])
        phrases.setdefault(tag_name, []).append(phrase)
        kinds = origins.setdefault(tag_name, [])
        if "youtube_title" not in kinds:
            kinds.append("youtube_title")
    picked = [
        TagCount(
            name,
            tuple(ids),
            tuple(matched.get(name, [])),
            tuple(phrases.get(name, [])),
            tuple(origins.get(name, ["youtube_tag"])),
        )
        for name, ids in videos.items()
        if len(ids) >= config.min_videos
    ]
    picked.sort(key=lambda c: (-c.video_count, best_rank[c.keyword], c.keyword))
    auto_dropped = sorted(
        ((common[n], len(v)) for n, v in dropped.items() if len(v) >= config.min_videos),
        key=lambda t: (-t[1], t[0].name),
    )
    return Picked(picked[:limit], auto_dropped)


def snapshot_cutoff(end_date: date | None) -> datetime | None:
    """지난 기간 판정의 스냅샷 기준 시각.

    판정 마지막 날(한국 날짜)이 끝나는 시각. `end_date`가 없으면 None(지금).
    """
    if end_date is None:
        return None
    day_end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=KST) + timedelta(days=1)
    return day_end.astimezone(UTC)


def _snapshot_problem(
    snapshot: PopularSnapshot | None,
    config: CandidateConfig,
    now: datetime,
    cutoff: datetime | None = None,
) -> str | None:
    """`cutoff`(지난 기간 판정)가 있으면 그 시각 기준으로 나이를 잰다. 없으면 지금 기준."""
    when = cutoff or now
    if snapshot is None:
        if cutoff is not None:
            return (
                f"그 시점({cutoff.astimezone(KST).date().isoformat()} 0시 KST) 이전"
                " YouTube 스냅샷이 없음 (지난 기간은 그때 스냅샷의 태그로만 본다)"
            )
        return "YouTube 스냅샷이 없음 (인기 영상을 저장한 기록이 없음, data youtube_collect)"
    age = when - snapshot.collected_at
    if age > timedelta(hours=config.max_age_hours):
        hours = age.total_seconds() / 3600
        base = "판정 기간 끝" if cutoff is not None else "지금"
        return (
            f"YouTube 스냅샷이 오래됨: 마지막 수집 {snapshot.collected_at.isoformat()}"
            f" ({base}보다 {hours:.1f}시간 전, 기준 {config.max_age_hours:g}시간)"
        )
    if not snapshot.videos:
        return "YouTube 스냅샷에 인기 영상이 없음"
    return None


def _summary(candidates: list[TrendCandidate], videos: int) -> str:
    rising = [c.keyword for c in candidates if c.naver.status == "rising"]
    unknown = sum(c.naver.status == "unknown" for c in candidates)
    text = (
        f"YouTube 인기 영상 {videos}개의 태그에서 후보 {len(candidates)}개,"
        f" 네이버 급상승 {len(rising)}개"
    )
    if rising:
        text += f" ({', '.join(rising)})"
    if unknown:
        text += f", 판정 불가 {unknown}개"
    return text + "."


@dataclass(frozen=True)
class CandidatesResult:
    output: TrendCandidatesOutput
    evidence: Evidence


async def naver_verdict(
    query: TrendScanInput, connector: NaverDatalabConnector, settings: Settings
) -> TrendVerdict:
    """그룹 하나를 네이버에서 읽어 trend_scan과 같은 함수로 판정한다 (content_recommend도 쓴다).

    못 읽으면 이 후보만 unknown이다.
    """
    name = query.keyword_groups[0].group_name
    try:
        response = await connector.search_trend(query)
    except ConnectorUnavailable as error:
        return TrendVerdict(
            group_name=name, status="unknown", unknown_reason=f"{NAVER_FAILED} [{error.code}]"
        )
    results = judge(query, response, settings).results
    if not results:
        return TrendVerdict(
            group_name=name, status="unknown", unknown_reason="네이버 응답에 이 키워드가 없음"
        )
    if results[0].status != "unknown":
        BOARD.record_verdict([name, *query.keyword_groups[0].keywords], results[0])
    return results[0]


def pick_verdict(
    group: TrendVerdict, names: list[TrendVerdict]
) -> tuple[TrendVerdict, Literal["group", "name"], list[str]]:
    """묶음 그룹 판정과 이름별 판정에서 후보의 판정을 고른다.

    - 이름별로 급상승인 이름 목록(`rising`)은 늘 남긴다
    - 묶음 그룹이 급상승이면 그 판정을 쓴다(`group`)
    - 묶음은 급상승이 아닌데(평범·판정 불가) 이름 하나라도 급상승이면, 급상승인 이름 중 z가 가장
      큰 판정을 올린다(`name`). 작은 이름의 급상승이 큰 이름에 묻히지 않게 한다
    """
    rising = [v for v in names if v.status == "rising"]
    rising_names = [v.group_name for v in rising]
    if group.status == "rising" or not rising:
        return group, "group", rising_names
    best = max(rising, key=lambda v: v.evidence.score or 0.0)
    return best, "name", rising_names


@dataclass(frozen=True)
class TrendingPick:
    keyword: str  # 정리한 이름 (별칭 표가 있으면 대표 이름)
    item_keyword: str  # 피드에 나온 그대로
    rank: int
    traffic: str | None


def pick_trending(snapshot: TrendingSnapshot, config: CandidateConfig) -> list[TrendingPick]:
    """인기 검색어를 태그와 같은 규칙(정리·별칭·제외 목록)으로 거른다.

    피드 순위 순, 이름마다 한 번.
    """
    out: list[TrendingPick] = []
    seen: set[str] = set()
    for item in snapshot.items:
        tag = clean_tag(item.keyword)
        name = config.canonical(tag)
        if not tag or tag in config.exclude_tags or name in config.exclude_tags:
            continue
        if _norm(name) in seen:
            continue
        seen.add(_norm(name))
        out.append(TrendingPick(name, item.keyword, item.rank, item.traffic))
    return out


def trending_db(env: Mapping[str, str]) -> str:
    """인기 검색어 DB: `DATA_TRENDING_DB`, 없으면 YouTube 스냅샷 DB와 같은 폴더의 기본 파일."""
    if env.get(TRENDING_DB_ENV):
        return str(env[TRENDING_DB_ENV])
    youtube = Path(env.get(DB_ENV) or str(DEFAULT_DB))
    return str(youtube.parent / "trending_snapshots.db")


def _trending_problem(
    snapshot: TrendingSnapshot | None,
    config: CandidateConfig,
    now: datetime,
    cutoff: datetime | None,
) -> str | None:
    when = cutoff or now
    if snapshot is None:
        if cutoff is not None:
            return (
                f"그 시점({cutoff.astimezone(KST).date().isoformat()} 0시 KST) 이전"
                " 인기 검색어 스냅샷이 없음"
            )
        return "인기 검색어 스냅샷이 없음 (수집 기록이 없음, data trending_collect)"
    age = when - snapshot.collected_at
    if age > timedelta(hours=config.trending_max_age_hours):
        hours = age.total_seconds() / 3600
        base = "판정 기간 끝" if cutoff is not None else "지금"
        return (
            f"인기 검색어 스냅샷이 오래됨: 마지막 수집 {snapshot.collected_at.isoformat()}"
            f" ({base}보다 {hours:.1f}시간 전, 기준 {config.trending_max_age_hours:g}시간)"
        )
    if not snapshot.items:
        return "인기 검색어 스냅샷에 검색어가 없음"
    return None


@dataclass
class Judged:
    keywords: list[str]  # 네이버 그룹 키워드 (대표 이름 + 별칭)
    verdict: TrendVerdict
    source: Literal["group", "name"]
    rising: list[str]
    names: list[TrendVerdict]
    group: TrendVerdict | None = None  # 묶음 그룹 판정 (상관을 잴 때 이 시계열을 쓴다)


async def judge_name(
    keyword: str,
    config: CandidateConfig,
    connector: NaverDatalabConnector,
    settings: Settings,
    start: date,
    end: date,
    queries: list[str],
) -> tuple[Judged, int]:
    """후보 하나: 묶음 그룹 한 번 + 별칭이 있으면 이름마다 한 번. (판정, 네이버 부른 수)."""
    keywords = config.keywords(keyword)
    query = TrendScanInput(
        keyword_groups=[TrendKeywordGroup(group_name=keyword, keywords=keywords)],
        start_date=start,
        end_date=end,
    )
    queries.append(
        f"naver search-trend group={keyword} keywords={','.join(keywords)} {start}~{end} date"
    )
    group = await naver_verdict(query, connector, settings)
    requests = 1
    names: list[TrendVerdict] = []
    if len(keywords) > 1:  # 별칭이 있으면 이름마다 따로도 판정한다 (작은 이름이 묻히지 않게)
        for name in keywords:
            one = TrendScanInput(
                keyword_groups=[TrendKeywordGroup(group_name=name, keywords=[name])],
                start_date=start,
                end_date=end,
            )
            queries.append(f"naver search-trend group={name} {start}~{end} date")
            names.append(await naver_verdict(one, connector, settings))
            requests += 1
    verdict, source, rising = pick_verdict(group, names)
    return Judged(keywords, verdict, source, rising, names, group), requests


# ---------------------------------------------------------------- 이름 자동 묶기


def log_correlation(a: TrendVerdict, b: TrendVerdict) -> tuple[float | None, int]:
    """두 판정 시계열의 같은 날 로그값 피어슨 상관과 쓴 날 수. 값이 0이거나 없는 날은 뺀다.

    네이버 값은 요청마다 배수만 다르다(로그에서는 더하기라 상관이 바뀌지 않음). 그래서 따로 받은
    두 요청을 비교할 수 있다. 날이 2일 미만이거나 한쪽이 평평하면 None.
    """
    left = {p.day: p.value for p in a.series if p.value > 0}
    right = {p.day: p.value for p in b.series if p.value > 0}
    days = sorted(set(left) & set(right))
    if len(days) < 2:
        return None, len(days)
    xs = [math.log(left[d]) for d in days]
    ys = [math.log(right[d]) for d in days]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None, len(days)
    r = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sx * sy)
    return round(r, 4), len(days)


def shared_videos(
    history: Sequence[HistoryVideo], names: Sequence[str], config: CandidateConfig
) -> dict[frozenset[str], int]:
    """쌓인 인기 영상에서 후보 이름 두 개가 같은 영상(태그·제목 구)에 함께 나온 영상 수.

    태그는 정리·별칭 표를 거친 이름이 후보 이름과 같을 때, 제목 구는 공백만 다를 때도 센다
    (`pick_names`가 구를 태그 후보에 합치는 규칙과 같다). 공백만 다른 후보가 둘이면 둘 다에 센다.
    """
    wanted = set(names)
    by_norm: dict[str, list[str]] = {}
    for n in names:
        by_norm.setdefault(_norm(n), []).append(n)
    out: dict[frozenset[str], int] = {}
    for video in history:
        found = {
            name for name in (config.canonical(clean_tag(t)) for t in video.tags) if name in wanted
        }
        if config.titles:
            words = (config.title_min_words, config.title_max_words)
            for phrase in title_phrases(video.title, *words):
                found.update(by_norm.get(_norm(config.canonical(phrase)), []))
        ordered = sorted(found)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                pair = frozenset((a, b))
                out[pair] = out.get(pair, 0) + 1
    return out


def name_pairs(
    judged: Sequence[tuple[str, Judged]],
    shared: Mapping[frozenset[str], int],
    config: CandidateConfig,
) -> list[NamePairEvidence]:
    """함께 나온 적이 있는 후보 쌍마다 상관을 재서 묶음(applied)·제안을 고른다. 상관 큰 순."""
    out: list[NamePairEvidence] = []
    for i, (a, ja) in enumerate(judged):
        for b, jb in judged[i + 1 :]:
            together = shared.get(frozenset((a, b)), 0)
            if together < config.group_min_shared or ja.group is None or jb.group is None:
                continue
            r, days = log_correlation(ja.group, jb.group)
            if r is None or days < config.group_min_days or r < config.group_suggest_corr:
                continue
            out.append(
                NamePairEvidence(
                    names=[a, b],
                    correlation=r,
                    days=days,
                    shared_videos=together,
                    applied=r >= config.group_merge_corr,
                )
            )
    out.sort(key=lambda e: (-e.correlation, e.names))
    return out


def components(names: Sequence[str], pairs: Sequence[NamePairEvidence]) -> list[list[str]]:
    """묶음(applied) 쌍으로 이어진 이름 덩어리 (2개 이상만, 원래 순서)."""
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in pairs:
        if pair.applied:
            a, b = pair.names
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)
    return [g for g in groups.values() if len(g) > 1]


def _trending_summary(found: list[TrendingCandidate], attached: int) -> str:
    rising = [c.keyword for c in found if c.naver.status == "rising"]
    text = f" 인기 검색어 후보 {len(found)}개(YouTube 후보와 겹친 {attached}개 제외)"
    text += f", 그중 네이버 급상승 {len(rising)}개"
    if rising:
        text += f" ({', '.join(rising)})"
    return text + "."


async def find_candidates(
    request: TrendCandidatesRequest,
    connector: NaverDatalabConnector,
    *,
    env: Mapping[str, str],
    settings: Settings = DEFAULT_SETTINGS,
    now: datetime | None = None,
) -> CandidatesResult:
    """최신 스냅샷(YouTube·인기 검색어)에서 후보를 뽑아 후보마다 네이버로 판정한다.

    상태를 바꾸지 않는다.
    """
    now = now or datetime.now(UTC)
    config = load_config(env)
    db = env.get(DB_ENV) or str(DEFAULT_DB)
    # 지난 기간(end_date를 준 요청)은 그 기간 끝 이전의 스냅샷을 쓴다. 비우면 지금 가장 최근 스냅샷
    cutoff = snapshot_cutoff(request.end_date)
    snapshot = latest_popular(db, cutoff)
    snapshot_query = f"youtube snapshot popular latest db={Path(db).name}"
    why = _snapshot_problem(snapshot, config, now, cutoff)

    tdb = trending_db(env)
    tsnap = latest_trending(tdb, config.trending_geo, cutoff)
    twhy = _trending_problem(tsnap, config, now, cutoff)
    tpicks = pick_trending(tsnap, config) if tsnap is not None and not twhy else []

    end = request.end_date or yesterday_kst(now)
    start = end - timedelta(days=REQUEST_DAYS - 1)
    queries = [snapshot_query]
    if snapshot is not None and not why:
        queries = [f"{snapshot_query} at={snapshot.collected_at.isoformat()}"]
    if tsnap is not None:
        queries.append(
            f"trending snapshot geo={tsnap.geo} at={tsnap.collected_at.isoformat()}"
            f" db={Path(tdb).name}"
        )
    requests = 0

    tags: list[TagCount] = []
    auto_excluded: list[AutoExcludedTerm] = []
    basis: str | None = None
    if snapshot is not None and not why:
        history = popular_history(db, snapshot.collected_at) if config.auto_exclude else []
        common = common_terms(history, config)
        if config.auto_exclude:
            days = len({d for v in history for d in v.days})
            basis = f"쌓인 인기 영상 {len(history)}개, 기록이 있는 날 {days}일"
        limit = request.max_candidates or config.max_candidates
        found = pick_names(snapshot, config, limit, common)
        tags = found.candidates
        auto_excluded = [
            AutoExcludedTerm(
                name=term.name,
                source=term.source,  # type: ignore[arg-type]  # youtube_tag | youtube_title
                snapshot_videos=n,
                videos=term.videos,
                total_videos=term.total_videos,
                doc_ratio=round(term.doc_ratio, 4),
                categories=term.categories,
                days=term.days,
                total_days=term.total_days,
                reason=term.reason(),
            )
            for term, n in found.auto_dropped
        ]
    youtube_names = {_norm(t.keyword) for t in tags}
    attach = {_norm(p.keyword): p for p in tpicks if _norm(p.keyword) in youtube_names}
    alone = [p for p in tpicks if _norm(p.keyword) not in youtube_names][: config.trending_max]

    def evidence_of(pick: TrendingPick, at: datetime) -> TrendingSearchEvidence:
        return TrendingSearchEvidence(
            rank=pick.rank, traffic=pick.traffic, snapshot_at=at, keyword=pick.item_keyword
        )

    judged_tags: list[tuple[TagCount, Judged]] = []
    for tag in tags:
        judged, n = await judge_name(tag.keyword, config, connector, settings, start, end, queries)
        requests += n
        judged_tags.append((tag, judged))

    pairs: list[NamePairEvidence] = []
    merged_names: dict[str, list[str]] = {}  # 대표 이름 → 자동으로 묶인 다른 이름
    if config.auto_group and snapshot is not None and len(judged_tags) > 1:
        history = popular_history(db, snapshot.collected_at)
        names = [t.keyword for t, _ in judged_tags]
        pairs = name_pairs(
            [(t.keyword, j) for t, j in judged_tags], shared_videos(history, names, config), config
        )
        rank_of = {v.video_id: v.rank for v in snapshot.videos}
        by_name = {t.keyword: (t, j) for t, j in judged_tags}
        for group in components(names, pairs):
            head, rest = group[0], group[1:]  # 앞(영상 많은 순)이 대표 이름
            parts = [by_name[n] for n in group]
            keywords: list[str] = []
            for _, j in parts:
                keywords.extend(k for k in j.keywords if k not in keywords)
            keywords = keywords[:MAX_KEYWORDS]
            query = TrendScanInput(
                keyword_groups=[TrendKeywordGroup(group_name=head, keywords=keywords)],
                start_date=start,
                end_date=end,
            )
            queries.append(
                f"naver search-trend group={head} keywords={','.join(keywords)} {start}~{end} date"
                " (자동 묶음)"
            )
            combined = await naver_verdict(query, connector, settings)
            requests += 1
            # 이름별 판정: 각 후보의 이름별 판정(별칭이 있으면) 또는 그 후보 그룹 판정
            one_by_one: list[TrendVerdict] = []
            for _, j in parts:
                one_by_one.extend(j.names or ([j.group] if j.group else []))
            verdict, source, rising = pick_verdict(combined, one_by_one)
            ids: list[str] = []
            matched: list[str] = []
            phrases: list[str] = []
            origins: list[str] = []
            for t, _ in parts:
                ids.extend(v for v in t.video_ids if v not in ids)
                matched.extend(m for m in t.matched if m not in matched)
                phrases.extend(m for m in t.phrases if m not in phrases)
                origins.extend(o for o in t.origins if o not in origins)
            ids.sort(key=lambda v: rank_of.get(v, 10**9))
            tag = TagCount(head, tuple(ids), tuple(matched), tuple(phrases), tuple(origins))
            judged = Judged(keywords, verdict, source, rising, one_by_one, combined)
            by_name[head] = (tag, judged)
            for n in rest:
                by_name.pop(n)
            merged_names[head] = rest
        judged_tags = [by_name[n] for n in names if n in by_name]

    candidates: list[TrendCandidate] = []
    thumbs = thumbs_of(
        db, [v for tag, _ in judged_tags for v in tag.video_ids[: config.rep_videos]]
    )
    for tag, judged in judged_tags:
        hit = attach.get(_norm(tag.keyword))
        candidates.append(
            TrendCandidate(
                keyword=tag.keyword,
                youtube=YouTubeTagEvidence(
                    video_count=tag.video_count,
                    snapshot_at=snapshot.collected_at if snapshot else now,
                    video_ids=list(tag.video_ids[: config.rep_videos]),
                    video_links=links(tag.video_ids[: config.rep_videos], thumbs),
                    matched_tags=list(tag.matched),
                    matched_phrases=list(tag.phrases),
                ),
                naver=judged.verdict,
                aliases=judged.keywords[1:],
                name_verdicts=judged.names,
                rising_names=judged.rising,
                naver_from=judged.source,
                origins=[*tag.origins, "trending_search"] if hit else list(tag.origins),  # type: ignore[list-item]
                trending=evidence_of(hit, tsnap.collected_at) if hit and tsnap else None,
                auto_grouped=merged_names.get(tag.keyword, []),
            )
        )
    trending: list[TrendingCandidate] = []
    for pick in alone:
        assert tsnap is not None  # alone이 있으면 스냅샷이 있다
        judged, n = await judge_name(pick.keyword, config, connector, settings, start, end, queries)
        requests += n
        trending.append(
            TrendingCandidate(
                keyword=pick.keyword,
                trending=evidence_of(pick, tsnap.collected_at),
                naver=judged.verdict,
                aliases=judged.keywords[1:],
                name_verdicts=judged.names,
                rising_names=judged.rising,
                naver_from=judged.source,
            )
        )

    tail = "" if twhy else _trending_summary(trending, len(attach))
    trending_status: Literal["known", "unknown"] = "unknown" if twhy else "known"
    if snapshot is None or why:
        why = why or ""
        summary = f"확인할 수 없음: {why}" + ("." + tail if tail else "")
        output = TrendCandidatesOutput(
            status="unknown",
            unknown_reason=why,
            snapshot_at=snapshot.collected_at if snapshot else None,
            snapshot_videos=len(snapshot.videos) if snapshot else 0,
            start_date=start if requests else None,
            end_date=end if requests else None,
            naver_calls=requests,
            summary=summary,
            trending_candidates=trending,
            trending_status=trending_status,
            trending_reason=twhy,
            trending_snapshot_at=tsnap.collected_at if tsnap else None,
        )
    else:
        summary = _summary(candidates, len(snapshot.videos)) + tail
        if auto_excluded:
            summary += (
                f" 자동 제외 {len(auto_excluded)}개 ({', '.join(a.name for a in auto_excluded)})."
            )
        applied = [e for e in pairs if e.applied]
        for e in applied:
            BOARD.record_pair(e.names[0], e.names[1])
        if applied:
            summary += f" 자동 묶음 {len(applied)}쌍."
        output = TrendCandidatesOutput(
            auto_groups=applied,
            group_suggestions=[e for e in pairs if not e.applied],
            auto_excluded=auto_excluded,
            exclusion_basis=basis,
            status="known",
            snapshot_at=snapshot.collected_at,
            snapshot_videos=len(snapshot.videos),
            start_date=start,
            end_date=end,
            candidates=candidates,
            naver_calls=requests,
            summary=summary,
            trending_candidates=trending,
            trending_status=trending_status,
            trending_reason=twhy,
            trending_snapshot_at=tsnap.collected_at if tsnap else None,
        )
    failed = [
        c.keyword
        for c in [*candidates, *trending]
        if (c.naver.unknown_reason or "").startswith(NAVER_FAILED)
    ]
    notes = [summary] + (["네이버를 읽지 못한 후보: " + ", ".join(failed)] if failed else [])
    if twhy:
        notes.append(f"인기 검색어 후보 없음: {twhy}")
    evidence = Evidence(queries=queries, data_summary="\n".join(notes), collected_at=now)
    return CandidatesResult(output, evidence)
