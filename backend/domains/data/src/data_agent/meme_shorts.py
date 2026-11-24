"""밈 키워드 쇼츠 검색 (11/24 밈 소스). 수집기가 하루(한국 날짜) 1번, 화면은 저장분만 읽는다.

11/24 유튜브 "요즘 유행"은 이슈 모음 채널의 #수능·#베스티였다. 의뢰의 "요즘 밈" 재료를 따로 둔다.

- 키워드: 화면 저장(회사 연결 › 유튜브 밈 키워드, `tenant.yt_meme_keywords`) > 회사 설정 trends.toml
  `[youtube] meme_keywords`. 최대 10개. 사람이 넣는다(코드에 박지 않는다). 없으면 검색하지 않는다
- 검색(공식 Data API): 키워드마다 `search.list` 최근 7일·videoDuration=short·regionCode=KR·
  order=viewCount 상위 10개 → `videos.list` 조회수, `channels.list` 구독자. 단가 search 100 +
  videos 1 + channels 1. 같은 장부(`youtube_quota`, 부른 곳 `meme_shorts`)를 거친다
- 쿼터: 키워드를 부르기 전에 오늘 YouTube 장부 합계 + 이번 검색이 하루 상한의 30%를 넘으면 그
  키워드부터 건너뛰고 이유를 남긴다. 남은 키워드는 다음 수집 회차에 다시 본다(같은 날 안)
- 저장: `DATA_MEME_SHORTS_DB`(기본 `backend/.data/meme_shorts.db`), 회사·날짜마다 한 줄
- 읽기(`shorts_search`, trend_memes 화면 "쇼츠 검색" 칸): 구독자 대비 조회 배수(조회 ÷ 구독자) 순.
  구독자 10만 이상 채널은 `big_channel`. 실존 인물·사건 분류기(trend_memes와 같은 규칙: 사람 이름
  자체·뉴스·사건·기부·재난·정치·사회·인물 분류)로 뺀 영상은 `removed`. 제목은 untrusted
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ax_contracts.schemas.data import MemeShortsSearch, MemeShortsVideo, MemeSkipped
from data_agent import company_scope

DB_ENV = "DATA_MEME_SHORTS_DB"
DEFAULT_DB = Path(__file__).resolve().parents[4] / ".data" / "meme_shorts.db"  # backend/.data
TREND_CONFIG_ENV = "DATA_TREND_CONFIG"
CALLER = "meme_shorts"  # YouTube 장부의 부른 곳
KST = timezone(timedelta(hours=9))
MAX_KEYWORDS = 10
PER_KEYWORD = 10  # 키워드마다 상위 10개
DAYS = 7
QUOTA_SHARE = 0.3  # 하루 상한의 30%
KEYWORD_UNITS = 102  # search 100 + videos 1 + channels 1
BIG_CHANNEL = 100_000  # 구독자 10만 이상은 따로 표시
SHOW_MAX = 20  # 화면에 보여 줄 영상 수 (분류해서 뺀 것 빼고)
NOT_YET = "아직 쇼츠 검색 기록이 없어요 (수집기가 하루 1번 찾아요)"


def _today(now: datetime) -> date:
    return now.astimezone(KST).date()


def _clean(words: Sequence[Any]) -> list[str]:
    out: list[str] = []
    for w in words:
        word = " ".join(str(w).split())
        if word and word.lower() not in {o.lower() for o in out}:
            out.append(word[:30])
    return out[:MAX_KEYWORDS]


def load_keywords(env: Mapping[str, str]) -> list[str]:
    """회사 밈 키워드: 화면 저장 > trends.toml [youtube] meme_keywords. 없으면 빈 목록."""
    from ax_agent_template.tenant import setting as tenant_setting
    from ax_agent_template.tenant import yt_meme_keywords

    saved = yt_meme_keywords()
    if saved is not None:
        return _clean(saved)
    path = env.get(TREND_CONFIG_ENV) or tenant_setting(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return []
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f).get("youtube") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return []
    listed = raw.get("meme_keywords", []) if isinstance(raw, dict) else []
    return _clean(listed) if isinstance(listed, list) else []


# ---------------------------------------------------------------- 저장


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS runs (tenant_id TEXT NOT NULL, day TEXT NOT NULL,"
                " value TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (tenant_id, day))"
            )

    def get(self, day: date) -> dict[str, Any] | None:
        key = (company_scope.active(), day.isoformat())
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM runs WHERE tenant_id = ? AND day = ?", key
            ).fetchone()
        return json.loads(row[0]) if row else None

    def latest(self) -> tuple[date, dict[str, Any]] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT day, value FROM runs WHERE tenant_id = ? ORDER BY day DESC LIMIT 1",
                (company_scope.active(),),
            ).fetchone()
        return (date.fromisoformat(row[0]), json.loads(row[1])) if row else None

    def put(self, day: date, value: Mapping[str, Any], at: datetime) -> None:
        body = json.dumps(value, ensure_ascii=False, default=str)
        row = (company_scope.active(), day.isoformat(), body, at.isoformat())
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?)", row)
            old = (day - timedelta(days=30)).isoformat()
            self._conn.execute("DELETE FROM runs WHERE day < ?", (old,))

    def close(self) -> None:
        self._conn.close()


def db_path(env: Mapping[str, str]) -> str:
    return env.get(DB_ENV) or str(DEFAULT_DB)


# ---------------------------------------------------------------- 수집 (하루 1번)


def quota_reason(used: int, cap: int, units: int = KEYWORD_UNITS) -> str | None:
    """오늘 장부 합계 + 이번 검색이 하루 상한의 30%를 넘으면 이유, 아니면 None (순수 계산)."""
    limit = int(cap * QUOTA_SHARE)
    if used + units > limit:
        return (f"YouTube 오늘 사용량이 하루 상한의 30%를 넘어 건너뜀 (오늘 {used:,}단위 + 검색"
                f" {units}단위 > {limit:,}단위)")  # fmt: skip
    return None


@dataclass
class CollectResult:
    keywords: int = 0
    searched: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    units: int = 0

    def line(self) -> str:
        tail = f" · 건너뜀 {', '.join(self.skipped)}" if self.skipped else ""
        return (f"[meme_shorts] 키워드 {self.keywords}개 중 검색 {len(self.searched)}개"
                f" · 할당량 {self.units}단위{tail}")  # fmt: skip


async def collect(env: Mapping[str, str], connector: Any, *, now: datetime | None = None,
                  store: Store | None = None, ledger: Any = None) -> CollectResult:  # fmt: skip
    """오늘 아직 안 찾은 키워드만 찾아 저장한다. 쿼터 30%를 넘으면 그 키워드부터 다음 회차로."""
    from data_agent import youtube_quota
    from data_agent.connectors import ConnectorUnavailable

    now = now or datetime.now(UTC)
    ledger = ledger or youtube_quota.LEDGER
    keywords = load_keywords(env)
    result = CollectResult(keywords=len(keywords))
    if not keywords:
        return result
    own = store is None
    store = store or Store(db_path(env))
    try:
        day = _today(now)
        saved = store.get(day) or {"keywords": keywords, "searched": [], "videos": [],
                                   "limits": [], "units": 0}  # fmt: skip
        todo = [k for k in keywords if k not in saved["searched"]]
        if not todo:
            return result
        limits: list[str] = []
        cap = youtube_quota.cap(env)
        for n, word in enumerate(todo):
            if why := quota_reason(ledger.units(), cap):
                rest = todo[n:]
                result.skipped = rest
                limits.append(f"{', '.join(rest)}: {why}")
                break
            before = getattr(connector, "quota_used", 0)
            try:
                stats = await connector.search_query_shorts(
                    word, now - timedelta(days=DAYS), "KR", max_results=PER_KEYWORD)  # fmt: skip
                subs = await connector.channels([s.channel_id for s in stats]) if stats else {}
            except ConnectorUnavailable as error:
                result.skipped = todo[n:]
                limits.append(f"{word}: 검색 실패 ({error.code})")
                result.units += getattr(connector, "quota_used", 0) - before
                break
            result.units += getattr(connector, "quota_used", 0) - before
            seen = {v["video_id"] for v in saved["videos"]}
            for s in stats:
                if s.video_id in seen:
                    continue
                seen.add(s.video_id)
                saved["videos"].append({
                    "video_id": s.video_id, "keyword": word, "title": s.title or None,
                    "channel_id": s.channel_id or None, "channel_title": s.channel_title or None,
                    "published_at": s.published_at.isoformat() if s.published_at else None,
                    "views": s.view_count, "subscribers": subs.get(s.channel_id),
                    "thumbnail_url": s.thumbnail_url,
                })  # fmt: skip
            saved["searched"].append(word)
            result.searched.append(word)
        saved["keywords"] = keywords
        saved["limits"] = limits
        saved["units"] = int(saved.get("units", 0)) + result.units
        saved["searched_at"] = now.isoformat()
        store.put(day, saved, now)
    finally:
        if own:
            store.close()
    return result


async def run(env: Mapping[str, str] | None = None) -> CollectResult | None:
    """수집기용. 키가 없거나 실패해도 수집은 계속한다."""
    from data_agent.connectors.youtube import YouTubeConnector

    env = os.environ if env is None else env
    if not load_keywords(env):
        return None
    try:
        result = await collect(env, YouTubeConnector(env, caller=CALLER))
    except Exception as error:  # noqa: BLE001
        print(f"[meme_shorts] 실패 ({type(error).__name__})", flush=True)
        return None
    if result.searched or result.skipped:
        print(result.line(), flush=True)
    return result


# ---------------------------------------------------------------- 읽기 (화면 "쇼츠 검색" 칸)


def views_per_subscriber(views: int | None, subscribers: int | None) -> float | None:
    """조회 ÷ 구독자 (소수 둘째 자리). 모르거나 구독자 0이면 None."""
    if views is None or not subscribers:
        return None
    return round(views / subscribers, 2)


def to_video(row: Mapping[str, Any]) -> MemeShortsVideo:
    from data_agent.video_links import video_url

    views, subs = row.get("views"), row.get("subscribers")
    return MemeShortsVideo(
        video_id=row["video_id"], keyword=row.get("keyword") or "", title=row.get("title"),
        channel_id=row.get("channel_id"), channel_title=row.get("channel_title"),
        published_at=row.get("published_at"), views=views, subscribers=subs,
        views_per_subscriber=views_per_subscriber(views, subs),
        big_channel=isinstance(subs, int) and subs >= BIG_CHANNEL,
        video_url=video_url(row["video_id"], short=True), thumbnail_url=row.get("thumbnail_url"),
    )  # fmt: skip


def ranked(rows: Sequence[Mapping[str, Any]]) -> list[MemeShortsVideo]:
    """구독자 대비 조회 배수 순 (모르면 뒤, 그다음 조회수 순)."""
    items = [to_video(r) for r in rows]

    def order(v: MemeShortsVideo) -> tuple[bool, float, int]:
        return v.views_per_subscriber is None, -(v.views_per_subscriber or 0), -(v.views or 0)

    return sorted(items, key=order)


async def classify(videos: Sequence[MemeShortsVideo], llm: Any, *, category_cache: Any = None,
                   context_id: str | None = None, limit: int = SHOW_MAX,
                   ) -> tuple[list[MemeShortsVideo], list[MemeSkipped]]:  # fmt: skip
    """실존 인물·사건 분류기 (trend_memes와 같은 규칙): (남길 영상 limit개까지, 뺀 영상).

    사람 이름 자체·뉴스·사건·기부·재난·정치·사회·인물 분류면 뺀다. 분류 실패는 "분류 대기"로 두고
    남긴다(trend_memes와 같다)."""
    from data_agent.skills.ad_material import NEVER_TREND
    from data_agent.skills.topic_category import TopicInput, classify_topics

    rest = list(videos)
    picked: list[MemeShortsVideo] = []
    removed: list[MemeSkipped] = []
    while rest and len(picked) < limit:
        batch, rest = rest[:limit], rest[limit:]
        inputs = [TopicInput((v.title or v.video_id)[:60], (), (v.title,) if v.title else ())
                  for v in batch]  # fmt: skip
        try:
            found = await classify_topics(inputs, llm, cache=category_cache, context_id=context_id)
        except Exception:  # noqa: BLE001 - 분류 실패는 "분류 대기"로 두고 보여 준다
            found = {}
        for v, t in zip(batch, inputs, strict=True):
            cat = found.get(t.keyword)
            name = v.title or v.video_id
            if cat is not None and cat.is_person:
                removed.append(MemeSkipped(name=name, kind="person",
                                           reason="사람 이름 자체라 뺌 (초상권)"))  # fmt: skip
                continue
            if cat is not None and cat.category in NEVER_TREND:
                removed.append(MemeSkipped(name=name, kind="category",
                                           reason=f"{cat.category} 분류라 뺌"))  # fmt: skip
                continue
            if len(picked) < limit:
                picked.append(v)
    return picked, removed


async def search_extra(env: Mapping[str, str], connector: Any, queries: Sequence[tuple[str, str]],
                       now: datetime, *, ledger: Any = None,
                       ) -> tuple[list[MemeShortsVideo], list[str], int]:  # fmt: skip
    """보고서용 추가 쇼츠 검색 (11/24 대상층): (키워드, 검색어) 몇 개만, 쿼터 30% 규칙 안에서.

    (영상, 한계 문장, 쓴 단위). 저장하지 않는다. 분류는 부르는 쪽이 `classify`로."""
    from data_agent import youtube_quota
    from data_agent.connectors import ConnectorUnavailable

    ledger = ledger or youtube_quota.LEDGER
    cap = youtube_quota.cap(env)
    rows: list[dict[str, Any]] = []
    limits: list[str] = []
    units = 0
    for n, (word, query) in enumerate(queries):
        if why := quota_reason(ledger.units(), cap):
            limits.append(f"쇼츠 대상층 검색 {', '.join(q for _, q in queries[n:])}: {why}")
            break
        before = getattr(connector, "quota_used", 0)
        try:
            stats = await connector.search_query_shorts(
                query, now - timedelta(days=DAYS), "KR", max_results=PER_KEYWORD)  # fmt: skip
            subs = await connector.channels([s.channel_id for s in stats]) if stats else {}
        except ConnectorUnavailable as error:
            limits.append(f"쇼츠 대상층 검색 {query}: 검색 실패 ({error.code})")
            units += getattr(connector, "quota_used", 0) - before
            break
        units += getattr(connector, "quota_used", 0) - before
        rows += [{"video_id": s.video_id, "keyword": word, "title": s.title or None,
                  "channel_id": s.channel_id or None, "channel_title": s.channel_title or None,
                  "published_at": s.published_at.isoformat() if s.published_at else None,
                  "views": s.view_count, "subscribers": subs.get(s.channel_id),
                  "thumbnail_url": s.thumbnail_url} for s in stats]  # fmt: skip
    seen: set[str] = set()
    unique = [r for r in rows if not (r["video_id"] in seen or seen.add(r["video_id"]))]
    return ranked(unique), limits, units


async def shorts_search(env: Mapping[str, str], now: datetime, llm: Any, *,
                        category_cache: Any = None,
                        context_id: str | None = None) -> MemeShortsSearch:  # fmt: skip
    """저장된 오늘(없으면 마지막) 검색 결과 + 실존 인물·사건 분류. 새로 검색하지 않는다."""
    keywords = load_keywords(env)
    out = MemeShortsSearch(keywords=keywords)
    if not keywords:
        out.reason = ("회사 밈 키워드가 없어 쇼츠를 찾지 않았어요 (회사 연결 › 유튜브 밈 키워드"
                      " 또는 trends.toml [youtube] meme_keywords)")  # fmt: skip
        return out
    path = db_path(env)
    got = None
    if str(path) == ":memory:" or Path(path).is_file():
        store = Store(path)
        try:
            got = store.latest()
        finally:
            store.close()
    if got is None:
        out.status, out.reason = "unknown", NOT_YET
        return out
    day, saved = got
    out.day, out.searched = day, list(saved.get("searched") or [])
    out.limits, out.units = list(saved.get("limits") or []), int(saved.get("units") or 0)
    out.searched_at = saved.get("searched_at")
    if day != _today(now):
        out.limits.insert(0, f"오늘 검색 전이라 {day} 결과예요")
    videos = ranked(saved.get("videos") or [])
    picked, removed = await classify(videos, llm, category_cache=category_cache,
                                     context_id=context_id)  # fmt: skip
    out.items, out.removed = picked, removed
    out.status = "known"
    if not picked:
        out.reason = "찾은 쇼츠가 없어요" if not out.removed else "찾은 쇼츠를 모두 분류에서 뺐어요"
    return out
