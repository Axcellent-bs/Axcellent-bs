"""쿠폰 계산·순서·중복 처리. 프레임워크에 의존하지 않는다.

- 총액은 건수×단가로 코드가 계산한다(LLM·요청 값을 쓰지 않는다).
- 보상 쿠폰(`compensation`)은 `context_id`별로 `sequence` 1부터 순서대로 한 건씩 처리한다.
  건너뛴 번호는 `SEQUENCE_GAP`이다.
- 같은 `sequence`나 같은 `idempotency_key`를 다시 받으면 **이전 결과**를 돌려준다.
  단, 이전 결과가 `completed`(지급 완료)일 때만이다. 보류(`held`)·취소(`canceled`)·
  실패(`failed`)는 다시 시도할 수 있어야 하므로 저장하지 않는다. 사람이 승인한 뒤 같은 키로
  다시 요청하면 safety가 이전 판정을 돌려주고, 여기서 새로 실행해 지급한다.
- 번호는 회신한 건마다 한 칸 넘어간다. 보류·실패 건도 번호는 소비하지만 결과는 저장하지 않는다.
- 같은 사건에서 같은 구매자에게 `sequence`만 달리 다시 요청하면 새 쿠폰을 만들지 않고
  이전 결과를 `linked_duplicate`로 돌려주고 번호만 넘긴다(이중 지급 방지).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

from ax_contracts.schemas import AgentResult

if TYPE_CHECKING:
    from marketing_agent.state_db import StateDb


def total_value(count: int, unit_value: int) -> int:
    return count * unit_value


class SequenceGap(Exception):
    """기다리는 번호보다 앞선 `sequence`가 들어왔다."""


_SEP = "\x1f"  # 저장 키: context_id + 구분자 + sequence


class CouponLedger:
    """`db`(`StateDb`)를 주면 지급 결과·번호 소비·쿠폰 번호를 저장하고 켤 때 다시 읽는다.

    서버를 다시 켜도 같은 키·같은 번호 재요청이 새 쿠폰을 만들지 않는다(2026-11-23).
    """

    def __init__(self, db: StateDb | None = None) -> None:
        self._db = db
        self._next = 1
        self._by_key: dict[str, AgentResult] = {}
        # context → sequence → 저장한 결과(저장하지 않는 건은 None). 번호를 소비했다는 기록이다
        self._consumed: dict[str, dict[int, AgentResult | None]] = defaultdict(dict)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._load()

    def _load(self) -> None:
        if self._db is None:
            return
        for key, raw in self._db.all("coupon_by_key"):
            self._by_key[key] = AgentResult.model_validate(raw)
        for key, raw in self._db.all("coupon_consumed"):
            context_id, _, sequence = key.rpartition(_SEP)
            result = AgentResult.model_validate(raw) if raw is not None else None
            self._consumed[context_id][int(sequence)] = result
        counter = dict(self._db.all("coupon_counter")).get("next")
        self._next = int(counter) if counter else 1

    def lock(self, context_id: str) -> asyncio.Lock:
        """같은 사건의 보상 요청은 한 건씩만 처리하기 위한 잠금."""
        return self._locks[context_id]

    def new_coupon_id(self) -> str:
        coupon_id = f"cpn_{self._next:04d}"
        self._next += 1
        if self._db is not None:
            self._db.put("coupon_counter", "next", self._next)
        return coupon_id

    def issued_count(self) -> int:
        """지급을 마친(저장된) 쿠폰 건수. 구매자 정보는 돌려주지 않는다."""
        return len(self._by_key)

    def previous_by_key(self, idempotency_key: str) -> AgentResult | None:
        return self._by_key.get(idempotency_key)

    def previous_by_sequence(self, context_id: str, sequence: int) -> AgentResult | None:
        return self._consumed[context_id].get(sequence)

    def check_sequence(self, context_id: str, sequence: int) -> None:
        """다음 번호보다 앞서면 `SequenceGap`. 이미 소비한 번호나 다음 번호는 통과한다."""
        expected = len(self._consumed[context_id]) + 1
        if sequence > expected:
            raise SequenceGap(f"{expected}번을 기다리는데 {sequence}번이 왔다")

    def record(
        self,
        context_id: str,
        idempotency_key: str,
        sequence: int | None,
        result: AgentResult,
        *,
        cache: bool,
    ) -> None:
        """결과를 남긴다. `cache`가 거짓이면 번호만 소비하고 결과는 저장하지 않는다."""
        if cache and idempotency_key not in self._by_key:  # 처음 저장한 결과가 기준이다
            self._by_key[idempotency_key] = result
            if self._db is not None:
                self._db.put("coupon_by_key", idempotency_key, result.model_dump(mode="json"))
        if sequence is not None:
            stored = result if cache else None
            self._consumed[context_id][sequence] = stored
            if self._db is not None:
                self._db.put(
                    "coupon_consumed",
                    f"{context_id}{_SEP}{sequence}",
                    stored.model_dump(mode="json") if stored is not None else None,
                )
