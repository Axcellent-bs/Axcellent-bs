"""재무 → 총괄 의견 받기 (`receive_finance_opinion`).

재무가 캠페인 손익·예산 거절·인사이트를 의견으로 보내면 총괄은 받은 그대로 보관한다.
재무는 못 보낸 의견을 "전달 대기"로 쌓았다가 다시 보내므로 같은 `opinion_id`는 한 번만 담는다.

- 받은 의견으로 이벤트를 열거나 다른 부서에 일을 시키지 않는다. 보관만 한다.
- 총괄이 아직 준비되지 않았으면(`manager`가 없음) 담지 않고 실패로 답한다. 재무는 전달 대기로
  두고 나중에 다시 보낸다.
- 개수가 `MAX_OPINIONS`를 넘으면 오래된 것부터 버린다. 재무 쪽에 원본이 남아 있다.
"""

from __future__ import annotations

from typing import Any

from ax_contracts.schemas.finance import FinanceOpinion

MAX_OPINIONS = 500


class FinanceOpinions:
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def add(self, opinion: FinanceOpinion, context_id: str) -> bool:
        """처음 받은 의견이면 담고 True, 이미 있으면 그대로 두고 False."""
        if opinion.opinion_id in self._rows:
            return False
        self._rows[opinion.opinion_id] = {
            "opinion": opinion.model_dump(mode="json"),
            "context_id": context_id,
        }
        while len(self._rows) > MAX_OPINIONS:
            del self._rows[next(iter(self._rows))]
        return True

    def list(self) -> list[dict[str, Any]]:
        return list(self._rows.values())
