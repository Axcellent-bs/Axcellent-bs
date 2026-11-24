"""마케팅 저장소는 요청 회사의 줄을 읽고 쓴다 (11/24 누비투어 연결)."""

from ax_agent_template import company_scope as request_scope
from marketing_agent import company_scope
from marketing_agent.state_db import StateDb


class _Store:
    company_id = company_scope.RequestCompany()

    def __init__(self, company_id: str | None = None) -> None:
        self.company_id = company_id


def test_store_follows_the_request_company_and_falls_back_to_its_own() -> None:
    made = _Store()  # 서버가 켤 때 한 번 만든 저장소(캣북스)
    assert made.company_id == "catbooks"
    with request_scope.using("nubi"):
        assert made.company_id == "nubi"
    with request_scope.using("shop-demo"):  # 별칭은 캣북스
        assert made.company_id == "catbooks"
    assert _Store("nubi").company_id == "nubi"  # 요청 밖이면 만들 때 받은 회사


def test_state_db_rows_are_kept_per_request_company() -> None:
    db = StateDb(":memory:")
    assert db.company_id == "catbooks"
    with request_scope.using("nubi"):
        assert db.company_id == "nubi"
