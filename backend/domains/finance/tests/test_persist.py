"""재무 기록을 파일에 남기고 다시 켤 때 이어 받는다 (`FINANCE_STATE_FILE`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ax_contracts.schemas import AgentResult, RequestMeta, TaskStatus
from finance_agent import persist
from finance_agent.handlers import State
from finance_agent.sources import MockSources


def _filled() -> State:
    s = State(MockSources())
    meta = RequestMeta(
        context_id="ctx_inc_1", token="x", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    s.ledger.coupons["ctx_inc_1"] = {"count": 2, "total": 4000}
    s.ledger.refunds["ord_7"] = 18000
    s.ledger.seen_events.update({"evt_1", "evt_2"})
    s.ledger.done["ctx_inc_1:seller_review:sel_1"] = AgentResult(
        meta=meta, status=TaskStatus.COMPLETED, output={"decision": "approved"}
    )
    s.ledger.market_ops.append({"skill": "settlement_hold", "seller_id": "sel_3", "status": "held"})
    s.ledger.support_costs[7] = {"record_id": "sc_7"}
    s.ledger.coupon_events["ctx_inc_1"] = [{"coupon_id": "cpn_1"}]
    s.records.closings[("day", "2026-10-28")] = {"by": "태윤", "at": "2026-10-29T00:00:00Z"}
    s.records.recon_notes["pay_1"] = "카드사 확인"
    s.records.recon_note_log.append({"payment_id": "pay_1"})
    s.records.batches["2026-10"] = {"status": "requested"}
    return s


def test_round_trip_keeps_everything(tmp_path: Path) -> None:
    path = tmp_path / "finance_state.json"
    before = _filled()
    persist.save(before, path)

    after = State(MockSources())
    assert persist.load(after, path)
    assert persist.dump(after) == persist.dump(before)
    assert after.held_sellers() == {"sel_3"}  # 정산 보류가 다시 켜도 남는다
    assert after.ledger.support_costs[7] == {"record_id": "sc_7"}  # 숫자 키 그대로
    assert after.records.closings[("day", "2026-10-28")]["by"] == "태윤"
    assert isinstance(after.ledger.done["ctx_inc_1:seller_review:sel_1"], AgentResult)


def test_save_writes_only_when_changed(tmp_path: Path) -> None:
    path = tmp_path / "finance_state.json"
    s = _filled()
    first = persist.save(s, path)
    stamp = path.stat().st_mtime_ns
    assert persist.save(s, path, first) == first
    assert path.stat().st_mtime_ns == stamp
    s.ledger.refunds["ord_8"] = 1000
    assert persist.save(s, path, first) != first


def test_missing_file_is_a_fresh_start(tmp_path: Path) -> None:
    assert not persist.load(State(MockSources()), tmp_path / "none.json")


def test_broken_file_is_kept_aside(tmp_path: Path) -> None:
    path = tmp_path / "finance_state.json"
    path.write_text('{"version": 99}', encoding="utf-8")
    s = State(MockSources())
    assert not persist.load(s, path)
    assert not path.exists() and (tmp_path / "finance_state.json.broken").exists()
    assert s.ledger.market_ops == []


def test_no_path_means_no_file(monkeypatch) -> None:
    monkeypatch.delenv("FINANCE_STATE_FILE", raising=False)
    assert persist.state_file() is None
