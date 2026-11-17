"""재무 → 총괄 의견 받기 (`receive_finance_opinion`).

재무가 캠페인 손익·예산 거절·인사이트를 의견으로 보내면 총괄은 받은 그대로 보관한다.
재무는 못 보낸 의견을 "전달 대기"로 쌓았다가 다시 보내므로 같은 `opinion_id`는 한 번만 담는다.

- 받은 의견으로 이벤트를 열거나 다른 부서에 일을 시키지 않는다. 보관만 한다.
- 총괄이 아직 준비되지 않았으면(`manager`가 없음) 담지 않고 실패로 답한다. 재무는 전달 대기로
  두고 나중에 다시 보낸다.
- 개수가 `MAX_OPINIONS`를 넘으면 오래된 것부터 버린다. 재무 쪽에 원본이 남아 있다.
- 저장소(`IncidentStore`, `ORCHESTRATOR_DB`)를 주면 받은 의견을 파일에도 적어 다시 켜도 남는다.
  총괄 화면(`GET /api/finance-opinions`)이 이 목록을 보여 준다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ax_contracts.schemas.finance import FinanceOpinion

if TYPE_CHECKING:
    from ax_orchestrator.store import IncidentStore

MAX_OPINIONS = 500

log = logging.getLogger("orchestrator.finance_opinions")


class FinanceOpinions:
    def __init__(self, store: IncidentStore | None = None) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._store = store

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
