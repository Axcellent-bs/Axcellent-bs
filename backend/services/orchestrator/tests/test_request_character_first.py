"""홍보 영상 의뢰는 회사 캐릭터 답을 먼저 받고 데이터분석을 시작한다 (11/24).

11/24 운영 테스트(wr_aa0669b6f7, PROP-23): 캐릭터 질문을 띄워 놓고 바로 데이터분석부터 진행해서,
사람이 캐릭터를 고르기 전에 기획서가 회사 기본 마스코트로 짜였다. 이제 promo_video는 맨 앞
사람 단계(character)가 캐릭터 답(POST /api/requests/{id}/brand-character)으로 닫혀야 다음이 돈다.

- 답(CR-… 또는 null=없어요) → 단계 완료 → 데이터분석 시작. 답이 없으면 기다린다
- 답 뒤에도 영상 준비 전까지 바꿀 수 있다. 기획이 시작된 뒤 바꾸면 plan_outdated
- 이 단계는 decide(승인·거절)로 닫히지 않는다. 멈추기·삭제는 기다리는 중에도 된다
- 다른 일·옛 기록(캐릭터 단계 없는 promo_video)은 지금 그대로
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentResult, RequestMeta, TaskStatus
from ax_orchestrator import request_tasks
from ax_orchestrator.main import app, state
from ax_orchestrator.registry import SkillEntry
from ax_orchestrator.request_classifier import Classification
from ax_orchestrator.request_intake import RequestIntake
from ax_orchestrator.request_runner import CHARACTER_WAIT, CHARACTER_WAIT_NOTE, RequestRunner
from ax_orchestrator.store import IncidentStore
from ax_orchestrator.work_requests import WorkRequests

BRAND = "CR-0123456789ab"
OTHER = "CR-aaaaaaaaaaaa"
THREAD = "orchchat-1"
HUMAN = {"Authorization": "Bearer human-test-token"}
INSIGHT = {
    "insight_id": "trend_report:rep_1",
    "title": "t",
    "summary": "s",
    "skill": "trend_report",
    "facts": [{"label": "검색량", "value": "1", "source": "trend"}],
}


def result(**output: Any) -> AgentResult:
    meta = RequestMeta(
        context_id="c", token="t", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    return AgentResult(meta=meta, status=TaskStatus.COMPLETED, output=output)


class Fake:
    def __init__(self) -> None:
        self.calls: list[str] = []
        grades = {
            "trend_report_create": ("data", "read"),
            "trend_report_insight": ("data", "read"),
            "receive_insight": ("planning", "A"),
            "prepare_video_draft_from_plan": ("marketing", "read"),
            "generate_media": ("marketing", "B"),
        }
        self.registry = SimpleNamespace(
            find=lambda s: (
                SkillEntry(grades[s][0], s, grades[s][1], None, "normal", True)
                if s in grades
                else None
            )
        )
        self.answers = {
            "trend_report_create": result(report_id="rep_1"),
            "trend_report_insight": result(report_id="rep_1", insight=INSIGHT),
            "receive_insight": result(proposal_id="PROP-1", video_plan={"concept": "c"}),
            "prepare_video_draft_from_plan": result(generate_media_input={"post_draft_id": "d"}),
            "generate_media": result(media_id="m"),
        }

    async def caller(self, dept: str, req: Any) -> AgentResult:
        self.calls.append(req.skill)
        return self.answers[req.skill]


def make(task: str = "promo_video", reqs: WorkRequests | None = None) -> tuple[WorkRequests, str]:
    reqs = reqs or WorkRequests()
    row = reqs.create("member", "신간 소개 영상 만들어 주세요")
    plan = request_tasks.plan_of(request_tasks.BY_NAME[task])
    reqs.set_plan(
        row["request_id"], task=task, confidence=0.9, reason="r", alternatives=[], plan=plan
    )
    reqs.link_thread(row["request_id"], THREAD)
    return reqs, row["request_id"]


async def waiting() -> tuple[WorkRequests, str, Fake, RequestRunner, list[str]]:
    """홍보 영상 의뢰를 받아 캐릭터 답을 기다리는 데까지 돌린다."""
    reqs, rid = make()
    fake = Fake()
    sent: list[str] = []
    r = RequestRunner(reqs, lambda: fake, retry_delay=0, notify=lambda _t, m: sent.append(m))
    await r.run(rid)
    return reqs, rid, fake, r, sent


# ---------------------------------------------------------------- 기다림


@pytest.mark.asyncio
async def test_promo_video_waits_for_the_character_and_does_not_call_data() -> None:
    reqs, rid, fake, r, sent = await waiting()
    row = reqs.get(rid)
    step = row["steps"]["character"]
    assert step["status"] == "waiting_human"
    assert step["reason_code"] == CHARACTER_WAIT and step["note"] == CHARACTER_WAIT_NOTE
    assert row["status"] == "waiting"  # 사람 확인 대기와 같은 값 (새 상태 값 없음)
    assert row["steps"]["trend"]["status"] == "pending"
    assert fake.calls == []  # 데이터분석을 부르지 않았다
    assert sent == []  # 접수 안내가 이미 물었다: "확인을 기다린다"를 따로 보내지 않는다
    # 다시 켜도 그대로 기다린다(자동으로 "캐릭터 없이" 가지 않는다)
    assert await r.resume_open() == 0
    await r.wait_idle()
    assert fake.calls == [] and reqs.get(rid)["steps"]["character"]["status"] == "waiting_human"


@pytest.mark.asyncio
async def test_a_record_answer_closes_the_step_and_starts_data() -> None:
    reqs, rid, fake, r, sent = await waiting()
    assert r.answer_brand_character(rid, BRAND, by="준하") == ("ok", False)
    await r.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["character"]["status"] == "completed"
    assert row["brand_character_record_id"] == BRAND
    assert row["brand_character_at"]  # 화면이 기획 시작 시각과 견준다
    assert fake.calls[:3] == ["trend_report_create", "trend_report_insight", "receive_insight"]
    assert row["steps"]["confirm"]["status"] == "waiting_human"  # 사람 확인은 지금 그대로
    assert sent[0] == f"[의뢰 {rid}] 캐릭터 {BRAND}로 할게요. 데이터분석에 시켰어요."


@pytest.mark.asyncio
async def test_a_no_character_answer_also_starts_data() -> None:
    reqs, rid, fake, r, sent = await waiting()
    assert r.answer_brand_character(rid, None) == ("ok", False)
    await r.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["character"]["status"] == "completed"
    assert row["brand_character_record_id"] is None
    assert fake.calls[0] == "trend_report_create"
    assert sent[0] == f"[의뢰 {rid}] 캐릭터 없이 할게요. 데이터분석에 시켰어요."
    assert rid.startswith("wr_")  # 화면은 wr_ 번호가 든 총괄 메시지에 카드를 붙인다


@pytest.mark.asyncio
async def test_an_answer_before_the_step_starts_still_closes_it() -> None:
    reqs, rid = make()
    fake = Fake()
    r = RequestRunner(reqs, lambda: fake, retry_delay=0)
    assert r.set_brand_character(rid, None) == "ok"  # 실행 전(pending)에 답이 왔다
    await r.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["character"]["status"] == "completed"
    assert fake.calls[0] == "trend_report_create"


# ---------------------------------------------------------------- 다시 바꾸기


@pytest.mark.asyncio
async def test_changing_the_answer_before_prepare_is_ok_and_after_plan_is_outdated() -> None:
    reqs, rid, fake, r, _ = await waiting()
    r.start = lambda *_a, **_k: None  # 첫 답 뒤 실행을 막고 기획 전 바꾸기를 본다
    assert r.answer_brand_character(rid, None) == ("ok", False)
    assert r.answer_brand_character(rid, BRAND) == ("ok", False)  # 기획 전: 그냥 바뀐다
    assert reqs.get(rid)["steps"]["character"]["status"] == "completed"  # 완료 그대로
    del r.start  # 원래 실행으로 되돌린다
    r.start(rid)
    await r.wait_idle()
    assert reqs.get(rid)["steps"]["plan"]["status"] == "completed"
    assert r.answer_brand_character(rid, OTHER) == ("ok", True)  # 기획서는 예전 캐릭터 기준
    assert reqs.get(rid)["brand_character_record_id"] == OTHER
    reqs.update_step(rid, "prepare", status="running")
    assert r.answer_brand_character(rid, None) == ("started", False)  # 지금 그대로
    assert reqs.get(rid)["brand_character_record_id"] == OTHER


# ---------------------------------------------------------------- 지금 그대로인 것


@pytest.mark.asyncio
async def test_other_tasks_start_right_away() -> None:
    reqs, rid = make("trend_report")
    fake = Fake()
    await RequestRunner(reqs, lambda: fake, retry_delay=0).run(rid)
    assert fake.calls == ["trend_report_create"]
    assert "character" not in reqs.get(rid)["steps"]


@pytest.mark.asyncio
async def test_an_old_promo_video_without_the_character_step_goes_on_as_before() -> None:
    reqs = WorkRequests()
    row = reqs.create("member", "홍보 영상")
    rid = row["request_id"]
    old = [
        p for p in request_tasks.plan_of(request_tasks.PROMO_VIDEO) if p["step_id"] != "character"
    ]
    old[0]["after"] = []  # 11/24 전 계획: 트렌드가 맨 앞
    reqs.set_plan(rid, task="promo_video", confidence=0.9, reason="r", alternatives=[], plan=old)
    reqs.update_step(rid, "trend", status="completed", result_ref="rep_1")  # 이미 진행 중이었다
    fake = Fake()
    r = RequestRunner(reqs, lambda: fake, retry_delay=0)
    await r.run(rid)
    row = reqs.get(rid)
    assert "character" not in row["steps"]  # 계획을 바꾸지 않는다
    assert fake.calls[:2] == ["trend_report_insight", "receive_insight"]
    assert row["steps"]["confirm"]["status"] == "waiting_human"
    assert r.answer_brand_character(rid, BRAND) == ("ok", True)  # 적기만 한다(기획 뒤라 안내)
    assert reqs.get(rid)["brand_character_record_id"] == BRAND


@pytest.mark.asyncio
async def test_the_character_step_is_not_closed_by_decide() -> None:
    reqs, rid, _, r, _ = await waiting()
    assert r.decide(rid, "character", True) == "not_waiting"
    assert r.decide(rid, "character", False) == "not_waiting"
    assert reqs.get(rid)["steps"]["character"]["status"] == "waiting_human"


@pytest.mark.asyncio
async def test_delete_works_while_waiting_for_the_character() -> None:
    reqs, rid, fake, r, _ = await waiting()
    assert r.hide(rid, by="준하") == "ok"
    row = reqs.get(rid)
    assert row["status"] == "rejected"
    assert row["steps"]["character"]["status"] == "skipped"
    assert r.answer_brand_character(rid, BRAND) == ("started", False)  # 멈춘 뒤에는 받지 않는다
    await r.wait_idle()
    assert fake.calls == []


@pytest.mark.asyncio
async def test_a_restart_keeps_the_character_wait(tmp_path: Any) -> None:
    path = str(tmp_path / "orc.db")
    reqs, rid = make(reqs=WorkRequests(IncidentStore(path)))
    fake = Fake()
    await RequestRunner(reqs, lambda: fake, retry_delay=0).run(rid)
    again = WorkRequests(IncidentStore(path))
    again.restore()
    step = again.get(rid)["steps"]["character"]
    assert step["status"] == "waiting_human" and step["reason_code"] == CHARACTER_WAIT
    r = RequestRunner(again, lambda: fake, retry_delay=0)
    assert await r.resume_open() == 0
    assert r.set_brand_character(rid, None) == "ok"
    await r.wait_idle()
    assert fake.calls[0] == "trend_report_create"


# ---------------------------------------------------------------- 접수 안내


class FakeLLM:
    def __init__(self, task: str) -> None:
        self.task = task

    async def structured(self, prompt: str, schema: Any, *, untrusted: Any = None) -> Any:
        return Classification(kind="request", task=self.task, confidence=0.9, reason="r")


@pytest.mark.asyncio
async def test_receipt_asks_for_the_character_first_and_other_tasks_keep_their_receipt() -> None:
    reqs = WorkRequests()
    intake = RequestIntake(reqs)
    intake.on_task = lambda _rid: None
    promo = await intake.receive("member", "홍보 영상 만들어 주세요", FakeLLM("promo_video"))
    rid = promo.request_id
    assert promo.reply.startswith(
        f"의뢰를 접수했어요 (번호 {rid}). 영상에 쓸 회사 캐릭터가 있는지 먼저 알려 주세요. "
        "답하면 데이터분석부터 시작해요."
    )
    trend = await intake.receive("member", "트렌드 분석해 줘", FakeLLM("trend_report"))
    assert "데이터분석에 시켰어요" in trend.reply and "캐릭터" not in trend.reply


# ---------------------------------------------------------------- API


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setattr(state, "mock", True)
    monkeypatch.setattr(state, "latency", 0.0)
    reqs = WorkRequests(IncidentStore(company_id="catbooks"))
    runner = RequestRunner(reqs, lambda: None, retry_delay=0)
    runner.start = lambda *_a, **_k: None  # 답·바꾸기 응답만 본다
    manager = SimpleNamespace(work_requests=reqs, request_runner=runner)
    monkeypatch.setattr(state, "manager", None)
    with TestClient(app) as c:
        monkeypatch.setattr(state, "manager", manager)
        c.reqs = reqs  # type: ignore[attr-defined]
        yield c


def test_api_says_plan_outdated_when_changed_after_the_plan(client: Any) -> None:
    _, rid = make(reqs=client.reqs)
    path = f"/api/requests/{rid}/brand-character"
    first = client.post(path, json={"record_id": None}, headers=HUMAN)
    assert first.status_code == 200 and first.json()["data"]["plan_outdated"] is False
    assert client.reqs.get(rid)["steps"]["character"]["status"] == "completed"
    client.reqs.update_step(rid, "plan", status="completed")
    late = client.post(path, json={"record_id": BRAND}, headers=HUMAN)
    assert late.status_code == 200 and late.json()["data"]["plan_outdated"] is True
