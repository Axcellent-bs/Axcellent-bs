"""총괄 연결 3개: 홍보 영상 마지막 단계, SNS 글 초안 주제, 개발 진행 현황."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ax_contracts.schemas import AgentResult, RequestMeta, TaskStatus
from ax_orchestrator import request_inputs as ri
from ax_orchestrator import request_tasks
from ax_orchestrator.registry import SkillEntry
from ax_orchestrator.request_classifier import Classification
from ax_orchestrator.request_intake import RequestIntake
from ax_orchestrator.request_runner import RequestRunner
from ax_orchestrator.work_requests import WorkRequests

TASK = "tsk_1"
NOW = datetime(2026, 11, 20, 15, 30, tzinfo=UTC)
INSIGHT = {
    "insight_id": "trend_report:rep_1",
    "title": "봄 신간 트렌드",
    "summary": "요약",
    "skill": "trend_report",
    "facts": [{"label": "검색량", "value": "120", "source": "trend"}],
}
PLAN = {"concept": "신간 소개", "script": "첫 문장.", "duration_s": 30, "aspect": "9:16"}
DRAFT = {
    "post_draft_id": "draft_1",
    "media_kind": "video",
    "scene_template_id": "cat_bookstore_video",
    "fields": {"season": "autumn", "color_theme": "warm cream", "category": "그림책"},
    "resolution": "720p",
    "duration_s": 15,
}


def result(status: TaskStatus = TaskStatus.COMPLETED, **output) -> AgentResult:
    meta = RequestMeta(
        context_id="c", token="t", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    return AgentResult(meta=meta, status=status, outcome="ok", output=output)


class Fake:
    def __init__(self, entries: dict[str, SkillEntry], answers: dict[str, AgentResult]) -> None:
        self.answers = answers
        self.calls: list = []
        self.registry = SimpleNamespace(find=entries.get)

    async def caller(self, dept, req):
        self.calls.append(req)
        return self.answers[req.skill]


def entry(dept: str, skill: str, grade: str = "read") -> SkillEntry:
    return SkillEntry(dept, skill, grade, None, "normal", True)


def make(task: str, text: str):
    reqs = WorkRequests()
    row = reqs.create("member", text)
    plan = request_tasks.plan_of(request_tasks.BY_NAME[task])
    reqs.set_plan(
        row["request_id"], task=task, confidence=0.9, reason="r", alternatives=[], plan=plan
    )
    return reqs, row["request_id"]


# ---- 개발 진행 현황 ------------


@pytest.mark.asyncio
async def test_dev_status_asks_the_new_skill_that_needs_no_owner() -> None:
    reqs, rid = make("dev_status", "제가 요청했던 기능이 어디까지 개발됐나요")
    fake = Fake(
        {"list_dev_progress": entry("dev", "list_dev_progress")},
        {"list_dev_progress": result(items=[])},
    )
    await RequestRunner(reqs, lambda: fake).run(rid)
    assert [c.skill for c in fake.calls] == ["list_dev_progress"]
    assert fake.calls[0].input == {}  # 담당자 번호 없이 전체 진행 중 항목
    assert reqs.get(rid)["status"] == "done"


# ---- SNS 글 초안: 주제 구절만 ------------


@pytest.mark.parametrize(
    ("text", "topic"),
    [
        ("신간 입고 소식을 알리는 인스타 문구 초안 부탁해요", "신간 입고 소식"),
        ("인스타에 올릴 신간 그림책 소개 글 써 주세요", "신간 그림책 소개"),
        ("봄맞이 책 추천 SNS 문구 만들어 줘", "봄맞이 책 추천"),
    ],
)
def test_sns_topic_keeps_only_the_subject_phrase(text: str, topic: str) -> None:
    out = ri.fill("sns_topic", text, NOW)
    assert out.ask is None and out.payload == {"topic": topic}
    assert topic in (out.note or "")


@pytest.mark.parametrize(
    "text",
    [
        "인스타 글 초안 만들어 주세요",  # 주제가 없다
        "신간 3권 소개 인스타 문구 써 줘",  # 숫자는 부서가 거절한다
        "https://shop.example 홍보 인스타 글 써 줘",  # 링크
        "#캣북스 인스타 문구 써 주세요",  # 태그
        "그림책 " * 40 + "인스타 문구 써 줘",  # 너무 길다
    ],
)
def test_sns_topic_asks_instead_of_sending_what_the_department_would_refuse(text: str) -> None:
    out = ri.fill("sns_topic", text, NOW)
    assert out.ask == ri.TOPIC_ASK and out.payload == {}


@pytest.mark.asyncio
async def test_sns_draft_sends_only_the_topic_and_the_receipt_says_what_it_used() -> None:
    class LLM:
        async def structured(self, prompt, schema, *, untrusted=None):
            return Classification(
                kind="request", task="sns_draft", confidence=0.9, reason="r", alternatives=[]
            )

    reqs = WorkRequests()
    intake = RequestIntake(reqs, clock=lambda: NOW)
    out = await intake.receive("m", "신간 입고 소식을 알리는 인스타 문구 초안 부탁해요", LLM())
    assert "주제 '신간 입고 소식'로 작성" in out.reply
    fake = Fake(
        {"draft_sns_post": entry("marketing", "draft_sns_post")},
        {"draft_sns_post": result(post_draft_id="d1")},
    )
    await RequestRunner(reqs, lambda: fake, clock=lambda: NOW).run(out.request_id)
    assert fake.calls[0].input == {"topic": "신간 입고 소식"}  # 문장 전체가 아니다


# ---- 홍보 영상 마지막 단계 ------------


def promo_entries() -> dict[str, SkillEntry]:
    return {
        "trend_report_create": entry("data", "trend_report_create"),
        "trend_report_insight": entry("data", "trend_report_insight"),
        "receive_insight": entry("planning", "receive_insight", "A"),
        "prepare_video_draft_from_plan": entry("marketing", "prepare_video_draft_from_plan"),
        "generate_media": entry("marketing", "generate_media", "B"),
    }


async def promo_until_confirm(plan_output: dict, prepare_output: dict):
    reqs, rid = make("promo_video", "우리 서점 홍보 영상 하나 만들어 주세요")
    fake = Fake(
        promo_entries(),
        {
            "trend_report_create": result(TaskStatus.WORKING, task_id=TASK),
            "trend_report_insight": result(report_id="rep_1", insight=INSIGHT),
            "receive_insight": result(**plan_output),
            "prepare_video_draft_from_plan": result(**prepare_output),
            "generate_media": result(job="queued"),
        },
    )
    runner = RequestRunner(reqs, lambda: fake)
    await runner.run(rid)
    assert fake.calls == []  # 11/24: 회사 캐릭터 답 전에는 데이터분석을 부르지 않는다
    assert runner.set_brand_character(rid, None) == "ok"  # 없어요
    await runner.wait_idle()
    done = {"task_id": TASK, "skill": "trend_report_create", "status": "done"}
    done["output"] = {"report_id": "rep_1"}
    runner.receive_async("completed", "data", done, f"ctx_{rid}")
    await runner.wait_idle()
    return reqs, rid, fake, runner


@pytest.mark.asyncio
async def test_after_the_person_confirms_the_draft_is_prepared_then_the_video_is_made() -> None:
    plan_out = {"proposal_id": "PROP-3", "outcome": "created", "video_plan": PLAN}
    reqs, rid, fake, runner = await promo_until_confirm(
        plan_out, {"post_draft_id": "draft_1", "generate_media_input": DRAFT}
    )
    row = reqs.get(rid)
    assert row["steps"]["confirm"]["status"] == "waiting_human"
    assert [c.skill for c in fake.calls][-1] == "receive_insight"  # 사람 확인 전에는 여기까지

    assert runner.decide(rid, "confirm", True, "준하") == "ok"
    await runner.wait_idle()
    skills = [c.skill for c in fake.calls]
    assert skills[-2:] == ["prepare_video_draft_from_plan", "generate_media"]
    prepare = next(c for c in fake.calls if c.skill == "prepare_video_draft_from_plan")
    assert prepare.input == {"proposal_id": "PROP-3", "video_plan": PLAN}  # 기획 결과를 그대로
    make_call = fake.calls[-1]
    assert make_call.input == DRAFT  # 준비 결과를 그대로 영상 제작에
    assert make_call.meta.idempotency_key == f"{rid}:generate_media:make"
    row = reqs.get(rid)
    assert row["status"] == "done"
    assert reqs.get_carry(rid, "plan") is None and reqs.get_carry(rid, "prepare") is None


@pytest.mark.asyncio
async def test_without_a_video_plan_nothing_is_prepared_and_a_person_is_told() -> None:
    plan_out = {"proposal_id": "PROP-3", "outcome": "deleted_recently", "video_plan": None}
    reqs, rid, fake, runner = await promo_until_confirm(plan_out, {})
    assert runner.decide(rid, "confirm", True, "준하") == "ok"
    await runner.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["prepare"]["status"] == "failed"
    assert row["steps"]["prepare"]["reason_code"] == "no_carry"
    assert row["steps"]["make"]["status"] == "skipped"
    assert all(c.skill != "generate_media" for c in fake.calls)
    assert row["status"] == "needs_human"


@pytest.mark.asyncio
async def test_when_the_department_cannot_prepare_the_video_is_not_made() -> None:
    plan_out = {"proposal_id": "PROP-3", "outcome": "created", "video_plan": PLAN}
    reqs, rid, fake, runner = await promo_until_confirm(plan_out, {})
    fake.answers["prepare_video_draft_from_plan"] = AgentResult(
        meta=RequestMeta(
            context_id="c", token="t", deadline=datetime.now(UTC) + timedelta(seconds=30)
        ),
        status=TaskStatus.FAILED,
        outcome="template_fields_missing",
        output={"retryable": False, "missing_fields": ["category"]},
    )
    runner.decide(rid, "confirm", True, "준하")
    await runner.wait_idle()
    row = reqs.get(rid)
    assert row["steps"]["prepare"]["status"] == "failed"
    assert row["steps"]["make"]["status"] == "skipped"
    assert all(c.skill != "generate_media" for c in fake.calls)  # 영상은 만들지 않는다
    assert row["status"] == "needs_human"
