"""고객 의뢰 기록: 저장하고 재시작해도 이어 갈 수 있게 정리한다 (work_requests.py)."""

import pytest

from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.mock.harness import Harness
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.store import IncidentStore
from ax_orchestrator.work_requests import (
    MAX_TEXT,
    RESTART_UNCONFIRMED,
    WorkRequests,
    ready_steps,
    settle_request,
)

PLAN = [
    {
        "step_id": "trend",
        "dept": "data",
        "skill": "trend_report_create",
        "label": "트렌드 보고서",
        "grade": "read",
    },
    {
        "step_id": "plan",
        "dept": "planning",
        "skill": "receive_insight",
        "label": "기획안",
        "after": ["trend"],
        "grade": "A",
    },
    {
        "step_id": "make",
        "dept": "marketing",
        "skill": "create_video",
        "label": "영상 제작",
        "after": ["plan"],
        "grade": "B",
    },
]


def planned(reqs: WorkRequests, account: str = "member") -> str:
    row = reqs.create(account, "홍보 영상 만들어 주세요")
    reqs.set_plan(
        row["request_id"],
        task="promo_video",
        confidence=0.9,
        reason="영상 제작 의뢰",
        alternatives=["trend_analysis"],
        plan=PLAN,
    )
    return row["request_id"]


def test_create_keeps_original_text_and_owner() -> None:
    reqs = WorkRequests()
    row = reqs.create("member", "영상 만들어 주세요")
    assert row["status"] == "received"
    assert row["account"] == "member"
    assert row["text"] == "영상 만들어 주세요"
    assert reqs.get(row["request_id"])["text"] == "영상 만들어 주세요"


def test_long_text_is_cut_not_rejected() -> None:
    reqs = WorkRequests()
    row = reqs.create("member", "가" * (MAX_TEXT + 500))
    assert len(row["text"]) == MAX_TEXT


def test_member_sees_only_own_requests() -> None:
    reqs = WorkRequests()
    reqs.create("member", "내 의뢰")
    reqs.create("other", "남의 의뢰")
    assert [r["text"] for r in reqs.list("member")] == ["내 의뢰"]
    assert len(reqs.list()) == 2


def test_plan_makes_pending_steps_and_ready_follows_after() -> None:
    reqs = WorkRequests()
    rid = planned(reqs)
    row = reqs.get(rid)
    assert row["status"] == "classified"
    assert {s["status"] for s in row["steps"].values()} == {"pending"}
    # 첫 단계부터: 선행 단계가 없는 것만 시킬 수 있다
    assert ready_steps(row) == ["trend"]
    reqs.update_step(rid, "trend", status="completed", result_ref="rep_1")
    assert ready_steps(reqs.get(rid)) == ["plan"]


def test_unknown_step_or_field_is_rejected() -> None:
    reqs = WorkRequests()
    rid = planned(reqs)
    with pytest.raises(KeyError):
        reqs.update_step(rid, "nope", status="completed")
    with pytest.raises(KeyError):
        reqs.update_step(rid, "trend", free_text="x")
    with pytest.raises(ValueError):
        reqs.set_status(rid, "weird")


def test_restart_requeues_read_step_but_does_not_resend_changing_step() -> None:
    reqs = WorkRequests()
    rid = planned(reqs)
    reqs.update_step(rid, "trend", status="running")
    row = reqs.get(rid)
    changed = settle_request(row)
    assert changed == {"requeued": ["trend"], "unconfirmed": []}
    assert row["steps"]["trend"]["status"] == "pending"

    reqs2 = WorkRequests()
    rid2 = planned(reqs2)
    reqs2.update_step(rid2, "trend", status="completed", result_ref="rep_1")
    reqs2.update_step(rid2, "plan", status="running")  # 상태를 바꾸는 일: 보냈는지 모른다
    row2 = reqs2.get(rid2)
    changed2 = settle_request(row2)
    assert changed2 == {"requeued": [], "unconfirmed": ["plan"]}
    assert row2["steps"]["plan"]["status"] == "waiting_human"
    assert row2["steps"]["plan"]["reason_code"] == RESTART_UNCONFIRMED
    assert row2["status"] == "waiting"


def test_unknown_grade_is_not_resent() -> None:
    reqs = WorkRequests()
    row = reqs.create("member", "x")
    reqs.set_plan(
        row["request_id"],
        task="t",
        confidence=None,
        reason=None,
        alternatives=None,
        plan=[{"step_id": "s", "dept": "data", "skill": "k", "label": "l"}],
    )
    reqs.update_step(row["request_id"], "s", status="running")
    changed = settle_request(reqs.get(row["request_id"]))
    assert changed["unconfirmed"] == ["s"]  # 등급을 모르면 다시 보내지 않는다


def test_waiting_steps_and_finished_requests_are_left_alone() -> None:
    reqs = WorkRequests()
    rid = planned(reqs)
    reqs.update_step(rid, "trend", status="completed", result_ref="rep_1")
    reqs.update_step(rid, "plan", status="held", review_id="rev_7")
    row = reqs.get(rid)
    assert settle_request(row) == {"requeued": [], "unconfirmed": []}
    assert row["steps"]["plan"]["status"] == "held"
    assert row["steps"]["plan"]["review_id"] == "rev_7"  # 번호가 남아 계속 기다린다

    reqs.set_status(rid, "done")
    done = reqs.get(rid)
    done["steps"]["make"]["status"] = "running"
    assert settle_request(done) == {"requeued": [], "unconfirmed": []}  # 닫힌 의뢰는 안 건드린다
    assert done["steps"]["make"]["status"] == "running"


def test_saved_requests_come_back_and_resume_after_restart(tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    first = WorkRequests(IncidentStore(path))
    rid = planned(first)
    first.update_step(rid, "trend", status="completed", result_ref="rep_1")
    first.update_step(rid, "plan", status="running")
    done = first.create("member", "끝난 의뢰")
    first.set_status(done["request_id"], "done")

    second = WorkRequests(IncidentStore(path))  # 다시 켠 총괄
    counts = second.restore()
    assert counts == {"restored": 2, "open": 1, "requeued": 0, "unconfirmed": 1}
    row = second.get(rid)
    assert row["text"] == "홍보 영상 만들어 주세요"
    assert row["steps"]["trend"]["result_ref"] == "rep_1"  # 결과 번호는 그대로
    assert row["steps"]["plan"]["status"] == "waiting_human"  # 보냈는지 몰라 다시 보내지 않음
    assert second.get(done["request_id"])["status"] == "done"
    # 정리한 결과가 저장돼 한 번 더 켜도 같은 상태다
    third = WorkRequests(IncidentStore(path))
    assert third.restore()["unconfirmed"] == 0
    assert third.get(rid)["steps"]["plan"]["status"] == "waiting_human"


def test_old_closed_requests_are_trimmed_but_open_are_kept(monkeypatch) -> None:
    from ax_orchestrator import work_requests

    monkeypatch.setattr(work_requests, "MAX_REQUESTS", 3)
    reqs = WorkRequests()
    open_one = reqs.create("member", "열린 의뢰")["request_id"]
    for i in range(4):
        rid = reqs.create("member", f"끝난 {i}")["request_id"]
        reqs.set_status(rid, "done")
    texts = [r["text"] for r in reqs.list()]
    assert "열린 의뢰" in texts
    assert open_one in {r["request_id"] for r in reqs.list()}
    assert len(reqs.list()) <= 4  # 열린 것은 지우지 않아 한도보다 하나 많을 수 있다


@pytest.mark.asyncio
async def test_manager_restore_keeps_requests_and_still_closes_incidents(tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    harness = Harness()
    store = IncidentStore(path)
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a, store=store)
    rid = planned(manager.work_requests)
    manager.work_requests.update_step(rid, "trend", status="running")

    harness2 = Harness()
    store2 = IncidentStore(path)
    engine2 = await harness2.start()
    manager2 = IncidentManager(engine2, scenario_a, store=store2)
    manager2.restore()
    assert manager2.resumed_requests["open"] == 1
    assert manager2.resumed_requests["requeued"] == 1
    assert manager2.work_requests.get(rid)["steps"]["trend"]["status"] == "pending"
