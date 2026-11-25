"""회사 분리(11/25, 로드맵 6단계): hr 저장소는 요청 회사마다 파일을 따로 연다.

누비투어로 들어오면 캣북스 조직 변경·명부·카드가 보이지 않고,
캣북스(또는 회사 모름)는 예전 파일 그대로.
"""

from __future__ import annotations

from pathlib import Path

from ax_agent_template import company_scope
from hr_agent.store import Store, company_path

ROW = (
    "INSERT INTO org_changes(change_id, context_id, member_id, kind, effective_date,"
    " counts_from_month, status, created_at) VALUES (?, 'ctx', 'm1', 'move', '2026-12-01',"
    " '2026-12', 'draft', '2026-11-25T00:00:00Z')"
)


def _ids(store: Store) -> list[str]:
    return [r["change_id"] for r in store.all("SELECT change_id FROM org_changes")]


def test_companies_do_not_see_each_other(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "hr.db"))
    store.execute(ROW, ("c-catbooks",))  # 요청 밖 = 캣북스 (예전 파일)
    with company_scope.using("nubi-tour"):
        assert _ids(store) == []  # 캣북스 조직 변경이 안 보인다
        store.execute(ROW, ("c-nubi",))
        with store.transaction():
            store.execute(ROW, ("c-nubi-2",))
        assert _ids(store) == ["c-nubi", "c-nubi-2"]
        store.execute("INSERT INTO mock_cards(card_id, data, created_at) VALUES ('k1', '{}', 'x')")
    with company_scope.using("catbooks"):
        assert _ids(store) == ["c-catbooks"]
        assert store.all("SELECT card_id FROM mock_cards") == []
    assert _ids(store) == ["c-catbooks"]
    assert (tmp_path / "hr.db").exists() and (tmp_path / "hr.nubi-tour.db").exists()
    store.close()


def test_company_path_keeps_catbooks_file_and_memory() -> None:
    assert company_path("/d/hr.db", "catbooks") == "/d/hr.db"
    assert Path(company_path("/d/hr.db", "nubi-tour")).name == "hr.nubi-tour.db"
    assert company_path(":memory:", "nubi-tour") == ":memory:"
    store = Store()
    with company_scope.using("study-on-edu"):
        store.execute(ROW, ("c-s",))
    with company_scope.using("nubi-tour"):
        assert _ids(store) == []  # 메모리도 회사마다 따로
    assert _ids(store) == []
