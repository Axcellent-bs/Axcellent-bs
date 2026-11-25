"""마케팅 제작안 확인 → 기획에 A2A 고치기 요청 (11/26 `plan_review`). 가짜 기획(A2A)만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from marketing_agent import handlers
from marketing_agent import plan_review as pr


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


Q = _load("request_creative_helpers_plan_review", "test_request_creative.py")
env = Q.env


def _scene(n: int, a: int, b: int, **kw: Any) -> dict[str, Any]:
    return {"n": n, "start_s": a, "end_s": b, "visual": f"장면 {n}", "caption": "",
            "prompt": f"scene {n} on a stage", "camera_en": "medium shot"} | kw  # fmt: skip


def _plan(*scenes: dict[str, Any], version: int = 1) -> dict[str, Any]:
    return {"concept": "무대 영상", "duration_s": scenes[-1]["end_s"], "script": "노래한다",
            "scenes": list(scenes), "version": version}  # fmt: skip


# 11/26 L 바뀐 기대값: 예전 V1은 장면 1(0~2초)이 짧은 것이었다. 이제 첫 2초 훅은 검사에서 빼므로
# 본론 장면 2(1초)가 짧은 제작안으로 본다
V1 = _plan(_scene(1, 0, 2), _scene(2, 2, 3), _scene(3, 3, 15))
STUCK = _plan(_scene(1, 0, 2), _scene(2, 2, 3), _scene(3, 3, 15), version=2)
V2 = _plan(_scene(1, 0, 5), _scene(2, 5, 10), _scene(3, 10, 15), version=2)


def _request(plan: dict[str, Any], character: str | None = None) -> AgentRequest:
    data: dict[str, Any] = {"proposal_id": "PROP-7", "video_plan": plan}
    if character:
        data["brand_character_record_id"] = character
    meta = RequestMeta(context_id="ctx_wr_rev1", token="t", requester="orchestrator",
                       idempotency_key="wr_rev1:prepare",
                       deadline=datetime.now(UTC) + timedelta(days=365))  # fmt: skip
    return AgentRequest(meta=meta, skill="prepare_video_draft_from_plan", input=data)


class Planning:
    """가짜 기획: revise_video_plan에 정해 둔 판을 돌려준다."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.sent: list[tuple[str, AgentRequest]] = []

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        self.sent.append((target, req))
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer == "slow":
            await asyncio.sleep(5)
        if isinstance(self.answer, str):
            return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome=self.answer)
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, outcome="revised",
                           output={"outcome": "revised", "video_plan": self.answer})  # fmt: skip


def _review(plan: dict[str, Any], planning: Planning, character: str | None = None) -> Any:
    return asyncio.run(pr.review(_request(plan, character), planning))


# ---------------------------------------------------------------- 확인 (코드)


def test_short_scene_is_a_duration_problem_but_end_card_is_not() -> None:
    got = pr.check(V1, has_character=False)
    assert got == [{"kind": "duration", "scene": 2,
                    "detail_ko": "장면 길이 1초 (영상 모델 최소 2초)"}]  # fmt: skip
    card = _plan(_scene(1, 0, 5), _scene(2, 5, 14), _scene(3, 14, 15, visual="채널 이름 엔드 카드"))
    assert pr.check(card, has_character=False) == []  # 엔드카드 1초는 그대로 뺀다


def test_min_scene_seconds_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pr.MIN_SCENE_ENV, "1")
    assert pr.check(V1, has_character=False) == []


# ---------------------------------------------------------------- L: 첫 훅은 짧은 것이 설계 (11/26)


def test_two_second_hook_with_four_scenes_is_not_a_problem_and_no_call() -> None:
    fun = _plan(_scene(1, 0, 2), _scene(2, 2, 7), _scene(3, 7, 12),
                _scene(4, 12, 15, visual="채널 이름 엔드 카드"))  # fmt: skip
    assert pr.MIN_SCENE_S == 2.0 and pr.check(fun, has_character=False) == []
    planning = Planning(V2)
    req, log = _review(fun, planning)
    assert planning.sent == [] and log is None  # 호출 0
    assert req.input["video_plan"] == fun  # 장면 시간 그대로 (훅 0~2초 유지)


def test_short_body_scene_is_still_a_problem_even_with_a_hook() -> None:
    body = _plan(_scene(1, 0, 2), _scene(2, 2, 3.5), _scene(3, 3.5, 15))
    assert [(x["kind"], x["scene"], x["detail_ko"]) for x in pr.check(body, False)] == [
        ("duration", 2, "장면 길이 1.5초 (영상 모델 최소 2초)")
    ]


def test_only_the_first_short_scene_from_zero_is_a_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pr.MIN_SCENE_ENV, "3")  # 기준을 올려도 첫 2초 훅은 뺀다
    hook = _plan(_scene(1, 0, 2), _scene(2, 2, 10), _scene(3, 10, 15))
    assert pr.check(hook, has_character=False) == []
    late = _plan(_scene(1, 0, 5), _scene(2, 5, 7), _scene(3, 7, 15))  # 두 번째 장면 2초는 훅 아님
    assert [x["scene"] for x in pr.check(late, has_character=False)] == [2]
    long_open = _plan(_scene(1, 0, 2.5), _scene(2, 2.5, 15))  # 2초보다 긴 첫 장면은 훅 아님
    assert [x["scene"] for x in pr.check(long_open, has_character=False)] == [1]


def test_another_person_close_to_the_camera_is_a_refs_problem() -> None:
    two = _scene(1, 0, 5, visual="회사 캐릭터와 친구가 마주 보고 웃는다")
    friend = _plan(two, _scene(2, 5, 10))
    assert [x["kind"] for x in pr.check(friend, has_character=True)] == ["refs"]
    assert pr.check(friend, has_character=False) == []  # 회사 캐릭터를 안 골랐으면 보지 않는다
    fans = _scene(1, 0, 5, visual="무대 앞 관객들 사이 친구가 손을 흔든다")
    crowd = _plan(fans, _scene(2, 5, 10))
    assert pr.check(crowd, has_character=True) == []  # 배경 군중은 괜찮다
    wide = _plan(_scene(1, 0, 5, visual="친구와 걷는다", camera_en="wide shot"), _scene(2, 5, 10))
    assert pr.check(wide, has_character=True) == []


def test_text_in_the_prompt_is_a_text_problem() -> None:
    sign = _plan(_scene(1, 0, 5, prompt="A neon sign with the shop name above the stage"),
                 _scene(2, 5, 10, prompt="no text, no logos, a singer on a stage"))  # fmt: skip
    assert [(x["kind"], x["scene"]) for x in pr.check(sign, has_character=False)] == [("text", 1)]


# ---------------------------------------------------------------- 고치기 요청 (A2A)


def test_short_scene_asks_planning_once_and_uses_version_two() -> None:
    planning = Planning(V2)
    req, log = _review(V1, planning)
    assert len(planning.sent) == 1
    target, sent = planning.sent[0]
    assert target == "planning" and sent.skill == "revise_video_plan"
    assert sent.input == {"proposal_id": "PROP-7", "from_version": 1, "requested_by": "marketing",
                          "problems": pr.check(V1, has_character=False)}  # fmt: skip
    assert sent.meta.idempotency_key == "ctx_wr_rev1:revise_video_plan:PROP-7:v1"
    assert req.input["video_plan"] == V2 and req.input["proposal_id"] == "PROP-7"
    assert [(v["version"], v["passed"]) for v in log["versions"]] == [(1, False), (2, True)]
    assert log["outcome"] == "revised" and log["needs_human"] is False
    assert log["summary_ko"] == "장면 2 장면 길이 1초 (영상 모델 최소 2초)"


def test_version_two_still_failing_is_not_asked_again_and_needs_a_person() -> None:
    planning = Planning(STUCK)
    req, log = _review(V1, planning)
    assert len(planning.sent) == 1  # 1회 상한
    assert req.input["video_plan"]["version"] == 2  # 2판으로 계속
    assert log["needs_human"] is True and log["versions"][-1]["passed"] is False


def test_planning_timeout_or_failure_keeps_version_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pr.TIMEOUT_ENV, "0.05")
    req, log = _review(V1, Planning("slow"))
    assert req.input["video_plan"] == V1 and log["outcome"] == "planning_timeout"
    assert log["needs_human"] is True
    req, log = _review(V1, Planning("revise_limit"))
    assert req.input["video_plan"] == V1 and log["outcome"] == "planning_revise_limit"
    _, log = _review(V1, Planning(ConnectionError()))
    assert log["outcome"] == "planning_error"


def test_already_revised_plan_or_clean_plan_makes_no_call() -> None:
    planning = Planning(V2)
    req, log = _review(STUCK, planning)
    assert planning.sent == [] and log and log["outcome"] == "revise_limit"  # 의뢰당 1회
    req, log = _review(V2, planning)
    assert planning.sent == [] and log is None and req.input["video_plan"] == V2  # 지금과 같음


# ---------------------------------------------------------------- 영상 초안 준비 (스킬)


def test_prepare_skill_prepares_with_version_two_and_keeps_the_log(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    planning = Planning(V2)
    monkeypatch.setattr(handlers, "PLAN_REVIEW_CALLER", planning)
    monkeypatch.setattr(handlers, "SCRIPT_LLM", lambda: None)
    brand = Q._brand(env)  # noqa: SLF001
    req = _request(V1, brand)
    out = asyncio.run(handlers.HANDLERS["prepare_video_draft_from_plan"](req))
    assert out.status == TaskStatus.COMPLETED and len(planning.sent) == 1
    rid = out.output["creative_record_id"]
    rec = env["store"].get(rid)
    assert [r["start_s"] for r in rec["storyboard"]["plan_scenes"]] == [0, 5, 10]  # 2판 장면
    review = rec["render"]["plan_review"]
    assert review["outcome"] == "revised" and review["needs_human"] is False


def test_prepare_skill_without_problems_does_not_call_planning(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    planning = Planning(V2)
    monkeypatch.setattr(handlers, "PLAN_REVIEW_CALLER", planning)
    monkeypatch.setattr(handlers, "SCRIPT_LLM", lambda: None)
    out = asyncio.run(handlers.HANDLERS["prepare_video_draft_from_plan"](
        _request(V2, Q._brand(env))  # noqa: SLF001
    ))  # fmt: skip
    assert out.status == TaskStatus.COMPLETED and planning.sent == []
    assert "plan_review" not in env["store"].get(out.output["creative_record_id"])["render"]
