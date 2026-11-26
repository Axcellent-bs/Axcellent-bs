"""사이트 변환기 회사의 상품·판매자·쿠폰 읽기 시간 (11/26 누비투어 데이터 홈 총거래액).

변환기는 부를 때마다 고객사 사이트를 쪽마다 읽어 첫 읽기가 5초를 넘길 수 있다. 5초에 끊기면 장부가
"products 없음"이 되어 기간 손익(데이터 홈 총거래액)이 하루 내내 확인할 수 없음으로 남았다.
"""

from __future__ import annotations

from ax_agent_template import company_scope
from finance_agent import books


def test_catbooks_keeps_the_short_side_call_timeout() -> None:
    assert books.side_call_seconds() == books.CALL_SECONDS
    with company_scope.using("catbooks"):
        assert books.side_call_seconds() == books.CALL_SECONDS


def test_adapter_company_gets_the_orders_timeout_for_products() -> None:
    with company_scope.using("c-cf2f0d"):
        assert books.side_call_seconds() == books.ORDERS_CALL_SECONDS > books.CALL_SECONDS
