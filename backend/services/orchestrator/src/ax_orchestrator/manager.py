"""이벤트 수신과 이벤트 판단 (ORC-02·03).

이벤트를 받아 `event_id` 중복을 거르고, 이벤트이면 `ctx_inc_`를 발급해 plan을 돌린다.
같은 구성요소의 열린 이벤트가 있으면 새로 열지 않고 합친다.
판단은 규칙(코드)이 한다. 원인·분석 문장은 만들지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from ax_contracts.schemas import EventEnvelope, EventType, SafetyReviewDecidedData
from ax_orchestrator.engine import Engine, ReviewOutcome, incident_metrics
from ax_orchestrator.finance_opinions import FinanceOpinions
from ax_orchestrator.models import Incident, Plan, iso, now
from ax_orchestrator.naming import event_title, event_type
from ax_orchestrator.plans.support_resolved import support_resolved
from ax_orchestrator.store import IncidentStore

log = logging.getLogger("orchestrator.manager")


def _review_log(outcome: ReviewOutcome, review_id: str, event_id: str) -> str:
    who = f" ({outcome.by})" if outcome.by else ""
    verdict = (
        "승인" if outcome.approved else "만료" if outcome.reason == "review_timeout" else "거절"
    )
    return f"안전 관문 결정 수신: {verdict}{who} — {review_id} ({event_id})"


def _since(env: EventEnvelope) -> datetime | None:
    """신호의 장애 시작 시각(data.since). 없거나 형식이 다르면 None."""
    raw = env.data.get("since")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _keep_recovery(inc: Incident, data: dict[str, Any]) -> None:
    """복구 신호가 알려 준 풀린 방법(`recovery`·`released_by`)을 해결 기록에 쓰려고 남긴다."""
    recovery = data.get("recovery")
    if recovery in ("fault_released", "metrics_normal"):
        by = data.get("released_by")
        inc.shared["recovery"] = {
            "recovery": recovery,
            "released_by": by if isinstance(by, str) and by else None,
        }


LOOKBACK = timedelta(minutes=15)  # 이벤트가 열리기 직전 실패 주문·문의도 같은 이벤트로 본다
INCIDENT_EVENTS = {
    EventType.SHOP_DEGRADED,
    EventType.INFRA_ANOMALY_DETECTED,
}
# 이벤트 종류별 plan. 없는 종류는 기본 plan(시나리오 A)
PLANS: dict[EventType, tuple[str, Callable[[], Plan]]] = {
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
    EventType.METRIC_ANOMALY,
    EventType.SUPPORT_INQUIRY_RECEIVED,
    EventType.SUPPORT_INQUIRY_SURGE,
    EventType.SUPPORT_INQUIRY_RESOLVED,
    # 사람 할 일함에서 끝낸 일 → 기다리던 단계를 이어 간다 (업무 요청 라우터)
    EventType.HANDOFF_COMPLETED,
    # 안전 관문의 사람 결정(승인·거절·만료) → 승인 대기 단계를 잇는다
    EventType.SAFETY_REVIEW_DECIDED,
]


# 다시 켜질 때 진행 중이던 단계의 상태: 이어 가지 않고 이 상태로 남긴다
UNFINISHED = {"pending", "running", "held", "waiting_human"}
INTERRUPTED_NOTE = "재시작으로 중단됨 (사람이 확인 필요)"


class IncidentManager:
    def __init__(
        self,
        engine: Engine,
        plan_factory: Callable[[], Plan],
        store: IncidentStore | None = None,
    ) -> None:
        self.engine = engine
        self.plan_factory = plan_factory
        self.store = store
        self.incidents: dict[str, Incident] = {}
        self.finance_opinions = FinanceOpinions()
        self._seen: set[str] = set()
        self._tasks: dict[str, asyncio.Task[Incident]] = {}

    def _persist(self, inc: Incident) -> None:
        if self.store is None:
            return
        try:
            self.store.save(inc)
        except Exception:  # noqa: BLE001 - 저장 실패가 이벤트 처리를 막지 않게 한다
            log.exception("이벤트 저장 실패: %s", inc.context_id)

    def restore(self) -> int:
        """저장된 이벤트와 처리한 event_id를 불러온다. 진행 중이던 이벤트의 수를 돌려준다.

        진행 중이던 이벤트(`open`)는 이어서 실행하지 않는다. 승인 대기·사람 할 일함 대기는 저장되지
        않았고, 이미 보낸 요청을 다시 보내면 안 되는 것이 있을 수 있어서다. 끝나지 않은 단계는
        "재시작으로 중단됨"으로 남기고 이벤트는 `degraded`로 닫아 사람이 확인하게 한다.
        """
        if self.store is None:
            return 0
        self._seen |= self.store.seen_ids()
        interrupted = 0
        for inc in self.store.load_all():
            if inc.status == "open":
                self._mark_interrupted(inc)
                self._persist(inc)
                interrupted += 1
            self.incidents[inc.context_id] = inc
        return interrupted

    @staticmethod
    def _mark_interrupted(inc: Incident) -> None:
        at = now()
        for run in inc.steps.values():
            if run.status in UNFINISHED:
                run.status, run.note, run.finished_at = "failed", INTERRUPTED_NOTE, at
                run.reason_code = run.reason_code or "restart_interrupted"
        inc.status = "degraded"
        inc.closed_at = at
        inc.settle_after_recovery()  # 복구 신호를 이미 받았으면 장애 중으로 남기지 않는다
        inc.summary = [Engine._summary_line(run) for run in inc.steps.values()]
        inc.metrics = incident_metrics(inc)
        inc.log(f"총괄이 다시 켜져 진행 중이던 이벤트를 이어 가지 않았다: {INTERRUPTED_NOTE}")

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

    def _latest_unrecovered(self, component: str, occurred_at: datetime | None) -> Incident | None:
        """정상 복귀를 붙일 이벤트: 같은 구성요소에서 복귀 시각이 없는 가장 최근 장애.

        이벤트는 자동 처리가 끝나면 닫히지만 사람이 고치는 동안 장애는 계속되므로 닫힌 것도 본다.
        """
        found = [
            inc
            for inc in self.incidents.values()
            if inc.component == component
            and inc.recovered_at is None
            and inc.event_type is not None
            and (occurred_at is None or inc.opened_at <= occurred_at)
        ]
        return max(found, key=lambda inc: inc.opened_at, default=None)

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
        `resumed`(사람 할 일함 처리 결과로 단계를 이어 감) ·
        `kept`(기다리는 단계가 아직 없어 보관, 그 단계가 대기로 오르면 이어 감) ·
        `unverified`(안전 관문이 그 결정을 확인해 주지 않아 무시) ·
        `unmatched`(이미 반영했거나 받을 이벤트가 없음).
        """
        if env.event_id in self._seen:
            return "duplicate", None
        self._seen.add(env.event_id)
        result = await self._process(env)
        # 이벤트를 저장한 뒤에 적는다: 그 사이에 꺼지면 같은 이벤트를 다시 받아 처리한다
        # (잃지 않는다)
        if self.store is not None:
            try:
                self.store.mark_seen(env.event_id)
            except Exception:  # noqa: BLE001 - 기록 실패가 이벤트 처리를 막지 않게 한다
                log.exception("처리한 event_id 저장 실패: %s", env.event_id)
        return result

    async def _process(self, env: EventEnvelope) -> tuple[str, Incident | None]:
        if env.type == EventType.HANDOFF_COMPLETED:
            return await self._resume_handoff(env)
        if env.type == EventType.SAFETY_REVIEW_DECIDED:
            return await self._resume_review(env)
        if env.type == EventType.SHOP_RECOVERED:
            target = self._latest_unrecovered(self._component(env), env.occurred_at)
            if target is not None:
                target.recovered_at = env.occurred_at
                _keep_recovery(target, env.data)
                target.settle_after_recovery()
                target.refresh_mttr()
                target.log(f"정상 복귀 신호 받음 ({env.source}, {iso(env.occurred_at)})")
                plan = self.plan_factory()
                await self.engine.refresh_impact(target, plan)
                if target.closed_at is not None:
                    # 닫힌 이벤트는 복구 신호를 기다리던 단계(장애 배너 내리기)를 이제 돌린다
                    await self.engine.rerun_after_recovery(target, plan)
                if target.closed_at is not None:  # 열려 있으면 이벤트가 닫힐 때 함께 남긴다
                    await self.engine.sync_times(target)
                    await self.engine.record_late_resolution(target, plan)
                self._persist(target)
                return "noted", target
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
        self._persist(inc)  # 단계가 돌기 전에 먼저 남긴다
        self._tasks[inc.context_id] = asyncio.create_task(self.engine.run(inc, plan))
        return "opened", inc

    def _mark_recovered(self, component: str, at: datetime) -> None:
        """복구 신호를 받으면 같은 구성요소의 degraded 이벤트가 장애 중이 아니게 된다."""
        for inc in self.incidents.values():
            if inc.component == component and inc.status == "degraded" and not inc.recovered_at:
                inc.recovered_at = max(at, inc.closed_at or at)
                inc.settle_after_recovery()
                inc.refresh_mttr()
                inc.log(f"복구 신호 수신 ({iso(at)})")
                self._persist(inc)

    async def _resume_handoff(self, env: EventEnvelope) -> tuple[str, Incident | None]:
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
            if self.engine.keep_early_handoff(inc, task_id, status, note):
                inc.log(
                    f"사람 할 일함 처리 결과를 먼저 받아 보관: {task_id} ({status}, {env.event_id})"
                )
                return "kept", inc
            return "unmatched", inc
        inc.log(f"사람 할 일함 처리 결과 수신: {task_id} ({status}, {env.event_id})")
        await self.engine.sync_times(inc)
        return "resumed", inc

    async def _resume_review(self, env: EventEnvelope) -> tuple[str, Incident | None]:
        """안전 관문의 사람 결정으로 승인 대기 단계를 잇는다 (`review_id`로 맞춘다).

        이벤트는 위조될 수 있어서, 안전 관문에 되물어 확인된 결정만 따른다(`Engine.settle_review`).
        승인이면 단계가 같은 요청을 다시 보내 이어 가고, 거절·만료면 거절로 끝난다.
        """
        if env.source != "safety":
            log.warning("안전 관문이 아닌 곳이 보낸 승인 결정은 무시: %s", env.source)
            return "ignored", None
        try:
            data = SafetyReviewDecidedData.model_validate(env.data)
        except ValueError:
            return "unmatched", None
        found = next(
            (
                (inc, hold)
                for inc in self.incidents.values()
                for hold in inc.holds.values()
                if hold.pending and hold.review_id == data.review_id
            ),
            None,
        )
        outcome = await self.engine.settle_review(data.review_id, data.decision)
        if outcome is None:
            if found is not None:
                found[0].log(
                    f"안전 관문 결정({data.decision})을 확인하지 못해 무시: {data.review_id}"
                )
            return "unverified", found[0] if found else None
        if found is None:
            kept = self.engine.keep_early_review(data.review_id, outcome)
            return ("kept" if kept else "unmatched"), None
        inc, hold = found
        hold.decide(outcome.approved, outcome.reason, outcome.by)
        inc.log(_review_log(outcome, data.review_id, env.event_id))
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
