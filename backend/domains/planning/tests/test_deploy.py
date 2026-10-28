"""배포 기록·배포 후 확인 (deploy.py, deploy_signals.py, deploy_store.py, REST)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentResult, ExecuteDecision, TaskStatus
from planning_agent.deploy_signals import (
    CombinedSource,
    DataAnomalySource,
    DejaFixIncidentSource,
    SignalUnavailable,
    parse_incidents,
)
from planning_agent.deploy_store import DeployStore, set_deploy_store
from planning_agent.domain.clock import kst
from planning_agent.domain.deploy import (
    Deployment,
    Signal,
    build_deployment,
    evaluate_check,
    rollback_grade,
)
from planning_agent.domain.models import PR, PrGate
from planning_agent.main import app

BASE = "/api/planning"
T0 = kst("10-21 14:02")
CAUSE_WORDS = ("원인", "때문", "탓", "로 인해", "유발")


@pytest.fixture(autouse=True)
def store() -> DeployStore:
    return set_deploy_store(DeployStore())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _pr(n: int, paths: list[str], merged: bool = True) -> PR:
    return PR(
        id=f"PR #{n}",
        number=n,
        task="T-1",
        title="t",
        branch="b",
        reviews=["통과"],
        gate=PrGate(result="통과", lines=1, files=1, paths="shop-api/"),
        merged=merged,
        merged_at=T0 if merged else None,
        changed_paths=paths,
    )


def _dep(prs: list[int] | None = None, paths: list[str] | None = None) -> Deployment:
    return Deployment(
        id="DEP-9",
        pr_numbers=prs or [28],
        env="dev",
        at=T0,
        status="succeeded",
        changed_paths=paths or [],
    )


def _incident(minutes: int, ref: str = "inc_1") -> Signal:
    return Signal(kind="incident", source="mock", ref=ref, at=T0 + timedelta(minutes=minutes))


# ---------------------------------------------------------------- 등급 기준


def test_rollback_grade_b_when_all_within_limits() -> None:
    params = {
        "suspect_count": 1,
        "low_confidence": 0,
        "irreversible_count": 0,
        "prior_rollbacks": 0,
    }
    assert rollback_grade(params) == ("B", [])


@pytest.mark.parametrize(
    ("key", "value", "rule"),
    [
        ("suspect_count", 2, "rollback_multi_suspect"),
        ("low_confidence", 1, "rollback_low_confidence"),
        ("irreversible_count", 1, "rollback_irreversible"),
        ("prior_rollbacks", 1, "rollback_repeat"),
    ],
)
def test_rollback_grade_c_thresholds(key: str, value: int, rule: str) -> None:
    params = {
        "suspect_count": 1,
        "low_confidence": 0,
        "irreversible_count": 0,
        "prior_rollbacks": 0,
    }
    grade, rules = rollback_grade({**params, key: value})
    assert grade == "C"
    assert rules == [rule]


@pytest.mark.parametrize("bad", [None, "0", 1.0, -1, True])
def test_rollback_grade_fail_closed(bad) -> None:
    params = {
        "suspect_count": 1,
        "low_confidence": 0,
        "irreversible_count": 0,
        "prior_rollbacks": 0,
    }
    params["low_confidence"] = bad
    grade, rules = rollback_grade(params)
    assert grade == "C"
    assert "invalid_low_confidence" in rules
    params.pop("low_confidence")
    assert rollback_grade(params)[0] == "C"


# ---------------------------------------------------------------- 판정


def test_check_ok_without_signals() -> None:
    res = evaluate_check(_dep(), [], at=T0)
    assert res.verdict == "ok"
    assert res.rollback is None
    assert res.candidate_prs == []
    assert res.evidence == ["배포 후 30분 안에 열린 장애·이상 지표 없음"]


def test_check_ignores_incident_outside_window() -> None:
    res = evaluate_check(_dep(), [_incident(-5), _incident(45)], at=T0)
    assert res.verdict == "ok"


def test_check_watch_on_anomaly_or_unavailable() -> None:
    anomaly = Signal(kind="anomaly", source="data", ref="payment_fail_rate")
    assert evaluate_check(_dep(), [anomaly], at=T0).verdict == "watch"
    down = Signal(kind="unavailable", source="dejafix")
    res = evaluate_check(_dep(), [down], at=T0)
    assert res.verdict == "watch"
    assert res.evidence == ["dejafix 신호를 읽지 못함"]
    undated = Signal(kind="incident", source="dejafix", ref="x")
    assert evaluate_check(_dep(), [undated], at=T0).verdict == "watch"


def test_check_suspect_b_when_one_pr_and_close() -> None:
    res = evaluate_check(_dep(), [_incident(12)], at=T0)
    assert res.verdict == "suspect"
    assert res.rollback is not None
    # 12분은 가깝지 않다 → 확신도 낮음 → C
    assert res.rollback.low_confidence == 1
    assert res.rollback.grade == "C"
    res = evaluate_check(_dep(), [_incident(2)], at=T0)
    assert res.rollback.grade == "B"
    assert res.evidence[0] == "배포 후 2분에 장애 1건 열림 (inc_1)"
    assert res.candidate_prs == ["PR #28"]


def test_check_suspect_c_multi_pr_irreversible_repeat() -> None:
    res = evaluate_check(_dep(prs=[28, 29]), [_incident(2)], at=T0)
    assert res.rollback.suspect_count == 2
    assert res.rollback.grade == "C"
    dep = _dep(paths=["shop-api/payment/migrations/0003.py"])
    res = evaluate_check(dep, [_incident(2)], at=T0)
    assert res.rollback.irreversible_count == 1
    assert res.rollback.grade == "C"
    res = evaluate_check(_dep(), [_incident(2)], at=T0, prior_rollbacks=1)
    assert res.rollback.rules == ["rollback_repeat"]


def test_check_evidence_has_no_cause_wording() -> None:
    sigs = [
        _incident(3),
        Signal(kind="incident", source="dejafix", title="DB 원인 장애"),
        Signal(kind="anomaly", source="data", ref="payment_fail_rate", at=T0),
        Signal(kind="unavailable", source="data", title="원인 불명 오류"),
    ]
    res = evaluate_check(_dep(), sigs, at=T0)
    for line in res.evidence:
        assert not any(w in line for w in CAUSE_WORDS), line


# ---------------------------------------------------------------- 배포 만들기


def test_build_deployment_dev_succeeds_prod_waits() -> None:
    dev = build_deployment(1, [_pr(28, ["shop-api/payment/config.py"])], "dev", T0)
    assert dev.status == "succeeded"
    assert dev.approval_reasons == []
    prod = build_deployment(2, [_pr(28, ["shop-api/payment/config.py"])], "prod", T0)
    assert prod.status == "requested"
    assert prod.approval_reasons == ["prod"]


def test_build_deployment_protected_path_waits() -> None:
    dep = build_deployment(1, [_pr(30, ["shop-web/src/lib/auth.tsx"])], "dev", T0)
    assert dep.status == "requested"
    assert dep.approval_reasons == ["protected_path"]
    assert dep.protected_paths == ["shop-web/src/lib/auth.tsx"]


def test_build_deployment_rejects_unmerged() -> None:
    from planning_agent.domain.deploy import DeployError

    with pytest.raises(DeployError) as err:
        build_deployment(1, [_pr(31, [], merged=False)], "dev", T0)
    assert err.value.code == "PR_NOT_MERGED"


# ---------------------------------------------------------------- 신호원


def test_parse_incidents_defensive() -> None:
    body = {
        "data": [
            {"id": 7, "name": "결제 지연", "startedAt": "2026-10-21T05:04:00Z"},
            {"context_id": "c2", "title": "t2", "opened_at": "2026-10-21T05:10:00"},
            {"title": "시각 없음"},
            "garbage",
        ]
    }
    rows = parse_incidents(body)
    assert [r.ref for r in rows] == ["7", "c2", None]
    assert rows[0].at == kst("10-21 14:04")
    assert rows[1].at == kst("10-21 14:10")  # 시간대 없는 값은 UTC
    assert rows[2].at is None
    with pytest.raises(SignalUnavailable):
        parse_incidents({"ok": True})


def test_dejafix_source_with_mock_transport() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=[{"id": "inc_9", "title": "x", "open_time": "2026-10-21T05:05:00Z"}]
        )

    async def run() -> list[Signal]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            src = DejaFixIncidentSource("http://dejafix.test/", client=http)
            return await src.signals(_dep(), T0, T0)

    rows = asyncio.run(run())
    assert seen[0].url.path == "/api/incidents"
    assert seen[0].url.params["status"] == "problem"
    assert rows[0].ref == "inc_9"
    assert rows[0].at is None  # open_time은 모르는 이름 → 시각 없음 → watch


def test_dejafix_source_error_becomes_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def run() -> list[Signal]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            src = CombinedSource([("dejafix", DejaFixIncidentSource("http://d", client=http))])
            return await src.signals(_dep(), T0, T0)

    rows = asyncio.run(run())
    assert [r.kind for r in rows] == ["unavailable"]


def test_data_source_not_called_in_mock() -> None:
    called: list[str] = []

    async def caller(target, req):
        called.append(target)
        raise AssertionError("목 모드에서 부르면 안 된다")

    rows = asyncio.run(DataAnomalySource(caller).signals(_dep(), T0, T0))
    assert rows == [] and called == []


def test_data_source_real_mode(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "0")
    sent = []

    async def caller(target, req):
        sent.append((target, req))
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            output={
                "verdicts": [
                    {
                        "metric": "payment_fail_rate",
                        "observed": 0.08,
                        "baseline": 0.02,
                        "ratio": 4.0,
                        "is_anomaly": True,
                        "severity": "high",
                    },
                    {"metric": "order_count", "observed": 1, "baseline": 1, "is_anomaly": False},
                ],
                "any_anomaly": True,
                "summary": "s",
            },
        )

    rows = asyncio.run(DataAnomalySource(caller).signals(_dep(), T0, T0 + timedelta(minutes=30)))
    assert sent[0][0] == "data"
    assert sent[0][1].skill == "anomaly_detection"
    assert [r.ref for r in rows] == ["payment_fail_rate"]
    assert rows[0].kind == "anomaly"


# ---------------------------------------------------------------- REST


def test_seed_deployment_is_suspect(client) -> None:
    body = client.get(f"{BASE}/deployments").json()
    assert body["meta"]["count"] == 1
    dep = body["data"][0]
    assert dep["id"] == "DEP-1"
    assert dep["pr_numbers"] == [28]
    assert dep["version"] == "shop-api v2.4.1"
    assert dep["status"] == "succeeded"
    check = dep["check"]
    assert check["verdict"] == "suspect"
    assert check["evidence"][0] == "배포 후 2분에 장애 1건 열림 (ctx_inc_0001)"
    assert check["rollback"]["grade"] == "B"
    assert check["candidate_prs"] == ["PR #28"]


def test_rest_prod_needs_approval_then_check(client, service) -> None:
    res = client.post(f"{BASE}/deployments", json={"pr_numbers": [28], "env": "prod"})
    assert res.status_code == 200
    body = res.json()
    assert body["meta"]["needs_approval"] is True
    dep = body["data"]
    assert dep["id"] == "DEP-2"
    assert dep["status"] == "requested"
    assert [c.action for c in service.gate_calls] == ["request_deploy"]

    res = client.post(f"{BASE}/deployments/DEP-2/check")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "NOT_DEPLOYED"

    res = client.post(f"{BASE}/deployments/DEP-2/approve", json={"approved_by": "김팀장"})
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "succeeded"
    assert res.json()["data"]["approved_by"] == "김팀장"
    assert service.gate_calls[-1].action == "approve_deploy"

    again = client.post(f"{BASE}/deployments/DEP-2/approve")
    assert again.status_code == 409

    res = client.post(f"{BASE}/deployments/DEP-2/check", json={"window_minutes": 60})
    assert res.status_code == 200
    detail = client.get(f"{BASE}/deployments/DEP-2").json()["data"]
    assert len(detail["checks"]) == 1
    # 목 시드 사건(10-21)은 새 배포 시각 뒤가 아니므로 장애 없음, 이상 지표도 구간 밖
    assert detail["check"]["verdict"] == "ok"
    # 배포 후 확인은 read: 관문을 거치지 않는다
    assert [c.action for c in service.gate_calls] == ["request_deploy", "approve_deploy"]


def test_rest_dev_deploy_succeeds(client) -> None:
    body = client.post(f"{BASE}/deployments", json={"pr_numbers": [28]}).json()
    assert body["data"]["status"] == "succeeded"
    assert body["meta"]["needs_approval"] is False


def test_rest_recheck_seed(client) -> None:
    res = client.post(f"{BASE}/deployments/DEP-1/check")
    assert res.status_code == 200
    assert res.json()["data"]["check"]["verdict"] == "suspect"
    detail = client.get(f"{BASE}/deployments/DEP-1").json()["data"]
    assert len(detail["checks"]) == 2


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"pr_numbers": []}, 400, "INVALID_DEPLOY"),
        ({"pr_numbers": ["28"]}, 400, "INVALID_DEPLOY"),
        ({"pr_numbers": [28], "env": "stage"}, 400, "INVALID_DEPLOY"),
        ({"pr_numbers": [999]}, 404, "NOT_FOUND"),
    ],
)
def test_rest_invalid_requests(client, body, status, code) -> None:
    res = client.post(f"{BASE}/deployments", json=body)
    assert res.status_code == status
    assert res.json()["error"]["code"] == code


def test_rest_unknown_deployment(client) -> None:
    assert client.get(f"{BASE}/deployments/DEP-99").status_code == 404
    assert client.post(f"{BASE}/deployments/DEP-99/check").status_code == 404
    assert client.post(f"{BASE}/deployments/DEP-99/approve").status_code == 404


def test_rest_gate_refusal_keeps_store(client, service, store) -> None:
    async def refuse(action, params, ctx, ev):
        return ExecuteDecision(execution_id="exe_x", grade="C", status="held")

    service._gate = refuse
    res = client.post(f"{BASE}/deployments", json={"pr_numbers": [28]})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "GATE_REJECTED"
    assert [d.id for d in store.deployments] == ["DEP-1"]


def test_reset_restores_seed(client) -> None:
    client.post(f"{BASE}/deployments", json={"pr_numbers": [28]})
    client.post(f"{BASE}/reset")
    assert client.get(f"{BASE}/deployments").json()["meta"]["count"] == 1
