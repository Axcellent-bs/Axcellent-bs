"""배포 기록·배포 후 확인 (deploy.py, deploy_signals.py, deploy_store.py, REST)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import config as template_config
from ax_contracts.schemas import AgentResult, ExecuteDecision, TaskStatus
from dev_agent.deploy import (
    Deployment,
    Signal,
    build_deployment,
    evaluate_check,
    rollback_grade,
)
from dev_agent.deploy_signals import (
    CombinedSource,
    DataAnomalySource,
    DejaFixIncidentSource,
    SignalUnavailable,
    parse_incidents,
)
from dev_agent.deploy_store import DeployStore, set_deploy_store
from dev_agent.main import app
from planning_agent.domain.clock import kst
from planning_agent.domain.models import PR, PrGate

pytestmark = pytest.mark.role("dev")  # 분리 3단계: 개발 서비스 역할로 돈다

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


def test_build_deployment_dev_approved_prod_waits() -> None:
    dev = build_deployment(1, [_pr(28, ["shop-api/payment/config.py"])], "dev", T0)
    # 운영 배포 도구라 승인 이유가 없어도 저절로 나가지 않는다
    # 사람이 눌러야 한다 ('배포됨'이 아니다)
    assert dev.status == "ready"
    assert dev.executed is False
    assert dev.note == "배포 실행은 사람이 눌러야 해요"
    assert len(dev.uid) == 8
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
    from dev_agent.deploy import DeployError

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


def test_dejafix_source_sends_agent_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENT_TEMPLATE_MOCK", "PLANNING_MOCK", "DEV_MOCK", "DEV_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AX_AGENT_TOKENS", "dev=tok-dev")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    async def run() -> list[Signal]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with template_config.agent_name_scope("dev"):
                return await DejaFixIncidentSource("http://d", client=http).signals(_dep(), T0, T0)

    asyncio.run(run())
    assert seen[0].headers["authorization"] == "Bearer tok-dev"


def test_dejafix_source_without_token_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    names = ("AGENT_TEMPLATE_MOCK", "PLANNING_MOCK", "DEV_MOCK", "DEV_TOKEN", "AX_AGENT_TOKENS")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json={"data": []})

    async def run() -> list[Signal]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            src = CombinedSource([("dejafix", DejaFixIncidentSource("http://d", client=http))])
            with template_config.agent_name_scope("dev"):
                return await src.signals(_dep(), T0, T0)

    rows = asyncio.run(run())
    assert [r.kind for r in rows] == ["unavailable"]
    assert not called


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
    # 승인했지만 배포 도구가 없어 실제 배포는 하지 않았다
    assert res.json()["data"]["status"] == "approved"
    assert res.json()["data"]["executed"] is False
    assert res.json()["data"]["approved_by"] == "김팀장"
    assert service.gate_calls[-1].action == "approve_deploy"

    again = client.post(f"{BASE}/deployments/DEP-2/approve", json={"approved_by": "김팀장"})
    assert again.status_code == 409

    # 실행되지 않은 배포는 배포 후 확인을 하지 않는다
    res = client.post(f"{BASE}/deployments/DEP-2/check", json={"window_minutes": 60})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "NOT_DEPLOYED"
    detail = client.get(f"{BASE}/deployments/DEP-2").json()["data"]
    assert detail["checks"] == []
    assert [c.action for c in service.gate_calls] == ["request_deploy", "approve_deploy"]


def test_rest_dev_deploy_recorded_not_executed(client) -> None:
    body = client.post(f"{BASE}/deployments", json={"pr_numbers": [28]}).json()
    assert body["data"]["status"] == "ready"
    assert body["data"]["executed"] is False
    assert body["meta"]["needs_approval"] is True  # 배포 실행은 사람이 누른다


def test_rest_recheck_seed(client, service) -> None:
    res = client.post(f"{BASE}/deployments/DEP-1/check")
    assert res.status_code == 200
    assert res.json()["data"]["check"]["verdict"] == "suspect"
    detail = client.get(f"{BASE}/deployments/DEP-1").json()["data"]
    assert len(detail["checks"]) == 2
    # 배포 후 확인은 read: 관문을 거치지 않는다
    assert service.gate_calls == []


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


def test_dev_signals_use_proxy_when_vault_has_dejafix(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ax_agent_template import tool_connections as tc
    from dev_agent import deploy_signals

    monkeypatch.setenv(tc.FILE_ENV, str(tmp_path / "tool-connections.json"))
    monkeypatch.delenv(deploy_signals.DEJAFIX_URL_ENV, raising=False)
    monkeypatch.delenv("AX_DEJAFIX_PROXY_URL", raising=False)
    tc._cache = None
    assert tc.dejafix_proxy_url(deploy_signals.DEJAFIX_URL_ENV) is None
    tc.upsert(
        "dejafix",
        "장애",
        {"url": "http://10.0.0.5", "username": "v"},
        {"password": "pw"},
        conn_id=None,
        by="t",
    )
    source = deploy_signals.default_source(deploy_signals.MockSignalSource([]))
    assert isinstance(source, CombinedSource)
    name, dejafix = source._sources[0]
    assert name == "dejafix"
    assert isinstance(dejafix, DejaFixIncidentSource)
    assert dejafix.base_url == "http://dejafix-proxy:8030"


def test_store_picks_up_dejafix_connected_after_startup(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """저장소를 만든 뒤(연결 없음) 보관함에 DejaFix를 추가하면 재시작 없이 그 신호원을 쓴다."""
    from ax_agent_template import tool_connections as tc
    from dev_agent import deploy_signals

    monkeypatch.setenv(tc.FILE_ENV, str(tmp_path / "tool-connections.json"))
    monkeypatch.delenv(deploy_signals.DEJAFIX_URL_ENV, raising=False)
    monkeypatch.delenv("AX_DEJAFIX_PROXY_URL", raising=False)
    tc._cache = None
    late_store = DeployStore()
    before = late_store.source
    assert not isinstance(before, CombinedSource)  # 연결 전: 목(테스트 전용) 신호원
    tc.upsert(
        "dejafix",
        "장애",
        {"url": "http://10.0.0.5", "username": "v"},
        {"password": "pw"},
        conn_id=None,
        by="t",
    )
    after = late_store.source
    assert isinstance(after, CombinedSource)
    assert isinstance(after._sources[0][1], DejaFixIncidentSource)


# ---------------------------------------------------------------- 실제 모드·정직한 상태


def _real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "0")
    for name in ("PLANNING_MOCK", "DEV_MOCK"):
        monkeypatch.delenv(name, raising=False)


def test_real_mode_has_no_seed_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """실제 모드에서는 시드(DEP-1·PR #28·payment_fail_rate)를 배포 기록에 넣지 않는다."""
    monkeypatch.delenv("PLANNING_STATE_DIR", raising=False)
    _real_mode(monkeypatch)
    real = DeployStore()
    assert real.deployments == []
    assert real.checks == {}
    real.reset()
    assert real.deployments == []


def test_mock_mode_keeps_seed_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANNING_STATE_DIR", raising=False)
    assert [d.id for d in DeployStore().deployments] == ["DEP-1"]


def test_deploy_without_tool_is_not_succeeded(client) -> None:
    """배포 도구가 없으니 승인이 필요 없는 배포도 '배포됨'으로 두지 않는다."""

    async def no_tool(target: str, req):
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.FAILED,
            outcome="no_deploy_tool",
            output={"message": "배포 도구가 연결되지 않았어요"},
        )

    set_deploy_store(DeployStore(infra=no_tool))
    dep = client.post(f"{BASE}/deployments", json={"pr_numbers": [28]}).json()["data"]
    res = client.post(f"{BASE}/deployments/{dep['id']}/dispatch", json={"approved_by": "예진"})
    dep = res.json()["data"]
    assert dep["status"] == "approved"
    assert dep["executed"] is False
    assert "배포 도구가 연결되지 않았어요" in dep["note"]
    assert dep["run"]["state"] == "no_tool"
    # 실행되지 않은 배포는 배포 후 확인을 하지 않는다 (없는 배포의 '정상'을 지어내지 않는다)
    res = client.post(f"{BASE}/deployments/{dep['id']}/check")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "NOT_DEPLOYED"


def test_approve_requires_approver_name(client, service) -> None:
    client.post(f"{BASE}/deployments", json={"pr_numbers": [28], "env": "prod"})
    for body in ({}, {"approved_by": ""}, {"approved_by": "   "}, {"approved_by": 3}):
        res = client.post(f"{BASE}/deployments/DEP-2/approve", json=body)
        assert res.status_code == 400, body
        assert res.json()["error"]["code"] == "APPROVER_REQUIRED"
    # 이름이 없으면 관문에도 가지 않는다 (기본 승인자를 지어내지 않는다)
    assert [c.action for c in service.gate_calls] == ["request_deploy"]
    res = client.post(f"{BASE}/deployments/DEP-2/approve", json={"approved_by": " 병환 "})
    assert res.status_code == 200
    assert res.json()["data"]["approved_by"] == "병환"
    assert res.json()["data"]["status"] == "approved"
    assert res.json()["data"]["executed"] is False


def test_deployments_survive_restart(tmp_path, monkeypatch: pytest.MonkeyPatch, service) -> None:
    """PLANNING_STATE_DIR가 있으면 배포 기록을 파일에 남겨 다시 켜도 그대로다."""
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    first = DeployStore()
    dep = asyncio.run(first.request(service, [28], "prod"))
    asyncio.run(first.approve(service, dep.id, "병환"))
    again = DeployStore()
    assert [d.id for d in again.deployments] == [d.id for d in first.deployments]
    got = again.get(dep.id)
    assert got is not None and (got.status, got.approved_by) == ("approved", "병환")
    assert again.latest_check("DEP-1") is not None  # 확인 기록도 남는다
    # 번호가 이어진다 (재시작 뒤 같은 DEP 번호를 다시 쓰지 않는다)
    nxt = asyncio.run(again.request(service, [28], "dev"))
    assert nxt.id == f"DEP-{len(first.deployments) + 1}"


def test_deployments_file_broken_starts_clean(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    _real_mode(monkeypatch)
    (tmp_path / "deploy_state_dev.json").write_text("{broken", encoding="utf-8")
    assert DeployStore().deployments == []


def test_unavailable_reason_is_shown_without_signal_title() -> None:
    """신호원이 없으면 '연결 없음'을 근거에 쓴다. 신호원이 준 글(title)은 근거에 옮기지 않는다."""
    missing = Signal(kind="unavailable", source="dejafix", title="원인 X", reason="not_connected")
    res = evaluate_check(_dep(), [missing], at=T0)
    assert res.evidence == ["dejafix 연결 없음 → 신호를 읽지 못함 (도구 연결 필요)"]
    failed = Signal(kind="unavailable", source="data", title="원인 X", reason="error")
    assert evaluate_check(_dep(), [failed], at=T0).evidence == ["data 신호를 읽지 못함 (응답 오류)"]


def test_missing_dejafix_source_marks_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    from dev_agent import deploy_signals

    src = CombinedSource([("dejafix", deploy_signals._Missing("주소 없음"))])
    rows = asyncio.run(src.signals(_dep(), T0, T0))
    assert [(r.kind, r.reason) for r in rows] == [("unavailable", "not_connected")]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def run() -> list[Signal]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            both = CombinedSource([("dejafix", DejaFixIncidentSource("http://d", client=http))])
            return await both.signals(_dep(), T0, T0)

    assert [r.reason for r in asyncio.run(run())] == ["error"]


def test_undated_anomaly_is_listed_apart() -> None:
    """시각 없는 이상 지표(data)는 '배포 후 N분'처럼 보이지 않게 따로 적는다."""
    dated = Signal(kind="anomaly", source="mock", ref="a", at=T0 + timedelta(minutes=3))
    undated = Signal(kind="anomaly", source="data", ref="payment_fail_rate")
    res = evaluate_check(_dep(), [dated, undated], at=T0)
    assert res.verdict == "watch"
    assert res.evidence == [
        "배포 후 3분 이상 지표 a",
        "시각을 모르는 이상 지표 payment_fail_rate (확인 구간 30분 안 어딘가)",
    ]


def test_save_failure_does_not_break_store_or_api(
    tmp_path, monkeypatch: pytest.MonkeyPatch, client
) -> None:
    """저장 폴더에 쓸 수 없어도 저장소는 만들어지고 배포 API는 동작한다 (저장만 건너뜀)."""
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("PLANNING_STATE_DIR", str(blocker))  # 파일이라 폴더를 만들 수 없다
    set_deploy_store(DeployStore())
    res = client.post(f"{BASE}/deployments", json={"pr_numbers": [28], "env": "prod"})
    assert res.status_code == 200
    dep_id = res.json()["data"]["id"]
    res = client.post(f"{BASE}/deployments/{dep_id}/approve", json={"approved_by": "병환"})
    assert res.status_code == 200
    assert client.post(f"{BASE}/deployments/DEP-1/check").status_code == 200


def test_file_from_other_mode_is_dropped(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """목 모드가 남긴 파일(시드 DEP-1)을 실제 모드가 읽지 않는다. 반대도 같다."""
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    assert [d.id for d in DeployStore().deployments] == ["DEP-1"]  # 목 모드가 파일을 씀
    _real_mode(monkeypatch)
    assert DeployStore().deployments == []  # 실제 모드: 버리고 새로 시작
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    assert [d.id for d in DeployStore().deployments] == ["DEP-1"]  # 목 모드: 다시 시드
