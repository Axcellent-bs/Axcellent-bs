"""회사 분리 (11/25): 정산 장부·정산 묶음·사건 보상 큐·문의함 읽기 상태가 다른 회사에 보이지 않는다.

예전에는 한 설치에 하나씩(전역)이라 누비투어 화면에 캣북스 것이 나왔다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ax_agent_template import company_scope
from support_agent import inquiry_pull, settlement_batches
from support_agent import settlement_books as sb
from support_agent.incident_queue import IncidentQueueStore


def _add(queue: IncidentQueueStore, tenant: str) -> None:
    queue.add("ctx_inc_1", "buy_0001", "delay", count=1, unit_value=3000, impact_ref=None,
              created_by="tester", tenant_id=tenant)  # fmt: skip


def test_incident_queue_rows_stay_in_their_company() -> None:
    queue = IncidentQueueStore()
    _add(queue, "shop-demo")
    with company_scope.using("nubi-tour"):
        assert queue.list() == []  # 캣북스 줄이 보이지 않는다
        assert queue.get("ctx_inc_1", "buy_0001", "delay") is None
        assert queue.find_buyer("ctx_inc_1", "buy_0001") is None
        _add(queue, "nubi-tour")  # 같은 사건·구매자·사유라도 캣북스 줄을 덮지 않는다
        assert [i.tenant_id for i in queue.list()] == ["nubi-tour"]
    with company_scope.using("catbooks"):
        assert [i.tenant_id for i in queue.list()] == ["shop-demo"]
    # 회사가 안 걸렸으면 캣북스 줄만(11/27: 예전에는 전부). 줄은 요청 안에서만 만들고 바뀐다
    assert [i.tenant_id for i in queue.list()] == ["shop-demo"]


def test_settlement_batches_are_kept_per_company(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(settlement_batches.ENV_FILE, str(tmp_path / "settlement_batches.json"))
    settlement_batches.use_store(
        settlement_batches.BatchStore(tmp_path / "settlement_batches.json")
    )
    settlement_batches.store().put("2026-W47", {"status": "requested"})
    with company_scope.using("nubi-tour"):
        nubi = settlement_batches.store()
        assert nubi is not settlement_batches.STORE
        assert nubi.get("2026-W47") is None  # 캣북스 묶음이 보이지 않는다
        assert nubi.path == tmp_path / "settlement_batches.nubi-tour.json"  # 캣북스 파일과 따로
    assert settlement_batches.store().get("2026-W47") is not None


def test_settlement_books_cache_is_per_company() -> None:
    sb.clear_cache()
    cat = sb.order_store()
    with company_scope.using("nubi-tour"):
        assert sb.company() == "nubi-tour"
        assert sb.order_store() is not cat
        assert sb.cached() is None
    assert sb.order_store() is cat
    sb.clear_cache()


def test_inquiry_pull_status_is_blank_for_a_company_it_does_not_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(inquiry_pull.ENV_TENANT, raising=False)
    inquiry_pull.reset_state()
    inquiry_pull.STATE["ingested_total"] = 7
    inquiry_pull.STATE["last_attempt_at"] = "2026-11-25T03:00:00Z"
    assert inquiry_pull.status()["ingested_total"] == 7  # 요청 밖·캣북스는 예전 그대로
    with company_scope.using("catbooks"):
        assert inquiry_pull.status()["ingested_total"] == 7
    with company_scope.using("nubi-tour"):
        other = inquiry_pull.status()
        assert other["ingested_total"] == 0 and other["connected"] is None
        assert other["not_pulled"] is True and other["enabled"] is False
    inquiry_pull.reset_state()
