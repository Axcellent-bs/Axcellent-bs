import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_orchestrator.main import app, state
from ax_orchestrator.mock.harness import Harness, new_incident
from ax_orchestrator.models import now
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.usage import CallRecord, RecordingCaller, UsageRecorder

# admin 토큰. 명부는 conftest.py의 `_write_tokens`가 넣는다
ADMIN = {"Authorization": "Bearer admin-test-token"}


def _req(skill: str = "impact_analysis", key: str | None = "k1") -> AgentRequest:
    meta = RequestMeta(
        context_id="ctx_inc_u",
        token="t",
        deadline=now() + timedelta(seconds=30),
        idempotency_key=key,
    )
    return AgentRequest(meta=meta, skill=skill)


def _rec(dept="data", skill="impact_analysis", status="completed", ms=100, ago=5.0, key=None):
    return CallRecord(
        dept, skill, now() - timedelta(seconds=ago), ms, status, None, "ctx_inc_u", key
    )


def test_summary_counts_success_rate_latency_and_retries() -> None:
    usage = UsageRecorder()
    usage.add(_rec(ms=100, key="a"))
    usage.add(_rec(ms=300, status="failed", key="b"))
    usage.add(_rec(ms=200, key="b"))  # 같은 멱등 키 = 재시도 1회
    usage.add(_rec(status="input-required", ms=50, key="c"))  # 대기는 성공률에서 뺀다
    usage.add(_rec(dept="archive"))
    s = usage.summary("data")
    assert (s["calls"], s["errors"], s["waiting"], s["retries"]) == (4, 1, 1, 1)
    assert s["success_rate"] == pytest.approx(0.667, abs=0.001)
    assert s["avg_ms"] == 162 and s["p95_ms"] == 300
    assert usage.summary("finance")["success_rate"] is None  # 호출이 없으면 지어내지 않는다


def test_old_calls_fall_out_of_the_window_and_series_has_fixed_buckets() -> None:
    usage = UsageRecorder()
    usage.add(_rec(ago=5))
    usage.add(_rec(ago=3600))  # 1시간 전
    assert usage.summary("data")["calls"] == 1
    series = usage.series("data")
    assert len(series) == 20 and sum(b["calls"] for b in series) == 1
    assert all(b["avg_ms"] is None for b in series if b["calls"] == 0)


def test_per_skill_summary() -> None:
    usage = UsageRecorder()
    usage.add(_rec(skill="a"))
    usage.add(_rec(skill="b"))
    assert usage.summary("data", skill="a")["calls"] == 1


@pytest.mark.asyncio
async def test_recording_caller_records_result_and_exception(monkeypatch) -> None:
    usage = UsageRecorder()
    # 실제 sleep은 Windows 타이머 해상도 때문에 10ms보다 짧게 깨어날 수 있다. 가짜 시계로 고정한다.
    clock = [0.0]
    monkeypatch.setattr(
        "ax_orchestrator.usage.time", type("FakeTime", (), {"perf_counter": lambda: clock[0]})
    )

    async def inner(dept, req):
        if req.skill == "boom":
            raise RuntimeError("down")
        clock[0] += 0.01
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED)

    caller = RecordingCaller(inner, usage)
    assert (await caller("data", _req())).status == TaskStatus.COMPLETED
    with pytest.raises(RuntimeError):
        await caller("data", _req("boom", "k2"))
    s = usage.summary("data")
    assert (s["calls"], s["errors"]) == (2, 1)
    assert usage.recent("data")[0]["status"] == "error"
    assert usage.recent("data")[1]["duration_ms"] >= 10


@pytest.mark.asyncio
async def test_scenario_records_every_call_per_dept(harness: Harness) -> None:
    engine = await harness.start()
    usage = UsageRecorder()
    engine.caller = RecordingCaller(engine.caller, usage)
    await engine.run(new_incident(), scenario_a())
    # 공지 초안·게시 2쌍 + 사과·보상 SNS 글 초안 2개
    assert usage.summary("marketing")["calls"] == 6
    assert usage.summary("archive")["calls"] == 4  # 유사 이벤트 조회, 기록, 해결 내용, 시각
    # 영향 분석 + 원인 후보 추정 + 처리 시간 요약(kpi_summary)
    assert usage.summary("data")["calls"] == 3


def _fresh_state(monkeypatch) -> None:
    monkeypatch.setattr(state, "mock", True)
    monkeypatch.setattr(state, "latency", 0.0)
    monkeypatch.setattr(state, "manager", None)
    monkeypatch.setattr(state, "usage", UsageRecorder())


def _wait_done(client, ctx: str) -> None:
    for _ in range(300):
        if client.get(f"/api/incidents/{ctx}").json()["data"]["status"] != "open":
            return
        time.sleep(0.02)


def test_departments_endpoint_exposes_usage_after_an_incident(monkeypatch) -> None:
    _fresh_state(monkeypatch)
    with TestClient(app) as client:
        before = client.get("/api/departments").json()
        assert [d["name"] for d in before["data"]][:3] == ["infra", "data", "planning"]
        assert all(d["usage"]["calls"] == 0 for d in before["data"])
        created = client.post("/api/demo/incidents", json={"fail_marketing": True}, headers=ADMIN)
        ctx = created.json()["data"]["context_id"]
        _wait_done(client, ctx)
        after = {d["name"]: d for d in client.get("/api/departments").json()["data"]}
    marketing = after["marketing"]
    # 공지 4번 + 게시 재시도 1번 + 사과·보상 SNS 글 초안 2번
    assert marketing["usage"]["calls"] == 7 and marketing["usage"]["retries"] == 1
    publish = next(s for s in marketing["skills"] if s["id"] == "publish_notice")
    assert publish["calls"] == 3 and publish["errors"] == 1
    assert len(marketing["series"]) == 20 and marketing["recent"][0]["skill"] == "publish_notice"
    assert after["archive"]["usage"]["calls"] == 4


def test_departments_marks_unavailable_dept(monkeypatch) -> None:
    _fresh_state(monkeypatch)
    with TestClient(app) as client:
        client.post("/api/demo/incidents", json={"infra_card_empty": True}, headers=ADMIN)
        depts = client.get("/api/departments").json()["data"]
    infra = next(d for d in depts if d["name"] == "infra")
    assert infra["available"] is False and infra["skills"] == []
    assert "카드" in infra["unavailable_reason"]


def test_usage_is_counted_per_company() -> None:
    """부서별 사용량은 그 호출의 회사로 나눠 센다 (11/25, 로드맵 6단계). 회사를 안 주면 전부."""
    usage = UsageRecorder()
    cat = _rec(ms=100)
    nubi = CallRecord("data", "impact_analysis", now(), 300, "completed", None, "ctx_n", None,
                      company="nubi-tour")  # fmt: skip
    usage.add(cat)
    usage.add(nubi)
    assert cat.company == "catbooks"  # 예전 기록·회사 없는 호출은 캣북스
    assert usage.summary("data", company="nubi-tour")["calls"] == 1
    assert usage.summary("data", company="nubi-tour")["avg_ms"] == 300
    assert usage.summary("data", company="catbooks")["calls"] == 1
    assert usage.summary("data")["calls"] == 2
    assert sum(b["calls"] for b in usage.series("data", company="nubi-tour")) == 1
    assert [r["context_id"] for r in usage.recent("data", company="nubi-tour")] == ["ctx_n"]


@pytest.mark.asyncio
async def test_recording_caller_stamps_request_company() -> None:
    usage = UsageRecorder()

    async def inner(_dept: str, req: AgentRequest) -> AgentResult:
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED)

    req = _req()
    nubi = req.model_copy(update={"meta": req.meta.model_copy(update={"tenant_id": "nubi-tour"})})
    await RecordingCaller(inner, usage)("data", nubi)
    await RecordingCaller(inner, usage)("data", _req())  # 기본 tenant(shop-demo) = 캣북스
    assert [r.company for r in usage._records] == ["nubi-tour", "catbooks"]
