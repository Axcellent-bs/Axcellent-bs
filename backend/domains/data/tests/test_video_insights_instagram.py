"""video_insights 인스타 (11/26 H): 우리 계정(DATA_IG_USER_ID) 미디어 인사이트와 계정 시청층.

가짜 Graph 응답(httpx.MockTransport)만 쓴다. 권한 없음·계정 기준 표시·100명 미만 비어 있음·
우리 계정이 아닌 회사·env 없음·토큰 값 없음을 본다."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_contracts.schemas.data import VideoInsightsRequest
from data_agent.connectors.instagram_graph import InstagramGraph
from data_agent.connectors.naver_searchad import JsonDailyCache
from data_agent.skills import video_insights as vi
from data_agent.skills import video_performance as vp

NOW = datetime(2026, 11, 26, 6, 0, tzinfo=UTC)
CODE = "DAbc123xyz"
URL = f"https://www.instagram.com/reel/{CODE}/"
TOKEN, USER = "ig-token-secret-1", "17841400000000000"
ENV_KEYS = {"DATA_IG_ACCESS_TOKEN": TOKEN, "DATA_IG_USER_ID": USER}


class FakeGraph:
    def __init__(self, *, username: str = "catbooks.lab", permission: bool = True,
                 demographics: bool = True) -> None:  # fmt: skip
        self.username, self.permission, self.demographics = username, permission, demographics
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        path = request.url.path.split("/", 2)[2]  # /v23.0/<path>
        self.paths.append(path)
        q = dict(request.url.params)
        if path == USER:
            return httpx.Response(200, json={"username": self.username, "id": USER})
        if path == f"{USER}/media":
            return httpx.Response(200, json={"data": [
                {"id": "m1", "permalink": f"https://www.instagram.com/reel/{CODE}/"},
                {"id": "m2", "permalink": "https://www.instagram.com/p/other/"},
            ]})  # fmt: skip
        if path == "m1/insights":
            if not self.permission:
                return httpx.Response(400, json={"error": {"code": 10, "message": "perm"}})
            assert q["metric"] == ",".join(vi.IG_METRICS)
            return httpx.Response(200, json={"data": [
                {"name": "views", "total_value": {"value": 900}},
                {"name": "reach", "values": [{"value": 700}]},
                {"name": "saved", "values": [{"value": 12}]},
                {"name": "shares", "values": [{"value": 5}]},
                {"name": "total_interactions", "values": [{"value": 60}]},
                {"name": "ig_reels_avg_watch_time", "values": [{"value": 6400}]},
            ]})  # fmt: skip
        if path == f"{USER}/insights":
            assert q["metric"] == "follower_demographics" and q["metric_type"] == "total_value"
            if not self.demographics:
                return httpx.Response(200, json={"data": []})  # 팔로워 100명 미만
            values = {"age": [("18-24", 30), ("25-34", 70)], "gender": [("F", 60), ("M", 40)]}
            rows = [{"dimension_values": [k], "value": n} for k, n in values[q["breakdown"]]]
            return httpx.Response(200, json={"data": [{"name": "follower_demographics",
                                                       "total_value": {"breakdowns": [
                                                           {"results": rows}]}}]})  # fmt: skip
        return httpx.Response(404, json={"error": {"code": 100}})


@pytest.fixture
def tenants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "tenants"
    (root / "catbooks").mkdir(parents=True)
    (root / "catbooks" / "tenant.toml").write_text(
        'name = "캣북스"\n[instagram]\npublish_username = "catbooks.lab"\n', encoding="utf-8"
    )
    monkeypatch.setenv("AX_TENANTS_DIR", str(root))
    return root


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {vp.DB_ENV: str(tmp_path / "perf.db"), **extra}
    store = vp.store_of(env)
    try:
        store.upsert(vp.Video(key=f"instagram:{CODE}", platform="instagram", video_id=CODE,
                              video_url=URL, campaign_id="CMP-IG", insight_id=None,
                              proposal_id=None, test=False, published_at=None, title=None,
                              registered_at=NOW, category=None, shorts_format=None))  # fmt: skip
    finally:
        store.close()
    return env


def _collect(env: dict[str, str], fake: FakeGraph) -> vi.CollectResult:
    graph = InstagramGraph(env, client=httpx.AsyncClient(transport=httpx.MockTransport(fake)),
                           calls=JsonDailyCache())  # fmt: skip
    videos, store = vp.store_of(env), vi.store_of(env)
    try:
        return asyncio.run(vi.collect_instagram_due(videos, store, graph, env, NOW))
    finally:
        videos.close()
        store.close()


def _read(env: dict[str, str], **req: Any) -> Any:
    return vi.video_insights(VideoInsightsRequest(**(req or {"video_url": URL})), env=env,
                             now=NOW).output  # fmt: skip


def test_reel_insights_and_account_audience(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **ENV_KEYS)
    assert _collect(env, FakeGraph()).saved == 1
    out = _read(env)
    assert out.status == "known" and out.platform == "instagram" and out.campaign_id == "CMP-IG"
    w = out.watch
    assert (w.views, w.reach, w.saved, w.shares, w.total_interactions) == (900, 700, 12, 5, 60)
    assert w.avg_view_seconds == 6.4 and w.source == "instagram_insights"  # 밀리초 ÷ 1000
    assert out.audience_basis == "account"  # 계정 기준 (영상 시청층이 아님)
    assert [(a.age, a.gender, a.percent) for a in out.audience] == [
        ("18-24", "", 30.0), ("25-34", "", 70.0), ("", "F", 60.0), ("", "M", 40.0)]
    assert {"도달", "저장"} <= {f.label for f in out.facts}
    assert out.needs == []


def test_no_permission_is_unknown_with_the_permission_need(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **ENV_KEYS)
    _collect(env, FakeGraph(permission=False))
    out = _read(env)
    assert out.status == "unknown" and out.reason == vi.IG_PERMISSION and out.watch is None
    need = {s.source: s for s in out.needs}["instagram_insights"]
    assert need.status == "check" and any("instagram_manage_insights" in n for n in need.needs)


def test_under_100_followers_leaves_audience_empty(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **ENV_KEYS)
    _collect(env, FakeGraph(demographics=False))
    out = _read(env)
    assert out.status == "known" and out.audience == []
    assert out.audience_reason == vi.IG_SAMPLE_SHORT and out.audience_basis == "account"


def test_another_company_account_gets_public_values_only(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **ENV_KEYS)
    fake = FakeGraph(username="someone.else")
    _collect(env, fake)
    out = _read(env)
    assert out.status == "unknown" and out.reason == vi.NOT_OUR_ACCOUNT
    assert "m1/insights" not in fake.paths  # 우리 계정이 아니면 인사이트를 부르지 않는다


def test_without_env_it_waits_and_lists_all_sources(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path)
    fake = FakeGraph()
    assert _collect(env, fake).saved == 0 and fake.paths == []
    out = _read(env)
    assert out.reason.startswith(vi.WAITING) and "DATA_IG_ACCESS_TOKEN" in out.reason
    assert {s.source for s in out.needs} == {"instagram_insights", "instagram_audience"}
    listed = _read(env, list_all=True)
    assert {s.source for s in listed.needs} == {"youtube_analytics", "youtube_reporting",
                                                "instagram_insights", "instagram_audience"}


def test_token_values_never_leave(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **ENV_KEYS)
    _collect(env, FakeGraph())
    _collect(env, FakeGraph(permission=False))
    text = json.dumps([_read(env).model_dump(mode="json"),
                       _read(env, list_all=True).model_dump(mode="json")], ensure_ascii=False)
    assert TOKEN not in text
