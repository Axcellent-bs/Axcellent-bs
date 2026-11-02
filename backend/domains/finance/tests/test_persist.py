"""재무 기록을 파일에 남기고 다시 켤 때 이어 받는다 (`FINANCE_STATE_FILE`)."""

from __future__ import annotations

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


def test_open_anomalies_survive_a_restart(tmp_path) -> None:
    """처리 전 의심 건은 다시 켜도 남는다. 예전 파일(목록 없음)도 읽는다."""
    from finance_agent import anomalies

    s = State(MockSources())
    anomalies.note_findings(s.records.anomalies, [{"code": "duplicate_coupon", "refs": ["ord_1"]}])
    path = tmp_path / "finance_state.json"
    persist.save(s, path)
    again = State(MockSources())
    assert persist.load(again, path)
    assert anomalies.listing(again.records.anomalies)["open_count"] == 1

    old = persist.dump(again)
    del old["records"]["anomalies"]
    third = State(MockSources())
    persist.restore(third, old)
    assert third.records.anomalies == {}


def test_restart_drops_open_refund_audit_keeps_the_rest(tmp_path) -> None:
    """예전 파일의 환불 의심 건은 처리 전이면 뺀다. 쿠폰 의심 건과 사람의 처리 기록은 남긴다."""
    from finance_agent import anomalies

    s = State(MockSources())
    anomalies.note_findings(
        s.records.anomalies,
        [
            {"code": "repeat_refund", "title": "같은 고객 환불 3번", "refs": ["rfd_1"]},
            {"code": "refund_rate_jump", "title": "환불률 급변", "refs": []},
            {"code": "split_under_limit", "title": "한도 바로 아래 환불 2번", "refs": ["rfd_2"]},
            {"code": "amount_recheck_failed", "detail": "환불 20,000원 넘음", "refs": ["exe_1"]},
            {"code": "split_under_limit", "title": "한도 바로 아래 쿠폰 3장", "refs": ["cpn_1"]},
            {"code": "amount_recheck_failed", "detail": "쿠폰 총액이 다릅니다", "refs": ["exe_2"]},
            {"code": "duplicate_coupon", "title": "같은 사건 보상 쿠폰 2번", "refs": ["cpn_2"]},
            {"code": "repeat_refund", "title": "처리한 예전 건", "refs": ["rfd_9"]},
        ],
    )
    done = anomalies.signature({"code": "repeat_refund", "refs": ["rfd_9"]})
    assert anomalies.resolve(s.records.anomalies, done, "태윤", "확인함")
    path = tmp_path / "finance_state.json"
    persist.save(s, path)
    again = State(MockSources())
    assert persist.load(again, path)
    listed = anomalies.listing(again.records.anomalies)
    assert {(e["code"], e["refs"][0]) for e in listed["open"]} == {
        ("split_under_limit", "cpn_1"),
        ("amount_recheck_failed", "exe_2"),
        ("duplicate_coupon", "cpn_2"),
    }
    assert [e["note"] for e in listed["resolved"]] == ["확인함"]
