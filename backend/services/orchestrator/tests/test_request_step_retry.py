"""의뢰 단계가 실패하면 그 단계부터 다시 (11/27 `retry_step`).

앞 단계(완료·결과 번호·넘길 값)와 계획은 그대로 두고 그 단계와 뒤 단계만 되돌린다. 보고서·제안·
제작 기록을 다시 만들지 않는다. 부서에는 새 멱등 키(:retryN)로 보낸다. 가짜 부서만 쓴다."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import TaskStatus
from ax_orchestrator.main import app, state
from ax_orchestrator.request_intake import RequestIntake
from ax_orchestrator.request_runner import RETRY_MAX, RequestRunner, downstream, retryable
from ax_orchestrator.store import IncidentStore
from ax_orchestrator.work_requests import WorkRequests


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


B = _load("brand_character_helpers_retry", "test_request_brand_character.py")
FAILED = B.result(TaskStatus.FAILED, output={"retryable": False})


def _keys(fake: Any, skill: str) -> list[str]:
    return [c[1].meta.idempotency_key for c in fake.calls if c[1].skill == skill]


async def _start(fake: Any) -> tuple[Any, str, RequestRunner]:
    reqs, rid = B.make()
    r = RequestRunner(reqs, lambda: fake, retry_delay=0)
    await r.run(rid)
    assert r.set_brand_character(rid, None) == "ok"
    await r.wait_idle()
    return reqs, rid, r


def test_downstream_and_retryable_rules() -> None:
    plan = [{"step_id": "a"}, {"step_id": "b", "after": ["a"]}, {"step_id": "c", "after": ["b"]},
            {"step_id": "x"}]  # fmt: skip
    assert downstream(plan, "b") == ["b", "c"] and downstream(plan, "a") == ["a", "b", "c"]
    assert retryable({"status": "failed"})
    assert retryable({"status": "rejected_by_human", "reason_code": "video_rejected"})
    assert retryable({"status": "skipped", "reason_code": "no_insight"})
    assert not retryable({"status": "rejected_by_human", "reason_code": "human_check"})
    assert not retryable({"status": "skipped", "reason_code": None})  # 앞 단계 때문에 건너뜀
    assert not retryable({"status": "completed"})


@pytest.mark.asyncio
async def test_trend_report_failed_then_retry_makes_a_new_report_and_keeps_step_one() -> None:
    fake = B.Fake()
    fake.answers["trend_report_create"] = [FAILED, B.result(TaskStatus.COMPLETED,
                                           output={"report_id": "rep_2"})]  # fmt: skip
    reqs, rid, r = await _start(fake)
    row = reqs.get(rid)
    assert row["steps"]["trend"]["status"] == "failed" and row["status"] == "needs_human"
    assert row["steps"]["insight"]["status"] == "skipped"
    char_before = row["steps"]["character"]
    assert r.retry_step(rid, "trend", by="재현") == "ok"
    await r.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["character"] == char_before  # 1단계 그대로
    assert row["steps"]["trend"]["result_ref"] == "rep_2"
    assert row["steps"]["trend"]["retry_count"] == 1
    assert row["steps"]["confirm"]["status"] == "waiting_human"
    rid_key = f"{rid}:trend_report_create:trend"
    assert _keys(fake, "trend_report_create") == [rid_key, f"{rid_key}:retry1"]  # 새 멱등 키
    assert any(e["message"] == "사람이 2단계를 다시 시도 (재현, 1/3)" for e in row["log"])


@pytest.mark.asyncio
async def test_insight_failed_reuses_the_report() -> None:
    fake = B.Fake()
    fake.answers["trend_report_insight"] = [FAILED, B.Fake().answers["trend_report_insight"][0]]
    reqs, rid, r = await _start(fake)
    assert reqs.get(rid)["steps"]["insight"]["status"] == "failed"
    assert r.retry_step(rid, "insight") == "ok"
    await r.wait_idle()
    assert fake.skills().count("trend_report_create") == 1  # 보고서 다시 안 만듦
    insight = [c[1] for c in fake.calls if c[1].skill == "trend_report_insight"]
    assert [c.input["report_id"] for c in insight] == ["rep_1", "rep_1"]
    assert reqs.get(rid)["steps"]["confirm"]["status"] == "waiting_human"


@pytest.mark.asyncio
async def test_plan_failed_retries_only_planning_with_the_same_insight() -> None:
    fake = B.Fake()
    good = B.Fake().answers["receive_insight"][0]
    fake.answers["receive_insight"] = [FAILED, good]
    reqs, rid, r = await _start(fake)
    assert reqs.get(rid)["steps"]["plan"]["status"] == "failed"
    assert r.retry_step(rid, "plan") == "ok"
    await r.wait_idle()
    skills = fake.skills()
    assert skills.count("trend_report_create") == 1 and skills.count("trend_report_insight") == 1
    plans = [c[1] for c in fake.calls if c[1].skill == "receive_insight"]
    assert len(plans) == 2 and plans[0].input == plans[1].input  # 같은 인사이트(넘길 값 그대로)
    row = reqs.get(rid)
    assert row["steps"]["plan"]["result_ref"] in (None, "PROP-1")
    assert row["steps"]["confirm"]["status"] == "waiting_human"


@pytest.mark.asyncio
async def test_prepare_failed_retries_with_the_same_plan_and_character() -> None:
    fake = B.Fake()
    fake.answers["prepare_video_draft_from_plan"] = [FAILED, B.Fake.prepared(B.MADE)]
    reqs, rid, r = await _start(fake)
    assert r.set_brand_character(rid, B.BRAND, by="준하") == "ok"
    r.decide(rid, "confirm", True, by="준하")
    await r.wait_idle()
    assert reqs.get(rid)["steps"]["prepare"]["status"] == "failed"
    assert r.retry_step(rid, "prepare") == "ok"
    await r.wait_idle()
    prep = [c[1] for c in fake.calls if c[1].skill == "prepare_video_draft_from_plan"]
    assert len(prep) == 2 and prep[0].input == prep[1].input  # 같은 제작안·캐릭터 → 같은 기록
    assert fake.skills().count("receive_insight") == 1
    row = reqs.get(rid)
    assert row["steps"]["confirm"]["status"] == "completed"  # 사람 확인 다시 안 함
    assert row["steps"]["prepare"]["result_ref"] == B.MADE
    assert row["steps"]["make"]["status"] == "waiting_async"


@pytest.mark.asyncio
async def test_video_failed_retries_only_step_seven_with_a_new_key() -> None:
    fake = B.Fake()
    reqs, rid, r = await B.until_video_held(fake)
    failed = B.done_event("t1", False, error="video_failed", creative_record_id=B.MADE)
    # 승인 뒤 만들다 실패한 경우로 시험한다 (긴 작업 대기에서 실패 알림)
    reqs.update_step(rid, "make", status="waiting_async", async_task_id="t1")
    assert r.receive_async("completed", "marketing", failed, f"ctx_{rid}") == "resumed"
    await r.wait_idle()
    before = reqs.get(rid)
    assert before["steps"]["make"]["status"] == "failed"
    kept = {k: v for k, v in before["steps"].items() if k != "make"}
    fake.answers["generate_media"] = [B.Fake.working("t3")]
    assert r.retry_step(rid, "make", by="재현") == "ok"
    await r.wait_idle()
    after = reqs.get(rid)
    assert {k: v for k, v in after["steps"].items() if k != "make"} == kept  # 1~6 그대로
    keys = _keys(fake, "generate_media")
    assert keys[-1] == f"{rid}:generate_media:make:retry1" and keys[0] != keys[-1]
    assert after["steps"]["make"]["status"] == "waiting_async"
    assert any("7단계를 다시 시도 (재현, 1/3)" in e["message"] for e in after["log"])


@pytest.mark.asyncio
async def test_busy_limit_stopped_and_not_failed() -> None:
    fake = B.Fake()
    fake.answers["trend_report_create"] = [FAILED]
    reqs, rid, r = await _start(fake)
    assert r.retry_step(rid, "character") == "not_failed"
    assert r.retry_step(rid, "nope") == "missing" and r.retry_step("REQ-x", "trend") == "missing"
    for n in range(1, RETRY_MAX + 1):
        assert r.retry_step(rid, "trend") == "ok"
        await r.wait_idle()
        assert reqs.get(rid)["steps"]["trend"]["retry_count"] == n
    assert r.retry_step(rid, "trend") == "limit"
    assert reqs.get(rid)["steps"]["trend"]["retry_count"] == RETRY_MAX
    # 기다리는 단계가 있으면 busy
    fake2 = B.Fake()
    fake2.answers["receive_insight"] = [FAILED]
    reqs2, rid2, r2 = await _start(fake2)
    reqs2.update_step(rid2, "insight", status="waiting_async")
    assert r2.retry_step(rid2, "plan") == "busy"
    # 사람이 삭제한 의뢰
    reqs2.update_step(rid2, "insight", status="completed")
    assert r2.hide(rid2, by="재현") == "ok"
    assert r2.retry_step(rid2, "plan") == "stopped"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setattr(state, "mock", True)
    monkeypatch.setattr(state, "latency", 0.0)
    reqs = WorkRequests(IncidentStore(company_id="catbooks"))
    manager = SimpleNamespace(
        work_requests=reqs,
        request_intake=RequestIntake(reqs),
        request_runner=RequestRunner(reqs, lambda: None, retry_delay=0),
    )
    monkeypatch.setattr(state, "manager", None)
    with TestClient(app) as c:
        monkeypatch.setattr(state, "manager", manager)
        c.reqs = reqs  # type: ignore[attr-defined]
        yield c


def test_api_codes(client: Any) -> None:
    reqs = client.reqs
    _, rid = B.make(reqs)
    path = f"/api/requests/{rid}/steps/trend/retry"
    assert client.post(path).status_code in (401, 403)
    nope = "/api/requests/REQ-nope/steps/trend/retry"
    assert client.post(nope, headers=B.HUMAN).status_code == 404
    assert client.post(path, headers=B.HUMAN).status_code == 422  # 실패한 단계가 아님
    reqs.update_step(rid, "trend", status="failed")
    reqs.update_step(rid, "insight", status="waiting_async")
    assert client.post(path, headers=B.HUMAN).status_code == 409
    reqs.update_step(rid, "insight", status="skipped")
    reqs.update_step(rid, "trend", retry_count=RETRY_MAX)
    assert client.post(path, headers=B.HUMAN).status_code == 422  # 상한
