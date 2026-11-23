"""ax-orchestrator 서버 진입점.

목 모드(`ORCHESTRATOR_MOCK=1`)에서는 가짜 부서(`mock/fake_departments.py`)로 시나리오 A를
끝까지 돌리고, `/demo` 목업 화면으로 이벤트 흐름을 보여 준다.

실제 모드(`ORCHESTRATOR_MOCK`이 1이 아님)에서는 7개 부서를 모두 실제 서비스로 부르고, 이벤트 버스
(`REDIS_URL`)로 판매 사이트의 `shop.degraded`를 받아 이벤트를 연다. 가짜 신호를 넣는 데모 입구와
승인 흉내는 목 모드에서만 연다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from ax_agent_template import company_scope as request_company
from ax_agent_template import events
from ax_agent_template.config import set_agent_name
from ax_contracts.schemas import TENANT_ID_DEFAULT, EventEnvelope, EventType
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS
from ax_orchestrator import request_tasks
from ax_orchestrator.a2a import build_a2a_app
from ax_orchestrator.auth import bind_company, require_admin, require_agent, require_human
from ax_orchestrator.chat.turn import notify_thread
from ax_orchestrator.diagnosis_cards import fetch_cards_via_archive
from ax_orchestrator.diagnosis_notes import send_note_via_agent
from ax_orchestrator.engine import Engine
from ax_orchestrator.hybrid import HybridDepartments, real_depts_from_env
from ax_orchestrator.inbox import post_to_inbox
from ax_orchestrator.manager import SUBSCRIBED, IncidentManager
from ax_orchestrator.mock.fake_departments import FakeDepartments
from ax_orchestrator.models import Incident, now
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.real import http_review_checker
from ax_orchestrator.registry import Registry
from ax_orchestrator.store import DB_ENV, IncidentStore
from ax_orchestrator.usage import BUCKET_SECONDS, WINDOW_SECONDS, RecordingCaller, UsageRecorder
from ax_orchestrator.work_requests import MAX_TEXT

log = logging.getLogger("orchestrator.main")

STATIC = Path(__file__).parent / "static"
DEPTS = ["infra", "data", "planning", "dev", "marketing", "support", "finance", "archive"]


class DemoRequest(BaseModel):
    """목업 화면의 장애 주입 옵션. 각 옵션은 가짜 부서의 동작을 바꾼다."""

    hold_rollback: bool = False  # 롤백이 C 등급 보류가 됨(사람 승인 대기)
    fail_marketing: bool = False  # 공지 게시가 한 번 실패(재시도로 복구)
    wrong_owner: bool = False  # 공지 게시가 소관 아님으로 거절 → 이관 부서 없음 → 이관 큐로 넘김
    infra_card_empty: bool = False  # 인프라 카드 수집 실패(= 현재 dev 상태)
    undetermined: bool = False  # 원인 미확정 → 롤백하지 않고 이관 큐로 넘김
    not_recovered: bool = False  # 복구 확인 실패 → 정상화 공지 없이 이관 큐로 넘김


class Decision(BaseModel):
    approved: bool


class AppState:
    def __init__(self) -> None:
        self.mock = os.getenv("ORCHESTRATOR_MOCK") == "1"
        self.hold_timeout = float(os.getenv("ORCHESTRATOR_HOLD_TIMEOUT", "600"))
        # 사람 할 일함 처리를 기다리는 시간(초). 넘으면 그 단계는 rejected_by_human (기본 하루)
        self.handoff_timeout = float(os.getenv("ORCHESTRATOR_HANDOFF_TIMEOUT", "86400"))
        # 원인을 못 찾아 사람에게 넘긴 장애를 사람이 해제하길 기다리는 시간(초, 기본 20분, 목 0).
        # 그 안에 풀리면 복구 검증 → 정상화 공지로 이어지고, 넘으면 예전처럼 복구 검증을 건너뛴다
        default_wait = "0" if self.mock else "1200"
        self.release_wait = float(os.getenv("ORCHESTRATOR_RELEASE_WAIT", default_wait))
        self.latency = float(os.getenv("ORCHESTRATOR_MOCK_LATENCY", "0.7"))
        self.registry = Registry()
        self.fakes: FakeDepartments | None = None
        # 회사마다 이벤트 목록·저장소·의뢰 실행기를 따로 둔다 (여러 회사 5단계, {회사: manager}).
        # skill 요청·이벤트·로그인 요청 안에서는 `company()`가 그 요청의 회사를 주고, 요청 밖(스스로
        # 도는 루틴·시험)은 기본 회사(catbooks)다. 매니저는 그 회사의 첫 요청에 만든다
        self.managers: dict[str, IncidentManager] = {}
        self.watchers: dict[str, asyncio.Task[None]] = {}
        self.serving = (
            False  # 서버가 떠 있는 동안만 True (늦게 만든 회사의 실행기를 이어 돌릴 때 쓴다)
        )
        self.published: list[dict[str, Any]] = []
        self.modes: dict[str, str] = {}
        self.usage = UsageRecorder()  # 이벤트·옵션이 바뀌어도 부서별 사용량은 이어서 쌓는다
        # 이벤트 저장소(ORCHESTRATOR_DB). 이벤트 목록(manager)과 같이 만든다.
        # 비어 있으면 메모리라 다시 켜면 사라진다
        self.stores: dict[str, IncidentStore] = {}

    @staticmethod
    def company() -> str:
        """지금 요청의 회사. 요청 밖이면 기본 회사(catbooks)."""
        return request_company.active() or TENANT_ID_CATBOOKS

    @property
    def manager(self) -> IncidentManager | None:
        """지금 요청 회사의 이벤트 매니저. 아직 안 만들었으면 None."""
        return self.managers.get(self.company())

    @manager.setter
    def manager(self, value: IncidentManager | None) -> None:
        if value is None:
            self.managers.pop(self.company(), None)
        else:
            self.managers[self.company()] = value

    @property
    def store(self) -> IncidentStore | None:
        return self.stores.get(self.company())

    @store.setter
    def store(self, value: IncidentStore | None) -> None:
        if value is None:
            self.stores.pop(self.company(), None)
        else:
            self.stores[self.company()] = value

    async def build(self, demo: DemoRequest | None = None) -> IncidentManager:
        demo = demo or DemoRequest()
        cid = self.company()
        created = self.manager is None
        if created:  # 새 이벤트 목록이면 저장소도 새로 연다 (그 회사 칸만 본다)
            if self.store is not None:
                self.store.close()
            self.store = IncidentStore.from_env(cid)
        assert self.store is not None
        fakes = FakeDepartments(latency=self.latency)
        if demo.infra_card_empty:
            fakes.empty_cards.add("infra")
        if demo.hold_rollback:
            fakes.script("infra", "rollback_deployment", "hold")
        if demo.fail_marketing:
            fakes.script("marketing", "publish_notice", "fail")
        if demo.wrong_owner:
            fakes.script("marketing", "publish_notice", "forbidden")
        if demo.undetermined:
            fakes.script("infra", "diagnose_incident", "undetermined")
        if demo.not_recovered:
            fakes.script("infra", "verify_recovery", "not_recovered")
        # 실제 모드는 가짜 부서를 쓰지 않는다.
        # 목 모드에서만 ORCHESTRATOR_REAL_DEPTS로 일부를 실제로 붙인다
        real = real_depts_from_env(DEPTS) if self.mock else set(DEPTS)
        if real:
            set_agent_name("orchestrator")
        caller = HybridDepartments(fakes, real)
        self.modes = caller.modes(DEPTS)
        await self.registry.load(DEPTS, caller.fetch_card)
        engine = Engine(
            self.registry,
            RecordingCaller(caller, self.usage),
            publish=self._publish,
            hold_timeout=self.hold_timeout,
            handoff_timeout=self.handoff_timeout,
            release_wait=self.release_wait,
            retry_delay=0.2,
            # 장애 배너 내리기처럼 정리 단계가 모두 실패하면 1분·3분·10분 뒤 다시 시도한다
            late_retry_delays=(60.0, 180.0, 600.0),
            persist=self.store.save,
            # 사람에게 넘긴("이관 큐") 단계는 사람의 받은 요청에도 올린다. 목 모드는 올리지 않는다
            inbox=None if self.mock else post_to_inbox,
            # 기본 회사는 지금까지처럼 `TENANT_ID`, 다른 회사는 그 회사 ID를 봉투·받은 요청에 싣는다
            tenant_id=os.getenv("TENANT_ID", TENANT_ID_DEFAULT)
            if cid == TENANT_ID_CATBOOKS
            else cid,
            # 안전 관문의 사람 결정을 되물어 확인한다. 목 모드는 AX_SAFETY_URL을 줄 때만 부른다
            review_check=(
                http_review_checker() if not self.mock or os.getenv("AX_SAFETY_URL") else None
            ),
        )
        self.fakes = fakes
        if self.manager is None:
            self.manager = IncidentManager(engine, scenario_a, store=self.store)
            self.manager.restore()  # 저장된 이벤트를 올린다(진행 중이던 건은 "재시작으로 중단됨")
            self.manager.request_runner.notify = notify_thread  # 의뢰 진행을 대화로 알린다
            # 사람에게 넘긴 의뢰를 받은 요청에 올린다. 목 모드는 올리지 않는다
            self.manager.request_escalation.poster = None if self.mock else post_to_inbox
        else:  # 같은 이벤트 목록을 유지하면서 부서 동작만 교체
            self.manager.engine = engine
        if created and self.serving and cid != TENANT_ID_CATBOOKS:
            await self.start_runner(cid)  # 서버가 뜬 뒤 처음 온 회사도 의뢰를 이어서 시킨다
        # 인사가 보낸 에이전트 평가 진단 요청은 받자마자 진단한다(목 모드·시험은 자동 시작 안 함)
        diagnoses = self.manager.agent_diagnoses
        diagnoses.auto_run = not self.mock
        diagnoses.cards_fetcher = None if self.mock else fetch_cards_via_archive
        diagnoses.note_sender = None if self.mock else send_note_via_agent
        return self.manager

    async def ensure_manager(self) -> None:
        """skill 요청 회사의 매니저가 없으면 만든다. 기본 회사는 지금까지처럼 켠 뒤에 만든다."""
        if self.manager is None and self.company() != TENANT_ID_CATBOOKS:
            await self.build()

    async def prepare_companies(self) -> list[str]:
        """기록이 있는 회사의 매니저를 미리 만들고, 의뢰를 이어 시킬 회사들을 돌려준다."""
        base = self.stores.get(TENANT_ID_CATBOOKS)
        if base is not None:
            for cid in base.company_ids():
                if cid != TENANT_ID_CATBOOKS and cid not in self.managers:
                    with request_company.using(cid):
                        await self.build()
        return list(self.managers)

    async def start_runner(self, cid: str) -> None:
        """그 회사의 의뢰 중 시킬 단계가 남은 것을 이어 시키고, 부서의 긴 작업 알림을 지켜본다."""
        manager = self.managers[cid]
        with request_company.using(cid):  # 대화에 알리는 일이 그 회사 대화 저장소로 가게 한다
            await manager.request_runner.resume_open()
            if cid not in self.watchers:
                self.watchers[cid] = asyncio.create_task(manager.request_runner.watch_async())

    async def stop_runners(self) -> None:
        for task in self.watchers.values():
            task.cancel()
        self.watchers.clear()

    async def _publish(self, kind: str, inc: Incident) -> None:
        self.published.append({"type": kind, "context_id": inc.context_id})


state = AppState()


async def start_event_bus() -> bool:
    """이벤트 버스(Redis Streams)를 구독해 `manager.process`로 넘긴다 (ORC-02).

    목 모드이거나 `REDIS_URL`이 없으면 구독하지 않는다(그때는 개발용 `POST /api/events`만 쓴다).
    판정 규칙은 입구와 상관없이 같은 `manager.process`다. 같은 `event_id`는 한 번만 처리된다.
    """
    if state.mock or not events.redis_configured():
        return False
    set_agent_name("orchestrator")
    if state.manager is None:
        await state.build()

    async def on_event(env: EventEnvelope) -> None:
        # 이벤트는 요청 밖에서 오므로 봉투 `tenant_id` 회사의 매니저가 받는다(별칭·빈 값은 catbooks)
        with request_company.using(env.tenant_id):
            manager = state.manager or await state.build()
            await manager.process(env)

    events.subscribe(SUBSCRIBED, on_event)
    events.start_consumer()
    return True


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await start_event_bus()
    # 다시 켠 직후에도 저장된 이벤트가 목록에 보이게 미리 만든다
    # (실제 모드, 또는 저장 파일을 준 목 모드)
    if state.manager is None and (not state.mock or os.getenv(DB_ENV)):
        await state.build()
    # 다시 켠 뒤 시킬 단계가 남은 의뢰를 이어서 시키고, 부서의 긴 작업 알림이 끊긴 의뢰 단계를
    # 주기적으로 닫는다. 저장소에 기록이 있는 회사는 첫 요청을 기다리지 않고 미리 만들어 같이 한다
    for cid in await state.prepare_companies():
        await state.start_runner(cid)
    state.serving = True
    try:
        yield
    finally:
        state.serving = False
        await state.stop_runners()
        await events.stop_consumer()


app = FastAPI(title="ax-orchestrator", lifespan=lifespan, dependencies=[Depends(bind_company)])


@app.get("/health")
def health() -> dict:
    return {"data": {"status": "ok"}, "meta": {}}


def _require_mock() -> None:
    if not state.mock:
        raise HTTPException(501, "실제 연동은 아직 없음 (ORCHESTRATOR_MOCK=1로 실행)")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/demo")


@app.get("/demo")
def demo_page() -> FileResponse:
    return FileResponse(STATIC / "demo.html")


@app.get("/api/registry")
async def registry() -> dict:
    if state.manager is None:
        await state.build()
    view = state.registry.view()
    for dept in view["depts"]:
        dept["mode"] = state.modes.get(dept["name"], "mock")
    return {"data": view, "meta": {"mock": state.mock, "modes": state.modes}}


@app.get("/api/departments")
async def departments() -> dict:
    """부서별 카드·skill·사용량(총괄이 부른 호출을 센 실측)과 최근 호출. 화면의 부서 카드가 쓴다."""
    if state.manager is None:
        await state.build()
    view = state.registry.view()
    current = now()
    items: list[dict[str, Any]] = []
    for dept in view["depts"]:
        name = dept["name"]
        skills = []
        for skill in dept["skills"]:
            per_skill = state.usage.summary(name, current, skill["id"])
            skills.append(
                {
                    **skill,
                    **{k: per_skill[k] for k in ("calls", "errors", "success_rate", "avg_ms")},
                    "retries": per_skill["retries"],
                    "series": state.usage.series(name, current, skill["id"]),
                }
            )
        items.append(_dept_item(name, dept["owner"], True, None, skills, current))
    for name, reason in view["unavailable"].items():
        items.append(_dept_item(name, None, False, reason, [], current))
    items.sort(key=lambda d: DEPTS.index(d["name"]) if d["name"] in DEPTS else len(DEPTS))
    return {
        "data": items,
        "meta": {
            "mock": state.mock,
            "window_seconds": WINDOW_SECONDS,
            "bucket_seconds": BUCKET_SECONDS,
        },
    }


def _dept_item(
    name: str,
    owner: str | None,
    available: bool,
    reason: str | None,
    skills: list[dict[str, Any]],
    current: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "owner": owner,
        "mode": state.modes.get(name, "mock"),
        "available": available,
        "unavailable_reason": reason,
        "skills": skills,
        "usage": state.usage.summary(name, current),
        "series": state.usage.series(name, current),
        "recent": state.usage.recent(name),
    }


@app.get("/api/registry/real")
async def registry_real() -> dict:
    """실제 부서의 Agent Card를 HTTP로 수집해 결과를 보여 준다(읽기 전용, 이벤트는 열지 않음)."""
    from ax_orchestrator.real import http_card_fetcher

    real = Registry()
    await real.load(DEPTS, http_card_fetcher())
    return {"data": real.view(), "meta": {"mock": False}}


@app.get("/api/incidents")
async def incidents() -> dict:
    items = state.manager.listing() if state.manager else []
    return {"data": items, "meta": {"mock": state.mock, "published": state.published}}


@app.get("/api/finance-opinions")
async def finance_opinions() -> dict:
    """재무가 보낸 의견(캠페인 손익·예산 의견·인사이트). 최근 받은 것부터. 읽기 전용."""
    rows = state.manager.finance_opinions.list() if state.manager else []
    return {"data": list(reversed(rows)), "meta": {"mock": state.mock}}


@app.get("/api/agent-diagnoses")
async def agent_diagnoses() -> dict:
    """인사가 에이전트 평가에서 반복을 찾아 보낸 진단 요청. 최근 받은 것부터. 읽기 전용."""
    rows = state.manager.agent_diagnoses.list() if state.manager else []
    return {"data": list(reversed(rows)), "meta": {"mock": state.mock}}


@app.post("/api/agent-diagnoses/{request_id}/revoke")
async def revoke_agent_diagnosis(request_id: str, _: str = Depends(require_human)) -> JSONResponse:
    """사람이 진단의 개선 노트 전달을 철회한다. 에이전트가 그 노트를 쓰지 않게 거둔다."""
    manager = state.manager
    delivery = await manager.agent_diagnoses.revoke(request_id) if manager else None
    if delivery is None:
        return JSONResponse(
            {"error": {"code": "NOT_FOUND", "message": "철회할 진단이 없어요"}}, 404
        )
    return JSONResponse({"data": delivery})


@app.get("/api/incidents/{context_id}")
async def incident(context_id: str) -> JSONResponse:
    inc = state.manager.incidents.get(context_id) if state.manager else None
    if inc is None:
        return JSONResponse({"error": {"code": "NOT_FOUND", "message": "이슈 없음"}}, 404)
    return JSONResponse({"data": inc.view(), "meta": {}})


@app.post("/api/demo/incidents")
async def inject(req: DemoRequest | None = None, _: str = Depends(require_admin)) -> dict:
    """판매 사이트 결제 장애를 주입한 것처럼 `shop.degraded` 이벤트를 넣는다(목 모드).

    운영자 전용이라 admin 토큰만 받는다.
    """
    _require_mock()
    manager = await state.build(req)
    env = EventEnvelope(
        event_id=f"evt_{uuid.uuid4().hex[:12]}",
        type=EventType.SHOP_DEGRADED,
        occurred_at=datetime.now(UTC),
        context_id=None,
        source="shop-api",
        idempotency_key=f"demo:{uuid.uuid4().hex[:8]}",
        data={"component": "payment", "metric_code": "error_rate_5xx", "value": 0.42},
    )
    inc = await manager.handle(env)
    assert inc is not None
    return {"data": {"context_id": inc.context_id}, "meta": {"mock": True}}


@app.post("/api/events")
async def receive_event(env: EventEnvelope, caller: str = Depends(require_agent())) -> dict:
    """다른 부서가 이벤트를 보내 볼 수 있는 입구.

    실제 모드에서도 열어 둔다(이벤트 버스와 같은 판단). 에이전트 토큰(`AX_AGENT_TOKENS`)만 받는다.

    실제 전달은 Redis Streams 구독(`start_event_bus`)이고, 그때도 판단은 같은 `manager.process`다.
    """
    # 사람 결정 이벤트는 안전 관문만 보낼 수 있다(다른 에이전트가 승인을 흉내 내지 못하게)
    if env.type == EventType.SAFETY_REVIEW_DECIDED and caller != "safety":
        raise HTTPException(403, "안전 관문만 보낼 수 있는 이벤트다")
    with request_company.using(env.tenant_id):  # 봉투의 회사 매니저가 받는다
        manager = state.manager or await state.build()
        outcome, inc = await manager.process(env)
    return {
        "data": {"outcome": outcome, "context_id": inc.context_id if inc else None},
        "meta": {"mock": state.mock},
    }


@app.post("/api/incidents/{context_id}/holds/{step_id}")
async def decide(
    context_id: str, step_id: str, body: Decision, _: str = Depends(require_human)
) -> dict:
    """목 모드의 승인·거절 흉내. 총괄이 승인하는 것이 아니다.

    사람 토큰(`SAFETY_HUMAN_TOKENS`)만 받는다. 그래도 목 모드에서만 열고,
    실제 모드의 사람 결정은 안전 관문 보류 큐 하나로만 간다(#265).
    """
    _require_mock()
    if not (state.manager and state.manager.hold_decision(context_id, step_id, body.approved)):
        raise HTTPException(404, "대기 중인 보류가 없음")
    return {"data": {"ok": True}, "meta": {}}


@app.post("/api/requests/{request_id}/steps/{step_id}/decision")
async def decide_request_step(
    request_id: str, step_id: str, body: Decision, who: str = Depends(require_human)
) -> dict:
    """사람 확인·승인 대기 중인 의뢰 단계를 승인하거나 거절한다. 사람 토큰만 받는다.

    승인하면 다음 단계로 이어진다. 상태를 바꾸는 일은 부서의 안전 관문도 따로 거친다.
    """
    runner = state.manager.request_runner if state.manager else None
    outcome = runner.decide(request_id, step_id, body.approved, by=who) if runner else "missing"
    if outcome == "missing":
        raise HTTPException(404, "없는 의뢰나 단계")
    if outcome == "not_waiting":
        raise HTTPException(409, "확인을 기다리는 단계가 아님")
    return {"data": {"ok": True}, "meta": {}}


def _public(row: dict) -> dict:
    """조회에 내는 의뢰 한 줄. 다음 단계로 넘길 부서 결과(`carry`)는 내지 않는다."""
    return {k: v for k, v in row.items() if k != "carry"}


@app.get("/api/requests")
async def list_requests(
    status: str | None = None, hidden: int = 0, _: str = Depends(require_human)
) -> dict:
    """접수된 고객 의뢰 (최근 것부터, 최대 100건). 고객 글이 들어 있어 사람 토큰만 본다.

    기본은 숨긴(삭제한) 의뢰를 뺀다. `?hidden=1`이면 숨긴 것만 준다 (11/23)."""
    wr = state.manager.work_requests if state.manager else None
    rows = wr.list(status=status, hidden=bool(hidden)) if wr else []
    rows = [_public(r) for r in rows]
    tasks = [{"name": t.name, "label": t.label} for t in request_tasks.TASKS]
    return {"data": list(reversed(rows))[:100], "meta": {"tasks": tasks}}


@app.get("/api/requests/{request_id}")
async def get_request(request_id: str, _: str = Depends(require_human)) -> dict:
    row = state.manager.work_requests.get(request_id) if state.manager else None
    if row is None:
        raise HTTPException(404, "없는 의뢰")
    return {"data": _public(row), "meta": {}}


@app.post("/api/requests/{request_id}/hide")
async def hide_request(request_id: str, who: str = Depends(require_human)) -> dict:
    """의뢰 삭제 = 멈추고 숨김 (기록은 남고 되돌릴 수 있다). 사람 토큰만.

    끝나지 않은 의뢰면 남은 단계를 "사람이 의뢰를 삭제함"으로 건너뛰고 의뢰는 rejected. 이미 부서에
    맡긴 긴 작업은 취소하지 않고, 결과가 와도 다음 단계로 넘기지 않는다."""
    runner = state.manager.request_runner if state.manager else None
    if (runner.hide(request_id, by=who) if runner else "missing") == "missing":
        raise HTTPException(404, "없는 의뢰")
    return {"data": {"ok": True}, "meta": {}}


@app.post("/api/requests/{request_id}/unhide")
async def unhide_request(request_id: str, who: str = Depends(require_human)) -> dict:
    """숨긴 의뢰를 되돌린다 (멈춘 단계는 그대로). 사람 토큰만."""
    runner = state.manager.request_runner if state.manager else None
    if (runner.unhide(request_id, by=who) if runner else "missing") == "missing":
        raise HTTPException(404, "없는 의뢰")
    return {"data": {"ok": True}, "meta": {}}


class EditBody(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)


def edit_llm() -> Any:
    """의뢰문을 다시 분류할 LLM (대화 접수와 같은 것). 테스트는 이 함수를 바꿔 끼운다."""
    from ax_orchestrator.chat.turn import default_llm

    return default_llm()


@app.post("/api/requests/{request_id}/edit")
async def edit_request(request_id: str, body: EditBody, who: str = Depends(require_human)) -> dict:
    """의뢰문 수정 = 고쳐서 다시 접수. 새 의뢰를 먼저 만들고(대화 접수와 같은 함수), 성공하면 옛
    의뢰를 멈추고 숨긴다. 새 접수에 실패하면 옛 의뢰는 그대로. 새 의뢰 번호를 돌려준다."""
    manager = state.manager
    if manager is None or manager.work_requests.get(request_id) is None:
        raise HTTPException(404, "없는 의뢰")
    llm = edit_llm()
    if llm is None:
        raise HTTPException(503, "LLM 설정이 없어 다시 접수하지 못했어요")
    try:
        outcome, new_id = await manager.request_runner.edit(
            request_id, body.text, manager.request_intake, llm, by=who
        )
    except Exception as exc:  # noqa: BLE001 - 새 접수 실패: 옛 의뢰는 건드리지 않았다
        log.warning("의뢰문 수정 실패 %s: %s", request_id, type(exc).__name__)
        raise HTTPException(502, "새 의뢰를 접수하지 못했어요. 지금 의뢰는 그대로예요") from None
    if outcome == "missing":
        raise HTTPException(404, "없는 의뢰")
    if outcome == "same":
        raise HTTPException(422, "바뀐 내용이 없어요")
    if outcome == "not_request":
        raise HTTPException(422, "고친 글이 의뢰로 접수되지 않았어요. 지금 의뢰는 그대로예요")
    return {"data": {"ok": True, "request_id": new_id, "replaces": request_id}, "meta": {}}


class RerunBody(BaseModel):
    task: str


@app.post("/api/requests/{request_id}/rerun")
async def rerun_request(
    request_id: str, body: RerunBody, who: str = Depends(require_human)
) -> dict:
    """잘못 골랐으면 사람이 일을 바꿔 다시 돌린다. 이전 선택은 기록에 남는다."""
    runner = state.manager.request_runner if state.manager else None
    outcome = runner.rerun(request_id, body.task, by=who) if runner else "missing"
    if outcome == "missing":
        raise HTTPException(404, "없는 의뢰")
    if outcome == "unknown_task":
        raise HTTPException(422, "일 목록에 없는 일")
    if outcome == "busy":
        raise HTTPException(409, "지금 진행 중이라 바꿀 수 없음")
    if outcome == "stopped":
        raise HTTPException(
            409, "삭제한 의뢰는 다시 돌릴 수 없어요. 의뢰문 수정으로 새로 접수해 주세요"
        )
    return {"data": {"ok": True}, "meta": {}}


# 총괄 자신의 `/a2a`·`/.well-known/agent-card.json`. 위의 경로가 먼저 맞고 나머지만 여기로 온다.
app.mount("/", build_a2a_app(lambda: state.manager, state.ensure_manager))
