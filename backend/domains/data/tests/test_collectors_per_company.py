"""데이터 수집기·시간대를 회사마다 (11/26). 다른 회사는 그 회사 설정(없으면 틀 기본값)만 보고,
캣북스 환경변수·`AX_TENANT` 폴더(캣북스 키워드·시간대)로 떨어지지 않는다. 캣북스는 예전 그대로."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template import company_scope, tenant
from data_agent import trending_collect, youtube_collect
from data_agent.connectors.youtube import VideoStat
from data_agent.skills import video_explorer
from data_agent.youtube_collect import YouTubeConfig, collect_once
from data_agent.youtube_store import Row, SnapshotStore, search_owners, search_source

NOW = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)  # 한국 10시
NUBI = "c-cf2f0d"
CATBOOKS_YOUTUBE = (
    Path(__file__).resolve().parents[3] / "config" / "tenants" / "catbooks" / "youtube.toml"
)


@pytest.fixture(autouse=True)
def _no_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenant, "current", lambda: None)  # 가입만 한 회사: 회사 설정 폴더 없음


def _stat(vid: str, rank: int | None = None) -> VideoStat:
    return VideoStat(video_id=vid, channel_id=f"ch_{vid}", category_id="10",
                     published_at=NOW - timedelta(hours=5), view_count=100, like_count=1,
                     comment_count=0, tags=(), rank=rank)  # fmt: skip


class FakeYouTube:
    """인기·추적·검색만 흉내. `fail`의 키워드는 예외."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.quota_used = 0
        self.searched: list[str] = []
        self.fail = fail or set()

    async def popular(self, region: str) -> list[VideoStat]:
        return [_stat("pop", rank=1)]

    async def videos(self, ids: list[str]) -> list[VideoStat]:
        return [_stat(i) for i in ids]

    async def search(self, query: str, after: datetime, region: str) -> list[VideoStat]:
        self.searched.append(query)
        if query in self.fail:
            raise RuntimeError("boom")
        return [_stat(f"v_{query}")]


def _sources(store: SnapshotStore) -> dict[str, str]:
    rows = store._conn.execute("SELECT video_id, sources FROM snapshots").fetchall()
    return {vid: src for vid, src in rows}


def test_each_company_searches_its_own_keywords_and_one_failure_does_not_stop_others() -> None:
    store = SnapshotStore(":memory:")
    yt = FakeYouTube(fail={"투어"})
    companies = [
        ("c-aaa111", YouTubeConfig(queries=("투어",))),
        (NUBI, YouTubeConfig(queries=("패키지여행",))),
        ("c-bbb222", YouTubeConfig()),  # 키워드 없음: 검색 안 함 (캣북스 키워드를 빌리지 않음)
    ]
    first = asyncio.run(collect_once(yt, store, YouTubeConfig(queries=("고양이",)), now=NOW,
                                     companies=companies))  # fmt: skip
    assert yt.searched == ["고양이", "투어", "패키지여행"]
    src = _sources(store)
    assert src["v_고양이"] == "search:고양이"  # 캣북스: 예전 모양 그대로
    assert src["v_패키지여행"] == f"search@{NUBI}:패키지여행"
    assert any("c-aaa111" in e for e in first.errors)  # 실패는 기록, 다른 회사는 계속
    assert first.searched and first.company_searched[NUBI] == 1
    assert "c-bbb222" not in first.company_searched
    # 같은 한국 날짜 두 번째: 회사마다 하루 1번
    later = NOW + timedelta(hours=1)
    again = asyncio.run(collect_once(yt, store, YouTubeConfig(queries=("고양이",)), now=later,
                                     companies=companies))  # fmt: skip
    assert yt.searched == ["고양이", "투어", "패키지여행"]
    assert not again.searched and not again.company_searched
    assert store.searched_on(NOW.astimezone(youtube_collect.KST).date(), NUBI)


def test_company_search_does_not_mark_catbooks_as_searched() -> None:
    store = SnapshotStore(":memory:")
    asyncio.run(collect_once(FakeYouTube(), store, YouTubeConfig(), now=NOW,
                             companies=[(NUBI, YouTubeConfig(queries=("여행",)))]))  # fmt: skip
    day = NOW.astimezone(youtube_collect.KST).date()
    assert store.searched_on(day, NUBI)
    assert not store.searched_on(day)  # 캣북스 검색 기록은 그대로 (키워드 생기면 그날 검색)


def test_search_owners_reads_old_and_company_sources() -> None:
    assert search_source("catbooks", "책") == "search:책"
    assert search_owners("popular,search:책") == {"catbooks"}
    assert search_owners(f"tracked,search@{NUBI}:여행,search:책") == {NUBI, "catbooks"}
    assert search_owners("popular,tracked") == set()


def test_other_company_youtube_and_trend_settings_ignore_catbooks_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = {youtube_collect.CONFIG_ENV: str(CATBOOKS_YOUTUBE), "AX_TENANT": "catbooks",
           trending_collect.TREND_CONFIG_ENV: "/x/catbooks/trends.toml"}  # fmt: skip
    monkeypatch.setenv(youtube_collect.CONFIG_ENV, str(CATBOOKS_YOUTUBE))
    assert youtube_collect.config_path(env) == CATBOOKS_YOUTUBE  # 요청 밖(캣북스): 예전 그대로
    with company_scope.using(NUBI):
        assert youtube_collect.config_path(env) != CATBOOKS_YOUTUBE
        assert youtube_collect.load_config(youtube_collect.config_path(env)).queries == ()
        assert "catbooks" not in str(trending_collect.config_path(env))


def test_other_company_timezone_is_its_own_not_catbooks_env() -> None:
    from data_agent.skills.campaign_effect import tenant_timezone

    env = {"DATA_TENANT_TIMEZONE": "Asia/Tokyo", "AX_TENANT": "catbooks"}
    assert str(tenant_timezone(env)) == "Asia/Tokyo"  # 캣북스: 예전 그대로
    with company_scope.using(NUBI):
        assert str(tenant_timezone(env)) == "Asia/Seoul"  # 틀 기본값


def test_explorer_hides_other_companies_keyword_videos(tmp_path: Path) -> None:
    db = tmp_path / "yt.db"
    store = SnapshotStore(db)
    store.save(NOW - timedelta(hours=2), [
        Row(_stat("pop", rank=1), ("popular",)),
        Row(_stat("cat"), ("search:고양이",)),
        Row(_stat("nubi"), (f"search@{NUBI}:여행",)),
        Row(_stat("both", rank=3), ("popular",)),
    ])  # fmt: skip
    store.save(NOW - timedelta(hours=1), [
        Row(_stat("both"), (f"search@{NUBI}:여행",)),  # 인기 목록에 든 적 있음 → 공용
        Row(_stat("cat"), ("tracked",)),
    ])  # fmt: skip
    store.close()
    pool = video_explorer.snapshot_pool(db, NOW - timedelta(days=7))
    assert pool["cat"].owners == {"catbooks"} and pool["nubi"].owners == {NUBI}
    assert not pool["pop"].owners and not pool["both"].owners
    payload = {"company_only": {v: sorted(i.owners) for v, i in pool.items() if i.owners}}
    assert video_explorer.hidden_for(payload, NUBI) == {"cat"}
    assert video_explorer.hidden_for(payload, "catbooks") == {"nubi"}
    assert video_explorer.hidden_for({}, NUBI) == set()  # 예전 저장분: 그대로


def test_company_configs_read_each_company_in_its_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    from data_agent import sales_collect

    monkeypatch.setattr(sales_collect, "watched_companies", lambda env: [NUBI])
    env = {youtube_collect.CONFIG_ENV: str(CATBOOKS_YOUTUBE), "AX_TENANT": "catbooks"}
    assert youtube_collect.load_config(CATBOOKS_YOUTUBE).queries  # 캣북스 키워드는 있다
    assert youtube_collect.company_configs(env) == [(NUBI, YouTubeConfig())]  # 빌리지 않음
