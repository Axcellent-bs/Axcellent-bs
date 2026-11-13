"""장애 장부: 시간 초과 결제(확인·환불 필요)를 보상 쿠폰과 따로 센다."""

from __future__ import annotations

from finance_agent import finance_config as fc
from finance_agent.incidents import incident_timeout_payments


def test_timeout_payments_in_window_are_counted(kit) -> None:
    when = kit.at("2026-10-14", "10:00")
    orders = [
        kit.order("ord_1", when, [("sel_a", "book", 1, 15000)], payment_status="timeout"),
        kit.order("ord_2", when, [("sel_a", "book", 1, 9000)], payment_status="failed"),
        kit.order("ord_3", kit.at("2026-10-20"), [("sel_a", "book", 1, 7000)],
                  payment_status="timeout"),  # 구간 밖
    ]  # fmt: skip
    books = kit.books(orders=orders)
    window = {
        "from": fc.iso(kit.at("2026-10-14", "09:00")),
        "to": fc.iso(kit.at("2026-10-14", "11:00")),
    }
    assert incident_timeout_payments(books, window) == {"total": 15000, "count": 1}
    assert incident_timeout_payments(books, None) is None
