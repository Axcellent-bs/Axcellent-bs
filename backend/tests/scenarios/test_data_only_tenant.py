"""시나리오: 데이터분석 AI만 산 회사가 처음부터 끝까지 도는지 (서버 없이 프로세스 안에서).

켠 서비스는 data 하나(켠 서비스 파일 `AX_ENABLED_SERVICES_FILE`)다.

1. 결제 장애 데이터: data 평가 생성기(`eval_world`)가 실제 shop-api 코드로 만든 결제 장애 케이스
2. data 감시(`monitor.run_once`)가 이상을 찾아 `metric.anomaly`를 발행한다.
   data 이상 탐지 skill도 A2A로 부른다
3. 총괄(IncidentManager + Engine)이 이벤트를 열고 data 영향 분석을 A2A로 부른다
4. 총괄의 다음 단계(원인 진단·공지·롤백·복구 검증·기록 등)는 꺼진 서비스로 가고,
   라우터(`call_agent`)가 조직도의 부서 할 일함(dashboard-api)으로 넘긴다
5. 사람이 할 일함에서 끝내면 `handoff.completed`가 나오고 총괄이 이어 간다
6. data `activity_report`에 data 카드 하나와 "AI를 쓰지 않는 부서로 넘긴 일"이 나온다

같은 data 부분(감시·이상 탐지·영향 분석)을 테스트 전용 가짜 회사(냉장 물류) 카탈로그로도 돌린다.

프로세스 안 연결:
- A2A·할 일함 HTTP는 호스트 이름으로 앱을 고르는 ASGI 전송(`Router`)으로 보낸다.
  띄우지 않은 서비스는
  연결 실패다(실제로 서버가 없는 것과 같다).
- 이벤트 버스는 목 모드(`AGENT_TEMPLATE_MOCK=1`, 테스트 전용)의 프로세스 안 버스다.
- 총괄의 부서 카드: 실제 총괄과 같이 띄운 서버(data)의 카드만 HTTP로 받는다. 꺼진 서비스는 카드를
  받으러 가지 않고, 총괄 레지스트리가 서비스 대응표(services.py)로 부서를 정해 라우터로 보낸다
  (#231 "막히는 곳" 1을 고친 뒤. 예전에는 카드 파일을 읽어 우회했다).
- 할 일함 저장소는 먼저 열지 않는다(동시 첫 요청에도 하나만 만든다, "막히는 곳" 3).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[2]
EVAL = BACKEND / "domains" / "data" / "eval"
SERVICES_DIR = BACKEND / "services"
FAKE_CATALOG = BACKEND / "domains" / "data" / "tests" / "catalogs" / "coldchain.toml"
CASE = EVAL / "cases" / "pay_full_10m_19h.json"  # 19:25~19:35 결제 전부 실패, 실패 주문 22건

ORG = """
assignments:
  incident: {unit: 서비스운영, assignee: 당직 담당}
  planning: {unit: 기획개발}
  dev: {unit: 기획개발}
  coupon: {unit: 마케팅}
  inquiry: {unit: 고객지원}
  claims: {unit: 고객지원}
  reviews: {unit: 고객지원}
  handover: {unit: 인사}
  decisions: {unit: 인사}
  finance: {unit: 재무}
"""
OUTCOMES: list[tuple[str, str]] = []  # 총괄이 받은 이벤트와 처리 결과 (opened·merged·resumed…)
HUMAN = {"Authorization": "Bearer human:operator"}  # 목 모드 사람 토큰 (헤더라 ASCII)
INBOX = "http://dashboard-api/api/dashboard/handoffs"


class Router(httpx.AsyncBaseTransport):
    """호스트 이름 → 프로세스 안 ASGI 앱. 모르는 호스트는 연결 실패 (띄우지 않은 서버)."""

    def __init__(self, apps: dict[str, Any]) -> None:
        self.apps = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}
        self.refused: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self.apps.get(request.url.host)
        if transport is None:
            self.refused.append(f"{request.url.host}{request.url.path}")
            raise httpx.ConnectError(f"{request.url.host}: 서버 없음", request=request)
        return await transport.handle_async_request(request)


@pytest.fixture
def tenant(monkeypatch, tmp_path):  # noqa: ANN001, ANN201
    """data만 켠 회사. 공통 로그·할 일함은 메모리, 켠 서비스 파일·조직도는 임시 파일."""
    from ax_agent_template import common_log, config, events, routing
    from ax_agent_template.common_log import SqliteLogStore
    from ax_dashboard_api import handoffs

    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")  # 토큰 mock:<이름>, 프로세스 안 이벤트 버스
    for name in ("DATA_MOCK", "AX_ENABLED_SERVICES", "DATA_TENANT_CATALOG"):
        monkeypatch.delenv(name, raising=False)
    enabled = tmp_path / "enabled_services.json"
    enabled.write_text(
        json.dumps(
            {"enabled": ["data"], "updated_at": "2026-10-30T00:00:00Z", "updated_by": "test"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(routing.ENABLED_FILE_ENV, str(enabled))
    monkeypatch.setattr(routing, "_enabled_cache", None)
    org = tmp_path / "org.yaml"
    org.write_text(ORG, encoding="utf-8")
    monkeypatch.setenv(routing.ORG_ENV, str(org))
    monkeypatch.setattr(routing, "_cache", None)
    monkeypatch.setenv("AX_DATA_URL", "http://data")
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dashboard-api")
    monkeypatch.setenv("AX_HANDOFF_DB", "")
    handoffs.reset_store()  # 먼저 열지 않는다: 첫 요청 두 개가 동시에 와도 저장소는 하나다
    OUTCOMES.clear()
    store = SqliteLogStore(":memory:")
    common_log.set_store(store)
    events.reset_mock_bus()
    monkeypatch.setattr(
        config, "_agent_name", None
    )  # 총괄이 set_agent_name을 불러도 끝나면 되돌린다
    yield store
    events.reset_mock_bus()
    handoffs.reset_store()
    common_log.set_store(None)
    store.close()


@pytest.fixture
def world():  # noqa: ANN201
    """결제 장애 케이스를 실제 shop-api 코드로 만든 쇼핑몰 데이터 (data 평가 생성기)."""
    sys.path.insert(0, str(EVAL))
    import eval_world

    case = json.loads(CASE.read_text(encoding="utf-8"))
    return eval_world, eval_world.build(case)


def _router(monkeypatch, shop_transport: httpx.BaseTransport | None) -> Router:  # noqa: ANN001
    """data·dashboard-api 앱만 붙인 전송. 모든 `httpx.AsyncClient`가 이 전송을 쓴다."""
    from ax_dashboard_api.main import app as dashboard_app
    from ax_memory.main import app as memory_app
    from ax_safety.main import app as safety_app
    from data_agent.clients import shop_client
    from data_agent.main import app as data_app

    # 띄운 서버: data + 공통 서버(서비스 13개가 아니라 늘 켜져 있다). 다른 서비스는 없다
    router = Router(
        {
            "data": data_app,
            "dashboard-api": dashboard_app,
            "memory": memory_app,
            "safety": safety_app,
        }
    )
    original = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.setdefault("transport", router)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    if shop_transport is not None:  # data의 shop-api 커넥터는 평가 생성기의 쇼핑몰을 읽는다
        shop = original(transport=shop_transport, base_url="http://shop")
        monkeypatch.setattr(
            shop_client,
            "shop_api",
            lambda env=None: shop_client.ShopApi("http://shop", token="scenario", client=shop),
        )
    return router


async def _orchestrator():  # noqa: ANN202
    """실제 총괄 부품(Registry·Engine·IncidentManager·scenario_a)에 실제 `call_agent`를 붙인다."""
    from ax_agent_template import events
    from ax_agent_template.client import call_agent
    from ax_agent_template.config import set_agent_name
    from ax_orchestrator.engine import Engine
    from ax_orchestrator.manager import SUBSCRIBED, IncidentManager
    from ax_orchestrator.mock.harness import DEPTS
    from ax_orchestrator.plans.scenario_a import scenario_a
    from ax_orchestrator.real import http_card_fetcher
    from ax_orchestrator.registry import Registry

    set_agent_name("orchestrator")
    registry = Registry()
    # 실제 총괄과 같다: 띄운 서버의 카드만 HTTP로 받는다 (꺼진 서비스는 받으러 가지 않는다)
    await registry.load(DEPTS, http_card_fetcher())
    published: list[str] = []

    async def publish(kind: str, inc: Any) -> None:
        published.append(kind)

    engine = Engine(
        registry,
        call_agent,
        publish=publish,
        hold_timeout=2.0,
        handoff_timeout=20.0,
        retry_delay=0.0,
    )
    manager = IncidentManager(engine, scenario_a)

    async def on_event(env: Any) -> None:
        outcome, _ = await manager.process(env)
        OUTCOMES.append((env.type.value, outcome))

    events.subscribe(SUBSCRIBED, on_event)
    return registry, manager, published


async def _detect(eval_world, built):  # noqa: ANN001, ANN202
    """data 감시 + data 이상 탐지 skill(A2A). 감시가 발행한 `metric.anomaly`를 돌려준다."""
    from ax_agent_template.client import call_agent
    from ax_contracts.schemas import AgentRequest, RequestMeta
    from data_agent import monitor
    from data_agent.clients.shop_client import get_connector

    monitor.reset_published()
    window_from, window_to = built.window
    fault_end = eval_world.parse(built.spec["injection"]["to"])
    emitted = await monitor.run_once(get_connector(), fault_end)
    meta = RequestMeta(
        context_id="ctx_req_scenario_anomaly",
        token="ignored",
        path=[],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    anomaly = await call_agent(
        "data",
        AgentRequest(
            meta=meta,
            skill="anomaly_detection",
            input={"window_from": window_from.isoformat(), "window_to": window_to.isoformat()},
        ),
    )
    return emitted, anomaly


async def _work_through_inbox(  # noqa: ANN202
    manager,  # noqa: ANN001
    router: Router,
    timeout: float = 25.0,
    *,
    before_waiting: bool = False,
):
    """사람이 할 일함을 보고 열린 일을 하나씩 끝낸다. 이벤트가 닫힐 때까지.

    `before_waiting`이면 총괄이 단계를 대기로 올리기 전이라도 보이는 즉시 끝낸다
    (사람이 먼저 끝내는 경우, "막히는 곳" 2). 기다리는 단계가 없는 기록용 일(시각 기록)은
    끝내지 않는다.
    """
    done: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with httpx.AsyncClient(transport=router) as http:
        while loop.time() < deadline:
            incs = list(manager.incidents.values())
            if incs and all(i.status != "open" for i in incs):
                return incs[0], done
            listed = (await http.get(INBOX, params={"status": "open"})).json()["data"]
            waiting = {
                r.handoff_task_id
                for i in incs
                for r in i.steps.values()
                if r.status == "waiting_human" and r.handoff_task_id
            }
            if before_waiting:
                ready = [t for t in listed if t["skill"] != "record_event_times"]
                ready = [t for t in ready if t["task_id"] not in {d["task_id"] for d in done}]
            else:
                ready = [t for t in listed if t["task_id"] in waiting]
            for task in ready:
                res = await http.post(
                    f"{INBOX}/{task['task_id']}/complete",
                    json={"result": f"{task['assignee_unit']}에서 처리함 ({task['skill']})"},
                    headers=HUMAN,
                )
                assert res.status_code == 200, res.text
                done.append(task)
            await asyncio.sleep(0.02)
    incs = list(manager.incidents.values())
    steps = {s: r.status for s, r in incs[0].steps.items()} if incs else {}
    raise AssertionError(f"이벤트가 닫히지 않음: {steps}")


# ------------------------------------------------------------------------------ 캣북스
def test_data_only_tenant_end_to_end(tenant, world, monkeypatch) -> None:  # noqa: ANN001
    from ax_agent_template.client import call_agent
    from ax_contracts.data_access.common_log import CommonLogQuery
    from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
    from ax_contracts.schemas.data import ActivityReportOutput, AnomalyDetectionOutput

    eval_world, built = world
    router = _router(monkeypatch, built.transport())

    async def scenario():  # noqa: ANN202
        registry, manager, _ = await _orchestrator()
        # 1·2. 결제 장애 → data 감시가 이상을 발행 → 총괄이 이벤트를 연다
        emitted, anomaly = await _detect(eval_world, built)
        # 3~5. 총괄 → data 영향 분석(A2A), 나머지는 사람 할 일함 → 사람이 끝냄 → 이어 감
        inc, done = await _work_through_inbox(manager, router)
        # 6. data 업무 리포트 (오늘, 공통 로그 기준)
        meta = RequestMeta(
            context_id="ctx_req_scenario_report",
            token="ignored",
            path=[],
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        )
        today = datetime.now(UTC).astimezone(KST).date()
        report = await call_agent(
            "data",
            AgentRequest(meta=meta, skill="activity_report", input={"start": today.isoformat()}),
        )
        async with httpx.AsyncClient(transport=router) as http:
            left = (await http.get(INBOX, params={"status": "open"})).json()["data"]
        return registry, manager, emitted, anomaly, inc, done, report, left

    registry, manager, emitted, anomaly, inc, done, report, left_open = asyncio.run(scenario())

    # data 감시와 이상 탐지
    severity = {e.data["metric"]: e.data["severity"] for e in emitted}
    assert severity.get("payment_fail_rate") == "high", severity  # 총괄이 이벤트를 여는 세기
    assert anomaly.status == TaskStatus.COMPLETED, anomaly.outcome
    out = AnomalyDetectionOutput.model_validate(anomaly.output)
    assert out.any_anomaly and "payment_fail_rate" in [v.metric_id for v in out.metric_verdicts]

    # 총괄: 이벤트 하나, data 영향 분석은 AI가, 꺼진 서비스 단계는 사람이
    assert len(manager.incidents) == 1 and inc.status in ("resolved", "degraded"), inc.status
    impact = inc.steps["impact"]
    assert impact.status == "completed" and impact.handoff_task_id is None
    assert inc.outputs["impact"]["failed_order_count"] == 22  # 평가 생성기의 정답
    by_human = {s for s, r in inc.steps.items() if r.handoff_task_id}
    steps = {s: (r.status, r.note) for s, r in inc.steps.items()}
    assert {"diagnose", "notice"} <= by_human, steps
    for step in by_human:
        run = inc.steps[step]
        assert run.status == "completed", (step, steps)  # 사람이 모두 끝냈다
        assert run.output["handled_by"] == "human" and run.output["result"], steps
    # 꺼진 서비스는 A2A로 부르지 않았다 (연결 시도 없음), 할 일은 조직도 부서로 갔다
    assert not [r for r in router.refused if r.endswith("/a2a")], router.refused
    assert {t["assignee_unit"] for t in done} <= {
        "서비스운영",
        "기획개발",
        "마케팅",
        "고객지원",
        "인사",
        "재무",
    }
    assert {t["to_service"] for t in done} >= {"incident", "coupon"}
    assert "data" in registry.cards and "data" not in {t["to_service"] for t in done}

    # data 업무 리포트: data 카드 하나, AI를 쓰지 않는 부서로 넘긴 일
    assert report.status == TaskStatus.COMPLETED, report.outcome
    rep = ActivityReportOutput.model_validate(report.output)
    assert [d.department for d in rep.departments] == ["data"]
    assert rep.enabled_services == ["data"]
    data_card = rep.departments[0]
    assert data_card.requests.total >= 2  # 이상 탐지 + 영향 분석 (+ 원인 후보·처리 시간 요약)
    unused = {u.department: u for u in rep.unused_handoffs}
    assert unused, "AI를 쓰지 않는 부서로 넘긴 일이 리포트에 있어야 한다"
    assert "data" not in unused
    recs = tenant.query(CommonLogQuery.model_validate({"kinds": ["task_status"], "limit": 500}))
    handed = [r for r in recs.records if r.payload.get("outcome") == "handed_to_human"]
    assert sum(u.count for u in unused.values()) == len(handed)  # 리포트 = 라우터가 넘긴 일 전부
    # 넘긴 일 = 사람이 끝낸 단계 + 총괄의 이벤트 시각 기록(hr record_event_times).
    # 시각 기록은 기다리는 단계가 없어 할 일함에 열린 채로 쌓인다 (PR 본문 "막히는 곳" 4)
    side = [r for r in handed if r.skill == "record_event_times"]
    assert len(handed) == len(done) + len(side)
    # 같은 idempotency_key는 한 일로 합쳐져서, 열린 일은 시각 기록 요청 수 이하다
    assert {t["skill"] for t in left_open} == {"record_event_times"}
    assert 1 <= len(left_open) <= len(side)
    assert {"infra", "marketing"} <= set(unused)
    # 총괄: 이벤트 하나를 열고(나머지 이상은 합침), 사람이 끝낸 일마다 이어 갔다
    assert [o for _, o in OUTCOMES].count("opened") == 1
    assert [o for t, o in OUTCOMES if t == "handoff.completed"] == ["resumed"] * len(done)


KST = __import__("zoneinfo").ZoneInfo("Asia/Seoul")


def test_disabled_services_are_handed_off_without_their_cards(
    tenant,
    world,
    monkeypatch,  # noqa: ANN001
) -> None:
    """꺼진 서비스 카드가 없어도 그 부서 단계는 조직도 부서의 받은 요청으로 간다 (#231 1).

    총괄은 꺼진 서비스 카드를 받으러 가지 않는다(서버가 없다). 레지스트리가 서비스 대응표로
    부서를 정하고,
    호출은 라우터가 사람 할 일함으로 넘긴다.
    """
    eval_world, built = world
    router = _router(monkeypatch, built.transport())

    async def scenario():  # noqa: ANN202
        registry, manager, _ = await _orchestrator()
        await _detect(eval_world, built)
        inc, done = await _work_through_inbox(manager, router)
        return registry, inc, done

    registry, inc, done = asyncio.run(scenario())
    assert set(registry.cards) == {"data"}
    assert {"infra", "marketing", "planning", "support", "hr"} <= set(registry.unavailable)
    assert all("꺼진 서비스" in why for why in registry.unavailable.values())
    skipped = [r.note for r in inc.steps.values() if "Agent Card에 skill 없음" in (r.note or "")]
    assert skipped == []  # 예전에는 원인 진단·비슷한 사례·보상·기록 등이 여기서 건너뛰었다
    handed = [s for s, r in inc.steps.items() if r.handoff_task_id]
    assert handed and len(done) == len(handed)
    assert not [r for r in router.refused if r.endswith("/.well-known/agent-card.json")]
    assert inc.status != "open"


def test_human_finishing_before_the_step_waits_is_not_lost(
    tenant,
    world,
    monkeypatch,  # noqa: ANN001
) -> None:
    """사람이 총괄의 "사람 처리 대기"보다 먼저 끝내도 결과를 잃지 않는다 (#231 2).

    받은 요청이 보이는 즉시 끝낸다. 대기 전에 온 결과는 보관(`kept`)했다가
    단계가 대기로 오르면 이어 간다.
    """
    eval_world, built = world
    router = _router(monkeypatch, built.transport())

    async def scenario():  # noqa: ANN202
        _, manager, _ = await _orchestrator()
        await _detect(eval_world, built)
        return await _work_through_inbox(manager, router, before_waiting=True)

    inc, done = asyncio.run(scenario())
    handed = {s: r for s, r in inc.steps.items() if r.handoff_task_id}
    assert handed and {r.status for r in handed.values()} == {"completed"}
    assert all(r.output.get("handled_by") == "human" for r in handed.values())
    outcomes = [o for t, o in OUTCOMES if t == "handoff.completed"]
    assert len(outcomes) == len(done) and set(outcomes) <= {"resumed", "kept"}
    assert "unmatched" not in outcomes


class MemoryConnector:
    """테스트 전용 냉장 물류 커넥터: 냉장 유통 이탈이 마지막 30분에 튄다."""

    def __init__(self, end: datetime) -> None:
        from data_agent.connectors import FailureEvent

        self.start = end - timedelta(minutes=30)
        self.events = [
            FailureEvent(
                at=self.start + timedelta(minutes=m), code=code, customer_id=who, amount=amount,
                category=cat, event_id=f"shp_{m}",
            )
            for m, code, who, amount, cat in (
                (1, "REEFER_FAILURE", "acct_a", 120000, "frozen"),
                (3, "REEFER_FAILURE", "acct_b", 80000, "chilled"),
                (7, "DOOR_LEFT_OPEN", "acct_a", 50000, "frozen"),
            )
        ]  # fmt: skip

    async def metric_series(self, metric_id, window_from, window_to, bucket="5m"):  # noqa: ANN001, ANN201
        out, at = [], window_from
        while at < window_to:
            spike = metric_id == "cold_chain_breach_count" and at >= self.start
            out.append((at, 20.0 if spike else 2.0))
            at += timedelta(minutes=5)
        return out

    async def failure_events(self, window_from, window_to, **_: Any):  # noqa: ANN001, ANN201
        from data_agent.connectors import FailureEvents

        return FailureEvents([e for e in self.events if window_from <= e.at <= window_to])

    async def transactions_volume(self, window_from, window_to):  # noqa: ANN001, ANN201
        return None


def test_fake_company_data_part(tenant, monkeypatch) -> None:  # noqa: ANN001
    """냉장 물류 카탈로그로 data 감시·이상 탐지·영향 분석이 A2A로 돈다 (총괄 흐름은 캣북스만)."""
    from ax_agent_template.client import call_agent
    from ax_agent_template.config import set_agent_name
    from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
    from ax_contracts.schemas.data import AnomalyDetectionOutput, ImpactAnalysisOutput
    from data_agent import catalog, monitor
    from data_agent.clients import shop_client

    end = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    memory = MemoryConnector(end)
    catalog.use_catalog(catalog.load_catalog(str(FAKE_CATALOG)))
    monkeypatch.setitem(shop_client.CONNECTORS, "memory", lambda env: memory)
    _router(monkeypatch, None)
    set_agent_name("orchestrator")  # 총괄이 부르는 것처럼 (요청자 = orchestrator)

    def meta(cid: str) -> RequestMeta:
        return RequestMeta(
            context_id=cid,
            token="ignored",
            path=[],
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        )

    window = {
        "window_from": (end - timedelta(minutes=30)).isoformat(),
        "window_to": end.isoformat(),
    }

    async def scenario():  # noqa: ANN202
        monitor.reset_published()
        problems: list[str] = []
        try:
            await monitor.run_once(memory, end)
        except Exception as error:  # noqa: BLE001 - 막히는 곳을 기록한다
            problems.append(f"monitor.run_once: {type(error).__name__}: {error}")
        anomaly = await call_agent(
            "data",
            AgentRequest(meta=meta("ctx_req_cold_1"), skill="anomaly_detection", input=window),
        )
        impact = await call_agent(
            "data", AgentRequest(meta=meta("ctx_inc_cold_1"), skill="impact_analysis", input=window)
        )
        return problems, anomaly, impact

    try:
        problems, anomaly, impact = asyncio.run(scenario())
    finally:
        catalog.use_catalog(None)

    assert anomaly.status == TaskStatus.COMPLETED, anomaly.outcome
    out = AnomalyDetectionOutput.model_validate(anomaly.output)
    assert out.verdicts == []  # 캣북스 칸에는 싣지 않는다
    assert {v.metric_id: v.status for v in out.metric_verdicts} == {
        "cold_chain_breach_count": "anomaly",
        "shipment_delay_count": "normal",
    }
    assert impact.status == TaskStatus.COMPLETED, impact.outcome
    got = ImpactAnalysisOutput.model_validate(impact.output)
    assert (got.failed_order_count, got.affected_customer_count, got.damage_amount) == (
        3,
        2,
        250000,
    )
    assert problems == [], problems  # 감시도 다른 회사 지표로 이벤트를 낸다
