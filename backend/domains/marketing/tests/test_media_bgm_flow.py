"""BGM 연결: off 기본은 이전과 같고, bgm_overlay는 영상→BGM→후처리 순서와 fail-closed를 지킨다.

ffmpeg는 가짜 실행기, 공급자는 목, 안전 관문은 가짜다(실제 호출 없음).
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, ExecuteDecision
from marketing_agent import handlers
from marketing_agent.media import skill
from marketing_agent.media.audio_mix import RunOutput
from marketing_agent.media.bgm_flow import generate_media_flow
from marketing_agent.media.config import MediaSettings
from marketing_agent.media.ledger import MediaLedger
from marketing_agent.media.mock import MockProvider

CTX = "ctx_bgm_0001"
FIELDS = {"season": "spring", "color_theme": "soft pastel", "category": "그림책"}
BGM_FIELDS = {"bgm_mood": "cozy", "bgm_genre": "lo-fi"}
BGM = {"bgm_scene_template_id": "cat_bookstore_bgm", "bgm_fields": BGM_FIELDS}
HDLR = b"....hdlr\x00\x00\x00\x00\x00\x00\x00\x00"
MUXED = b"\x00\x00\x00\x20ftypisom" + HDLR + b"vide-x" + HDLR + b"soun-x"


class Gate:
    """audio만 보류시킬 수 있는 가짜 관문."""

    def __init__(self, hold_audio: bool = False) -> None:
        self.hold_audio = hold_audio
        self.seen: list[dict] = []

    async def __call__(self, action, params, context, evidence):  # noqa: ANN001
        self.seen.append({"action": action, "params": params, "key": context.idempotency_key})
        if self.hold_audio and params["media_kind"] == "audio":
            return ExecuteDecision(
                execution_id="exe_h",
                grade="B",
                status="held",
                review_id="rev_1",
                hold_reason="over_threshold",
            )
        return ExecuteDecision(execution_id=f"exe_{len(self.seen)}", grade="B", status="executed")


class Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: Sequence[str], timeout_s: int) -> RunOutput:
        self.calls.append(tuple(args))
        Path(args[-1]).write_bytes(MUXED if len(self.calls) == 2 else b"AAC")
        return RunOutput(0)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()
    for name in ("MARKETING_AUDIO_MODE", "MARKETING_VIDEO_PROVIDER", "MARKETING_AUDIO_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path / "media"))


def _request(**over) -> AgentRequest:
    input_ = {
        "post_draft_id": "DRAFT-1",
        "media_kind": "video",
        "scene_template_id": "cat_bookstore_video",
        "fields": FIELDS,
    } | over
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CTX,
                "token": "mock:orchestrator",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            },
            "skill": "generate_media",
            "input": input_,
        }
    )


def _flow(ledger: MediaLedger, runner: Runner | None = None, **over):
    return generate_media_flow(
        _request(**over),
        ledger=ledger,
        from_decision=handlers._from_decision,
        runner=runner or Runner(),
    )


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Gate:
    fake = Gate()
    monkeypatch.setattr(skill, "execute", fake)
    return fake


@pytest.fixture
def overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MARKETING_AUDIO_MODE", "bgm_overlay")


# ---- off(기본): 이전과 같다 ----


@pytest.mark.asyncio
async def test_off_video_is_same_as_generate_media(gate: Gate) -> None:
    runner = Runner()
    flow = await _flow(MediaLedger(), runner)
    direct = await skill.generate_media(
        _request(), ledger=MediaLedger(), from_decision=handlers._from_decision
    )
    assert flow.outcome == direct.outcome == "generated"
    assert flow.output is not None and direct.output is not None
    drop = {"generated_at", "execution_id", "provider_job_id", "replayed"}
    assert {k: v for k, v in flow.output.items() if k not in drop} == {
        k: v for k, v in direct.output.items() if k not in drop
    }
    assert flow.output["muted"] is True and "bgm" not in flow.output
    assert runner.calls == []  # ffmpeg를 부르지 않는다
    assert [g["params"]["media_kind"] for g in gate.seen] == ["video", "video"]


@pytest.mark.asyncio
async def test_off_audio_and_bgm_fields_are_still_rejected(gate: Gate) -> None:
    audio = await _flow(
        MediaLedger(),
        media_kind="audio",
        scene_template_id="cat_bookstore_bgm",
        fields=BGM_FIELDS,
    )
    assert audio.outcome == "invalid_input" and gate.seen == []
    extra = await _flow(MediaLedger(), **BGM)
    assert extra.outcome == "invalid_input" and gate.seen == []


@pytest.mark.asyncio
async def test_unknown_audio_mode_value_means_off(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_AUDIO_MODE", "BGM-ON")
    result = await _flow(MediaLedger())
    assert result.outcome == "generated" and result.output is not None
    assert "bgm" not in result.output


def test_settings_default_off() -> None:
    assert MediaSettings.from_env().audio_mode == "off"


# ---- bgm_overlay ----


@pytest.mark.asyncio
async def test_overlay_runs_video_then_bgm_then_postprocess(gate: Gate, overlay: None) -> None:
    runner = Runner()
    result = await _flow(MediaLedger(), runner, **BGM)
    assert result.outcome == "generated" and result.output is not None
    out = result.output
    assert [g["params"]["media_kind"] for g in gate.seen] == ["video", "audio"]
    audio_params = gate.seen[1]["params"]
    assert audio_params["resolution"] == "30s" and "duration_s" not in audio_params
    assert audio_params["ai_generated"] is True and audio_params["cost_krw"] == 100
    assert len(runner.calls) == 2  # 자르기 + 합치기
    assert out["bgm"]["model_id"] == "mock-media" and len(out["bgm"]["prompt_sha256"]) == 64
    assert out["bgm"]["provider_job_id"].startswith("mock_")
    assert out["bgm"]["execution_id"] == "exe_2"
    assert out["postprocess"]["commands"] == [list(c) for c in runner.calls]
    assert out["postprocess"]["audio_tracks"] == 1
    final = Path(out["asset_ref"])
    assert final.name == "asset_bgm.mp4"
    assert out["sha256"] == out["postprocess"]["final_sha256"] == hashlib.sha256(MUXED).hexdigest()
    assert hashlib.sha256(final.read_bytes()).hexdigest() == out["sha256"]
    assert out["source_video_asset_ref"] != out["asset_ref"] and out["muted"] is False
    assert out["total_cost_krw"] == 1100 and out["ai_generated"] is True
    assert result.evidence is not None and "exe_2" in result.evidence.ref_ids


@pytest.mark.asyncio
async def test_overlay_video_keeps_source_audio_and_uses_separate_key(
    gate: Gate, overlay: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []
    original = MockProvider.generate
    monkeypatch.setattr(
        MockProvider, "generate", lambda self, req: (seen.append(req), original(self, req))[1]
    )
    await _flow(MediaLedger(), **BGM)
    assert [r.media_kind for r in seen] == ["video", "audio"]
    assert seen[0].remove_audio is False  # 받은 뒤 overlay_bgm이 오디오를 버린다
    await skill.generate_media(
        _request(), ledger=MediaLedger(), from_decision=handlers._from_decision
    )
    assert gate.seen[0]["key"] != gate.seen[2]["key"]  # 무음 영상 요청과 키가 다르다


@pytest.mark.asyncio
async def test_overlay_requires_bgm_fields_before_spending(gate: Gate, overlay: None) -> None:
    result = await _flow(MediaLedger())
    assert result.outcome == "invalid_input" and gate.seen == []


@pytest.mark.asyncio
async def test_overlay_image_is_unchanged(gate: Gate, overlay: None) -> None:
    result = await _flow(MediaLedger(), media_kind="image", scene_template_id="cat_bookstore_image")
    assert result.outcome == "generated" and result.output is not None
    assert "bgm" not in result.output


# ---- fail-closed ----


@pytest.mark.asyncio
async def test_gate_hold_on_audio_never_calls_bgm_provider(
    overlay: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(skill, "execute", Gate(hold_audio=True))
    kinds = []
    original = MockProvider.generate
    monkeypatch.setattr(
        MockProvider,
        "generate",
        lambda self, req: (kinds.append(req.media_kind), original(self, req))[1],
    )
    runner = Runner()
    ledger = MediaLedger()
    result = await _flow(ledger, runner, **BGM)
    assert result.outcome == "bgm_held" and result.output is not None
    assert result.status.value == "input-required"
    assert result.output["review_id"] == "rev_1" and result.output["stage"] == "bgm"
    assert result.output["retry_replays_video"] is True
    assert kinds == ["video"] and runner.calls == []  # BGM 공급자·ffmpeg 호출 없음
    again = await _flow(ledger, runner, **BGM)  # 영상은 장부에서 재생돼 다시 생성되지 않는다
    assert again.outcome == "bgm_held" and kinds == ["video"]


@pytest.mark.asyncio
async def test_postprocess_failure_is_reported(gate: Gate, overlay: None) -> None:
    class Bad(Runner):
        def __call__(self, args, timeout_s):  # noqa: ANN001
            return RunOutput(1, "boom")

    result = await _flow(MediaLedger(), Bad(), **BGM)
    assert result.outcome == "bgm_postprocess_failed" and result.output is not None
    assert result.output["reason"] == "ffmpeg_failed"
