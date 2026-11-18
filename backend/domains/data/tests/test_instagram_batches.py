"""인스타 레이더 묶음 수집: 50개씩 나누기·커서 이어받기·한도에서 멈춤·하루 상한. 실제 API 없음."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from data_agent import instagram_store as st
from data_agent.connectors import instagram_graph as ig
from data_agent.connectors.naver_searchad import JsonDailyCache
from data_agent.skills import instagram as igs

NOW = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)  # 한국 11-18 12:00
ACCOUNTS = [f"acct{i:03d}" for i in range(161)]


def _env(tmp_path: Path, accounts: list[str] = ACCOUNTS, extra: str = "") -> dict[str, str]:
    cfg = tmp_path / "trends.toml"
    names = ", ".join(f'"{a}"' for a in accounts)
    cfg.write_text(f"[instagram]\nradar_accounts = [{names}]\n{extra}", encoding="utf-8")
    return {"DATA_TREND_CONFIG": str(cfg), st.RADAR_ENV: str(tmp_path / "ig_radar.db")}


def _media(account: str) -> list[dict[str, Any]]:
    return [{"id": f"{account}-{i}", "media_product_type": "REELS",
             "timestamp": (NOW - timedelta(days=1)).isoformat(), "view_count": 100}
            for i in range(3)]  # fmt: skip


class FakeGraph:
    """business_media만. `fail_at`에 닿으면 그 오류를 던진다."""

    def __init__(self, fail: dict[str, Exception] | None = None) -> None:
        self.fail = fail or {}
        self.asked: list[str] = []

    async def business_media(self, account, limit=20):  # noqa: ANN001, ANN201
        self.asked.append(account)
        if account in self.fail:
            raise self.fail[account]
        return _media(account)


def _batch(env: dict[str, str], graph: Any, now: datetime) -> igs.RadarBatch:
    store = st.RadarStore(env[st.RADAR_ENV])  # 회차마다 새로 열어 커서가 저장소에서 이어지는지 본다
    try:
        return asyncio.run(igs.radar_batch(igs.load_config(env), graph, store, now))
    finally:
        store.close()


def test_161_accounts_in_batches_of_50_and_cycle_completes(tmp_path: Path) -> None:
    env = _env(tmp_path)
    graph = FakeGraph()
    sizes, positions = [], []
    for k in range(4):
        before = len(graph.asked)
        got = _batch(env, graph, NOW + timedelta(minutes=20 * k))
        sizes.append(len(graph.asked) - before)
        positions.append(got.position)
    assert sizes == [50, 50, 50, 11] and positions == [50, 100, 150, 161]
    assert graph.asked == ACCOUNTS  # 순서대로, 한 번씩
    assert "바퀴 다 돎" in (got.note or "")
    day, media, failed = st.radar_latest(env[st.RADAR_ENV])
    assert day == date(2026, 11, 18) and len(media) == 161 and failed == {}
    # 같은 날에는 다시 돌지 않는다
    again = _batch(env, graph, NOW + timedelta(hours=3))
    assert len(graph.asked) == 161 and "날짜가 바뀌면" in (again.note or "")


def test_batch_size_and_interval_from_config(tmp_path: Path) -> None:
    env = _env(tmp_path, extra="radar_batch_size = 40\nradar_batch_interval_min = 30\n")
    config = igs.load_config(env)
    assert (config.radar_batch_size, config.radar_batch_interval_min) == (40, 30)
    assert len(config.radar_accounts) == 161  # 예전 상한 60에 잘리지 않는다
    graph = FakeGraph()
    _batch(env, graph, NOW)
    early = _batch(env, graph, NOW + timedelta(minutes=29))  # 간격 안 됨: 부르지 않음
    assert len(graph.asked) == 40 and "묶음 간격" in (early.note or "") and early.position == 40
    _batch(env, graph, NOW + timedelta(minutes=30))
    assert len(graph.asked) == 80


def test_cursor_continues_in_next_run(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _batch(env, FakeGraph(), NOW)
    second = FakeGraph()  # 새 커넥터·새 저장소 연결 (다음 회차)
    got = _batch(env, second, NOW + timedelta(minutes=21))
    assert second.asked == ACCOUNTS[50:100] and got.position == 100
    store = st.RadarStore(env[st.RADAR_ENV])
    cur = store.cursor()
    store.close()
    assert cur is not None and cur[0] == date(2026, 11, 18) and cur[1] == 100


def test_app_request_limit_stops_batch_and_keeps_cursor(tmp_path: Path) -> None:
    env = _env(tmp_path)
    limit = ig.IgError("RATE_LIMITED", "한도: Graph API 호출 제한(code 4)")
    graph = FakeGraph({ACCOUNTS[10]: limit})
    got = _batch(env, graph, NOW)
    assert graph.asked == ACCOUNTS[:11]  # 한도를 받은 계정에서 바로 멈춤
    assert got.position == 10 and got.read == 10 and got.status == "rate_limited"
    assert "커서 유지" in (got.note or "")
    store = st.RadarStore(env[st.RADAR_ENV])
    assert not store.done(date(2026, 11, 18), ACCOUNTS[10])  # 실패로 적지 않음
    store.close()
    nxt = FakeGraph()
    got2 = _batch(env, nxt, NOW + timedelta(minutes=20))
    assert nxt.asked[0] == ACCOUNTS[10] and got2.position == 60


def test_app_request_limit_from_real_connector_code_4(tmp_path: Path) -> None:
    """진짜 커넥터 + 가짜 HTTP: 메타 'Application request limit reached'(code 4)에서 멈춘다."""
    env = _env(tmp_path, ACCOUNTS[:5])
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["fields"])
        if len(calls) == 3:
            return httpx.Response(400, json={"error": {
                "message": "(#4) Application request limit reached", "type": "OAuthException",
                "code": 4}})  # fmt: skip
        return httpx.Response(200, json={"business_discovery": {"media": {"data": [{"id": "m"}]}}})

    graph = ig.InstagramGraph({"DATA_IG_ACCESS_TOKEN": "t", "DATA_IG_USER_ID": "1"},
                              client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                              calls=JsonDailyCache())  # fmt: skip
    got = _batch(env, graph, NOW)
    assert len(calls) == 3 and got.position == 2 and got.status == "rate_limited"


def test_daily_limit_stops_batch(tmp_path: Path) -> None:
    """DATA_IG_DAILY_LIMIT(서버 .env)에 닿으면 멈추고 커서를 둔다. 기본값은 코드 그대로."""
    assert ig.DEFAULT_DAILY_LIMIT == 150
    env = _env(tmp_path)
    ok = {"business_discovery": {"media": {"data": [{"id": "m"}]}}}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=ok))
    client = httpx.AsyncClient(transport=transport)
    graph = ig.InstagramGraph({"DATA_IG_ACCESS_TOKEN": "t", "DATA_IG_USER_ID": "1",
                               "DATA_IG_DAILY_LIMIT": "7"}, client=client,
                              calls=JsonDailyCache())  # fmt: skip
    got = _batch(env, graph, NOW)
    assert got.position == 7 and got.read == 7 and got.status == "rate_limited"
    assert "커서 유지" in (got.note or "")


def test_permission_and_no_data_skip_only_that_account(tmp_path: Path) -> None:
    env = _env(tmp_path, ACCOUNTS[:4])
    graph = FakeGraph({ACCOUNTS[1]: ig.IgError("NO_DATA", "데이터 없음"),
                       ACCOUNTS[2]: ig.IgError("PERMISSION", ig.PERMISSION_NOTE)})  # fmt: skip
    got = _batch(env, graph, NOW)
    assert graph.asked == ACCOUNTS[:4] and got.position == 4 and got.failed == 2
    _, media, failed = st.radar_latest(env[st.RADAR_ENV])
    assert set(media) == {ACCOUNTS[0], ACCOUNTS[3]} and set(failed) == {ACCOUNTS[1], ACCOUNTS[2]}


def test_partial_cycle_is_not_judged(tmp_path: Path) -> None:
    """첫 바퀴가 끝나기 전에는 일부 계정만으로 판정하지 않고 진행 상황을 이유로 준다."""
    env = {**_env(tmp_path), "DATA_IG_ACCESS_TOKEN": "t", "DATA_IG_USER_ID": "1",
           st.AUDIO_ENV: str(tmp_path / "ig_audio.db")}  # fmt: skip
    _batch(env, FakeGraph(), NOW)
    assert st.radar_latest(env[st.RADAR_ENV]) == (None, {}, {})
    out = asyncio.run(igs.instagram_radar(object(), env=env, now=NOW)).output
    assert out.status == "unknown" and "첫 바퀴를 모으는 중 (50/161곳)" in (out.reason or "")


def test_catbooks_radar_accounts_are_161_without_duplicates() -> None:
    """캣북스 trends.toml 레이더 계정: 161개, 중복 0, 상한에 잘리지 않음, 4회차(50·50·50·11)."""
    import tomllib

    path = Path(__file__).resolve().parents[3] / "config/tenants/catbooks/trends.toml"
    with path.open("rb") as f:
        raw = tomllib.load(f)["instagram"]["radar_accounts"]
    assert len(raw) == 161 and len(set(raw)) == 161
    assert all(a and a == a.strip().lstrip("@") for a in raw)
    config = igs.load_config({"DATA_TREND_CONFIG": str(path)})
    assert list(config.radar_accounts) == raw
    assert (config.radar_batch_size, config.radar_batch_interval_min) == (50, 20)


def test_new_day_starts_new_cycle(tmp_path: Path) -> None:
    env = _env(tmp_path, ACCOUNTS[:3])
    _batch(env, FakeGraph(), NOW)  # 11-18 바퀴 다 돎
    nxt = FakeGraph()
    got = _batch(env, nxt, NOW + timedelta(days=1))
    assert nxt.asked == ACCOUNTS[:3] and "2026-11-19 바퀴 다 돎" in (got.note or "")
    day, _, _ = st.radar_latest(env[st.RADAR_ENV])
    assert day == date(2026, 11, 19)
