"""회사 Higgsfield 계정(원격 MCP)으로 그림·영상 만들기. 연결한 회사만 MCP, 아니면 예전 키 길.

가짜 Higgsfield(MCP·토큰·업로드·결과 파일)와 가짜 안전 관문만 쓴다. 실제로 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_agent_template import company_scope
from ax_contracts.schemas import ExecuteDecision, HoldReason, RequestMeta
from ax_secret_store import resolver
from ax_secret_store.crypto import generate_master_key
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import higgsfield_mcp_auth as auth
from marketing_agent import higgsfield_status as hs
from marketing_agent.higgsfield_mcp import MCP_URL
from marketing_agent.media.ledger import MediaLedger

META = RequestMeta(
    context_id="ctx_test_mcp",
    token="t",
    idempotency_key="k0",
    deadline=datetime.now(UTC) + timedelta(seconds=60),
)
SHEET = {"summary_ko": "소녀", "prompt_en": "Character sheet ...", "negative_prompt_en": "blurry"}
BOARD = {"title_ko": "들판", "sheet_prompt_en": "A storyboard sheet ...", "negative_prompt_en": "x"}
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + bytes(32)
REFRESH = "DUMMY-HF-REFRESH-0001"
ACCESS = "DUMMY-HF-ACCESS-0001"
JOB_IMG = "11111111-1111-4111-8111-111111111111"
JOB_VID = "22222222-2222-4222-8222-222222222222"


class Higgsfield:
    """토큰 주소·MCP·업로드 PUT·결과 파일을 흉내 낸다. 키 길(api.higgsfield.ai)이 불리면 남긴다."""

    def __init__(self) -> None:
        self.tools: list[tuple[str, dict[str, Any]]] = []
        self.api_calls: list[str] = []
        self.put_auth: list[str | None] = []
        self.credits = 100.0
        self.cost = 2.0
        self.job_status = "completed"
        self.waits = 0

    def _tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.tools.append((name, args))
        if name == "balance":
            return {"credits": self.credits, "subscription_plan_type": "ultra"}
        if name.startswith("estimate_"):
            return {"cost": {"credits": int(self.cost), "credits_exact": self.cost}}
        if name == "media_upload":
            upload = {
                "upload_url": "https://upload.example/put",
                "media_id": "media-1",
                "method": "PUT",
                "content_type": "image/jpeg",
                "instructions": "PUT the bytes",
            }
            return {"uploads": [upload]}
        if name == "media_confirm":
            return {"results": [{"media_id": args["media_id"], "status": "confirmed"}]}
        if name.startswith("generate_"):
            job = JOB_VID if "video" in name else JOB_IMG
            return {"jobs": [{"index": 0, "job_id": job, "status": "queued"}], "submitted_count": 1}
        if name == "jobs_wait":
            self.waits += 1
            job = args["jobs"][0]["job_id"]
            if self.waits == 1:  # 한 번은 아직 도는 중
                return {"jobs": [{"index": 0, "job_id": job, "status": "in_progress"}]}
            ext = "mp4" if job == JOB_VID else "jpg"
            row = {"index": 0, "job_id": job, "status": self.job_status}
            if self.job_status == "completed":
                row["result_url"] = f"https://cdn.example/out.{ext}"
            return {"jobs": [row], "all_terminal": True}
        raise AssertionError(name)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == auth.TOKEN_URL:
            return httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3600})
        if url == MCP_URL:
            assert request.headers["authorization"] == f"Bearer {ACCESS}"
            body = json.loads(request.content)
            if body["method"] == "notifications/initialized":
                return httpx.Response(202)
            if body["method"] == "initialize":
                result: dict[str, Any] = {"protocolVersion": "2025-06-18"}
            else:
                out = self._tool(body["params"]["name"], body["params"]["arguments"])
                result = {"structuredContent": out}
            msg = {"jsonrpc": "2.0", "id": body["id"], "result": result}
            return httpx.Response(
                200,
                text=f"event: message\ndata: {json.dumps(msg)}\n\n",
                headers={"content-type": "text/event-stream"},
            )
        if url == "https://upload.example/put":
            self.put_auth.append(request.headers.get("authorization"))
            return httpx.Response(200)
        if url.startswith("https://cdn.example/"):
            return httpx.Response(200, content=b"video-bytes" if url.endswith(".mp4") else JPEG)
        self.api_calls.append(url)
        return httpx.Response(404)

    def names(self) -> list[str]:
        return [n for n, _ in self.tools]


class Gate:
    def __init__(self) -> None:
        self.status = {"generate_media": "executed", "generate_creative_video": "held"}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(
        self, action: str, params: dict, meta: RequestMeta, evidence: Any
    ) -> ExecuteDecision:
        self.calls.append((action, dict(params)))
        status = self.status[action]
        extra: dict[str, Any] = {}
        if status == "held":
            extra = {"hold_reason": list(HoldReason)[0], "review_id": "rev_1"}
        return ExecuteDecision(
            execution_id=f"ex_{len(self.calls)}",
            grade="C" if action == "generate_creative_video" else "B",
            status=status,  # type: ignore[arg-type]
            **extra,
        )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    # 예전 키 길 설정도 다 있다. 회사 계정이 연결된 회사만 MCP로 가는지 본다
    monkeypatch.setenv("MARKETING_HIGGSFIELD_API_KEY", "kid:ksecret")
    monkeypatch.setenv("MARKETING_HIGGSFIELD_IMAGE_MODEL", "google/nano-banana/edit")
    monkeypatch.setenv("MARKETING_HIGGSFIELD_VIDEO_MODEL", "kling/video/image-to-video")
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_IMAGE_MODEL", "nano_banana_pro")
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", "kling3_0_turbo")
    monkeypatch.setenv(auth.CLIENT_ID_ENV, "DUMMY-PUBLIC-CLIENT")
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path))
    # 우리 키 월 예산은 비워 둔다: 회사 계정(구독 크레딧)은 예산 없이 만든다
    for name in (
        "MARKETING_QUOTA_IMAGE_COUNT_MONTHLY",
        "MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY",
        "MARKETING_MEDIA_KEY_OWNER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AX_SECRET_STORE", "db")
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("AX_SECRET_CACHE_TTL", "0")
    resolver.reset_for_tests()
    auth.forget()
    hs.reset_cache()
    svc = resolver.service()
    svc.store_oauth_token("catbooks", auth.TOKEN_NAME, REFRESH, "test")  # 캣북스만 연결
    # 설치 기본 API 키(db 모드에서는 키 저장소 기본 tenant에 있다)
    svc.import_value(resolver.default_tenant(), "MARKETING_HIGGSFIELD_API_KEY", "kid:ksecret", "t")
    store = cr.CreativeRecords(":memory:")
    cr.set_store(store)
    hf = Higgsfield()
    gate = Gate()
    monkeypatch.setattr(
        rr, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(hf.handle))
    )
    monkeypatch.setattr(rr, "execute", gate)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(rr, "SLEEP", no_sleep)
    yield {"store": store, "hf": hf, "gate": gate, "ledger": MediaLedger(":memory:"), "svc": svc}
    cr.set_store(None)
    auth.forget()
    hs.reset_cache()
    resolver.reset_for_tests()


def _run(coro_fn, company: str = "catbooks"):  # noqa: ANN001, ANN202
    async def go():  # noqa: ANN202
        out = await coro_fn()
        await rr.wait_idle()
        return out

    with company_scope.using(company):
        return asyncio.run(go())


def _sheet(env: dict[str, Any], company: str = "catbooks") -> str:
    rid = env["store"].add_sheet(SHEET, None, None)

    async def go() -> None:
        rr.start_sheet(env["ledger"], META, rid, SHEET, JPEG)

    _run(go, company)
    return rid


def _board(env: dict[str, Any]) -> str:
    rid = _sheet(env)
    env["store"].attach_storyboard(rid, "들판 이야기", 6, BOARD)

    async def go() -> None:
        rr.maybe_start_board(env["ledger"], META, rid)

    _run(go)
    return rid


def test_connected_company_makes_the_sheet_with_its_own_account(
    env: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rid = _sheet(env)
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "done" and part["route"] == "mcp" and part["request_id"] == JOB_IMG
    hf = env["hf"]
    assert hf.api_calls == []  # 키 길은 부르지 않았다
    # 예상 크레딧·남은 크레딧을 본 뒤 관문 → 참조 올리기 → 배치 만들기 → 기다리기
    names = hf.names()
    assert names[:2] == ["estimate_image_cost", "balance"]
    assert names[2:] == [
        "media_upload",
        "media_confirm",
        "generate_image_batch",
        "jobs_wait",
        "jobs_wait",
    ]
    params = dict(hf.tools)["generate_image_batch"]["requests"][0]["params"]
    assert params["model"] == "nano_banana_pro" and params["use_unlim"] is False
    assert params["medias"] == [{"value": "media-1", "role": "image_references"}]
    assert "Avoid: blurry" in params["prompt"]
    assert hf.put_auth == [None]  # 업로드 PUT에는 토큰을 보내지 않는다
    action, gate_params = env["gate"].calls[0]
    assert action == "generate_media" and gate_params["media_kind"] == "image"
    assert gate_params["provider"] == "higgsfield_mcp" and gate_params["ai_generated"] is True
    assert gate_params["billing"] == "company_subscription"
    assert gate_params["credits_estimate"] == 2.0 and "구독 크레딧" in gate_params["summary"]
    for value in (REFRESH, ACCESS):
        assert value not in caplog.text and value not in json.dumps(part)


def test_company_without_a_connection_keeps_the_api_key_path(env: dict[str, Any]) -> None:
    rid = _sheet(env, "nubi")
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["route"] == "api"
    assert env["hf"].tools == []  # MCP는 부르지 않았다
    # 키 길은 예전처럼 우리 키 월 예산이 없으면 막힌다
    assert part["status"] == "blocked" and part["error"] == "quota_not_configured"


def test_not_enough_credits_stops_before_the_gate(env: dict[str, Any]) -> None:
    env["hf"].credits = 1.0
    rid = _sheet(env)
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "blocked" and part["error"] == "credits_insufficient"
    assert env["gate"].calls == []
    assert "generate_image_batch" not in env["hf"].names()


def test_failed_job_is_recorded_and_the_ledger_is_released(env: dict[str, Any]) -> None:
    env["hf"].job_status = "nsfw"
    rid = _sheet(env)
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "failed" and part["error"] == "mcp_job_nsfw"


def test_missing_mcp_model_is_unavailable(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MARKETING_HIGGSFIELD_MCP_IMAGE_MODEL")
    rid = _sheet(env)
    part = env["store"].get_render(rid)["sheet_image"]
    assert part["status"] == "unavailable" and part["error"] == "model_not_configured"
    assert env["hf"].tools == [] and env["gate"].calls == []


def test_video_is_approved_by_a_human_then_made_with_the_same_account(
    env: dict[str, Any],
) -> None:
    rid = _board(env)
    video = _run(lambda: rr.request_video(env["ledger"], META, rid, "gaon"))
    assert video["status"] == "awaiting_approval" and video["route"] == "mcp"
    assert video["company_id"] == "catbooks"
    assert "generate_video_batch" not in env["hf"].names()  # 승인 전에는 만들지 않는다
    action, params = env["gate"].calls[-1]
    assert action == "generate_creative_video" and params["billing"] == "company_subscription"
    assert params["provider"] == "higgsfield_mcp" and params["credits_estimate"] == 2.0

    env["gate"].status["generate_creative_video"] = "executed"  # 사람이 승인
    env["hf"].waits = 0
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    done = env["store"].get_render(rid)["video"]
    assert done["status"] == "done" and done["asset"] == "video.mp4"
    batch = dict(env["hf"].tools)["generate_video_batch"]["requests"][0]["params"]
    assert batch["model"] == "kling3_0_turbo" and batch["duration"] == video["seconds"]
    assert batch["medias"] == [{"value": "media-1", "role": "start_image"}]
    assert batch["aspect_ratio"] == "9:16"
    assert env["hf"].api_calls == []


def test_video_does_not_fall_back_to_the_api_key_after_disconnecting(env: dict[str, Any]) -> None:
    rid = _board(env)
    _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    req = env["svc"].stage("delete", "catbooks", auth.TOKEN_NAME, None, "admin")
    env["svc"].apply(req["id"], "admin")  # 승인 기다리는 사이 연결을 끊었다
    env["gate"].status["generate_creative_video"] = "executed"
    _run(lambda: rr.check_video(env["ledger"], META, rid))
    video = env["store"].get_render(rid)["video"]
    assert video["status"] == "failed" and video["error"] == "higgsfield_not_connected"
    assert env["hf"].api_calls == []  # API 키 크레딧으로 바꿔 만들지 않았다


def test_video_request_is_refused_when_credits_are_short(env: dict[str, Any]) -> None:
    rid = _board(env)
    env["hf"].credits = 1.0
    with pytest.raises(rr.RenderError, match="credits_insufficient"):
        _run(lambda: rr.request_video(env["ledger"], META, rid, None))
    assert not any(a == "generate_creative_video" for a, _ in env["gate"].calls)


def test_status_shows_the_connected_account_credits_and_plan(env: dict[str, Any]) -> None:
    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(env["hf"].handle))

    out = _run(lambda: hs.probe(client()))
    assert out.source == "mcp" and out.connection == "ok"
    assert out.credits == 100.0 and out.plan == "ultra"
    assert env["hf"].names() == ["balance"]  # 읽기만
    other = _run(lambda: hs.probe(client()), "nubi")
    assert other.source == "api_key" and other.credits is None


def test_status_asks_to_reconnect_when_higgsfield_refuses(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        if str(request.url) == auth.TOKEN_URL:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    out = _run(lambda: hs.probe(client))
    assert out.source == "mcp" and out.connection == "invalid_key"
    assert "다시 연결" in out.message_ko
