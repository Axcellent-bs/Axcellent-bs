"""이벤트 수신과 이벤트 판단 (ORC-02·03).

이벤트를 받아 `event_id` 중복을 거르고, 이벤트이면 `ctx_inc_`를 발급해 plan을 돌린다.
같은 구성요소의 열린 이벤트가 있으면 새로 열지 않고 합친다.
판단은 규칙(코드)이 한다. 원인·분석 문장은 만들지 않는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.engine import Engine
from ax_orchestrator.models import Incident, Plan, iso
from ax_orchestrator.naming import event_title, event_type
from ax_orchestrator.plans.finance_anomaly import finance_anomaly
from ax_orchestrator.plans.support_resolved import support_resolved


def _since(env: EventEnvelope) -> datetime | None:
    """신호의 장애 시작 시각(data.since). 없거나 형식이 다르면 None."""
    raw = env.data.get("since")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


LOOKBACK = timedelta(minutes=15)  # 이벤트가 열리기 직전 실패 주문·문의도 같은 이벤트로 본다
INCIDENT_EVENTS = {
    EventType.SHOP_DEGRADED,
    EventType.INFRA_ANOMALY_DETECTED,
    EventType.FINANCE_ANOMALY_DETECTED,
}
# 이벤트 종류별 plan. 없는 종류는 기본 plan(시나리오 A)
PLANS: dict[EventType, tuple[str, Callable[[], Plan]]] = {
    EventType.FINANCE_ANOMALY_DETECTED: ("finance_anomaly", finance_anomaly),
    EventType.SUPPORT_INQUIRY_RESOLVED: ("support_resolved", support_resolved),
}
# 장애가 아니라 건마다 하나씩 여는 이벤트 (문의 처리 결과, 판매자 입점)
CASE_EVENTS = {EventType.SUPPORT_INQUIRY_RESOLVED}
SEVERITY_OPENS = {"high", "critical"}
SUPPORT_EVENTS = {EventType.SUPPORT_INQUIRY_RECEIVED, EventType.SUPPORT_INQUIRY_SURGE}

SUBSCRIBED = [
    EventType.SHOP_DEGRADED,
    EventType.SHOP_RECOVERED,
    EventType.INFRA_ANOMALY_DETECTED,
    EventType.FINANCE_ANOMALY_DETECTED,
    EventType.METRIC_ANOMALY,
    EventType.SUPPORT_INQUIRY_RECEIVED,
    EventType.SUPPORT_INQUIRY_SURGE,
    EventType.SUPPORT_INQUIRY_RESOLVED,
    # 사람 할 일함에서 끝낸 일 → 기다리던 단계를 이어 간다 (업무 요청 라우터)
    EventType.HANDOFF_COMPLETED,
]


class IncidentManager:
    def __init__(self, engine: Engine, plan_factory: Callable[[], Plan]) -> None:
        self.engine = engine
        self.plan_factory = plan_factory
        self.incidents: dict[str, Incident] = {}
        self._seen: set[str] = set()
        self._tasks: dict[str, asyncio.Task[Incident]] = {}

    def judge(self, env: EventEnvelope) -> str:
        """`open` | `note` | `ignore`."""
        if env.type in INCIDENT_EVENTS or env.type in CASE_EVENTS:
            return "open"
        if env.type == EventType.METRIC_ANOMALY:
            severity = str(env.data.get("severity", "")).lower()
            return "open" if severity in SEVERITY_OPENS else "note"
        if env.type in SUPPORT_EVENTS or env.type == EventType.SHOP_RECOVERED:
            return "note"
        return "ignore"

    def open_for(self, component: str) -> Incident | None:
        return next(
            (i for i in self.incidents.values() if i.status == "open" and i.component == component),
            None,
        )

    def find_active(self, component: str | None, occurred_at: datetime | None) -> Incident | None:
        """문의·주문 시각에 해당하는 이벤트 하나를 찾는다 (`find_active_incident`, #272).

        시각이 없으면 열린 이벤트만 본다. 시각이 있으면 이벤트 구간(시작 15분 전 ~ 종료)에
        들어오는 이벤트를 본다. 열린 이벤트가 먼저이고, 그다음은 최근에 열린 순서다.
        """
        found = [
            inc
            for inc in self.incidents.values()
            if (component is None or inc.component == component) and self._covers(inc, occurred_at)
        ]
        found.sort(
            key=lambda inc: (
                inc.status != "open",
                inc.status != "degraded" or inc.recovered_at is not None,
                -inc.opened_at.timestamp(),
            )
        )
        return found[0] if found else None

    @staticmethod
    def _covers(inc: Incident, occurred_at: datetime | None) -> bool:
        if occurred_at is None:
            return inc.status == "open"
        if occurred_at < inc.opened_at - LOOKBACK:
            return False
        if inc.status == "degraded" and inc.recovered_at is None:
            return True  # 대응은 끝났지만 장애가 안 풀렸다: 구간이 열려 있다
        end = inc.recovered_at or inc.closed_at
        return end is None or occurred_at <= end

    async def handle(self, env: EventEnvelope) -> Incident | None:
        """이벤트 하나를 처리한다. 새로 연 이벤트 또는 합쳐진 이벤트를 돌려준다."""
        _, inc = await self.process(env)
        return inc

    async def process(self, env: EventEnvelope) -> tuple[str, Incident | None]:
        """처리 결과를 `(outcome, 이벤트)`으로 돌려준다.

        outcome: `duplicate`(같은 event_id) · `ignored`(총괄 소관 아님) ·
        `noted`(기록만, 열린 이벤트가 있으면 거기에) · `merged`(열린 이벤트에 합침) ·
        `opened`(새 이벤트) ·
        `resumed`(사람 할 일함 처리 결과로 단계를 이어 감) · `unmatched`(기다리는 단계가 없음).
        """
        if env.event_id in self._seen:
            return "duplicate", None
        self._seen.add(env.event_id)
        if env.type == EventType.HANDOFF_COMPLETED:
            return self._resume_handoff(env)
        verdict = self.judge(env)
        component = self._component(env)
        existing = self.open_for(component)
        if verdict == "ignore":
            return "ignored", None
        if verdict == "note":
            if env.type == EventType.SHOP_RECOVERED:
                self._mark_recovered(component, env.occurred_at)
            if existing is not None:
                existing.log(f"이벤트 수신: {env.type.value} ({env.source})")
            return "noted", existing
        if existing is not None:
            existing.log(f"같은 이벤트에 합침: {env.type.value} ({env.source}, {env.event_id})")
            return "merged", existing
        inc = Incident(
            context_id=Incident.new_context_id(),
            kind=PLANS[env.type][0] if env.type in PLANS else "scenario_a",
            title=self._title(env, component),
            event_type=self._event_type(env, component),
            component=component,
            opened_at=env.occurred_at,
            since=_since(env),
        )
        inc.log(f"이벤트 수신: {env.type.value} ({env.source}, {iso(env.occurred_at)})")
        self.incidents[inc.context_id] = inc
        inc.signal = dict(env.data)
        plan = PLANS[env.type][1]() if env.type in PLANS else self.plan_factory()
        self._tasks[inc.context_id] = asyncio.create_task(self.engine.run(inc, plan))
        return "opened", inc

    def _mark_recovered(self, component: str, at: datetime) -> None:
        """복구 신호를 받으면 같은 구성요소의 degraded 이벤트가 장애 중이 아니게 된다."""
        for inc in self.incidents.values():
            if inc.component == component and inc.status == "degraded" and not inc.recovered_at:
                inc.recovered_at = max(at, inc.closed_at or at)
                inc.log(f"복구 신호 수신 ({iso(at)})")

    def _resume_handoff(self, env: EventEnvelope) -> tuple[str, Incident | None]:
        """같은 context_id 이벤트에서 같은 task_id를 기다리는 단계를 찾아 사람 결과를 넣는다.

        이미 반영한 일이거나(다른 event_id로 다시 와도) 기다리는 단계가 없으면 `unmatched`.
        사람이 적은 결과는 `untrusted.result`로 오고, 데이터로만 단계 결과에 넣는다.
        """
        inc = self.incidents.get(env.context_id or "")
        task_id = env.data.get("task_id")
        status = env.data.get("status")
        result = env.untrusted.get("result")
        if inc is None or not isinstance(task_id, str) or not isinstance(status, str):
            return "unmatched", inc
        note = result if isinstance(result, str) else None
        if not self.engine.complete_handoff(inc, task_id, status, note):
            return "unmatched", inc
        inc.log(f"사람 할 일함 처리 결과 수신: {task_id} ({status}, {env.event_id})")
        return "resumed", inc

    @staticmethod
    def _component(env: EventEnvelope) -> str:
        """이벤트를 묶는 구성요소. 고객지원 처리 결과는 문의마다 따로 열어 합쳐지지 않게 한다."""
        if env.type == EventType.SUPPORT_INQUIRY_RESOLVED:
            return f"support-inquiry-{env.data.get('inquiry_id')}"
        return str(env.data.get("component") or "api")

    @staticmethod
    def _event_type(env: EventEnvelope, component: str) -> str | None:
        if env.type in CASE_EVENTS:
            return None
        return event_type(component, env.data.get("metric_code"))

    @staticmethod
    def _title(env: EventEnvelope, component: str) -> str:
        if env.type == EventType.SUPPORT_INQUIRY_RESOLVED:
            return f"고객 문의 #{env.data.get('inquiry_id')} 처리 결과 기록"
        return event_title(component, env.data.get("metric_code"))

    async def wait(self, context_id: str) -> Incident:
        return await self._tasks[context_id]

    def hold_decision(self, context_id: str, step_id: str, approved: bool) -> bool:
        inc = self.incidents.get(context_id)
        return bool(inc and self.engine.decide_hold(inc, step_id, approved))

    def listing(self) -> list[dict[str, Any]]:
        return [i.view() for i in sorted(self.incidents.values(), key=lambda i: i.opened_at)]
