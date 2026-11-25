"""영상 재생 속도 1.2배 (AI 티 줄이기, 11/25). 가짜 Higgsfield·가짜 ffmpeg만 쓴다.

- 길이: 만들 길이 = round(기획 길이 × 배속), 모델·서버 상한을 넘으면 상한, 실제 배속 = 만든 ÷ 기획
- ffmpeg: 같은 한 번에 `setpts=PTS/배속`(먼저) → 자막·엔드카드, 소리 `atempo`(소리 없으면 생략)
- 글꼴이 없어 자막을 건너뛰어도 배속은 한다(`render.overlay.speed_applied`)
- 설정이 비면 서버 설정 `MARKETING_VIDEO_SPEED`, 그것도 없으면 1.2
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import render_settings as store
from ax_contracts.schemas.marketing_render_settings import RenderSettingsValues, RenderVideoSummary
from marketing_agent import creative_overlay as ov
from marketing_agent import creative_render as rr
from marketing_agent import render_settings as rs
from marketing_agent.media import caption_overlay as co


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


OV = _load("creative_overlay_helpers_speed", "test_creative_overlay.py")
M = _load("creative_render_mcp_helpers_speed", "test_creative_render_mcp.py")
overlay_env = OV.env
mcp_env = M.env


@pytest.fixture(autouse=True)
def _settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "render_settings.json"
    monkeypatch.setenv(store.FILE_ENV, str(path))
    monkeypatch.delenv(rs.SPEED_ENV, raising=False)
    monkeypatch.delenv(rr.VIDEO_DURATION_ENV, raising=False)
    rs.reset_cache()
    return path


def _save(path: Path, row: dict[str, Any], company: str = "catbooks") -> None:
    path.write_text(json.dumps({"companies": {company: row}}), encoding="utf-8")


# ---------------------------------------------------------------- 길이 계산


def test_fifteen_seconds_at_1_2_is_made_as_eighteen() -> None:
    assert rr.sped_seconds(15, "kling_4_0_flash", "plan", "1.2") == (18, None, 15, 1.2)


def test_a_cap_of_fifteen_keeps_fifteen_and_speed_one() -> None:
    # 모르는 모델 상한 15초(MAX_VIDEO_S): 18초를 못 만들어 15초 · 배속 1.0
    assert rr.sped_seconds(15, "kling3_0_turbo", "plan", "1.2") == (15, None, 15, 1.0)


def test_server_duration_cap_also_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(rr.VIDEO_DURATION_ENV, "16")
    made, clamped, planned, speed = rr.sped_seconds(15, "kling_4_0_flash", "plan", "1.2")
    assert (made, clamped, planned) == (16, None, 15)
    assert speed == pytest.approx(16 / 15, abs=0.001)


def test_partly_capped_speed_is_made_over_plan() -> None:
    # 15 × 1.5 = 22.5 → 22초인데 상한 20 → 20초, 실제 배속 20/15
    made, _, planned, speed = rr.sped_seconds(15, "kling_4_0_flash", "plan", "1.5")
    assert (made, planned) == (20, 15)
    assert speed == pytest.approx(1.333, abs=0.001)


def test_speed_one_is_todays_length() -> None:
    assert rr.sped_seconds(15, "kling_4_0_flash", "plan", "1.0") == (15, None, 15, 1.0)
    # 기획 길이가 상한을 넘으면 지금처럼 줄인 사실(seconds_clamped_from)을 남기고 배속은 1.0
    assert rr.sped_seconds(30, "kling_4_0_flash", "plan", "1.2") == (20, 30, 20, 1.0)
    # 회사 설정 정수 초도 같은 규칙
    assert rr.sped_seconds(None, "kling_4_0_flash", 10, "1.5") == (15, None, 10, 1.5)


def test_unknown_speed_text_is_one() -> None:
    for raw in (None, "", "fast", "0.5", "3"):
        assert rr.speed_value(raw) == 1.0
    assert rr.speed_value("1.2") == 1.2


# ---------------------------------------------------------------- 설정


def test_empty_setting_uses_the_server_value_then_1_2(
    _settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert rs.effective("catbooks").speed == "1.2"
    assert rs.defaults().speed == "1.2"
    monkeypatch.setenv(rs.SPEED_ENV, "1.5")
    assert rs.effective("catbooks").speed == "1.5"
    monkeypatch.setenv(rs.SPEED_ENV, "2.0")  # 모르는 값이면 1.2
    assert rs.effective("catbooks").speed == "1.2"
    _save(_settings_file, {"speed": "1.0"})
    assert rs.effective("catbooks").speed == "1.0"  # 회사 화면 값이 먼저


def test_store_keeps_only_known_speeds() -> None:
    assert store.clean({"speed": "1.5"}) == {"speed": "1.5"}
    assert store.clean({"speed": "2.0"}) == {}
    assert store.clean({"speed": 1.2}) == {}
    assert RenderSettingsValues(speed="1.2").speed == "1.2"
    with pytest.raises(ValueError):
        RenderSettingsValues.model_validate({"speed": "2.0"})


# ---------------------------------------------------------------- 요청 요약·예상 크레딧


def _durations(obj: Any) -> list[int]:
    if isinstance(obj, dict):
        found = [v for k, v in obj.items() if k == "duration" and isinstance(v, int)]
        return found + [d for v in obj.values() for d in _durations(v)]
    if isinstance(obj, list):
        return [d for v in obj for d in _durations(v)]
    return []


def test_summary_has_speed_and_generated_seconds_and_quotes_the_made_length(
    mcp_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", "kling_4_0_flash")
    rid = M._sheet(mcp_env)  # noqa: SLF001
    mcp_env["store"].attach_storyboard(rid, "들판", 6, M.BOARD | {rr.PLAN_DURATION_KEY: 15})
    M._run(lambda: _start_board(mcp_env, rid))  # noqa: SLF001
    video = M._run(lambda: rr.request_video(mcp_env["ledger"], M.META, rid, "gaon"))  # noqa: SLF001
    assert video["status"] == "awaiting_approval"
    assert (video["seconds"], video["speed"], video["plan_seconds"]) == (18, 1.2, 15)
    summary = RenderVideoSummary.model_validate(video["summary"])
    assert (summary.seconds, summary.generated_seconds, summary.speed) == (18, 18, 1.2)
    # 예상 크레딧(미리 보기)은 만든 길이로 묻는다
    assert 18 in _durations([args for _, args in mcp_env["hf"].tools])
    assert "1.2배속으로 틀어 15초" in video["params"]["summary"]


async def _start_board(env: dict[str, Any], rid: str) -> None:
    rr.maybe_start_board(env["ledger"], M.META, rid)


# ---------------------------------------------------------------- ffmpeg 인자


def _args(speed: float, has_audio: bool = True, texts: bool = True) -> list[str]:
    items = [(Path("c.txt"), 0.0, 2.0, "h/26")] if texts else []
    return co.build_args(
        "ffmpeg", Path("in.mp4"), Path("out.mp4"), Path("f.ttf") if texts else None, items,
        speed=speed, has_audio=has_audio,
    )  # fmt: skip


def test_ffmpeg_speeds_up_first_then_draws_text_and_tempo_for_sound() -> None:
    args = _args(1.2)
    filters = args[args.index("-vf") + 1]
    assert filters.startswith("setpts=PTS/1.2,drawtext=")
    assert args[args.index("-af") + 1] == "atempo=1.2"
    assert args[args.index("-c:a") + 1] == "aac"


def test_ffmpeg_without_a_sound_track_skips_atempo() -> None:
    args = _args(1.5, has_audio=False)
    assert "-af" not in args and args[args.index("-c:a") + 1] == "copy"
    assert args[args.index("-vf") + 1].startswith("setpts=PTS/1.5,")


def test_ffmpeg_at_speed_one_is_todays_args() -> None:
    items = [(Path("c.txt"), 0.0, 2.0, "h/26")]
    old = co.build_args("ffmpeg", Path("in.mp4"), Path("out.mp4"), Path("f.ttf"), items)
    assert _args(1.0) == old
    assert "setpts" not in " ".join(old) and "-af" not in old


def test_speed_only_needs_no_font() -> None:
    args = _args(1.2, texts=False)
    assert args[args.index("-vf") + 1] == "setpts=PTS/1.2"
    with pytest.raises(co.OverlayError):
        _args(1.0, texts=False)  # 배속도 글도 없으면 할 일이 없다


# ---------------------------------------------------------------- 자막 흐름과 같이


def _speed_record(env: dict[str, Any], speed: float) -> str:
    rid = OV._record(env)  # noqa: SLF001
    render = env["store"].get_render(rid)
    render["video"]["speed"] = speed
    env["store"].set_render(rid, render)
    return rid


def test_overlay_speeds_up_and_captions_use_plan_times(
    overlay_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ov, "PROBE", lambda _p: 9.6)  # 8초 기획 × 1.2 = 9.6초로 만든 영상
    monkeypatch.setattr(ov, "PROBE_AUDIO", lambda _p: True)
    rid = _speed_record(overlay_env, 1.2)
    state = OV._run(lambda: ov.apply(rid))  # noqa: SLF001
    assert state["status"] == "applied" and state["speed_applied"] is True
    args = overlay_env["ff"].calls[0]
    filters = args[args.index("-vf") + 1]
    assert filters.startswith("setpts=PTS/1.2,")
    # 자막 시각은 빨라진 영상 기준(기획 시각): 엔드카드는 마지막 2초 = 6~8초
    assert "enable='between(t,0,2)'" in filters and "enable='between(t,6,8)'" in filters
    assert args[args.index("-af") + 1] == "atempo=1.2"
    rec = overlay_env["store"].get(rid)
    assert rr.publish_video_part(rec) == "overlay_video"


def test_no_font_still_speeds_up(
    overlay_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(co.FONT_ENV)
    monkeypatch.setattr(ov, "PROBE_AUDIO", lambda _p: False)
    rid = _speed_record(overlay_env, 1.2)
    state = OV._run(lambda: ov.apply(rid))  # noqa: SLF001
    assert state["status"] == "applied" and state["speed_applied"] is True
    assert state["captions_skipped"] == "font_not_found" and state["captions"] == 0
    args = overlay_env["ff"].calls[0]
    assert args[args.index("-vf") + 1] == "setpts=PTS/1.2"
    assert "-af" not in args  # 소리 트랙 없음
    # 배속한 영상이 내보내진다
    assert rr.publish_video_part(overlay_env["store"].get(rid)) == "overlay_video"


def test_old_record_without_speed_is_todays_overlay(overlay_env: dict[str, Any]) -> None:
    rid = OV._record(overlay_env)  # noqa: SLF001
    state = OV._run(lambda: ov.apply(rid))  # noqa: SLF001
    assert state["status"] == "applied" and state["speed_applied"] is False
    args = overlay_env["ff"].calls[0]
    assert "setpts" not in args[args.index("-vf") + 1] and "-af" not in args


def test_record_speed_reads_the_video_or_its_summary() -> None:
    assert ov.record_speed({"video": {"speed": 1.5}}) == 1.5
    assert ov.record_speed({"video": {"summary": {"speed": 1.2}}}) == 1.2
    assert ov.record_speed({}) == 1.0
