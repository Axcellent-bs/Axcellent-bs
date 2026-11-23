"""제작 기록 영상 썸네일 후보 3장(Higgsfield) → 사람이 고르기 → 유튜브 승인 해시·썸네일 적용.

가짜 Higgsfield(회사 계정 MCP·키 길)·가짜 안전 관문·가짜 구글 API만 쓴다. ffmpeg는 부르지 않는다
(첫 프레임·JPEG 정리는 가짜 함수로 바꾼다).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_agent_template import company_scope
from ax_contracts.schemas import AgentRequest, ExecuteDecision, HoldReason, RequestMeta, TaskStatus
from ax_secret_store import resolver
from ax_secret_store.crypto import generate_master_key
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import creative_thumbnails as ct
from marketing_agent import handlers, sns_media
from marketing_agent import higgsfield_mcp_auth as auth
from marketing_agent import higgsfield_status as hs
from marketing_agent import youtube as yt
from marketing_agent.higgsfield_mcp import MCP_URL
from marketing_agent.media.ledger import MediaLedger

META = RequestMeta(
    context_id="ctx_test_thumbs",
    token="t",
    idempotency_key="k0",
    deadline=datetime.now(UTC) + timedelta(seconds=60),
)
SHEET = {
    "summary_ko": "흰 슈트 안드로이드",
    "prompt_en": "sheet",
    "negative_prompt_en": "x",
    "consistency_en": "a white-suited android with round blue eyes",
}
SHOTS = [
    {"no": 1, "prompt_en": "android walks into the office", "has_character": True},
    {"no": 2, "prompt_en": "empty desk at dawn", "has_character": False},
    {"no": 3, "prompt_en": "android high-fives the team", "has_character": True},
]
BOARD = {
    "title_ko": "AXcellent 출근 첫날",
    "sheet_prompt_en": "board",
    "style_en": "soft 3D animation",
    "negative_prompt_en": "gore",
    "shots": SHOTS,
}
VIDEO = b"\x00\x00\x00\x18ftypmp42" + bytes(64)


def _jpeg(tag: str) -> bytes:
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    return b"\xff\xd8" + app0 + tag.encode() + b"\xff\xd9"


FRAME = _jpeg("first-frame")
REFRESH = "DUMMY-HF-REFRESH-0002"
ACCESS = "DUMMY-HF-ACCESS-0002"


def _job(n: int) -> str:
    return f"3333333{n}-3333-4333-8333-333333333333"


class Higgsfield:
    """회사 계정 MCP·키 길(api.higgsfield.ai)·업로드·결과 그림을 흉내 낸다(장마다 다른 그림)."""

    def __init__(self) -> None:
        self.tools: list[tuple[str, dict[str, Any]]] = []
        self.api: list[tuple[str, dict[str, Any] | None]] = []
        self.credits = 100.0
        self.cost = 2.0
        self.made = 0
        self.fail_job: int | None = None  # 이 번호 작업은 실패

    def _tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.tools.append((name, args))
        if name == "balance":
            return {"credits": self.credits, "subscription_plan_type": "ultra"}
        if name.startswith("estimate_"):
            return {"cost": {"credits_exact": self.cost}}
        if name == "media_upload":
            return {"uploads": [{"upload_url": "https://upload.example/put", "media_id": "m-ref",
                                 "content_type": "image/jpeg"}]}  # fmt: skip
        if name == "media_confirm":
            return {"results": [{"media_id": args["media_id"], "status": "confirmed"}]}
        if name == "generate_image_batch":
            self.made += 1
            return {"jobs": [{"index": 0, "job_id": _job(self.made), "status": "queued"}]}
        if name == "jobs_wait":
            job = args["jobs"][0]["job_id"]
            n = int(job[7])
            if n == self.fail_job:
                return {"jobs": [{"index": 0, "job_id": job, "status": "failed"}]}
            url = f"https://cdn.example/{n}.jpg"
            return {"jobs": [{"index": 0, "job_id": job, "status": "completed", "result_url": url}]}
        raise AssertionError(name)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == auth.TOKEN_URL:
            return httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3600})
        if url == MCP_URL:
            body = json.loads(request.content)
            if body["method"] == "notifications/initialized":
                return httpx.Response(202)
            if body["method"] == "initialize":
                result: dict[str, Any] = {"protocolVersion": "2025-06-18"}
            else:
                result = {"structuredContent": self._tool(body["params"]["name"],
                                                          body["params"]["arguments"])}  # fmt: skip
            msg = {"jsonrpc": "2.0", "id": body["id"], "result": result}
            return httpx.Response(200, text=f"event: message\ndata: {json.dumps(msg)}\n\n",
                                  headers={"content-type": "text/event-stream"})  # fmt: skip
        if url == "https://upload.example/put":
            return httpx.Response(200)
        if url.startswith("https://cdn.example/"):
            return httpx.Response(200, content=_jpeg(url))
        # 키 길(api.higgsfield.ai)
        body = json.loads(request.content) if request.content else None
        self.api.append((url, body))
        if url.endswith("/files/generate-upload-url"):
            urls = {"public_url": "https://cdn.example/ref.jpg"}
            return httpx.Response(200, json=urls | {"upload_url": "https://upload.example/put"})
        if url.startswith(f"{rr.API_HOST}/requests/"):
            n = url.split("/requests/")[1][0]
            images = [{"url": f"https://cdn.example/a{n}.jpg"}]
            return httpx.Response(200, json={"status": "completed", "images": images})
        if request.method == "POST":
            self.made += 1
            return httpx.Response(200, json={"request_id": f"{self.made}reqreqreq"})
        return httpx.Response(404)


class Google:
    def __init__(self) -> None:
        self.thumbnail_status = 200
        self.thumbnails: list[tuple[str, bytes]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == yt.TOKEN_URL:
            return httpx.Response(200, json={"access_token": "ya29.DUMMY"})
        if url.startswith(yt.THUMBNAIL_URL):
            self.thumbnails.append((request.url.params["videoId"], request.content))
            return httpx.Response(self.thumbnail_status)
        if url.startswith(yt.UPLOAD_URL):
            return httpx.Response(200, headers={"location": "https://upload.example/session1"})
        if url == "https://upload.example/session1":
            return httpx.Response(200, json={"id": "vid123"})
        return httpx.Response(404)


class Gate:
    def __init__(self) -> None:
        self.status = {ct.ACTION: "held", yt.PUBLISH_ACTION: "held"}
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def __call__(self, action, params, meta, evidence):  # noqa: ANN001, ANN204
        self.calls.append((action, dict(params), str(meta.idempotency_key)))
        status = self.status[action]
        extra: dict[str, Any] = {}
        if status == "held":
            extra = {"hold_reason": list(HoldReason)[0], "review_id": f"rev_{len(self.calls)}"}
        return ExecuteDecision(execution_id=f"ex_{len(self.calls)}", grade="C", status=status,
                               **extra)  # fmt: skip


def _copy(raw: Path, out: Path) -> bool:
    out.write_bytes(raw.read_bytes())
    return True


def _frame(video: Path, out: Path) -> bool:
    out.write_bytes(FRAME)
    return True


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_IMAGE_MODEL", "nano_banana_pro")
    monkeypatch.setenv(auth.CLIENT_ID_ENV, "DUMMY-PUBLIC-CLIENT")
    for name in (
        "MARKETING_HIGGSFIELD_API_KEY",
        "MARKETING_HIGGSFIELD_IMAGE_MODEL",
        "MARKETING_QUOTA_IMAGE_COUNT_MONTHLY",
        "MARKETING_MEDIA_KEY_OWNER",
    ):
        monkeypatch.delenv(name, raising=False)
    for key in yt.KEYS:
        monkeypatch.setenv(key, f"DUMMY-{key}")
    monkeypatch.setenv("AX_SECRET_STORE", "db")
    monkeypatch.setenv("AX_SECRET_MASTER_KEY", generate_master_key())
    monkeypatch.setenv("AX_SECRET_DB_FILE", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("AX_SECRET_CACHE_TTL", "0")
    resolver.reset_for_tests()
    auth.forget()
    hs.reset_cache()
    svc = resolver.service()
    svc.store_oauth_token("catbooks", auth.TOKEN_NAME, REFRESH, "test")  # 캣북스만 연결
    monkeypatch.setattr(yt, "_secret", lambda name: f"DUMMY-{name}")  # 유튜브 채널 연결됨
    store = cr.CreativeRecords(":memory:")
    cr.set_store(store)
    hf, google, gate = Higgsfield(), Google(), Gate()
    monkeypatch.setattr(
        rr, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(hf.handle))
    )
    monkeypatch.setattr(
        yt, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(google.handle))
    )
    monkeypatch.setattr(ct, "execute", gate)
    monkeypatch.setattr(yt, "execute", gate)
    monkeypatch.setattr(ct, "NORMALIZE", _copy)
    monkeypatch.setattr(ct, "FIRST_FRAME", _frame)
    monkeypatch.setattr(ct, "PROBE", lambda _p: (1920, 1080))

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(rr, "SLEEP", no_sleep)
    yt._status_cache.update(at=0.0, value=None)  # noqa: SLF001
    rid = store.add_sheet(SHEET, None, None)
    store.attach_storyboard(rid, "AI 직원이 출근한다", 3, BOARD)
    yield {"store": store, "hf": hf, "google": google, "gate": gate, "rid": rid,
           "dir": tmp_path, "ledger": MediaLedger(":memory:")}  # fmt: skip
    cr.set_store(None)
    auth.forget()
    hs.reset_cache()
    resolver.reset_for_tests()


def _with_video(env: dict[str, Any], part: str = "video") -> None:
    rid = env["rid"]
    name = "video.mp4" if part == "video" else "final.mp4"
    path = env["dir"] / "creative" / rid / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(VIDEO)
    render = env["store"].get_render(rid)
    render[part] = {"status": "done", "asset": name, "sha256": hashlib.sha256(VIDEO).hexdigest()}
    env["store"].set_render(rid, render)


def _run(coro_fn, company: str = "catbooks"):  # noqa: ANN001, ANN202
    async def go():  # noqa: ANN202
        out = await coro_fn()
        await rr.wait_idle()
        await yt.wait_idle()
        return out

    with company_scope.using(company):
        return asyncio.run(go())


def _approve_and_make(env: dict[str, Any]) -> dict[str, Any]:
    rid = env["rid"]
    held = _run(lambda: ct.request(env["ledger"], META, rid, "태윤"))
    assert held["status"] == "awaiting_approval"
    env["gate"].status[ct.ACTION] = "executed"
    _run(lambda: ct.check(env["ledger"], META, rid))
    return ct.get(rid)


# ---------------------------------------------------------------- 만들기
def test_three_candidates_after_human_approval_with_ai_mark(env: dict[str, Any]) -> None:
    _with_video(env)
    rid = env["rid"]
    held = _run(lambda: ct.request(env["ledger"], META, rid, "태윤"))
    # 사람 결정(C)에 올라가고, 승인 전에는 Higgsfield 만들기를 부르지 않는다
    assert held["status"] == "awaiting_approval" and held["review_id"] == "rev_1"
    assert "generate_image_batch" not in [n for n, _ in env["hf"].tools]
    action, params, key = env["gate"].calls[0]
    assert action == "generate_creative_thumbnails" and params["count"] == 3
    assert params["aspect_ratio"] == "9:16"  # Higgsfield 영상은 세로
    assert params["billing"] == "company_subscription" and params["credits_estimate"] == 6.0
    assert params["ai_generated"] is True and params["source_part"] == "video"
    # 같은 키로 다시 물어 승인되면 만든다
    env["gate"].status[ct.ACTION] = "executed"
    _run(lambda: ct.check(env["ledger"], META, rid))
    assert env["gate"].calls[1][2] == key
    done = ct.get(rid)
    assert done["status"] == "done" and done["route"] == "mcp"
    assert [c["kind"] for c in done["candidates"]] == ["closeup", "scene", "moment"]
    batches = [a for n, a in env["hf"].tools if n == "generate_image_batch"]
    assert len(batches) == 3
    first = batches[0]["requests"][0]["params"]
    assert first["aspect_ratio"] == "9:16" and first["model"] == "nano_banana_pro"
    assert first["medias"] == [{"value": "m-ref", "role": "image_references"}]  # 첫 프레임 참조
    assert "white-suited android" in first["prompt"] and "No text" in first["prompt"]
    assert "upper third" in first["prompt"]  # 세로 영상의 제목 자리
    prompts = [b["requests"][0]["params"]["prompt"] for b in batches]
    assert "empty desk at dawn" in prompts[1] and "high-fives" in prompts[2]
    # 저장: 회사 폴더 아래 그 기록 폴더 thumbs/, XMP AI 표시, meta.json 기록
    folder = env["dir"] / "creative" / rid / "thumbs"
    for c in done["candidates"]:
        path = folder / f"{c['media_id']}.jpg"
        assert ct.THUMB_ID.match(c["media_id"]) and c["is_ai_generated"] is True
        assert sns_media.sha256_file(path) == c["sha256"]
        assert 'catbooks:ai_generated="true"' in (sns_media.jpeg_xmp(path) or "")
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert len(meta["ai_label"]) == 3
    assert all(v["ai_generated"] and v["machine_mark"] for v in meta["ai_label"].values())
    assert ct.current_cover(rid)["media_id"] == done["candidates"][0]["media_id"]  # 기본값 첫 장


def test_wide_final_video_and_api_key_path(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 회사 계정 연결이 없는 회사는 기존 키 길로 만든다(우리 키라 이미지 월 예산이 필요)
    resolver.service().import_value(
        resolver.default_tenant(), "MARKETING_HIGGSFIELD_API_KEY", "kid:ksecret", "t"
    )
    monkeypatch.setenv("MARKETING_HIGGSFIELD_IMAGE_MODEL", "google/nano-banana/edit")
    monkeypatch.setenv("MARKETING_QUOTA_IMAGE_COUNT_MONTHLY", "100")
    with company_scope.using("nubi"):
        rid = cr.store().add_sheet(SHEET, None, None)
        cr.store().attach_storyboard(rid, "x", 3, BOARD)
        env["rid"] = rid
        env["store"] = cr.store()
    folder = env["dir"] / "companies" / "nubi" / "creative" / rid
    folder.mkdir(parents=True)
    (folder / "final.mp4").write_bytes(VIDEO)
    with company_scope.using("nubi"):
        render = cr.store().get_render(rid)
        render["final_video"] = {"status": "done", "asset": "final.mp4", "source": "upload",
                                 "sha256": hashlib.sha256(VIDEO).hexdigest()}  # fmt: skip
        cr.store().set_render(rid, render)
    env["gate"].status[ct.ACTION] = "executed"
    out = _run(lambda: ct.request(env["ledger"], META, rid, "태윤"), "nubi")
    assert out["route"] == "api" and out["aspect"] == "16:9"  # 가로 완성 영상에 맞춤
    with company_scope.using("nubi"):
        done = ct.get(rid)
    assert done["status"] == "done" and len(done["candidates"]) == 3
    assert all((folder / "thumbs" / f"{c['media_id']}.jpg").is_file() for c in done["candidates"])
    submits = [b for u, b in env["hf"].api if u.endswith("/google/nano-banana/edit")]
    assert len(submits) == 3 and submits[0]["aspect_ratio"] == "16:9"
    assert submits[0]["image_urls"] == ["https://cdn.example/ref.jpg"]
    assert "left third" in submits[0]["prompt"]
    assert env["hf"].tools == []  # 회사 계정(MCP)은 부르지 않았다


def test_without_any_connection_nothing_is_made(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    with company_scope.using("nubi"):  # 회사 계정 연결도 키도 없다
        rid = cr.store().add_sheet(SHEET, None, None)
        render = cr.store().get_render(rid)
        render["video"] = {"status": "done", "asset": "video.mp4", "sha256": "a" * 64}
        cr.store().set_render(rid, render)
    with pytest.raises(ct.ThumbError) as err:
        _run(lambda: ct.request(env["ledger"], META, rid, "태윤"), "nubi")
    assert err.value.code == "not_connected"
    with company_scope.using("nubi"):
        assert ct.get(rid)["status"] == "blocked"
    assert env["gate"].calls == [] and env["hf"].made == 0


def test_rejected_approval_makes_nothing_and_partial_failure_keeps_the_rest(
    env: dict[str, Any],
) -> None:
    _with_video(env)
    rid = env["rid"]
    _run(lambda: ct.request(env["ledger"], META, rid, "태윤"))
    env["gate"].status[ct.ACTION] = "rejected"
    _run(lambda: ct.check(env["ledger"], META, rid))
    assert ct.get(rid)["status"] == "rejected" and env["hf"].made == 0
    # 다시 요청 → 승인 → 두 번째 장만 실패해도 나머지는 남는다
    env["gate"].status[ct.ACTION] = "held"
    env["hf"].fail_job = 2
    state = _approve_and_make(env)
    assert state["status"] == "done" and len(state["candidates"]) == 2
    assert state["errors"] == ["mcp_job_failed"]


def test_credits_short_blocks_before_the_gate(env: dict[str, Any]) -> None:
    _with_video(env)
    env["hf"].credits = 5.0  # 한 장(2)은 되지만 세 장(6)은 모자란다
    with pytest.raises(ct.ThumbError) as err:
        _run(lambda: ct.request(env["ledger"], META, env["rid"], "태윤"))
    assert err.value.code == "credits_insufficient" and env["gate"].calls == []


def _req(skill: str, inp: dict[str, Any]) -> AgentRequest:
    return AgentRequest(meta=META, skill=skill, input=inp)


def test_reading_the_record_auto_requests_once_after_the_video(env: dict[str, Any]) -> None:
    rid = env["rid"]
    get = handlers.HANDLERS["creative_record_get"]
    out = _run(lambda: get(_req("creative_record_get", {"record_id": rid})))
    assert "thumbnails" not in out.output["render"]  # 영상 전에는 걸지 않는다
    _with_video(env)
    out = _run(lambda: get(_req("creative_record_get", {"record_id": rid})))
    thumbs = out.output["render"]["thumbnails"]
    assert thumbs["status"] == "awaiting_approval" and thumbs["params"]["auto_requested"] is True
    _run(lambda: get(_req("creative_record_get", {"record_id": rid})))
    held = [c for c in env["gate"].calls if c[0] == ct.ACTION]
    assert len(held) == 2 and held[0][2] == held[1][2]  # 다시 읽으면 같은 키로 묻기만 한다
    # 승인되면 기록을 읽을 때 만든다 → 목록에 썸네일 상태가 실린다
    env["gate"].status[ct.ACTION] = "executed"
    _run(lambda: get(_req("creative_record_get", {"record_id": rid})))
    items = _run(lambda: handlers.HANDLERS["creative_records_list"](
        _req("creative_records_list", {"limit": 5}))).output["items"]  # fmt: skip
    assert items[0]["render"]["thumbnails"] == "done"
    # 다시 만들기 skill: 후보가 있으면 regenerate일 때만
    req = handlers.HANDLERS["creative_thumbnails_request"]
    same = _run(lambda: req(_req("creative_thumbnails_request", {"record_id": rid})))
    assert same.output["thumbnails"]["round"] == 1
    again = _run(lambda: req(_req("creative_thumbnails_request",
                                  {"record_id": rid, "regenerate": True})))  # fmt: skip
    assert again.output["thumbnails"]["round"] == 2


# ---------------------------------------------------------------- 고르기
def test_select_only_candidates_and_other_company_is_not_found(env: dict[str, Any]) -> None:
    _with_video(env)
    rid = env["rid"]
    state = _approve_and_make(env)
    ids = [c["media_id"] for c in state["candidates"]]
    sel = handlers.HANDLERS["creative_thumbnail_select"]
    bad = _run(lambda: sel(_req("creative_thumbnail_select",
                                {"record_id": rid, "media_id": "thm_ffffffffffff"})))  # fmt: skip
    assert (bad.status, bad.outcome) == (TaskStatus.FAILED, "cover_not_candidate")
    ok = _run(lambda: sel(_req("creative_thumbnail_select",
                               {"record_id": rid, "media_id": ids[1], "who": "태윤"})))  # fmt: skip
    assert ok.output["changed"] is True and ok.output["cover_rev"] == state["cover_rev"] + 1
    assert ct.current_cover(rid)["media_id"] == ids[1]
    again = _run(lambda: sel(_req("creative_thumbnail_select",
                                  {"record_id": rid, "media_id": ids[1]})))  # fmt: skip
    assert again.output["changed"] is False
    with company_scope.using("nubi"), pytest.raises(ct.ThumbError) as err:
        ct.select(rid, ids[0], "남")
    assert err.value.code == "not_found"
    assert ct.cover_file(rid, ids[1]) is not None and ct.cover_file(rid, "thm_000000000000") is None


# ---------------------------------------------------------------- 유튜브
def _youtube_draft(env: dict[str, Any]) -> None:
    _run(lambda: yt.write_draft(env["rid"], lambda: None, "ctx"))


def test_youtube_hash_includes_cover_and_thumbnail_is_applied(env: dict[str, Any]) -> None:
    _with_video(env)
    rid = env["rid"]
    _youtube_draft(env)
    before = _run(lambda: yt.request_upload(META, rid, "태윤"))
    key_without = before["key"]
    assert "cover_token" not in before["params"]  # 후보 없는 기록은 옛 해시 그대로
    yt.reset_for_cover(rid)
    state = _approve_and_make(env)
    req = _run(lambda: yt.request_upload(META, rid, "태윤"))
    assert req["status"] == "awaiting_approval" and req["key"] != key_without
    first = state["candidates"][0]["media_id"]
    assert req["params"]["cover_media_id"] == first and ct.get(rid)["chosen"] == first
    assert req["params"]["cover_token"].startswith(f"{first}:{state['candidates'][0]['sha256']}:")
    env["gate"].status[yt.PUBLISH_ACTION] = "executed"
    _run(lambda: yt.check_upload(META, rid))
    done = yt._get(rid)  # noqa: SLF001
    assert done["status"] == "done" and done["video_id"] == "vid123"
    assert done["thumbnail"] == {"media_id": first, "applied": True, "reason": None}
    path = env["dir"] / "creative" / rid / "thumbs" / f"{first}.jpg"
    assert env["google"].thumbnails == [("vid123", path.read_bytes())]
    # 올린 뒤에는 잠긴다(고르기·다시 만들기 모두)
    other = state["candidates"][1]["media_id"]
    with pytest.raises(ct.ThumbError) as err:
        ct.select(rid, other, "태윤")
    assert err.value.code == "cover_locked"
    with pytest.raises(ct.ThumbError) as err2:
        _run(lambda: ct.request(env["ledger"], META, rid, "태윤", regenerate=True))
    assert err2.value.code == "cover_locked"


def test_channel_without_custom_thumbnails_keeps_the_video(env: dict[str, Any]) -> None:
    _with_video(env)
    rid = env["rid"]
    _approve_and_make(env)
    _youtube_draft(env)
    env["gate"].status[yt.PUBLISH_ACTION] = "executed"
    env["google"].thumbnail_status = 403  # 채널 미인증
    _run(lambda: yt.request_upload(META, rid, "태윤"))
    done = yt._get(rid)  # noqa: SLF001
    assert done["status"] == "done" and done["video_id"] == "vid123"
    assert done["thumbnail"]["applied"] is False
    assert done["thumbnail"]["reason"] == "thumbnail_http_403"


def test_changing_cover_while_waiting_needs_a_new_approval(env: dict[str, Any]) -> None:
    _with_video(env)
    rid = env["rid"]
    state = _approve_and_make(env)
    _youtube_draft(env)
    first = _run(lambda: yt.request_upload(META, rid, "태윤"))
    assert first["status"] == "awaiting_approval"
    picked = ct.select(rid, state["candidates"][2]["media_id"], "태윤")
    assert picked["approvals_reset"] == ["youtube"]
    reset = yt._get(rid)  # noqa: SLF001
    assert (reset["status"], reset["error"], reset["key"]) == ("draft", "cover_changed", None)
    second = _run(lambda: yt.request_upload(META, rid, "태윤"))
    assert second["key"] != first["key"]  # 새 승인
    # A→B→A로 되돌려도 해시가 다르다(cover_rev)
    ct.select(rid, state["candidates"][0]["media_id"], "태윤")
    third = _run(lambda: yt.request_upload(META, rid, "태윤"))
    assert third["key"] not in (first["key"], second["key"])


def test_cover_file_changed_after_approval_is_not_uploaded(env: dict[str, Any]) -> None:
    _with_video(env)
    rid = env["rid"]
    state = _approve_and_make(env)
    _youtube_draft(env)
    _run(lambda: yt.request_upload(META, rid, "태윤"))
    path = env["dir"] / "creative" / rid / "thumbs" / f"{state['candidates'][0]['media_id']}.jpg"
    path.write_bytes(b"\xff\xd8swapped")  # 승인 뒤 그림이 바뀌었다
    env["gate"].status[yt.PUBLISH_ACTION] = "executed"
    _run(lambda: yt.check_upload(META, rid))
    cur = yt._get(rid)  # noqa: SLF001
    assert (cur["status"], cur["error"]) == ("failed", "cover_changed")
    assert env["google"].thumbnails == []
