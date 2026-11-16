"""인사 → 총괄 에이전트 평가 진단 요청 받기 (`diagnose_agent_issue`).

인사가 에이전트 평가에서 같은 단계의 확인 필요가 반복된 것을 찾아 보내면 총괄은 요청으로 저장한다.
이 단계는 접수까지다. 원인 진단과 해당 에이전트에 전달하는 일은 이후 단계에서 한다.

- 같은 (에이전트, 단계)에 열린 요청(`received`·`diagnosing`)이 있으면 새 근거만 더한다(`updated`).
- 이미 있는 근거만 오면 그대로 둔다(`duplicate`).
- 닫힌 요청(진단 끝남·근거 부족·전달됨)이 있으면 그 근거와 겹치지 않는 새 근거가
  `MIN_NEW_EVIDENCE`건 이상일 때만 새 요청으로 받는다(같은 이슈로 진단을 되풀이하지 않는다).
- 에이전트 하나가 하루(서울 시간)에 새로 받는 요청은 `DAILY_LIMIT`건까지다. 넘으면 받지 않고
  `limited`로 답하며, 인사가 다음 판정 때 같은 조건이면 다시 보낸다.
- 저장소(`IncidentStore`, `ORCHESTRATOR_DB`)를 주면 파일에도 적어 다시 켜도 남는다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from ax_contracts.schemas.diagnosis import DiagnoseAgentIssueInput, DiagnoseAgentIssueOutput

if TYPE_CHECKING:
    from ax_orchestrator.store import IncidentStore

OPEN_STATES = ("received", "diagnosing")
MIN_NEW_EVIDENCE = 2
DAILY_LIMIT = 1
MAX_REQUESTS = 500
KST = timedelta(hours=9)

log = logging.getLogger("orchestrator.agent_diagnoses")


def _day(value: str) -> str:
    return (datetime.fromisoformat(value) + KST).date().isoformat()


class AgentDiagnoses:
    def __init__(self, store: IncidentStore | None = None, daily_limit: int = DAILY_LIMIT) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._store = store
        self._daily_limit = daily_limit
        self._seq = 0

    def restore(self) -> int:
        """저장된 요청을 올린다. 올린 수를 돌려준다."""
        if self._store is None:
            return 0
        for row in self._store.load_diagnoses():
            request_id = row.get("request_id")
            if isinstance(request_id, str):
                self._rows[request_id] = row
                tail = request_id.rsplit("-", 1)[-1]
                self._seq = max(self._seq, int(tail) if tail.isdigit() else 0)
        return len(self._rows)

    def _save(self, row: dict[str, Any]) -> None:
        self._rows[row["request_id"]] = row
        if self._store is None:
            return
        try:
            self._store.save_diagnosis(row["request_id"], row["received_at"], row)
        except Exception:  # noqa: BLE001 - 저장 실패가 받기를 막지 않게 한다(인사가 다시 보낸다)
            log.exception("진단 요청 저장 실패: %s", row["request_id"])

    def _same(self, agent: str, step_id: str) -> list[dict[str, Any]]:
        return [r for r in self._rows.values() if r["agent"] == agent and r["step_id"] == step_id]

    def receive(
        self, req: DiagnoseAgentIssueInput, *, now: datetime | None = None
    ) -> DiagnoseAgentIssueOutput:
        now = now or datetime.now(UTC)
        stamp = now.isoformat()
        incoming = {e.context_id: e.model_dump(mode="json") for e in req.evidence}
        same = self._same(req.agent, req.step_id)
        opened = next((r for r in same if r["status"] in OPEN_STATES), None)
        if opened is not None:
            have = {e["context_id"] for e in opened["evidence"]}
            fresh = [e for cid, e in incoming.items() if cid not in have]
            if not fresh:
                return DiagnoseAgentIssueOutput(
                    request_id=opened["request_id"], status="duplicate", duplicate=True
                )
            opened["evidence"] = [*fresh, *opened["evidence"]]
            opened["flagged"] = len(opened["evidence"])
            opened["window"] = max(opened["window"], req.window)
            opened["updated_at"] = stamp
            self._save(opened)
            return DiagnoseAgentIssueOutput(request_id=opened["request_id"], status="updated")
        seen = {e["context_id"] for r in same for e in r["evidence"]}
        fresh_ids = [cid for cid in incoming if cid not in seen]
        if same and len(fresh_ids) < MIN_NEW_EVIDENCE:
            return DiagnoseAgentIssueOutput(
                request_id=same[-1]["request_id"], status="duplicate", duplicate=True
            )
        today = _day(stamp)
        made_today = [
            r
            for r in self._rows.values()
            if r["agent"] == req.agent and _day(r["received_at"]) == today
        ]
        if len(made_today) >= self._daily_limit:
            return DiagnoseAgentIssueOutput(status="limited")
        self._seq += 1
        request_id = f"diag-{self._seq}"
        evidence = [incoming[cid] for cid in incoming if not same or cid in fresh_ids]
        self._save(
            {
                "request_id": request_id,
                "agent": req.agent,
                "step_id": req.step_id,
                "step_label": req.step_label,
                "window": req.window,
                "flagged": len(evidence),
                "criteria_version": req.criteria_version,
                "evidence": evidence,
                "status": "received",
                "requested_at": req.requested_at.isoformat(),
                "received_at": stamp,
                "updated_at": stamp,
            }
        )
        self._trim()
        return DiagnoseAgentIssueOutput(request_id=request_id, status="received")

    def _trim(self) -> None:
        while len(self._rows) > MAX_REQUESTS:
            oldest = next(iter(self._rows))
            del self._rows[oldest]
            if self._store is not None:
                try:
                    self._store.drop_diagnosis(oldest)
                except Exception:  # noqa: BLE001
                    log.exception("오래된 진단 요청 지우기 실패: %s", oldest)

    def list(self) -> list[dict[str, Any]]:
        return list(self._rows.values())
