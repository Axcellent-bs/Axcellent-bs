"""회사별 만들기 설정 (11/25): 그림·영상 모델, 비율, 길이, 그림 크레딧, 영상 요약, 모델 목록.

가짜 Higgsfield(MCP)와 가짜 안전 관문만 쓴다. 실제로 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_agent_template import render_settings as store
from marketing_agent import creative_mcp as mcp
from marketing_agent import creative_render as rr
from marketing_agent import render_settings as rs
from marketing_agent.higgsfield_mcp import MCP_URL

sys.path.insert(0, str(Path(__file__).parent))
from test_creative_render_mcp import (  # noqa: E402, F401 - env는 시험 도구(fixture)
    META,
    Higgsfield,
    _board,
    _run,
    _sheet,
    env,
)


@pytest.fixture(autouse=True)
def _file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "render_settings.json"
    monkeypatch.setenv(store.FILE_ENV, str(path))
    rs.reset_cache()
    return path


def _save(path: Path, row: dict[str, Any], company: str = "catbooks") -> None:
    data = json.loads(path.read_text()) if path.exists() else {"companies": {}}
    data["companies"][company] = row
    path.write_text(json.dumps(data), encoding="utf-8")


def _generate(env: dict[str, Any], kind: str) -> dict[str, Any]:  # noqa: F811
    return dict(env["hf"].tools)[f"generate_{kind}_batch"]["requests"][0]["params"]


def test_no_settings_keeps_todays_behavior(env: dict[str, Any]) -> None:  # noqa: F811
    rid = _board(env)
    video = _run(lambda: rr.request_video(env["ledger"], META, rid, "gaon"))
    assert video["summary"] == {
        "model": "kling3_0_turbo",
        "seconds": video["seconds"],
        "seconds_clamped_from": None,
        "aspect": "9:16",
        "reference": "storyboard_image",
        "credits_estimate": 2.0,
        # 11/25 재생 속도: 설정이 비면 서버 설정, 그것도 없으면 1.2배속(5초 → 6초로 만든다)
        "speed": 1.2,
        "generated_seconds": video["seconds"],
    }
    assert video["seconds"] == 6 and video["plan_seconds"] == 5
    image = _generate(env, "image")
    assert image["model"] == "nano_banana_pro" and image["aspect_ratio"] == "3:4"


def test_company_models_and_aspect_apply(env: dict[str, Any], _file: Path) -> None:  # noqa: F811
    _save(_file, {"image_model": "img_x", "video_model": "kling_4_0_flash", "aspect": "1:1"})
    rid = _board(env)
    for name, args in env["hf"].tools:
        if name == "generate_image_batch":
            params = args["requests"][0]["params"]
            assert params["model"] == "img_x" and params["aspect_ratio"] == "1:1"
    video = _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    assert video["summary"]["model"] == "kling_4_0_flash"
    assert video["summary"]["aspect"] == "1:1"
    assert "kling_4_0_flash" in env["gate"].calls[-1][1]["summary"]  # 안전 요청 요약에도
    assert "1:1" in env["gate"].calls[-1][1]["summary"]
    assert "예상 2 크레딧" in env["gate"].calls[-1][1]["summary"]
    env["gate"].status["generate_creative_video"] = "executed"
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    params = _generate(env, "video")
    assert params["model"] == "kling_4_0_flash" and params["aspect_ratio"] == "1:1"


def test_other_company_values_are_not_mixed(env: dict[str, Any], _file: Path) -> None:  # noqa: F811
    _save(_file, {"video_model": "other_model", "aspect": "16:9", "image_credits_ok": False},
          company="someone-else")  # fmt: skip
    rid = _board(env)
    video = _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    assert video["summary"]["model"] == "kling3_0_turbo" and video["summary"]["aspect"] == "9:16"
    assert store.saved("someone-else")["video_model"] == "other_model"
    assert store.saved("catbooks") == {}


def test_credits_off_stops_every_image_step_without_reserving(
    env: dict[str, Any],  # noqa: F811
    _file: Path,
) -> None:
    _save(_file, {"image_credits_ok": False})
    rid = _sheet(env)
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "unavailable" and part["error"] == "image_credits_disabled"
    assert env["hf"].tools == [] and env["gate"].calls == []
    assert rr.configured("image", rr.MCP, "catbooks") == "image_credits_disabled"
    assert rr.configured("video", rr.MCP, "catbooks") is None  # 영상은 그대로
    # 시작을 우회해 그림 단계를 직접 불러도 예약 전에 멈춘다
    env["store"].set_render(rid, {})
    asyncio.run(
        rr._render_image(
            env["ledger"],
            META,
            rid,
            "board_image",
            "p",
            "",
            None,
            "3:4",  # noqa: SLF001
            rr.MCP,
            "catbooks",
        )  # fmt: skip
    )
    assert env["store"].get_render(rid)["board_image"]["error"] == "image_credits_disabled"
    assert env["hf"].tools == [] and env["gate"].calls == []


def test_credits_ok_true_is_the_same_as_empty(env: dict[str, Any], _file: Path) -> None:  # noqa: F811
    _save(_file, {"image_credits_ok": True})
    rid = _sheet(env)
    assert env["store"].get_render(rid)["sheet_image"]["status"] == "done"


@pytest.mark.parametrize(
    ("setting", "plan_s", "want"),
    [
        ("plan", 8, (8, None)),  # 기획 길이
        (6, 8, (6, None)),  # 정수가 이긴다
        (30, 8, (20, 30)),  # 모델 상한(20)으로 줄이고 원래 값을 남긴다
        ("plan", 30, (20, 30)),
        ("plan", None, (5, None)),  # 기획 길이도 없으면 지금 규칙
    ],
)
def test_seconds_plan_integer_and_clamp(
    setting: Any, plan_s: Any, want: tuple[int, int | None]
) -> None:
    assert rr.plan_seconds(plan_s, "kling_4_0_flash", setting) == want


def test_server_duration_cap_still_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(rr.VIDEO_DURATION_ENV, "10")
    assert rr.plan_seconds(None, "kling_4_0_flash", 15) == (10, 15)


def test_video_summary_has_clamp_and_is_there_while_held(
    env: dict[str, Any],  # noqa: F811
    _file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", "kling_4_0_flash")
    _save(_file, {"video_seconds": 40})
    rid = _board(env)
    video = _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    assert video["status"] == "awaiting_approval"
    stored = env["store"].get_render(rid)["video"]["summary"]
    assert stored == video["summary"]
    assert stored["seconds"] == 20 and stored["seconds_clamped_from"] == 40
    assert stored["model"] == "kling_4_0_flash"


# ---------------------------------------------------------------- 모델 목록
class Explorer(Higgsfield):
    def __init__(self) -> None:
        super().__init__()
        self.explored = 0
        self.fail = False

    def _tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name != "models_explore":
            return super()._tool(name, args)
        self.tools.append((name, args))
        self.explored += 1
        if self.fail:
            raise AssertionError("fail")
        if args["type"] == "image":
            return {"items": [{"id": "img_new", "name": "Image New"}]}
        return {
            "items": [
                {
                    "id": "vid_new",
                    "name": "Video New",
                    "durations": [3, 5, 12],
                    "aspect_ratios": ["9:16", "21:9", "16:9"],
                },  # fmt: skip
                {"id": "vid_blank", "name": "No Info"},
            ]
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail and str(request.url) == MCP_URL:
            body = json.loads(request.content)
            if body.get("method") == "tools/call":
                return httpx.Response(500)
        return super().handle(request)


@pytest.fixture
def explorer(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Explorer:  # noqa: F811
    hf = Explorer()
    monkeypatch.setattr(
        rs, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(hf.handle))
    )
    return hf


def test_models_come_from_models_explore_and_are_cached_for_a_day(
    explorer: Explorer, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(rs, "clock", lambda: now[0])
    first, source = asyncio.run(rs.models("catbooks"))
    assert source == "higgsfield" and explorer.explored == 2  # 그림 1 + 영상 1
    assert {m.id for m in first.image} == {"nano_banana_pro", "img_new"}  # 서버 설정 모델도
    by_id = {m.id: m for m in first.video}
    assert (by_id["vid_new"].min_s, by_id["vid_new"].max_s) == (3, 12)
    assert by_id["vid_new"].aspects == ["9:16", "16:9"]  # 저장할 수 없는 비율은 뺀다
    assert by_id["vid_blank"].min_s is None and by_id["vid_blank"].aspects == []  # 지어내지 않는다
    assert by_id["kling_4_0_flash"].max_s == 20  # 아는 모델
    assert mcp.video_max_seconds("vid_new") == 12  # 길이 줄이기가 쓴다
    asyncio.run(rs.models("catbooks"))
    assert explorer.explored == 2  # 하루 안에는 다시 묻지 않는다
    now[0] += 24 * 3600 + 1
    asyncio.run(rs.models("catbooks"))
    assert explorer.explored == 4
    assert all(name == "models_explore" for name, _ in explorer.tools if name != "balance")


def test_explore_failure_falls_back_to_known_models_only(
    explorer: Explorer,
    env: dict[str, Any],  # noqa: F811
) -> None:
    explorer.fail = True
    out, source = asyncio.run(rs.models("catbooks"))
    assert source == "known"
    assert {m.id for m in out.video} == {"kling_4_0_flash", "kling3_0_turbo"}
    assert [m.id for m in out.image] == ["nano_banana_pro"]
    assert {m.id: m.min_s for m in out.video} == {"kling_4_0_flash": None, "kling3_0_turbo": None}
    assert {m.id: m.aspects for m in out.video} == {"kling_4_0_flash": [], "kling3_0_turbo": []}


def test_no_connection_gives_known_models_without_calling(explorer: Explorer) -> None:
    out, source = asyncio.run(rs.models(None))
    assert source == "known" and explorer.explored == 0 and out.video
