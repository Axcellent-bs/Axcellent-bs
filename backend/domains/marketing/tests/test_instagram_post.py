"""제작 기록·의뢰 영상 → AI 인스타 캡션 초안 → 사람 승인(C) → AI 라벨 영상 릴스 게시.

가짜 Graph API(httpx MockTransport)·가짜 관문·가짜 LLM·가짜 라벨러만 쓴다. 토큰은 더미 값이다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from ax_contracts.schemas import ExecuteDecision, HoldReason, RequestMeta
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import instagram_post as ip
from marketing_agent import request_video_publish, sns, sns_media
from marketing_agent.sns_publish import publisher
from marketing_agent.sns_publish.instagram_graph import GraphInstagramAdapter

META = RequestMeta(
    context_id="ctx_test_ig",
    tenant_id="catbooks",
    token="t",
    idempotency_key="k0",
    deadline=datetime.now(UTC) + timedelta(seconds=60),
)
SHEET = {"summary_ko": "흰 슈트 안드로이드", "prompt_en": "sheet", "negative_prompt_en": "x"}
BOARD = {"title_ko": "AXcellent 출근 첫날", "sheet_prompt_en": "board", "shots": []}
VIDEO = b"\x00\x00\x00\x18ftypmp42" + bytes(64)
TOKEN = "DUMMY-IG-TOKEN-0001"
USER = "17841400000000001"
CTX = "ctx_wr_abc123"
PLAN = "AI 직원이 첫 출근을 한다\n\n재무 AI가 장부를 정리한다"


class Graph:
    """가짜 Instagram Graph API. 받은 호출을 남긴다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.status = "FINISHED"
        self.publish: httpx.Response | Exception | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        if request.method == "POST":
            body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        else:
            body = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.calls.append((request.method, f"{url.scheme}://{url.netloc}{url.path}", body))
        path = url.path.rstrip("/")
        if path.endswith(f"/{USER}/media"):
            return httpx.Response(200, json={"id": "c1"})
        if path.endswith("/c1"):
            return httpx.Response(200, json={"status_code": self.status})
        if path.endswith(f"/{USER}/media_publish"):
            if isinstance(self.publish, Exception):
                raise self.publish
            return self.publish or httpx.Response(200, json={"id": "m1"})
        if path.endswith(f"/{USER}/content_publishing_limit"):
            return httpx.Response(
                200, json={"data": [{"quota_usage": 1, "config": {"quota_total": 50}}]}
            )
        if path.endswith("/m1"):
            if body.get("fields") == "is_ai_generated":
                return httpx.Response(200, json={"is_ai_generated": True})
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/ABC/"})
        return httpx.Response(404)

    def posted(self, tail: str) -> list[dict[str, str]]:
        return [b for m, u, b in self.calls if m == "POST" and u.endswith(tail)]


class Gate:
    def __init__(self, status: str = "held") -> None:
        self.status = status
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def __call__(self, action, params, meta, evidence):  # noqa: ANN001, ANN204
        self.calls.append((action, params, str(meta.idempotency_key)))
        extra: dict[str, Any] = {}
        if self.status == "held":
            extra = {"hold_reason": list(HoldReason)[0], "review_id": "rev_ig"}
        return ExecuteDecision(execution_id="ex_ig", grade="C", status=self.status, **extra)


class FakeLlm:
    seen: dict[str, Any] = {}

    async def structured(self, prompt, model, untrusted, context_id):  # noqa: ANN001, ANN201
        FakeLlm.seen = dict(untrusted)
        return model(caption="AI 직원이 출근했어요\naxcellent.cloud", hashtags=["AI직원", "#릴스"])


def fake_labeler(source: Path, out: Path, *, generator_model: str, media_kind: str) -> None:
    """sns_label.apply_label과 같은 결과 모양(labeled.mp4 + meta.json ai_label)."""
    out.mkdir(parents=True, exist_ok=True)
    labeled = out / "labeled.mp4"
    labeled.write_bytes(Path(source).read_bytes() + b"AI-LABEL")
    meta = {
        "ai_label": {
            "label_overlay": True,
            "labeled_file": "labeled.mp4",
            "labeled_sha256": sns_media.sha256_file(labeled),
            "source_sha256": sns_media.sha256_file(Path(source)),
            "ai_generator_model": generator_model,
        }
    }
    (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(media))
    monkeypatch.setenv("MARKETING_SNS_PROVIDER", "instagram")
    monkeypatch.setenv("MARKETING_IG_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("MARKETING_IG_USER_ID", USER)
    monkeypatch.setenv("MARKETING_IG_MEDIA_BASE_URL", "https://media.example")
    for name in ("AX_SECRET_STORE", "MARKETING_IG_API_BASE_URL", "MARKETING_IG_API_VERSION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SNS_PENDING_DB", str(tmp_path / "pending.db"))
    store = cr.CreativeRecords(str(tmp_path / "creative.db"))
    cr.set_store(store)
    graph, gate, hooks = Graph(), Gate(), []
    monkeypatch.setattr(
        ip, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(graph.handle))
    )
    monkeypatch.setattr(ip, "execute", gate)
    monkeypatch.setattr(ip, "LABELER", fake_labeler)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(ip, "SLEEP", no_sleep)
    monkeypatch.setattr(publisher, "PUBLISHED_HOOKS", [lambda row, mid, res: hooks.append(
        (row.post_draft_id, row.tenant_id, mid, res.get("permalink")))])  # fmt: skip
    publisher.reset_runtime()
    rid = store.add_sheet(SHEET, None, None)
    store.attach_storyboard(rid, "AI 직원이 출근한다", 6, BOARD)
    sns_store = sns.SnsStore(str(tmp_path / "sns.db"))
    yield {"store": store, "graph": graph, "gate": gate, "hooks": hooks, "rid": rid,
           "media": media.resolve(), "sns": sns_store, "dir": tmp_path}  # fmt: skip
    publisher.reset_runtime()
    cr.set_store(None)


def _with_video(env: dict[str, Any], part: str = "video") -> Path:
    rid = env["rid"]
    path = (
        rr.asset_path(rid, "video")
        if part == "video"
        else rr.asset_path(rid, "video").parent / "final.mp4"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(VIDEO)
    render = env["store"].get_render(rid)
    render[part] = {
        "status": "done",
        "asset": path.name,
        "sha256": hashlib.sha256(VIDEO).hexdigest(),
    }
    env["store"].set_render(rid, render)
    return path


def _run(coro_fn):  # noqa: ANN001, ANN202
    async def go():  # noqa: ANN202
        out = await coro_fn()
        await ip.wait_idle()
        return out

    return asyncio.run(go())


def _record(env: dict[str, Any]) -> ip.Target:
    return ip.Target("record", env["rid"])


# ---------------------------------------------------------------- 제작 기록 영상
def test_ai_draft_keeps_the_ai_notice_and_adds_hashtags(env: dict[str, Any]) -> None:
    _with_video(env)
    draft = _run(lambda: ip.write_record_draft(env["rid"], FakeLlm, "ctx"))
    assert (draft["status"], draft["source"], draft["part"]) == ("draft", "ai", "video")
    assert ip.AI_NOTICE in draft["caption"] and "#AXcellent" in draft["caption"]
    assert "#AI직원" in draft["caption"] and draft["hashtags"] == ["AI직원", "릴스"]
    assert FakeLlm.seen["storyboard_title"] == "AXcellent 출근 첫날"
    edited = _run(
        lambda: ip.write_record_draft(env["rid"], FakeLlm, "ctx", edits={"caption": "새 캡션"})
    )
    assert edited["source"] == "human_edit" and edited["caption"].startswith("새 캡션")
    assert ip.AI_NOTICE in edited["caption"]  # 사람이 지워도 다시 붙는다


def test_without_llm_a_template_caption_is_written(env: dict[str, Any]) -> None:
    draft = _run(lambda: ip.write_record_draft(env["rid"], lambda: None, "ctx"))
    assert draft["source"] == "template" and "AXcellent" in draft["caption"]
    assert draft["part"] is None  # 아직 영상이 없다
    with pytest.raises(ip.InstagramError, match="video_not_ready"):
        _run(lambda: ip.request_record(META, env["rid"], "민지"))


def test_request_is_held_then_posts_the_labeled_reel_after_approval(env: dict[str, Any]) -> None:
    source = _with_video(env)
    _run(lambda: ip.write_record_draft(env["rid"], FakeLlm, "ctx"))
    held = _run(lambda: ip.request_record(META, env["rid"], "민지"))
    assert (held["status"], held["review_id"]) == ("awaiting_approval", "rev_ig")
    action, params, key = env["gate"].calls[0]
    labeled = source.parent / "instagram" / "video" / "labeled.mp4"
    assert action == ip.PUBLISH_ACTION and labeled.is_file()
    assert params["video_sha256"] == sns_media.sha256_file(labeled)
    assert params["is_ai_generated"] is True and params["ai_labeled"] is True
    assert params["video_sha256"] != hashlib.sha256(VIDEO).hexdigest()  # 원본이 아니라 라벨 영상
    assert env["graph"].calls == []  # 승인 전에는 아무것도 보내지 않는다

    again = _run(lambda: ip.request_record(META, env["rid"], "민지"))  # 두 번 눌러도 그대로
    assert again["status"] == "awaiting_approval" and len(env["gate"].calls) == 1

    env["gate"].status = "executed"
    _run(lambda: ip.check_record(META, env["rid"]))
    done = ip.get(_record(env))
    assert done["status"] == "done", done
    assert done["permalink"] == "https://www.instagram.com/reel/ABC/" and done["media_id"] == "m1"
    assert env["gate"].calls[-1][2] == key  # 같은 키로 다시 물었다
    container = env["graph"].posted(f"/{USER}/media")[0]
    assert container["media_type"] == "REELS" and container["is_ai_generated"] == "true"
    assert container["video_url"] == "https://media.example/labeled.mp4"
    assert container["caption"] == done["caption"] and container["access_token"] == TOKEN
    assert len(env["graph"].posted("/media_publish")) == 1
    assert env["hooks"] == [(env["rid"], "catbooks", "m1", done["permalink"])]
    assert TOKEN not in json.dumps(done)
    with pytest.raises(ip.InstagramError, match="already_requested"):
        _run(lambda: ip.write_record_draft(env["rid"], FakeLlm, "ctx", regenerate=True))


def test_refusals_before_asking_a_human(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ip.InstagramError, match="not_found"):
        _run(lambda: ip.request_record(META, "CR-000000000000", "민지"))
    _with_video(env)
    with pytest.raises(ip.InstagramError, match="draft_missing"):
        _run(lambda: ip.request_record(META, env["rid"], "민지"))
    _run(lambda: ip.write_record_draft(env["rid"], FakeLlm, "ctx"))
    monkeypatch.delenv("MARKETING_IG_ACCESS_TOKEN")
    with pytest.raises(ip.InstagramError, match="instagram_not_connected"):
        _run(lambda: ip.request_record(META, env["rid"], "민지"))
    assert env["gate"].calls == [] and env["graph"].calls == []


def test_label_failure_never_asks_or_posts(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from marketing_agent import sns_label

    def broken(*_: Any, **__: Any) -> None:
        raise sns_label.LabelError("ffmpeg_not_found")

    monkeypatch.setattr(ip, "LABELER", broken)
    _with_video(env)
    _run(lambda: ip.write_record_draft(env["rid"], FakeLlm, "ctx"))
    with pytest.raises(ip.InstagramError, match="label_failed"):
        _run(lambda: ip.request_record(META, env["rid"], "민지"))
    assert env["gate"].calls == []


def test_rejected_review_posts_nothing(env: dict[str, Any]) -> None:
    _with_video(env)
    _run(lambda: ip.write_record_draft(env["rid"], FakeLlm, "ctx"))
    _run(lambda: ip.request_record(META, env["rid"], "민지"))
    env["gate"].status = "rejected"
    _run(lambda: ip.check_record(META, env["rid"]))
    cur = ip.get(_record(env))
    assert (cur["status"], cur["error"]) == ("rejected", "gate_rejected")
    assert env["graph"].calls == [] and env["hooks"] == []


def test_file_changed_after_approval_is_not_posted(env: dict[str, Any]) -> None:
    source = _with_video(env)
    _run(lambda: ip.write_record_draft(env["rid"], FakeLlm, "ctx"))
    _run(lambda: ip.request_record(META, env["rid"], "민지"))
    (source.parent / "instagram" / "video" / "labeled.mp4").write_bytes(b"other")
    env["gate"].status = "executed"
    _run(lambda: ip.check_record(META, env["rid"]))
    assert ip.get(_record(env))["error"] == "video_changed"
    assert env["graph"].calls == []


def test_halted_account_is_not_posted(env: dict[str, Any]) -> None:
    _with_video(env)
    _run(lambda: ip.write_record_draft(env["rid"], FakeLlm, "ctx"))
    _run(lambda: ip.request_record(META, env["rid"], "민지"))
    publisher.store().halt_set("ig_spam_suspected", "other")
    env["gate"].status = "executed"
    _run(lambda: ip.check_record(META, env["rid"]))
    assert ip.get(_record(env))["error"] == "publish_halted"
    assert env["graph"].posted("/media") == []


def test_lost_publish_response_is_not_sent_again(env: dict[str, Any]) -> None:
    _with_video(env)
    _run(lambda: ip.write_record_draft(env["rid"], FakeLlm, "ctx"))
    env["gate"].status = "executed"
    env["graph"].publish = httpx.ReadTimeout("lost")
    _run(lambda: ip.request_record(META, env["rid"], "민지"))
    cur = ip.get(_record(env))
    assert (cur["status"], cur["error"]) == ("failed", "publish_response_lost")
    assert len([c for c in env["graph"].calls if c[1].endswith("/media_publish")]) == 1


def test_graph_error_is_classified_and_spam_halts_publishing(env: dict[str, Any]) -> None:
    _with_video(env)
    _run(lambda: ip.write_record_draft(env["rid"], FakeLlm, "ctx"))
    env["gate"].status = "executed"
    env["graph"].publish = httpx.Response(
        400, json={"error": {"code": 4, "error_subcode": 2207051}}
    )
    _run(lambda: ip.request_record(META, env["rid"], "민지"))
    assert ip.get(_record(env))["error"] == "ig_spam_suspected"
    assert publisher._halted()  # noqa: SLF001 - SNS 게시도 같이 멈춘다


# ---------------------------------------------------------------- 의뢰로 만든 영상
def _draft() -> sns.SnsPostDraft:
    caption = sns._compose(PLAN, "reel")  # noqa: SLF001
    return sns.SnsPostDraft(
        post_draft_id="spd_a",
        source_kind="brief",
        source_ref="prop_1",
        platform="instagram",
        media_kind="reel",
        caption=caption,
        hashtags=[],
        caption_sha256=sns._sha(caption),  # noqa: SLF001
        required_text=[PLAN, sns.DEMO_TEXT, sns.TERMS_TEXT],
        context_id=CTX,
    )


def _with_request_video(env: dict[str, Any]) -> sns.SnsPostDraft:
    draft = _draft()
    folder = env["media"] / "catbooks" / CTX / "med_1"
    folder.mkdir(parents=True, exist_ok=True)
    labeled = folder / "labeled.mp4"
    labeled.write_bytes(b"labeled-video-with-ai-mark")
    sha = sns_media.sha256_file(labeled)
    meta = {"media_id": "med_1", "media_kind": "video", "post_draft_id": draft.post_draft_id,
            "generated_at": "2026-11-23T01:00:00+00:00", "prompt": "Scene 1: office.",
            "ai_label": {"label_overlay": True, "labeled_sha256": sha, "labeled_file": "labeled.mp4"}}  # fmt: skip
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    linked = draft.model_copy(
        update={"asset_ref": sns_media.asset_ref_for(labeled), "asset_sha256": sha}
    )
    env["sns"].save(linked)
    return linked


def test_request_video_posts_after_approval_and_registers_performance(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []

    async def register(row: Any, *, drafts: Any = None, send: Any = None) -> dict[str, Any]:
        seen.append((row.post_draft_id, row.result["permalink"], drafts is env["sns"]))
        return {"registered": True, "campaign_id": "wr_abc123", "video_id": "v1"}

    monkeypatch.setattr(request_video_publish, "register_published", register)
    env["sns"].save(_draft())  # 라벨 영상이 아직 없는 초안
    with pytest.raises(ip.InstagramError, match="video_not_ready"):
        _run(lambda: ip.write_request_draft(env["sns"], "spd_a", FakeLlm, "ctx"))
    _with_request_video(env)
    draft = _run(lambda: ip.write_request_draft(env["sns"], "spd_a", FakeLlm, "ctx"))
    assert (draft["source"], draft["request_id"], draft["part"]) == (
        "ai",
        "wr_abc123",
        "labeled_video",
    )
    assert FakeLlm.seen["plan_text"].startswith("AI 직원이 첫 출근을 한다")
    held = _run(lambda: ip.request_request(META, env["sns"], "spd_a", "민지"))
    assert held["status"] == "awaiting_approval"
    assert env["gate"].calls[0][1]["request_id"] == "wr_abc123"
    items = ip.attach_states([{"post_draft_id": "spd_a"}, {"post_draft_id": "spd_b"}])
    assert items[0]["instagram"]["status"] == "awaiting_approval" and items[1]["instagram"] is None

    env["gate"].status = "executed"

    async def go() -> None:
        await ip.refresh_requests(META)
        await ip.wait_idle()

    from marketing_agent import handlers

    monkeypatch.setattr(handlers.STATE, "sns", env["sns"])
    asyncio.run(go())
    done = ip.get(ip.Target("request", "spd_a"))
    assert done["status"] == "done" and done["permalink"].startswith("https://www.instagram.com/")
    assert done["performance"]["registered"] is True
    assert seen == [("spd_a", done["permalink"], True)]
    assert env["hooks"][0][0] == "spd_a"
    container = env["graph"].posted(f"/{USER}/media")[0]
    assert container["is_ai_generated"] == "true" and container["media_type"] == "REELS"


def test_other_company_cannot_see_or_post_a_request_video(env: dict[str, Any]) -> None:
    _with_request_video(env)
    _run(lambda: ip.write_request_draft(env["sns"], "spd_a", FakeLlm, "ctx"))
    other_sns = sns.SnsStore(str(env["dir"] / "sns.db"), company_id="axcellent")
    other = cr.CreativeRecords(str(env["dir"] / "creative.db"), company_id="axcellent")
    cr.set_store(other)
    assert other.request_instagram_all() == {}
    with pytest.raises(ip.InstagramError, match="not_found"):
        _run(lambda: ip.request_request(META, other_sns, "spd_a", "x"))
    with pytest.raises(ip.InstagramError, match="not_found"):
        _run(lambda: ip.write_request_draft(other_sns, "spd_a", FakeLlm, "ctx"))
    assert env["gate"].calls == []


# ---------------------------------------------------------------- 로그인 연결 어댑터
@pytest.fixture
def login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AX_TENANT", "catbooks")
    monkeypatch.setenv("AX_COMPANY_AI_DIR", str(tmp_path / "company_ai"))
    monkeypatch.setenv("MARKETING_IG_OAUTH_REFRESH_TOKEN", "DUMMY-LOGIN-TOKEN")
    monkeypatch.setenv("MARKETING_IG_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("MARKETING_IG_USER_ID", USER)
    monkeypatch.setenv("MARKETING_IG_API_BASE_URL", "https://graph.facebook.com")
    monkeypatch.setenv("MARKETING_IG_API_VERSION", "v27.0")
    folder = tmp_path / "company_ai" / "catbooks"
    folder.mkdir(parents=True)
    return folder


def test_adapter_uses_the_login_link_with_the_judged_api(login: Path) -> None:
    info = login / "instagram_login.json"
    info.write_text(json.dumps({"kind": "ig", "user_id": "999"}), encoding="utf-8")
    adapter = GraphInstagramAdapter.from_env()
    assert adapter.via == "login" and adapter._token == "DUMMY-LOGIN-TOKEN"  # noqa: SLF001
    assert adapter._user_id == "999" and adapter._root == "https://graph.instagram.com"  # noqa: SLF001
    info.write_text(
        json.dumps({"kind": "fb", "api_version": "v21.0", "user_id": "998"}), encoding="utf-8"
    )
    adapter = GraphInstagramAdapter.from_env()
    assert adapter._root == "https://graph.facebook.com/v21.0" and adapter._user_id == "998"  # noqa: SLF001


def test_adapter_falls_back_to_the_manual_token(
    login: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MARKETING_IG_OAUTH_REFRESH_TOKEN")
    (login / "instagram_login.json").write_text(
        json.dumps({"kind": "ig", "user_id": "999"}), encoding="utf-8"
    )
    adapter = GraphInstagramAdapter.from_env()
    assert adapter.via == "manual" and adapter._token == TOKEN  # noqa: SLF001
    assert adapter._root == "https://graph.facebook.com/v27.0" and adapter._user_id == USER  # noqa: SLF001
