"""재무 → 총괄 의견 받기 (`receive_finance_opinion`).

재무가 캠페인 손익·예산 거절·인사이트를 의견으로 보내면 총괄은 받은 그대로 보관한다.
재무는 못 보낸 의견을 "전달 대기"로 쌓았다가 다시 보내므로 같은 `opinion_id`는 한 번만 담는다.

- 받은 의견으로 이벤트를 열거나 다른 부서에 일을 시키지 않는다. 다만 기획이 알아야 하는 의견
  (`planning_insight`가 실린 의견, 예: 콘텐츠 제작비 재배분)은 기획 `receive_insight`로 넘긴다
  (`finance_relay`, 11/18). 넘긴 결과는 줄의 `relay`에 남는다.
- 총괄이 아직 준비되지 않았으면(`manager`가 없음) 담지 않고 실패로 답한다. 재무는 전달 대기로
  두고 나중에 다시 보낸다.
- 개수가 `MAX_OPINIONS`를 넘으면 오래된 것부터 버린다. 재무 쪽에 원본이 남아 있다.
- 저장소(`IncidentStore`, `ORCHESTRATOR_DB`)를 주면 받은 의견을 파일에도 적어 다시 켜도 남는다.
  총괄 화면(`GET /api/finance-opinions`)이 이 목록을 보여 준다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ax_contracts.schemas.finance import FinanceOpinion
from ax_contracts.schemas.planning import ReceiveInsightInput
from ax_orchestrator import finance_relay

if TYPE_CHECKING:
    from ax_orchestrator.store import IncidentStore

MAX_OPINIONS = 500

log = logging.getLogger("orchestrator.finance_opinions")


RelaySender = Callable[[ReceiveInsightInput, str], Awaitable[dict[str, Any]]]


class FinanceOpinions:
    def __init__(
        self, store: IncidentStore | None = None, tenant: Callable[[], str | None] | None = None
    ) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._store = store
        # 기획에 넘기는 곳 (시험은 바꿔 끼운다)과 넘기는 중인 일.
        # 매니저 회사로 넘긴다(11/25): 다른 회사 의견이 캣북스 기획에 들어가지 않게
        self._tenant = tenant or (lambda: None)
        self.relay_sender: RelaySender = lambda inp, ctx: finance_relay.send_to_planning(
            inp, ctx, self._tenant()
        )
        self.retry_delays: tuple[float, ...] = finance_relay.RETRY_DELAYS
        self._relaying: dict[str, asyncio.Task] = {}

    def restore(self) -> int:
        """저장된 의견을 올린다. 올린 수를 돌려준다."""
        if self._store is None:
            return 0
        for row in self._store.load_opinions():
            opinion_id = row["opinion"].get("opinion_id")
            if isinstance(opinion_id, str):
                self._rows[opinion_id] = row
        self._trim()
        return len(self._rows)

    def add(self, opinion: FinanceOpinion, context_id: str) -> bool:
        """처음 받은 의견이면 담고 True, 이미 있으면 그대로 두고 False."""
        if opinion.opinion_id in self._rows:
            return False
        row = {
            "opinion": opinion.model_dump(mode="json"),
            "context_id": context_id,
            "received_at": datetime.now(UTC).isoformat(),
        }
        inp = finance_relay.planning_input(row["opinion"])
        if inp is not None:  # 기획이 알아야 하는 의견: 넘길 차례로 둔다
            row["relay"] = (
                {"to": "planning", "status": "pending", "attempts": 0}
                if not isinstance(inp, str)
                else {"to": "planning", "status": "invalid", "attempts": 0, "problem": inp}
            )
        self._rows[opinion.opinion_id] = row
        if self._store is not None:
            try:
                self._store.save_opinion(opinion.opinion_id, row["received_at"], row)
            except Exception:  # noqa: BLE001 - 저장 실패가 받기를 막지 않게 한다(재무에 원본이 있다)
                log.exception("재무 의견 저장 실패: %s", opinion.opinion_id)
        self._trim()
        return True

    def _trim(self) -> None:
        while len(self._rows) > MAX_OPINIONS:
            oldest = next(iter(self._rows))
            del self._rows[oldest]
            if self._store is not None:
                try:
                    self._store.drop_opinion(oldest)
                except Exception:  # noqa: BLE001
                    log.exception("오래된 재무 의견 지우기 실패: %s", oldest)

    def list(self) -> list[dict[str, Any]]:
        return list(self._rows.values())

    # ------------------------------------------------------------ 기획에 넘기기 (11/18)
    def _save(self, opinion_id: str) -> None:
        if self._store is None:
            return
        try:
            self._store.update_opinion(opinion_id, self._rows[opinion_id])
        except Exception:  # noqa: BLE001
            log.exception("재무 의견 전달 상태 저장 실패: %s", opinion_id)

    def pending_relays(self) -> list[str]:
        return [
            oid
            for oid, row in self._rows.items()
            if (row.get("relay") or {}).get("status") == "pending" and oid not in self._relaying
        ]

    async def relay_once(self, opinion_id: str) -> bool:
        """한 번 넘긴다. 넘겼으면(기획이 받음·이미 받음) True."""
        row = self._rows.get(opinion_id)
        if row is None or (row.get("relay") or {}).get("status") != "pending":
            return False
        inp = finance_relay.planning_input(row["opinion"])
        if not isinstance(inp, ReceiveInsightInput):
            return False
        relay = row["relay"]
        got = await self.relay_sender(inp, row["context_id"])
        relay["attempts"] = relay.get("attempts", 0) + 1
        relay["at"] = datetime.now(UTC).isoformat()
        if got.get("ok"):
            relay.update(
                status="delivered", outcome=got.get("outcome"), proposal_id=got.get("proposal_id")
            )
            relay.pop("problem", None)
        else:
            relay["problem"] = got.get("problem")
        self._save(opinion_id)
        return relay["status"] == "delivered"

    async def relay(self, opinion_id: str) -> None:
        """넘기고, 못 넘기면 정한 간격으로 다시 보낸다. 다 실패하면 pending으로 남는다."""
        delays = (0.0, *self.retry_delays)
        for wait in delays:
            if wait:
                await asyncio.sleep(wait)
            if await self.relay_once(opinion_id):
                return

    def start_relays(self) -> int:
        """넘길 차례인 의견마다 넘기기를 시작한다(기다리지 않는다). 시작한 수."""
        started = 0
        for oid in self.pending_relays():
            task = asyncio.create_task(self.relay(oid))
            self._relaying[oid] = task
            task.add_done_callback(lambda _t, oid=oid: self._relaying.pop(oid, None))
            started += 1
        return started
