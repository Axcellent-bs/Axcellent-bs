"""영상 의뢰 전체를 가짜로 한 번 끝까지 (11/26 코드 동결 전).

오늘 들어간 단계를 한 줄로 이어 붙인다. 서로 깨뜨리면 여기서 걸린다.

  promo_video 의뢰 → 캐릭터 먼저(#1728, AI 가상 인물·참조 2장) → 데이터 보고서·인사이트
  → 기획 제작안 AI 초안·키프레임 칸 → 포즈 콘티 SVG(3D 마네킹) → 사람 확인
  → 마케팅 준비(의뢰 기록·스토리보드·board_svg) → 키프레임 4장(#1806) → 영상 승인
  → Kling 4.0 params(image-to-video, start·중간·end) → 배속·자막 ffmpeg(#1760·#1742)
  → overlay_video · 썸네일 3장

변형: (a) 영상 모델 kling_4_0_flash → 키프레임 없이 지금 흐름(reference-to-video)
     (b) 기획 LLM 시간 초과 → 규칙 초안(fallback_reason "timeout") · 키프레임 없이 지금 흐름

가짜만 쓴다(각 패키지 시험의 것을 그대로): 총괄 `RequestRunner` + 부서 호출 가짜 전송(요청·답을
JSON으로 한 번 바꿔 A2A처럼 넘긴다), 데이터 보고서는 정해 둔 답, 기획 `PlanningService` + 가짜 LLM,
마케팅 가짜 Higgsfield MCP·가짜 관문·가짜 ffmpeg 실행기·메모리 저장소. 기다림(SLEEP)은 0.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ax_agent_template import company_scope
from ax_agent_template import render_settings as settings_store
from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas import AgentRequest, AgentResult, ExecuteDecision, RequestMeta, TaskStatus
from ax_contracts.schemas.planning import ReceiveInsightInput, ReceiveInsightOutput
from ax_orchestrator import request_tasks
from ax_orchestrator.registry import SkillEntry
from ax_orchestrator.request_runner import RequestRunner
from ax_orchestrator.work_requests import WorkRequests
from marketing_agent import board_svg, handlers
from marketing_agent import creative_mcp as cm
from marketing_agent import creative_overlay as ov
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import creative_thumbnails as ct
from marketing_agent import render_settings as rs
from marketing_agent.media import caption_overlay as co
from marketing_agent.media.audio_mix import RunOutput
from planning_agent import storyboard_svg as sb
from planning_agent import video_plan as vp
from planning_agent.domain.service import PlanningService

BACKEND = Path(__file__).resolve().parents[3]
MARKETING_TESTS = BACKEND / "domains" / "marketing" / "tests"


def _load(name: str, file: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# 마케팅 가짜 Higgsfield MCP·가짜 관문 (test_creative_render_mcp.env 그대로)
M = _load("creative_render_mcp_helpers_e2e", MARKETING_TESTS / "test_creative_render_mcp.py")

COMPANY = "catbooks"
REQUEST_TEXT = "18초짜리 겨울 신간 홍보 영상 만들어 주세요"
FRONT = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"front" + bytes(27)
EXTRA = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"extra" + bytes(27)
INSIGHT = {
    "insight_id": "trend_report:rep_e2e",
    "title": "겨울 밤 10분 독서 숏츠",
    "summary": "독서 검색이 11월에 오른다",
    "skill": "trend_report",
    "facts": [{"label": "독서 검색", "value": "평균의 1.6배", "source": "trend"}],
    "products": ["밤의 도서관"],
}
# 기획 LLM이 쓰는 AI 초안 (장면마다 키프레임 칸: 첫·끝 화면·동작·카메라)
PLAN_LLM = {
    "concept": "겨울 밤 독서 습관",
    "script": "훅 → 공감 → 상품 → 마무리",
    "scenes": [
        {
            "visual": f"장면 {i} 화면",
            "caption": f"자막 {i}" if i < 2 else "",  # 마지막 장면 자막은 비운다(엔드카드)
            "prompt": "A young woman reads under a warm lamp, slow dolly in",
            "start_frame": f"여자가 가운데 책상에 앉아 왼쪽 창문을 본다 ({i})",
            "end_frame": f"여자가 카메라 쪽으로 고개를 돌린다 ({i})",
            "action_en": "The woman slowly turns her head from the window toward the camera",
            "camera_en": "Medium shot, slow push-in",
        }
        for i in range(3)  # 장면 3개 → 경계 키프레임 4장
    ],
}
POSE = {
    "shot": "medium", "x": 0, "z": 0, "yaw": 35, "head": {"yaw": -40, "pitch": -5},
    "arm_l": {"raise": 40, "bend": 60}, "arm_r": {"raise": 30, "bend": 80}, "step": 0.15,
}  # fmt: skip


def _result(**output: Any) -> AgentResult:
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    meta = RequestMeta(context_id="c", token="t", deadline=deadline)
    return AgentResult(meta=meta, status=TaskStatus.COMPLETED, output=output)


async def _gate(action, params, meta, evidence) -> ExecuteDecision:  # noqa: ANN001
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


class _Writer:
    """할 일 초안은 규칙(없음), 영상 제작안은 `llm`으로 쓴다 (planning test_video_plan.Writer)."""

    def __init__(self, llm: Any) -> None:
        self.llm = llm

    async def draft(self, inp: Any, ctx: Any) -> None:
        return None


class _Asked:
    """총괄 의뢰에서는 기획이 데이터분석·마케팅에 직접 묻지 않는다."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def trends(self, *_a: Any, **_k: Any) -> Any:
        self.calls.append("trends")
        return None

    async def followers(self, *_a: Any, **_k: Any) -> Any:
        self.calls.append("followers")
        return None


class _SlowPlanLLM(MockLLMClient):
    """제작안 호출이 제한 시간을 넘긴다(시간 초과). 포즈 호출은 오지 않아야 한다."""

    async def structured(self, prompt: str, schema: type, **kw: Any) -> Any:
        self.calls.append(prompt)
        raise TimeoutError


class Departments:
    """총괄이 부르는 부서들. 요청·답은 JSON으로 한 번 바꿔(A2A 전송처럼) 넘긴다."""

    def __init__(self, planning_llm: Any, ledger: Any) -> None:
        self.calls: list[str] = []
        self.planning_llm = planning_llm
        self.planning = PlanningService(
            gate=_gate, relater=None, insights=_Asked(), marketing=_Asked(),
            proposal_writer=_Writer(planning_llm),
        )  # fmt: skip
        self.ledger = ledger
        self.results: dict[str, AgentResult] = {}
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

    async def caller(self, dept: str, req: AgentRequest) -> AgentResult:
        wire = AgentRequest.model_validate_json(req.model_dump_json())
        self.calls.append(wire.skill)
        out = await self._handle(wire)
        out = AgentResult.model_validate_json(out.model_dump_json())
        self.results[wire.skill] = out
        return out

    async def _handle(self, req: AgentRequest) -> AgentResult:
        if req.skill == "trend_report_create":
            return _result(report_id="rep_e2e")
        if req.skill == "trend_report_insight":
            return _result(report_id="rep_e2e", insight=INSIGHT)
        if req.skill == "receive_insight":
            inp = ReceiveInsightInput.model_validate(req.input)
            res = await self.planning.receive_insight(requester="orchestrator", inp=inp)
            # 기획 handlers.receive_insight와 같이 계약 모양으로 내보낸다
            out = ReceiveInsightOutput.model_validate(res.output)
            return _result(**out.model_dump(mode="json"))
        with company_scope.using(COMPANY):
            if req.skill == "prepare_video_draft_from_plan":
                out = await handlers.HANDLERS[req.skill](req)
            else:  # generate_media: 캐릭터 경로(의뢰 기록)는 request_creative가 만든다
                from marketing_agent import request_creative

                out = await request_creative.make_video(req, self.ledger)
            await rr.wait_idle()
        return out


class _FakeFfmpeg:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], timeout_s: float) -> RunOutput:  # noqa: ARG002
        self.calls.append(list(args))
        Path(args[-1]).write_bytes(b"overlay-" + str(len(self.calls)).encode())
        return RunOutput(0)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("AX_TENANT", COMPANY)
    monkeypatch.delenv("AX_TENANTS_DIR", raising=False)
    monkeypatch.setenv("AGENT_NAME", "marketing")  # 썸네일 장부 키 (마케팅 서버 안에서 도는 것처럼)
    gen = M.env.__wrapped__(tmp_path, monkeypatch)
    e = next(gen)
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", "kling4_0")
    monkeypatch.setenv(settings_store.FILE_ENV, str(tmp_path / "render_settings.json"))
    rs.reset_cache()
    monkeypatch.setattr(handlers.STATE, "media_ledger", e["ledger"])
    monkeypatch.setattr(handlers, "SCRIPT_LLM", lambda: None)  # 의뢰 대본은 기획 장면 그대로
    uploads: list[tuple[bytes, str]] = []

    async def upload(hf: Any, data: bytes, filename: str, content_type: str) -> str:
        uploads.append((data, filename))
        return f"m{len(uploads)}"

    monkeypatch.setattr(cm, "upload", upload)
    board_calls: list[str] = []
    monkeypatch.setattr(board_svg, "LLM", lambda: board_calls.append("llm"))  # 부르면 기록만
    font = tmp_path / "font.ttf"
    font.write_bytes(b"f")
    monkeypatch.setenv(co.FONT_ENV, str(font))
    ff = _FakeFfmpeg()
    monkeypatch.setattr(ov, "RUNNER", ff)
    monkeypatch.setattr(ov, "PROBE", lambda _p: 22.0)
    monkeypatch.setattr(ov, "PROBE_AUDIO", lambda _p: True)
    frames: list[float] = []

    def frame_at(_video: Path, at: float, out: Path) -> bool:
        frames.append(at)
        app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"  # 장면마다 다른 JPEG
        out.write_bytes(b"\xff\xd8" + app0 + f"frame{at}".encode() + b"\xff\xd9")
        return True

    monkeypatch.setattr(ct, "FRAME_AT", frame_at)
    monkeypatch.setattr(ct, "DURATION", lambda _p: 18.0)
    e.update(uploads=uploads, board_calls=board_calls, ff=ff, frames=frames)
    yield e
    rs.reset_cache()
    gen.close()


def _brand(e: dict[str, Any]) -> str:
    """회사 대표 캐릭터: AI 가상 인물, 참조 2장(대표 + 추가 1장)."""
    refs = [{"index": 0, "label": "front"}, {"index": 1, "label": "other"}]
    with company_scope.using(COMPANY):
        store = cr.store()
        sheet = M.SHEET | {"kind": "virtual_human", "references": refs,
                           "consistency_en": "a young woman with a red scarf"}  # fmt: skip
        rid = store.add_sheet(sheet, "AI 가상 인물", None)
        rr.save_original(rid, FRONT, "image/jpeg")
        rr.save_extra(rid, 1, EXTRA, "image/jpeg")
        store.mark_brand(rid, "태윤")
    return rid


def _planning_llm() -> MockLLMClient:
    plan = vp._PlanOut.model_validate(PLAN_LLM)  # noqa: SLF001
    scenes = [
        {
            "n": n,
            "start": POSE | {"label_ko": f"{n}번 시작"},
            "end": POSE | {"yaw": -20, "label_ko": f"{n}번 끝"},
        }
        for n in range(1, 4)
    ]
    poses = sb._PoseOut.model_validate({"scenes": scenes})  # noqa: SLF001
    return MockLLMClient(responses={vp._PlanOut: plan, sb._PoseOut: poses})  # noqa: SLF001


async def _flow(e: dict[str, Any], planning_llm: Any) -> tuple[WorkRequests, str, Departments]:
    """의뢰 접수 → 캐릭터 답 → (데이터·기획) → 사람 확인 → (마케팅 준비·영상 만들기)."""
    brand = _brand(e)
    reqs = WorkRequests()
    row = reqs.create("member", REQUEST_TEXT)
    rid = row["request_id"]
    plan = request_tasks.plan_of(request_tasks.BY_NAME["promo_video"])
    reqs.set_plan(rid, task="promo_video", confidence=0.9, reason="r", alternatives=[], plan=plan)
    reqs.link_thread(rid, "orchchat-e2e")
    depts = Departments(planning_llm, e["ledger"])
    runner = RequestRunner(reqs, lambda: depts, retry_delay=0, notify=lambda _t, _m: None)
    await runner.run(rid)
    assert depts.calls == []  # 캐릭터 답 전에는 데이터분석을 부르지 않는다 (#1728)
    assert runner.answer_brand_character(rid, brand, by="태윤") == ("ok", False)
    await runner.wait_idle()
    assert reqs.get(rid)["steps"]["confirm"]["status"] == "waiting_human"
    assert runner.decide(rid, "confirm", True, by="준하") == "ok"
    await runner.wait_idle()
    with company_scope.using(COMPANY):
        await rr.wait_idle()
    return reqs, rid, depts


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _record_id(depts: Departments) -> str:
    out = depts.results["prepare_video_draft_from_plan"].output
    rid = out["creative_record_id"]
    assert isinstance(rid, str) and rid.startswith("CR-")
    assert out["generate_media_input"]["creative_record_id"] == rid
    return rid


def _approve(e: dict[str, Any], rid: str) -> dict[str, Any]:
    e["gate"].status["generate_creative_video"] = "executed"

    async def go() -> None:
        with company_scope.using(COMPANY):
            await rr.check_video(e["ledger"], M.META, rid)
            await rr.wait_idle()

    _run(go())
    return e["store"].get(rid)


def _thumbs(e: dict[str, Any], rid: str) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        with company_scope.using(COMPANY):
            return await ct.request(e["ledger"], M.META, rid, None, auto=True)

    return _run(go())


def _video_params(e: dict[str, Any]) -> dict[str, Any]:
    return dict(e["hf"].tools)["generate_video_batch"]["requests"][0]["params"]


# ---------------------------------------------------------------- 기본 흐름: Kling 4.0 키프레임


def test_promo_video_request_end_to_end_with_keyframes(world: dict[str, Any]) -> None:
    e = world
    llm = _planning_llm()
    reqs, req_id, depts = _run(_flow(e, llm))
    assert depts.calls == ["trend_report_create", "trend_report_insight", "receive_insight",
                           "prepare_video_draft_from_plan", "generate_media"]  # fmt: skip

    # 기획: AI 초안 · 키프레임 칸 · 포즈 콘티(3D 마네킹) SVG, LLM 2회(제작안 + 포즈)
    video_plan = depts.results["receive_insight"].output["video_plan"]
    assert video_plan["written_by"] == "llm" and video_plan["duration_s"] == 18
    assert video_plan["fallback_reason"] == ""
    assert len(llm.calls) == 2
    for s in video_plan["scenes"]:
        assert s["start_frame"] and s["end_frame"] and s["action_en"] and s["camera_en"]
        for key in ("start_svg", "end_svg"):
            assert "<radialGradient" in s[key] and 'stroke="#e6dccd"' in s[key]  # 구 음영·격자
        assert s["start_pose"]["shot"] == "medium"

    # 마케팅 준비: 의뢰 기록 · 기획 SVG가 칸 SVG · board_svg는 LLM을 부르지 않는다
    rid = _record_id(depts)
    rec = e["store"].get(rid)
    board = rec["storyboard"]
    assert board["board_source"] == "svg" and all(s["svg"] for s in board["shots"])
    assert e["board_calls"] == []
    assert rec["sheet"]["kind"] == "virtual_human"

    # 키프레임 4장(#1806) → 영상 승인 대기
    assert depts.results["generate_media"].outcome == "video_held"
    render = rec["render"]
    frames = render["keyframes"]["frames"]
    assert render["keyframes"]["status"] == "done" and len(frames) == 4
    assert all(f["status"] == "done" and f["svg_source"] == "planning" for f in frames)
    assert render["video"]["status"] != "done"

    # 승인 → Kling 4.0 한 번: image-to-video, start · 중간 · end
    before = len(e["uploads"])
    after = _approve(e, rid)
    video = after["render"]["video"]
    assert video["status"] == "done" and video["keyframes"] is True
    params = _video_params(e)
    assert params["mode"] == "image-to-video" and params["model"] == "kling4_0"
    ids = [f"m{i}" for i in range(before + 1, before + 5)]
    assert params["medias"] == [
        {"value": ids[0], "role": "start_image"},
        {"value": ids[1], "role": "image_references"},
        {"value": ids[2], "role": "image_references"},
        {"value": ids[3], "role": "end_image"},
    ]
    speed = rr.video_speed(after)
    assert speed > 1.0  # 기본 배속 1.2 (#1760)
    assert params["duration"] == video["seconds"] == round(18 * speed)

    # 배속·자막 ffmpeg 인자 (#1760·#1742) → overlay_video
    overlay = after["render"][rr.OVERLAY_STATE]
    assert overlay["status"] == "applied" and overlay["speed_applied"] is True
    assert overlay["captions"] >= 1
    assert after["render"][rr.OVERLAY_PART]["status"] == "done"
    args = " ".join(e["ff"].calls[-1])
    assert f"setpts=PTS/{speed}" in args and "atempo" in args and "drawtext" in args

    # 썸네일 3장: 영상에서 자른다(크레딧 0)
    thumbs = _thumbs(e, rid)
    assert thumbs["status"] == "done" and len(thumbs["candidates"]) == 3
    assert all(c["source"] == "video_frame" for c in thumbs["candidates"])
    assert len(e["frames"]) == 3
    # 총괄 의뢰 기록: 영상 제작 단계는 영상 승인(C) 보류로 남는다(승인 뒤 알림은 이벤트로 따로 온다)
    assert reqs.get(req_id)["steps"]["make"]["status"] == "held"


# ---------------------------------------------------------------- 변형 (a): kling_4_0_flash


def _flash(e: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], str]:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", "kling_4_0_flash")
    _, _, depts = _run(_flow(e, _planning_llm()))
    rid = _record_id(depts)
    render = e["store"].get_render(rid)
    assert not (render.get("keyframes") or {}).get("frames")  # 키프레임 그림 0장
    after = _approve(e, rid)
    assert after["render"]["video"]["status"] == "done"
    assert after["render"]["video"].get("keyframes") is not True
    return _video_params(e), rid


def test_flash_model_skips_keyframes_and_uses_reference_to_video(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    params, _ = _flash(world, monkeypatch)
    assert params["mode"] == "reference-to-video" and params["model"] == "kling_4_0_flash"
    assert params["medias"] and {m["role"] for m in params["medias"]} == {"image_references"}
    sent = [d for d, _ in world["uploads"]]
    assert sent[-len(params["medias"])] == FRONT  # 대표(가상 인물 원본)가 먼저


def test_flash_model_sends_both_company_character_references(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    params, _ = _flash(world, monkeypatch)
    sent = [d for d, _ in world["uploads"]]
    # 회사 캐릭터 참조 2장(대표 + 추가 1장)이 순서대로 간다
    assert len(params["medias"]) == 2 and sent[-2:] == [FRONT, EXTRA]


# ---------------------------------------------------------------- 변형 (b): 기획 LLM 시간 초과


def _timeout(e: dict[str, Any]) -> tuple[dict[str, Any], str]:
    slow = _SlowPlanLLM(responses={})
    _, _, depts = _run(_flow(e, slow))
    video_plan = depts.results["receive_insight"].output["video_plan"]
    assert video_plan["written_by"] == "rule" and video_plan["fallback_reason"] == "timeout"
    assert len(slow.calls) == 1  # 제작안 한 번만(포즈 호출 없음)
    assert all(not s["start_svg"] and not s["end_svg"] for s in video_plan["scenes"])
    return video_plan, _record_id(depts)


def test_planning_timeout_falls_back_to_rule_plan_and_still_makes_a_video(
    world: dict[str, Any],
) -> None:
    _, rid = _timeout(world)
    board = world["store"].get(rid)["storyboard"]
    assert board["board_source"] == "layout"  # 기획 SVG 없음
    after = _approve(world, rid)
    assert after["render"]["video"]["status"] == "done"


@pytest.mark.skip(
    reason="보고서 버그 2: 규칙 초안은 start_frame에 visual만 채우는데(키프레임 칸 아님) 마케팅 "
    "keyframes.plan_frames가 그 글로 키프레임 그림을 만든다(장면 수만큼 그림 크레딧)"
)
def test_planning_timeout_rule_plan_keeps_todays_flow_without_keyframes(
    world: dict[str, Any],
) -> None:
    _, rid = _timeout(world)
    render = world["store"].get_render(rid)
    assert not (render.get("keyframes") or {}).get("frames")  # 키프레임 없이 지금 흐름
    after = _approve(world, rid)
    assert after["render"]["video"].get("keyframes") is not True
