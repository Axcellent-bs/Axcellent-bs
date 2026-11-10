"""미발행 이벤트가 많이 쌓여도 flush는 묶음씩만 읽고, drain이 이어서 비운다.

로컬 주문 48만 건에서 flush가 미발행 전부를 한 번에 읽어 시작하자마자 멈춘 일의 재발 방지.
"""

import pytest

from ax_agent_template import events
from ax_contracts.schemas import EventType
from ax_shop_api import outbox
from ax_shop_api.db import Db


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()


def _fill(db: Db, n: int) -> None:
    with db.tx() as conn:
        conn.execute("DELETE FROM outbox_events")
        for i in range(n):
            data = {
                "order_id": f"ord_{i:05d}",
                "buyer_id": "buy_0001",
                "items": [
                    {"product_id": "prd_0001", "seller_id": "sel_01", "qty": 1, "unit_price": 1000}
                ],
                "total_amount": 1000,
            }
            outbox.enqueue(conn, EventType.ORDER_CREATED, data, f"t:{i}")


def _unsent(db: Db) -> int:
    with db.tx() as conn:
        row = conn.execute("SELECT COUNT(*) FROM outbox_events WHERE sent_at IS NULL").fetchone()
        return row[0]


@pytest.mark.asyncio
async def test_flush_sends_at_most_one_batch_in_order() -> None:
    db = Db(":memory:")
    _fill(db, 12)
    assert await outbox.flush(db, batch=5) == 5
    assert _unsent(db) == 7
    sent = [e.data["order_id"] for e in events.MOCK_PUBLISHED]
    assert sent == [f"ord_{i:05d}" for i in range(5)]  # 오래된 순


@pytest.mark.asyncio
async def test_drain_empties_backlog_batch_by_batch() -> None:
    db = Db(":memory:")
    _fill(db, 12)
    assert await outbox.drain(db, batch=5) == 12
    assert _unsent(db) == 0
    assert len({e.event_id for e in events.MOCK_PUBLISHED}) == 12
    assert await outbox.drain(db, batch=5) == 0  # 남은 것이 없으면 바로 끝난다


@pytest.mark.asyncio
async def test_drain_stops_when_publish_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    db = Db(":memory:")
    _fill(db, 12)

    async def not_connected(envelope):  # noqa: ANN001
        raise NotImplementedError("Redis 연동 전")

    monkeypatch.setattr(outbox, "publish", not_connected)
    assert await outbox.drain(db, batch=5) == 0  # 무한히 돌지 않는다
    assert _unsent(db) == 12
