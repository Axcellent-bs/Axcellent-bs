"""인스타 "왜 떴나"(레이더 묶음)·"왜 뜨나"(인기 오디오) 근거. 숫자는 전부 코드가 계산한다.

LLM은 레이더 묶음 결론 한 줄만 쓴다(`instagram.describe_groups`, 묶음 설명과 같은 1번 호출).
모르면 값 없음 + 이유이고 0으로 채우지 않는다. 판정(2.5배·7일·min_accounts)·묶기·광고 재료는
건드리지 않는다.

레이더 묶음 근거 (`group_facts`, 비교 기준 = 그 묶음 계정들의 최근 게시물 전체):
- spread: 계정 수, 묶음 게시물 첫~마지막 게시 간격(시간)
- format: 묶음에서 가장 많은 형식(릴스·영상·사진·캐러셀) 비율 vs 평소 비율
- engagement: 댓글÷좋아요 중앙값 vs 평소 (둘 중 하나라도 없는 게시물은 뺌, 다 없으면 줄을 뺌)
- caption: 첫 줄이 물음표로 끝나는 비율 vs 평소, 캡션 길이 중앙값 vs 평소
- time: 한국 시각 3시간 구간 중 가장 많은 구간과 그 비율 vs 평소
- 평소와 차이가 작으면 줄을 뺀다: 배수 차 < `MIN_FACT_RATIO`(1.5), 비율은 거기에 더해 차이
  < `MIN_SHARE_GAP`(20%p)이면 뺀다 → "다른 점"만 남는다
- cross: 유튜브(trend_memes `load_videos` + `units_of`, 이번 7일·직전 7일 같은 단위 키를 쓴 쇼츠
  채널 수), 네이버(trend_memes `search_ratios`, 하루 캐시·trend_age 예산 그대로. 실패는 그 줄만)
- unknown: 쓴 오디오는 공식 API에 없다 (고정)

게시물 한 줄 근거 (`standout`): 그 계정 평소와 가장 크게 다른 점 1개 (코드 템플릿).

인기 오디오 근거 (`audio_why`): rank(7일 전 → 오늘), youtube(제목·태그에 곡명이 든 쇼츠 수,
이번 7일 vs 직전 7일, 2글자 이하 곡명은 가수 이름이 같이 있을 때만), search(네이버, 상위 5곡),
unknown(레이더 계정의 사용 여부는 확인 불가). 오디오 결론 문장은 만들지 않는다.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_contracts.schemas.data import (
    InstagramAudioOutput,
    InstagramRadarPost,
    InstagramWhyFact,
    MemeSearch,
)
from data_agent import instagram_store as st

MIN_FACT_RATIO = 1.5  # 평소 대비 배수가 이보다 작으면(또는 1/이보다 크면) 다른 점이 아님
MIN_SHARE_GAP = 0.20  # 비율 비교는 차이가 이보다 작아도 다른 점이 아님 (20%p)
SEARCH_AUDIO_TOP = 5  # 네이버로 볼 인기 오디오 수 (순위 순)
SHORT_TITLE = 2  # 이 글자 수 이하 곡명은 가수 이름이 같이 있을 때만 센다 (오탐 방지)
WEEK = timedelta(days=7)
NO_AUDIO = "쓴 오디오: 공식 API가 게시물 오디오를 주지 않음"
AUDIO_USE_UNKNOWN = "레이더 계정 중 이 곡을 쓴 게시물: 공식 API로 확인 불가"
SLOTS = ("한밤", "새벽", "아침", "오전", "낮", "오후", "저녁", "밤")  # 한국 시각 3시간 구간
FORMATS = {"VIDEO": "영상", "IMAGE": "사진", "CAROUSEL_ALBUM": "캐러셀"}

Fact = tuple[InstagramWhyFact, str | None]  # (근거, LLM에 줄 숫자 없는 말. 없으면 안 줌)


# ---------------------------------------------------------------- 게시물 값


def _posted(m: Mapping[str, Any]) -> datetime | None:
    from data_agent.skills.instagram import _posted as posted

    return posted(m)


def fmt_of(m: Mapping[str, Any]) -> str | None:
    """형식 이름: 릴스 · 영상 · 사진 · 캐러셀 (media_type이 없던 예전 행이면 None)."""
    if m.get("media_product_type") == "REELS":
        return "릴스"
    return FORMATS.get(str(m.get("media_type") or ""))


def engagement(m: Mapping[str, Any]) -> float | None:
    likes, comments = m.get("like_count"), m.get("comments_count")
    if not isinstance(likes, int) or not isinstance(comments, int) or likes <= 0:
        return None
    return comments / likes


def asks_question(m: Mapping[str, Any]) -> bool | None:
    cap = m.get("caption")
    if cap is None:
        return None
    first = next((line.strip() for line in str(cap).splitlines() if line.strip()), "")
    return first.endswith(("?", "？"))


def caption_len(m: Mapping[str, Any]) -> int | None:
    cap = m.get("caption")
    return None if cap is None else len(str(cap).strip())


def slot_of(m: Mapping[str, Any]) -> int | None:
    at = _posted(m)
    return None if at is None else (at.astimezone(UTC) + timedelta(hours=9)).hour // 3


def slot_name(slot: int) -> str:
    return f"{SLOTS[slot]}({slot * 3}~{slot * 3 + 3}시)"


# ---------------------------------------------------------------- 비교 규칙


def ratio_differs(ratio: float | None) -> bool:
    if ratio is None:
        return True  # 평소가 0이고 이번은 0이 아님
    return ratio >= MIN_FACT_RATIO or (ratio > 0 and 1 / ratio >= MIN_FACT_RATIO) or ratio == 0


def share_differs(now: float, base: float) -> bool:
    if abs(now - base) < MIN_SHARE_GAP - 1e-9:  # 부동소수 오차 (0.3 − 0.1 = 0.1999…)
        return False
    return ratio_differs(now / base if base > 0 else None)


def _pct(v: float) -> str:
    return f"{round(v * 100)}%"


def _ratio(now: float, base: float) -> float | None:
    return round(now / base, 2) if base > 0 else None


def _known(values: Iterable[Any]) -> list[Any]:
    return [v for v in values if v is not None]


def _share_fact(kind: str, now_vals: Sequence[Any], base_vals: Sequence[Any], pick: Any,
                label: str, more: str, less: str) -> Fact | None:  # fmt: skip
    """`pick` 값의 비율: 이번 vs 평소. 어느 쪽이든 아는 값이 없거나 차이가 작으면 None."""
    now_k, base_k = _known(now_vals), _known(base_vals)
    if not now_k or not base_k:
        return None
    a = sum(1 for v in now_k if v == pick) / len(now_k)
    b = sum(1 for v in base_k if v == pick) / len(base_k)
    if not share_differs(a, b):
        return None
    fact = InstagramWhyFact(kind=kind, label=label, value=_pct(a), baseline=_pct(b),  # type: ignore[arg-type]
                            ratio=_ratio(a, b))  # fmt: skip
    return fact, (more if a > b else less)


def _median_fact(kind: str, now_vals: Sequence[float | None], base_vals: Sequence[float | None],
                 label: str, show: Any, more: str, less: str) -> Fact | None:  # fmt: skip
    now_k, base_k = _known(now_vals), _known(base_vals)
    if not now_k or not base_k:
        return None
    a, b = statistics.median(now_k), statistics.median(base_k)
    if a == 0 and b == 0:
        return None
    r = _ratio(a, b)
    if not ratio_differs(r):
        return None
    fact = InstagramWhyFact(kind=kind, label=label, value=show(a), baseline=show(b), ratio=r)  # type: ignore[arg-type]
    return fact, (more if a > b else less)


# ---------------------------------------------------------------- 레이더 묶음


Media = Sequence[Mapping[str, Any]]


def compare_facts(now: Media, base: Media) -> list[Fact]:
    """평소(base)와 다른 점만: format · engagement · caption(질문·길이) · time."""
    out: list[Fact | None] = []
    fmts = _known(fmt_of(m) for m in now)
    if fmts:
        top = sorted(Counter(fmts).items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        out.append(_share_fact("format", [fmt_of(m) for m in now], [fmt_of(m) for m in base], top,
                               f"형식: {top} 비율", f"{top} 형식이 평소보다 많음",
                               f"{top} 형식이 평소보다 적음"))  # fmt: skip
    out.append(_median_fact(
        "engagement", [engagement(m) for m in now], [engagement(m) for m in base],
        "댓글률 (댓글÷좋아요, 중앙값)", lambda v: f"{v * 100:.1f}%",
        "댓글 비율이 평소보다 높음", "댓글 비율이 평소보다 낮음"))  # fmt: skip
    out.append(_share_fact(
        "caption", [asks_question(m) for m in now], [asks_question(m) for m in base], True,
        "첫 줄이 질문(?)인 비율", "첫 줄을 질문으로 쓴 게시물이 평소보다 많음",
        "첫 줄을 질문으로 쓴 게시물이 평소보다 적음"))  # fmt: skip
    out.append(_median_fact(
        "caption", [caption_len(m) for m in now], [caption_len(m) for m in base],
        "캡션 길이 (중앙값)", lambda v: f"{round(v)}자", "캡션이 평소보다 김",
        "캡션이 평소보다 짧음"))  # fmt: skip
    slots = _known(slot_of(m) for m in now)
    if slots:
        top_slot = sorted(Counter(slots).items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        out.append(_share_fact(
            "time", [slot_of(m) for m in now], [slot_of(m) for m in base], top_slot,
            f"올린 시간: {slot_name(top_slot)}, 한국", f"{SLOTS[top_slot]} 시간대에 몰림",
            f"{SLOTS[top_slot]} 시간대가 평소보다 적음"))  # fmt: skip
    return [f for f in out if f is not None]


def spread_fact(posts: Sequence[InstagramRadarPost]) -> Fact:
    accounts = len({p.account for p in posts})
    times = sorted(p.posted_at for p in posts if p.posted_at is not None)
    if len(times) < 2:
        fact = InstagramWhyFact(kind="spread", label="퍼짐", value=f"{accounts}곳",
                                reason="게시 시각이 둘 이상 있어야 간격을 셈")  # fmt: skip
        return fact, "여러 계정에서 같이 뜸"
    hours = (times[-1] - times[0]).total_seconds() / 3600
    value = f"{accounts}곳 · 첫~마지막 {hours:.0f}시간"
    word = "여러 계정이 하루 안에 같이 올림" if hours <= 24 else "여러 계정이 며칠에 걸쳐 올림"
    return InstagramWhyFact(kind="spread", label="퍼짐", value=value), word


def group_key(name: str) -> str:
    """묶음 이름 → trend_memes 단위 키 (hashtag:… · phrase:…)."""
    from data_agent.skills.trend_memes import normalize

    if name.startswith("#"):
        return f"hashtag:{normalize(name).replace(' ', '')}"
    return f"phrase:{normalize(name)}"


@dataclass(frozen=True)
class VideoUnits:
    channel: str | None
    published_at: datetime
    keys: frozenset[str]
    text: str  # 곡명 찾기용 (제목 + 태그, 공백 없앤 정규화)


def video_units(videos: Sequence[Any]) -> list[VideoUnits]:
    from data_agent.skills.trend_memes import compact, units_of

    out = []
    for v in videos:
        if v.published_at is None:
            continue
        keys = frozenset(units_of(v.title, v.tags))
        out.append(VideoUnits(v.channel, v.published_at, keys,
                              compact(" ".join([v.title or "", *v.tags]))))  # fmt: skip
    return out


def _windows(now: datetime) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
    return (now - WEEK, now), (now - 2 * WEEK, now - WEEK)


def youtube_fact(name: str, units: Sequence[VideoUnits], now: datetime,
                 loaded: bool) -> Fact:  # fmt: skip
    label = "유튜브 쇼츠: 같은 해시태그·구절 채널 수 (이번 7일 · 직전 7일)"
    if not loaded:
        fact = InstagramWhyFact(kind="cross", label=label, reason="유튜브 쇼츠 표본이 없음")
        return fact, None
    key = group_key(name)
    (a0, a1), (b0, b1) = _windows(now)
    cur = {u.channel for u in units if key in u.keys and u.channel and a0 <= u.published_at < a1}
    prev = {u.channel for u in units if key in u.keys and u.channel and b0 <= u.published_at < b1}
    fact = InstagramWhyFact(kind="cross", label=label, value=f"{len(cur)}개 채널",
                            baseline=f"{len(prev)}개 채널",
                            ratio=_ratio(len(cur), len(prev)))  # fmt: skip
    if not cur:
        return fact, "유튜브 쇼츠에는 아직 없음"
    return fact, ("유튜브 쇼츠에서도 지난주보다 늘어남" if len(cur) > len(prev)
                  else "유튜브 쇼츠에서는 늘지 않음")  # fmt: skip


SEARCH_WORD = {"rising": "상승", "normal": "보통"}


def search_fact(label: str, s: MemeSearch, kind: str = "cross") -> Fact:
    if s.status in SEARCH_WORD:
        fact = InstagramWhyFact(kind=kind, label=label, value=SEARCH_WORD[s.status],  # type: ignore[arg-type]
                                baseline="평소 검색량", ratio=s.ratio)  # fmt: skip
        word = "네이버 검색도 오르는 중" if s.status == "rising" else "네이버 검색은 평소 수준"
        return fact, word
    why = s.reason or ("확인 안 함" if s.status == "not_checked" else "모름")
    return InstagramWhyFact(kind=kind, label=label, reason=why), None  # type: ignore[arg-type]


async def naver(names: Sequence[tuple[str, Sequence[str]]], connector: Any,
                env: Mapping[str, str], now: datetime,
                age_store: Any = None) -> list[MemeSearch]:  # fmt: skip
    """trend_memes `search_ratios` 그대로 (하루 캐시·trend_age 예산 공유). 실패는 줄마다 이유."""
    from data_agent.skills.trend_memes import search_ratios

    if not names:
        return []
    if connector is None:
        return [MemeSearch(status="unknown", reason="네이버 커넥터가 없음")] * len(names)
    try:
        got, _, _ = await search_ratios(names, connector, env, now, age_store=age_store)
    except Exception as error:  # noqa: BLE001 - 네이버 실패는 그 줄만
        why = f"네이버를 읽지 못함 ({type(error).__name__})"
        return [MemeSearch(status="unknown", reason=why)] * len(names)
    return got


UNKNOWN_FACT: Fact = (InstagramWhyFact(kind="unknown", label=NO_AUDIO, reason=NO_AUDIO), None)


async def radar_facts(
    groups: Sequence[tuple[str, Sequence[str], Sequence[InstagramRadarPost]]],
    media: Mapping[str, Sequence[Mapping[str, Any]]],
    env: Mapping[str, str],
    now: datetime,
    *,
    connector: Any = None,
    videos: Sequence[Any] | None = None,
    age_store: Any = None,
) -> list[list[Fact]]:
    """묶음마다 근거 (spread · 다른 점 · cross 유튜브·네이버 · unknown)."""
    if not groups:
        return []
    by_id = {str(m["id"]): m for ms in media.values() for m in ms}
    if videos is None:
        from data_agent.skills.trend_memes import load_videos

        try:
            videos = load_videos(env, now)
        except Exception:  # noqa: BLE001 - 유튜브 표본을 못 읽으면 그 줄만 모름
            videos = []
    units = video_units(videos)
    searches = await naver([(name, list(members)) for name, members, _ in groups], connector,
                           env, now, age_store)  # fmt: skip
    out: list[list[Fact]] = []
    for (name, _, posts), s in zip(groups, searches, strict=True):
        accounts = {p.account for p in posts}
        now_media = [by_id[p.media_id] for p in posts if p.media_id in by_id]
        base = [m for a in accounts for m in media.get(a, [])]
        facts = [spread_fact(posts), *compare_facts(now_media, base),
                 youtube_fact(name, units, now, bool(units)),
                 search_fact("네이버 검색어 트렌드 (묶음 이름)", s), UNKNOWN_FACT]  # fmt: skip
        out.append(facts)
    return out


# ---------------------------------------------------------------- 게시물 한 줄


def standout(m: Mapping[str, Any], account_media: Sequence[Mapping[str, Any]]) -> str | None:
    """계정 평소와 가장 크게 다른 점 1개 (댓글률·형식·첫 줄 질문·시간·캡션 길이). 없으면 None."""
    others = [x for x in account_media if str(x.get("id")) != str(m.get("id"))] or account_media
    cands: list[tuple[float, int, str]] = []  # (점수, 순서, 문장)

    def add(score: float | None, text: str) -> None:
        if score is not None and score >= MIN_FACT_RATIO:
            cands.append((score, -len(cands), text))

    def share(pick: Any, fn: Any) -> float | None:
        known = _known(fn(x) for x in others)
        return sum(1 for v in known if v == pick) / len(known) if known else None

    e = engagement(m)
    base_e = _known(engagement(x) for x in others)
    if e is not None and base_e:
        b = statistics.median(base_e)
        if b > 0 and e > 0:
            add(max(e / b, b / e), f"댓글률 {e * 100:.1f}% · 평소 {b * 100:.1f}%")
    if (f := fmt_of(m)) is not None and (s := share(f, fmt_of)) is not None:
        add(1 / s if s > 0 else 99.0, f"{f} · 이 계정 평소 {_pct(s)}만 {f}")
    if asks_question(m) and (s := share(True, asks_question)) is not None:
        add(1 / s if s > 0 else 99.0, f"첫 줄이 질문 · 평소 {_pct(s)}")
    if (slot := slot_of(m)) is not None and (s := share(slot, slot_of)) is not None:
        add(1 / s if s > 0 else 99.0, f"{slot_name(slot)}에 올림 · 평소 {_pct(s)}")
    n = caption_len(m)
    base_n = _known(caption_len(x) for x in others)
    if n and base_n and (b := statistics.median(base_n)) > 0:
        add(max(n / b, b / n), f"캡션 {n}자 · 평소 {round(b)}자")
    if not cands:
        return None
    return max(cands)[2]


# ---------------------------------------------------------------- 인기 오디오


def title_hits(title: str, artist: str | None,
               units: Sequence[VideoUnits]) -> list[VideoUnits] | None:  # fmt: skip
    """곡명이 제목·태그에 든 쇼츠. 짧은 곡명은 가수 이름이 같이 있을 때만 (없으면 None)."""
    from data_agent.skills.trend_memes import compact

    t = compact(title)
    if not t:
        return None
    if len(t) <= SHORT_TITLE:
        a = compact(artist or "")
        if len(a) < 2:
            return None
        return [u for u in units if t in u.text and a in u.text]
    return [u for u in units if t in u.text]


def audio_youtube_fact(title: str | None, artist: str | None, units: Sequence[VideoUnits],
                       now: datetime, loaded: bool) -> InstagramWhyFact:  # fmt: skip
    label = "유튜브 쇼츠: 제목·태그에 곡명 (이번 7일 · 직전 7일)"
    if not loaded:
        return InstagramWhyFact(kind="youtube", label=label, reason="유튜브 쇼츠 표본이 없음")
    if not title:
        return InstagramWhyFact(kind="youtube", label=label, reason="곡명이 없음")
    hits = title_hits(title, artist, units)
    if hits is None:
        why = f"곡명이 {SHORT_TITLE}글자 이하라 가수 이름 없이는 세지 않음 (오탐 방지)"
        return InstagramWhyFact(kind="youtube", label=label, reason=why)
    (a0, a1), (b0, b1) = _windows(now)
    cur = sum(1 for u in hits if a0 <= u.published_at < a1)
    prev = sum(1 for u in hits if b0 <= u.published_at < b1)
    return InstagramWhyFact(kind="youtube", label=label, value=f"{cur}개", baseline=f"{prev}개",
                            ratio=_ratio(cur, prev))  # fmt: skip


SEARCH_LABEL = "네이버 검색어 트렌드 (곡명)"


async def audio_why(
    out: InstagramAudioOutput,
    env: Mapping[str, str],
    now: datetime,
    *,
    connector: Any = None,
    videos: Sequence[Any] | None = None,
    age_store: Any = None,
) -> None:
    """인기 오디오 항목마다 `why_facts` (rank · youtube · search · unknown). 결론 문장은 없음."""
    if not out.items or out.day is None:
        return
    days = st.audio_days(st.path_of(env, st.AUDIO_ENV), out.audio_type, out.day, days=8)
    week_ago = out.day - timedelta(days=7)
    old = {r["audio_id"]: r["rank"] for r in days.get(week_ago) or []}
    if videos is None:
        from data_agent.skills.trend_memes import load_videos

        try:
            videos = load_videos(env, now)
        except Exception:  # noqa: BLE001
            videos = []
    units = video_units(videos)
    top = sorted((it for it in out.items if it.title), key=lambda it: it.rank)[:SEARCH_AUDIO_TOP]
    searches = await naver([(str(it.title), []) for it in top], connector, env, now, age_store)
    by_search = {it.audio_id: s for it, s in zip(top, searches, strict=True)}
    for it in out.items:
        label = "순위 (7일 전 → 오늘)"
        if not days.get(week_ago):
            rank = InstagramWhyFact(kind="rank", label=label, value=f"{it.rank}위",
                                    reason="7일 전 목록이 없음 (수집 기록 부족)")  # fmt: skip
        elif it.audio_id in old:
            rank = InstagramWhyFact(kind="rank", label=label, value=f"{it.rank}위",
                                    baseline=f"{old[it.audio_id]}위")  # fmt: skip
        else:
            rank = InstagramWhyFact(kind="rank", label=label, value=f"{it.rank}위",
                                    baseline="7일 전 목록에 없음")  # fmt: skip
        if it.audio_id in by_search:
            search = search_fact(SEARCH_LABEL, by_search[it.audio_id], "search")[0]
        else:
            why = (f"상위 {SEARCH_AUDIO_TOP}곡만 확인 (네이버 예산)" if it.title
                   else "곡명이 없음")  # fmt: skip
            search = InstagramWhyFact(kind="search", label=SEARCH_LABEL, reason=why)
        it.why_facts = [
            rank, audio_youtube_fact(it.title, it.artist, units, now, bool(units)), search,
            InstagramWhyFact(kind="unknown", label=AUDIO_USE_UNKNOWN, reason=AUDIO_USE_UNKNOWN),
        ]  # fmt: skip
