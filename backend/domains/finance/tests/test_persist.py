"""재무 기록을 파일에 남기고 다시 켤 때 이어 받는다 (`FINANCE_STATE_FILE`)."""

from __future__ import annotations

import json
from pathlib import Path

from finance_agent import persist
from finance_agent.handlers import State
from finance_agent.sources import MockSources


def _filled() -> State:
    s = State(MockSources())
    s.ledger.coupons["ctx_inc_1"] = {"count": 2, "total": 4000}
    s.ledger.refunds["ord_7"] = 18000
    s.ledger.seen_events.update({"evt_1", "evt_2"})
    s.ledger.support_costs[7] = {"record_id": "sc_7"}
    s.ledger.coupon_events["ctx_inc_1"] = [{"coupon_id": "cpn_1"}]
    s.records.closings[("day", "2026-10-28")] = {"by": "태윤", "at": "2026-10-29T00:00:00Z"}
    s.records.recon_notes["pay_1"] = "카드사 확인"
    s.records.recon_note_log.append({"payment_id": "pay_1"})
    s.records.batches["2026-10"] = {"status": "requested"}
    s.records.team_budgets["2026-11"] = {"total": 2700000, "signature": "abc", "by": "태윤"}
    return s


def test_round_trip_keeps_everything(tmp_path: Path) -> None:
    path = tmp_path / "finance_state.json"
    before = _filled()
    persist.save(before, path)

    after = State(MockSources())
    assert persist.load(after, path)
    assert persist.dump(after) == persist.dump(before)
    assert after.ledger.support_costs[7] == {"record_id": "sc_7"}  # 숫자 키 그대로
    assert after.records.closings[("day", "2026-10-28")]["by"] == "태윤"
    assert after.records.team_budgets["2026-11"]["by"] == "태윤"  # 확정한 팀 예산도 남는다
    assert after.ledger.refunds == {"ord_7": 18000}


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
    assert s.ledger.coupons == {}


def test_no_path_means_no_file(monkeypatch) -> None:
    monkeypatch.delenv("FINANCE_STATE_FILE", raising=False)
    assert persist.state_file() is None


def test_old_suspicion_records_are_gone_after_a_restart(tmp_path) -> None:
    """예전 파일에 남은 의심 건은 읽지 않고, 다음 저장 때 파일에서도 사라진다."""
    path = tmp_path / "finance_state.json"
    s = State(MockSources())
    old = persist.dump(s)
    old["records"]["anomalies"] = {
        "abc": {"finding_ref": "abc", "code": "duplicate_coupon", "status": "open", "refs": []}
    }
    path.write_text(json.dumps(old), encoding="utf-8")
    again = State(MockSources())
    assert persist.load(again, path)
    assert not hasattr(again.records, "anomalies")
    persist.save(again, path)
    assert "anomalies" not in json.loads(path.read_text("utf-8"))["records"]


def test_an_old_state_file_with_removed_policy_records_still_loads(tmp_path) -> None:
    """없앤 경보 기록이 든 예전 파일로 시작해도 깨지지 않고, 사람의 메모는 파일에 남는다."""
    import json

    path = tmp_path / "finance_state.json"
    s = _filled()
    persist.save(s, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    acks = {"old_rule": {"alert": "warn", "month": "2026-10", "note": "캠페인", "by": "태윤"}}
    raw["records"]["policy_acks"] = acks
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    again = State(MockSources())
    assert persist.load(again, path)
    assert again.ledger.refunds == {"ord_7": 18000}
    assert persist.dump(again)["records"]["policy_acks"] == acks
