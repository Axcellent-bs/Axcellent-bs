"""인사이트 브리프 판·다시 추천 (`brief_versions`, `BriefStore` v5). 실제 LLM은 부르지 않는다.

- 자동 판은 1번·trigger auto로 저장되고, 다른 쪽이 만드는 중이면 기다렸다가 저장된 판을 준다
- 다시 추천 판에서도 같은 주제는 같은 insight_id (기획 제안·지난번 결과 연결 유지)
- 판 이름("00:27 자동"·"11:16 다시 추천")·판 목록·횟수·diff·예상 LLM %(실제 중앙값 / 첫 자동 추정)
- 만드는 중 문장에 누가(자동이면 "자동으로")·언제
- 핸들러: 다시 추천 시작(started·blocked), 진행 상태, 특정 판 읽기, 공통 로그 시작/끝·토큰
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta
from ax_contracts.schemas.data import TrendInsight, TrendInsightBriefOutput, TrendInsightBriefRequest
from data_agent import brief_versions as bv
from data_agent import llm_budget
from data_agent.skills import trend_insight_brief as tib


def _brief_tests() -> ModuleType:
    """기존 브리프 테스트의 가짜 재료(지금 인기·카탈로그·가짜 LLM)를 그대로 쓴다."""
    path = Path(__file__).with_name("test_trend_insight_brief.py")
    spec = importlib.util.spec_from_file_location("brief_fixtures_for_versions", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bt = _brief_tests()


@pytest.fixture(autouse=True)
def budget(monkeypatch: pytest.MonkeyPatch) -> llm_budget.Budget:
    fresh = llm_budget.Budget(None)
    monkeypatch.setattr(llm_budget, "BUDGET", fresh)
    return fresh


def _env(tmp_path: Path) -> dict[str, str]:
    env = bt._env(tmp_path)
    # 감지 스냅샷 파일은 tmp (실제 수집 DB를 읽지 않게)
    env |= {"DATA_YOUTUBE_DB": str(tmp_path / "yt.db"),
            "DATA_TRENDING_DB": str(tmp_path / "trending.db")}  # fmt: skip
    return env


def _make(env: dict, build: bv.BuildContext | None = None, regenerate: bool = False):  # noqa: ANN202
    return bt.brief.make_brief(
        TrendInsightBriefRequest(regenerate=regenerate), bt.BriefLLM(), env=env,
        popular=bt.POPULAR, popular_reason=None, rising=[bt._rising("다이어리", 3.0)],
        age_fn=bt._ages([]), promo=None, catalog=bt.CATALOG, now=bt.NOW, build=build,
    )  # fmt: skip


def _rules(env: dict) -> str:
    return tib.rules_version(tib.load_config(env))


def test_auto_brief_is_version_1_auto_and_regenerate_keeps_insight_ids(tmp_path: Path) -> None:
    env = _env(tmp_path)
    first = asyncio.run(_make(env)).output
    ctx = bv.BuildContext(env["DATA_INSIGHT_BRIEF_DB"], bt.DAY, _rules(env), "manual", "재현",
                          bv.Detect(None, "k2"), None)  # fmt: skip
    asyncio.run(_make(env, build=ctx, regenerate=True))
    store = tib.BriefStore(env["DATA_INSIGHT_BRIEF_DB"])
    try:
        rows = store.versions(bt.DAY, _rules(env))
        second = store.version(bt.DAY, _rules(env), 2)
        latest = store.get(bt.DAY, _rules(env), bt.NOW)
    finally:
        store.close()
    assert [(r["version"], r["trigger"], r["requested_by"]) for r in rows] == [
        (1, "auto", None), (2, "manual", "재현")]
    assert ctx.version == 2 and latest is not None and latest.version == 2  # briefs 표 = 최신 판
    ids = {i.topic: i.insight_id for i in first.insights}
    assert second is not None
    assert all(ids[i.topic] == i.insight_id for i in second.insights if i.topic in ids)


def test_auto_waits_for_a_build_in_progress_and_takes_that_version(tmp_path: Path) -> None:
    env = _env(tmp_path)
    store = tib.BriefStore(env["DATA_INSIGHT_BRIEF_DB"])
    token = store.claim_build("재현", "manual", datetime.now(UTC))
    assert token is not None

    async def other() -> None:  # 다른 쪽이 1초 뒤 판을 저장하고 잠금을 푼다
        await asyncio.sleep(1.0)
        out = TrendInsightBriefOutput(status="known", insights=[
            TrendInsight(insight_id="i1", rank=1, topic="다른쪽판", line="x")])  # fmt: skip
        store.put(bt.DAY, _rules(env), out, bt.NOW, trigger="manual", requested_by="재현")
        store.release_build(token)

    async def run() -> TrendInsightBriefOutput:
        waiter = asyncio.create_task(other())
        out = (await _make(env)).output
        await waiter
        return out

    got = asyncio.run(run())
    store.close()
    assert got.cached and [i.topic for i in got.insights] == ["다른쪽판"]


def _seed(env: dict, now: datetime) -> None:
    store = tib.BriefStore(env["DATA_INSIGHT_BRIEF_DB"])
    day, rules = tib.today_kst(now), _rules(env)
    auto = datetime(2026, 11, 8, 15, 27, tzinfo=UTC)  # 한국 11-09 00:27
    manual = datetime(2026, 11, 9, 2, 16, tzinfo=UTC)  # 한국 11:16
    a = TrendInsightBriefOutput(status="known", insights=[
        TrendInsight(insight_id=f"i{t}", rank=n, topic=t, line="x")
        for n, t in enumerate(["A", "B", "C"], 1)])  # fmt: skip
    b = TrendInsightBriefOutput(status="known", insights=[
        TrendInsight(insight_id=f"i{t}", rank=n, topic=t, line="x")
        for n, t in enumerate(["A", "C", "D", "E"], 1)])  # fmt: skip
    store.put(day, rules, a, auto, trigger="auto", detect_key="k1", detect_as_of=auto,
              tokens=50_000)  # fmt: skip
    store.put(day, rules, b, manual, trigger="manual", requested_by="재현", detect_key="k2",
              detect_as_of=manual, tokens=None)  # fmt: skip
    store.close()


def test_decorate_labels_diff_count_and_estimate(tmp_path: Path) -> None:
    env = _env(tmp_path)
    now = datetime(2026, 11, 9, 3, 0, tzinfo=UTC)  # 한국 12:00
    _seed(env, now)
    out = bv.decorate(tib.TrendInsightBriefOutput(), env, now, version=2)
    assert out.version == 2 and out.version_label == "11:16 다시 추천"
    assert [v.label for v in out.versions] == ["00:27 자동", "11:16 다시 추천"]
    assert (out.regen_used, out.regen_limit, out.latest_version) == (1, 3, 2)
    d = out.diff_from_previous
    assert d is not None and (d.new_count, d.gone_count) == (2, 1) and d.gone_topics == ["B"]
    assert d.from_label == "00:27 자동"
    # 다시 추천 토큰 기록이 없으면 첫 자동 브리프 토큰으로 추정 (50,000 ÷ 400,000)
    assert (out.estimated_llm_pct, out.estimated) == (12.5, True)
    assert out.regen_blocked is not None and out.regen_blocked.code == "cooldown"
    assert out.regen_blocked.text == "12:16 이후에 다시 추천할 수 있어요"
    first = bv.decorate(tib.TrendInsightBriefOutput(), env, now, version=1)
    assert first.version_label == "00:27 자동" and first.diff_from_previous is None


def test_estimate_uses_the_median_of_real_regenerations(tmp_path: Path) -> None:
    env = _env(tmp_path)
    store = tib.BriefStore(env["DATA_INSIGHT_BRIEF_DB"])
    out = TrendInsightBriefOutput(status="known")
    for i, tokens in enumerate((30_000, 40_000, 90_000)):
        store.put(bt.DAY, _rules(env), out, bt.NOW + timedelta(hours=i), trigger="manual",
                  requested_by="재현", tokens=tokens)  # fmt: skip
    pct, basis, guessed = bv.estimate(store, store.versions(bt.DAY, _rules(env)), 400_000)
    store.close()
    assert (pct, guessed) == (10.0, False) and basis == "최근 다시 추천 3번의 토큰 중앙값"


def test_in_progress_text_says_who_and_when(tmp_path: Path) -> None:
    env = _env(tmp_path)
    store = tib.BriefStore(env["DATA_INSIGHT_BRIEF_DB"])
    now = datetime(2026, 11, 9, 2, 16, tzinfo=UTC)  # 한국 11:16
    token = store.claim_build("재현", "manual", now)
    why = bv.blocked(store, bt.DAY, _rules(env), now, llm_share=0.1, detect=bv.Detect(None, "k"))
    assert why is not None and why.text == "지금 만드는 중이에요 (재현이 11:16에 시작)"
    store.release_build(token or "")
    store.claim_build("자동", "auto", now)
    why = bv.blocked(store, bt.DAY, _rules(env), now, llm_share=0.1, detect=bv.Detect(None, "k"))
    store.close()
    assert why is not None and why.text == "지금 만드는 중이에요 (자동으로 11:16에 시작)"


# ---------------------------------------------------------------- 핸들러


def _request(payload: dict) -> AgentRequest:
    meta = RequestMeta(context_id="ctx_test_regen", token="t",
                       deadline=datetime.now(UTC) + timedelta(minutes=1))  # fmt: skip
    return AgentRequest(meta=meta, skill="trend_insight_brief", input=payload)


def test_handler_regenerate_status_version_and_common_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: llm_budget.Budget
) -> None:
    from ax_agent_template import common_log
    from ax_contracts.data_access.common_log import CommonLogQuery
    from data_agent import handlers

    env = _env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(bv, "COOLDOWN", timedelta(0))
    common_log.set_store(common_log.SqliteLogStore(str(tmp_path / "common.db")))
    now = datetime.now(UTC)
    store = tib.BriefStore(env["DATA_INSIGHT_BRIEF_DB"])
    store.put(tib.today_kst(now), _rules(env), TrendInsightBriefOutput(status="known", insights=[
        TrendInsight(insight_id="iA", rank=1, topic="A", line="x")]), now, trigger="auto",
        detect_key="old")  # fmt: skip
    store.close()

    async def fake_build(request, data, env_, build=None):  # noqa: ANN001, ANN202
        await asyncio.sleep(0.1)
        budget.add(1, 1234, skill="trend_insight_brief", task="문장")
        build.save(TrendInsightBriefOutput(status="known", insights=[
            TrendInsight(insight_id="iB", rank=1, topic="B", line="x")]), tokens=1234)  # fmt: skip

    monkeypatch.setattr(handlers, "_build_brief", fake_build)

    async def run() -> tuple:
        started = await handlers.HANDLERS["trend_insight_brief"](
            _request({"action": "regenerate_start", "requested_by": "재현"}))
        again = await handlers.HANDLERS["trend_insight_brief"](
            _request({"action": "regenerate_start", "requested_by": "태윤"}))
        await bv.wait_all()
        status = await handlers.HANDLERS["trend_insight_brief"](_request({"action": "status"}))
        old = await handlers.HANDLERS["trend_insight_brief"](_request({"version": 1}))
        return started, again, status, old

    try:
        started, again, status, old = asyncio.run(run())
        events = common_log.get_store().query(CommonLogQuery(kinds=["event_published"])).records
    finally:
        common_log.reset_store()
    assert started.outcome == "started"
    assert again.outcome == "blocked" and again.output["regen_blocked"]["code"] == "in_progress"
    job = status.output["regen_job"]
    assert (job["state"], job["version"], job["tokens"]) == ("done", 2, 1234)
    assert status.output["insights"] == [] and status.output["regen_used"] == 1
    assert status.output["version_label"].endswith("다시 추천")
    assert old.output["version"] == 1 and old.output["insights"][0]["topic"] == "A"
    assert old.output["version_label"].endswith("자동") and old.output["latest_version"] == 2
    got = [(e.payload["event"], e.payload.get("requested_by"), e.payload.get("tokens"))
           for e in events]  # fmt: skip
    assert ("brief_regen_started", "재현", None) in got
    assert ("brief_regen_finished", "재현", 1234) in got


def test_handler_blocks_at_70_percent_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: llm_budget.Budget
) -> None:
    from data_agent import handlers

    for k, v in _env(tmp_path).items():
        monkeypatch.setenv(k, v)
    budget.add(10, 290_000, skill="trend_popular", task="분류")  # 72.5%
    got = asyncio.run(handlers.HANDLERS["trend_insight_brief"](
        _request({"action": "regenerate_start", "requested_by": "재현"})))  # fmt: skip
    assert got.outcome == "blocked"
    assert got.output["regen_blocked"] == {"code": "llm_budget",
                                           "text": "LLM 오늘 72% · 다시 추천은 70% 미만일 때만"}  # fmt: skip
