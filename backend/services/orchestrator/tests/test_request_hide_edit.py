"""고객 의뢰 삭제(멈추고 숨김)·되돌리기·의뢰문 수정(고쳐서 다시 접수) (11/23).
실제 부서·LLM은 안 부른다.

- 삭제: 끝나지 않은 의뢰면 남은 단계를 skipped("사람이 의뢰를 삭제함")로 닫고 의뢰는 rejected.
  이미 맡긴 긴 작업의 결과가 와도 다음 단계로 넘기지 않는다. 끝난 의뢰는 숨기기만
- 목록: 기본은 숨긴 의뢰를 빼고, ?hidden=1이면 숨긴 것만
- 수정: 새 의뢰를 먼저(대화 접수와 같은 함수), 성공하면 옛 의뢰를 멈추고 숨김.
  실패하면 옛 의뢰 그대로
- 사람 토큰만 (없으면 401), 다른 회사 의뢰는 404
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import TaskStatus
from ax_orchestrator import main
from ax_orchestrator.main import app, state
from ax_orchestrator.request_classifier import Classification
from ax_orchestrator.request_intake import RequestIntake
from ax_orchestrator.request_runner import HIDE_NOTE, RequestRunner
from ax_orchestrator.store import IncidentStore
from ax_orchestrator.work_requests import WorkRequests


def _load(name: str, file: str) -> Any:
    """tests 폴더는 패키지가 아니라 파일로 읽는다 (의뢰 실행 시험의 가짜 부서를 같이 쓴다)."""
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


R = _load("request_runner_helpers_hide", "test_request_runner.py")
PLAN, HUMAN_FLOW, Fake, entry, make, result, runner = (R.PLAN, R.HUMAN_FLOW, R.Fake, R.entry,
                                                       R.make, R.result, R.runner)  # fmt: skip

HUMAN = {"Authorization": "Bearer human-test-token"}
AGENT = {"Authorization": "Bearer shop-test-token"}


def _waiting_on_human() -> tuple[WorkRequests, str, Fake, RequestRunner]:
    reqs, rid = make(plan=HUMAN_FLOW)
    fake = Fake({"s_a": entry("data", "s_a"), "s_d": entry("marketing", "s_d")},
                {"s_a": [result(TaskStatus.COMPLETED)], "s_d": [result(TaskStatus.COMPLETED)]})
    return reqs, rid, fake, runner(reqs, fake)


# ---------------------------------------------------------------- 삭제 = 멈추고 숨김


@pytest.mark.asyncio
async def test_hiding_an_open_request_stops_the_remaining_steps() -> None:
    reqs, rid, fake, r = _waiting_on_human()
    await r.run(rid)
    assert reqs.get(rid)["steps"]["c"]["status"] == "waiting_human"
    assert r.hide(rid, by="준하") == "ok"
    row = reqs.get(rid)
    assert row["status"] == "rejected" and row["hidden_by"] == "준하" and row["hidden_at"]
    assert row["steps"]["a"]["status"] == "completed"  # 끝난 단계는 그대로
    for sid in ("c", "d"):
        assert row["steps"][sid]["status"] == "skipped"
        assert row["steps"][sid]["note"] == f"{HIDE_NOTE} (준하)"
    assert r.decide(rid, "c", True) == "not_waiting"  # 멈춘 뒤에는 사람 확인도 이어지지 않는다
    await r.wait_idle()
    assert [c[0] for c in fake.calls] == ["data"] and reqs.get(rid)["status"] == "rejected"


@pytest.mark.asyncio
async def test_a_long_task_result_after_hiding_is_not_passed_on() -> None:
    plan = [PLAN[0], {**PLAN[1], "after": ["a"]}]
    reqs, rid = make(plan=plan)
    fake = Fake({"s_a": entry("data", "s_a"), "s_b": entry("planning", "s_b")},
                {"s_a": [result(TaskStatus.WORKING, output={"task_id": "t_9"})],
                 "s_b": [result(TaskStatus.COMPLETED)]})  # fmt: skip
    r = runner(reqs, fake)
    await r.run(rid)
    assert reqs.get(rid)["steps"]["a"]["status"] == "waiting_async"
    r.hide(rid, by="준하")
    row = reqs.get(rid)
    assert row["steps"]["a"]["status"] == "skipped" and row["steps"]["b"]["status"] == "skipped"
    got = r.receive_async("completed", "data", {"task_id": "t_9", "skill": "s_a",
                                                "status": "done", "output": {}},
                          f"ctx_{rid}")  # fmt: skip
    await r.wait_idle()
    assert got == "unmatched" and [c[0] for c in fake.calls] == ["data"]  # 기획은 안 부른다
    assert reqs.get(rid)["status"] == "rejected"


@pytest.mark.asyncio
async def test_a_step_in_flight_does_not_reopen_the_request() -> None:
    """부서에 보내던 중 삭제: 돌아온 결과는 그 단계에만 남고 다음 단계·의뢰 상태는 그대로."""
    reqs, rid = make()
    r: RequestRunner | None = None

    class Slow(Fake):
        async def caller(self, dept, req):  # noqa: ANN001, ANN201
            if req.skill == "s_a" and r is not None:
                r.hide(rid, by="준하")  # 보내는 사이에 사람이 삭제
            return await super().caller(dept, req)

    fake = Slow({"s_a": entry("data", "s_a"), "s_b": entry("planning", "s_b")},
                {"s_a": [result(TaskStatus.COMPLETED)], "s_b": [result(TaskStatus.COMPLETED)]})
    r = runner(reqs, fake)
    await r.run(rid)
    row = reqs.get(rid)
    assert [c[0] for c in fake.calls] == ["data"]
    assert row["steps"]["b"]["status"] == "skipped" and row["status"] == "rejected"


@pytest.mark.asyncio
async def test_hiding_a_finished_request_only_hides_and_unhide_brings_it_back() -> None:
    reqs, rid = make()
    fake = Fake({"s_a": entry("data", "s_a"), "s_b": entry("planning", "s_b")},
                {"s_a": [result(TaskStatus.COMPLETED)], "s_b": [result(TaskStatus.COMPLETED)]})
    r = runner(reqs, fake)
    await r.run(rid)
    assert reqs.get(rid)["status"] == "done"
    r.hide(rid, by="준하")
    row = reqs.get(rid)
    assert row["status"] == "done" and row["hidden_at"] and not row.get("stopped_at")
    assert all(s["status"] == "completed" for s in row["steps"].values())
    assert reqs.list(hidden=False) == []
    assert [x["request_id"] for x in reqs.list(hidden=True)] == [rid]
    assert r.unhide(rid, by="준하") == "ok"
    assert reqs.get(rid)["hidden_at"] is None and reqs.list(hidden=True) == []
    assert r.hide("wr_none") == "missing" and r.unhide("wr_none") == "missing"


@pytest.mark.asyncio
async def test_unhide_keeps_stopped_steps() -> None:
    reqs, rid, _fake, r = _waiting_on_human()
    await r.run(rid)
    r.hide(rid)
    r.unhide(rid)
    row = reqs.get(rid)
    assert row["hidden_at"] is None and row["status"] == "rejected"
    assert row["steps"]["c"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_a_deleted_request_cannot_be_rerun_even_after_unhide() -> None:
    """삭제 → 되돌리기 → 일 다시 고르기: stopped로 막고 상태·단계·계획은 그대로."""
    reqs, rid, fake, r = _waiting_on_human()
    await r.run(rid)
    r.hide(rid, by="준하")
    r.unhide(rid, by="준하")
    before = reqs.get(rid)
    assert r.rerun(rid, "sales_analysis", by="준하") == "stopped"
    await r.wait_idle()
    row = reqs.get(rid)
    assert row["status"] == "rejected" and row["task"] == before["task"]
    assert row["plan"] == before["plan"] and row["steps"] == before["steps"]
    assert {row["steps"][s]["status"] for s in ("c", "d")} == {"skipped"}
    assert not row.get("corrections") and [c[0] for c in fake.calls] == ["data"]


# ---------------------------------------------------------------- 의뢰문 수정


class FakeLLM:
    def __init__(self, classification: Classification | None) -> None:
        self.classification = classification

    async def structured(self, prompt, schema, *, untrusted=None):  # noqa: ANN001, ANN201
        if self.classification is None:
            raise RuntimeError("분류 실패")
        return self.classification


def _ask(task: str = "sales_analysis") -> Classification:
    return Classification(kind="request", task=task, confidence=0.9, reason="의뢰")


def _intake_setup() -> tuple[WorkRequests, RequestIntake, RequestRunner, str]:
    reqs = WorkRequests()
    intake = RequestIntake(reqs)
    r = RequestRunner(reqs, lambda: None, retry_delay=0)
    old = reqs.create("member", "홍보 영상 만들어 주세요")
    reqs.link_thread(old["request_id"], "orchchat-1")
    reqs.set_buyer(old["request_id"], "b_1")
    return reqs, intake, r, old["request_id"]


@pytest.mark.asyncio
async def test_edit_makes_the_new_request_first_then_hides_the_old_one() -> None:
    reqs, intake, r, old = _intake_setup()
    outcome, new = await r.edit(old, "지난주 매출 알려 주세요", intake, FakeLLM(_ask()), by="준하")
    assert outcome == "ok" and new and new != old
    o, n = reqs.get(old), reqs.get(new)
    assert o["hidden_at"] and o["status"] == "rejected" and o["replaced_by"] == new
    assert n["replaces"] == old and n["text"] == "지난주 매출 알려 주세요"
    assert n["account"] == "member" and n["thread_id"] == "orchchat-1"  # 같은 의뢰자·대화
    assert n["buyer_id"] == "b_1" and not n.get("hidden_at")


@pytest.mark.asyncio
async def test_edit_failure_leaves_the_old_request_alone() -> None:
    reqs, intake, r, old = _intake_setup()
    before = reqs.get(old)
    # 분류 LLM 실패: 분류기가 "분류 못 함"으로 돌려줘 새 의뢰를 만들지 않는다
    assert await r.edit(old, "다른 글", intake, FakeLLM(None)) == ("not_request", None)

    class Broken:
        async def receive(self, *_a: Any, **_k: Any) -> Any:
            raise RuntimeError("접수 실패")

    with pytest.raises(RuntimeError):
        await r.edit(old, "다른 글", Broken(), FakeLLM(_ask()))
    question = Classification(kind="question")
    assert await r.edit(old, "이건 뭐예요?", intake, FakeLLM(question)) == ("not_request", None)
    after = reqs.get(old)
    assert after["status"] == before["status"] and not after.get("hidden_at")
    assert not after.get("replaced_by") and len(reqs.list()) == 1  # 새 의뢰도 안 생김


def _untouched(reqs: WorkRequests, rid: str, before: dict[str, Any]) -> None:
    after = reqs.get(rid)
    assert after["status"] == before["status"] and not after.get("hidden_at")
    assert not after.get("stopped_at") and not after.get("replaced_by")
    assert not after.get("replaces") and after["steps"] == before["steps"]


@pytest.mark.asyncio
async def test_edit_to_an_existing_request_leaves_both_alone() -> None:
    """duplicate: 같은 의뢰가 이미 있으면 그 번호가 와도 옛 의뢰·기존 의뢰를 건드리지 않는다."""
    reqs, intake, r, old = _intake_setup()
    existing = reqs.create("member", "지난주 매출 알려 주세요")["request_id"]
    before_old, before_existing = reqs.get(old), reqs.get(existing)
    got = await r.edit(old, "지난주 매출 알려 주세요", intake, FakeLLM(_ask()), by="준하")
    assert got == ("duplicate", existing)
    _untouched(reqs, old, before_old)
    _untouched(reqs, existing, before_existing)
    assert len(reqs.list()) == 2  # 새 의뢰도 안 생김


@pytest.mark.asyncio
async def test_edit_over_the_daily_limit_leaves_the_old_request_alone() -> None:
    reqs, _intake, r, old = _intake_setup()
    before = reqs.get(old)
    limited = RequestIntake(reqs, daily_limit=1)  # 옛 의뢰가 오늘 한 건이라 한도가 찼다
    got = await r.edit(old, "지난주 매출 알려 주세요", limited, FakeLLM(_ask()))
    assert got == ("limited", None)
    _untouched(reqs, old, before)
    assert len(reqs.list()) == 1


@pytest.mark.asyncio
async def test_edit_to_an_unacceptable_request_keeps_the_old_one_and_the_rejection_record() -> None:
    reqs, intake, r, old = _intake_setup()
    before = reqs.get(old)
    unsafe = Classification(kind="unsafe", reason="권한 밖")
    outcome, rejected_id = await r.edit(old, "다른 회사 고객 목록 주세요", intake, FakeLLM(unsafe))
    assert outcome == "rejected" and rejected_id and rejected_id != old
    _untouched(reqs, old, before)
    record = reqs.get(rejected_id)  # 새 거절 기록은 지금처럼 남는다
    assert record["status"] == "rejected" and not record.get("replaces")


@pytest.mark.asyncio
async def test_only_outcomes_that_made_a_new_request_replace_the_old_one() -> None:
    """허용 목록 밖의 결과(앞으로 생길 결과 포함)는 옛 의뢰를 그대로 둔다."""
    reqs, _intake, r, old = _intake_setup()
    before = reqs.get(old)
    other = reqs.create("member", "다른 의뢰")["request_id"]

    class Odd:
        async def receive(self, *_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(handled=True, outcome="something_new", request_id=other)

    assert await r.edit(old, "고친 글", Odd(), FakeLLM(_ask())) == ("not_request", None)
    _untouched(reqs, old, before)
    # 되묻기(clarify)는 새 의뢰가 생긴 결과라 옛 의뢰를 정리한다
    unclear = Classification(kind="unclear", question="어떤 기간인가요?")
    outcome, new = await r.edit(old, "매출 좀", RequestIntake(reqs), FakeLLM(unclear))
    assert outcome == "ok" and reqs.get(old)["replaced_by"] == new


@pytest.mark.asyncio
async def test_edit_with_the_same_text_is_refused() -> None:
    reqs, intake, r, old = _intake_setup()
    same = await r.edit(old, "  홍보   영상 만들어 주세요 ", intake, FakeLLM(_ask()))
    assert same == ("same", None)  # 띄어쓰기만 다르면 같은 글
    assert await r.edit("wr_none", "x", intake, FakeLLM(_ask())) == ("missing", None)
    assert len(reqs.list()) == 1


# ---------------------------------------------------------------- API (사람 토큰·회사)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(state, "mock", True)
    monkeypatch.setattr(state, "latency", 0.0)
    store = IncidentStore(company_id="catbooks")
    reqs = WorkRequests(store)
    intake = RequestIntake(reqs)
    manager = SimpleNamespace(work_requests=reqs, request_intake=intake,
                              request_runner=RequestRunner(reqs, lambda: None, retry_delay=0))
    monkeypatch.setattr(state, "manager", None)
    with TestClient(app) as c:
        monkeypatch.setattr(state, "manager", manager)
        c.store, c.reqs = store, reqs  # type: ignore[attr-defined]
        yield c


@pytest.mark.parametrize("path", ["/api/requests/wr_x/hide", "/api/requests/wr_x/unhide",
                                  "/api/requests/wr_x/edit"])  # fmt: skip
def test_hide_unhide_edit_need_a_human_token(client: Any, path: str) -> None:
    kw = {"json": {"text": "새 글"}}
    assert client.post(path, **kw).status_code == 401
    assert client.post(path, headers=AGENT, **kw).status_code == 403
    assert client.post(path, headers=HUMAN, **kw).status_code == 404  # 없는 의뢰


def test_list_hides_deleted_requests_by_default(client: Any) -> None:
    keep = client.reqs.create("member", "매출 알려 주세요")["request_id"]
    gone = client.reqs.create("member", "홍보 영상 만들어 주세요")["request_id"]
    assert client.post(f"/api/requests/{gone}/hide", headers=HUMAN).status_code == 200
    shown = [r["request_id"] for r in client.get("/api/requests", headers=HUMAN).json()["data"]]
    hidden = client.get("/api/requests?hidden=1", headers=HUMAN).json()["data"]
    assert shown == [keep] and [r["request_id"] for r in hidden] == [gone]
    assert hidden[0]["hidden_by"] and hidden[0]["status"] == "rejected"
    assert client.post(f"/api/requests/{gone}/unhide", headers=HUMAN).status_code == 200
    assert len(client.get("/api/requests", headers=HUMAN).json()["data"]) == 2


def test_rerun_api_refuses_a_deleted_request_with_409(client: Any) -> None:
    rid = client.reqs.create("member", "홍보 영상 만들어 주세요")["request_id"]
    assert client.post(f"/api/requests/{rid}/hide", headers=HUMAN).status_code == 200
    assert client.post(f"/api/requests/{rid}/unhide", headers=HUMAN).status_code == 200
    before = client.reqs.get(rid)
    res = client.post(f"/api/requests/{rid}/rerun", json={"task": "sales_analysis"},
                      headers=HUMAN)  # fmt: skip
    assert res.status_code == 409
    assert res.json()["detail"] == (
        "삭제한 의뢰는 다시 돌릴 수 없어요. 의뢰문 수정으로 새로 접수해 주세요"
    )
    row = client.reqs.get(rid)
    assert row["status"] == "rejected" and row["stopped_at"]
    assert (row["task"], row["plan"], row["steps"]) == (
        before["task"], before["plan"], before["steps"],
    )  # fmt: skip


def test_another_companys_request_is_404(client: Any) -> None:
    other = IncidentStore(company_id="nubi")
    other._db = client.store._db  # noqa: SLF001 - 같은 파일(메모리 DB)을 다른 회사로 쓴다
    nubi = WorkRequests(other)
    rid = nubi.create("member", "누비 의뢰")["request_id"]
    assert client.reqs.get(rid) is None and WorkRequests(client.store).restore()["restored"] == 0
    for path in (f"/api/requests/{rid}/hide", f"/api/requests/{rid}/unhide"):
        assert client.post(path, headers=HUMAN).status_code == 404
    assert client.post(f"/api/requests/{rid}/edit", json={"text": "x"},
                       headers=HUMAN).status_code == 404  # fmt: skip
    assert client.get(f"/api/requests/{rid}", headers=HUMAN).status_code == 404


def test_edit_api(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    old = client.reqs.create("member", "홍보 영상 만들어 주세요")["request_id"]
    monkeypatch.setattr(main, "edit_llm", lambda: FakeLLM(_ask()))
    same = client.post(f"/api/requests/{old}/edit", json={"text": "홍보 영상 만들어 주세요"},
                       headers=HUMAN)  # fmt: skip
    assert same.status_code == 422 and same.json()["detail"] == "바뀐 내용이 없어요"
    assert client.post(f"/api/requests/{old}/edit", json={"text": ""},
                       headers=HUMAN).status_code == 422  # fmt: skip
    assert client.post(f"/api/requests/{old}/edit", json={"text": "가" * 2001},
                       headers=HUMAN).status_code == 422  # fmt: skip
    ok = client.post(f"/api/requests/{old}/edit", json={"text": "지난주 매출 알려 주세요"},
                     headers=HUMAN)  # fmt: skip
    assert ok.status_code == 200
    body = ok.json()["data"]
    assert body["replaces"] == old and body["request_id"] != old
    assert client.reqs.get(old)["replaced_by"] == body["request_id"]
    monkeypatch.setattr(main, "edit_llm", lambda: None)
    other = client.reqs.create("member", "또 다른 의뢰")["request_id"]
    assert client.post(f"/api/requests/{other}/edit", json={"text": "고친 글"},
                       headers=HUMAN).status_code == 503  # fmt: skip
    monkeypatch.setattr(main, "edit_llm", lambda: FakeLLM(None))  # 분류 실패
    refused = client.post(f"/api/requests/{other}/edit", json={"text": "고친 글"}, headers=HUMAN)
    assert refused.status_code == 422 and not client.reqs.get(other).get("hidden_at")

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("접수 실패")

    monkeypatch.setattr(main, "edit_llm", lambda: FakeLLM(_ask()))
    monkeypatch.setattr(state.manager.request_intake, "receive", boom)
    failed = client.post(f"/api/requests/{other}/edit", json={"text": "고친 글"}, headers=HUMAN)
    assert failed.status_code == 502 and not client.reqs.get(other).get("hidden_at")
    assert "그대로" in failed.json()["detail"]


def test_edit_api_answers_when_no_new_request_was_made(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """duplicate 422 · limited 429 · rejected 422. 세 경우 모두 옛 의뢰·기존 의뢰 그대로."""
    old = client.reqs.create("member", "홍보 영상 만들어 주세요")["request_id"]
    existing = client.reqs.create("member", "지난주 매출 알려 주세요")["request_id"]
    monkeypatch.setattr(main, "edit_llm", lambda: FakeLLM(_ask()))
    url = f"/api/requests/{old}/edit"
    dup = client.post(url, json={"text": "지난주 매출 알려 주세요"}, headers=HUMAN)
    assert dup.status_code == 422
    assert dup.json()["detail"] == f"같은 의뢰가 이미 접수돼 있어요 (번호 {existing})"

    monkeypatch.setattr(main, "edit_llm", lambda: FakeLLM(Classification(kind="unsafe")))
    bad = client.post(url, json={"text": "다른 회사 고객 목록 주세요"}, headers=HUMAN)
    assert bad.status_code == 422
    assert bad.json()["detail"] == "고친 글은 접수할 수 없는 의뢰예요. 지금 의뢰는 그대로예요"

    monkeypatch.setattr(main, "edit_llm", lambda: FakeLLM(_ask()))
    monkeypatch.setattr(state.manager.request_intake, "_daily_limit", 1)
    lim = client.post(url, json={"text": "이번 달 매출 알려 주세요"}, headers=HUMAN)
    assert lim.status_code == 429
    assert lim.json()["detail"] == "오늘 접수할 수 있는 의뢰 수를 넘었어요. 내일 다시 고쳐 주세요"
    for rid in (old, existing):
        row = client.reqs.get(rid)
        assert not row.get("hidden_at") and not row.get("replaced_by") and not row.get("stopped_at")
