"""의뢰로 만든 영상(wr_…) → AI 유튜브 초안 → 사람 승인(C) → AI 라벨 영상 올리기.

가짜 구글 API·가짜 관문·가짜 LLM. 라벨 확인은 목록·미리보기와 같은 기준(`meta.json` 기록·해시)이다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_contracts.schemas import AgentRequest, ExecuteDecision, HoldReason, RequestMeta, TaskStatus
from marketing_agent import creative_records as cr
from marketing_agent import handlers, sns, sns_media
from marketing_agent import request_video_youtube as rvy
from marketing_agent import youtube as yt

CTX = "ctx_wr_abc123"
DRAFT_ID = "spd_a"
PLAN = "AI 직원이 첫 출근을 한다\n\n재무 AI가 장부를 정리한다\n\n기획팀 제안 기반"
LABELED = b"labeled-video-with-ai-mark"
META = RequestMeta(
    context_id="ctx_test_rvy",
    token="t",
    idempotency_key="k0",
    deadline=datetime.now(UTC) + timedelta(seconds=60),
)


class Google:
    def __init__(self) -> None:
        self.uploaded: list[dict[str, Any]] = []
        self.bytes: list[bytes] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == yt.TOKEN_URL:
            return httpx.Response(200, json={"access_token": "ya29.DUMMY"})
        if url.startswith(yt.UPLOAD_URL):
            self.uploaded.append(json.loads(request.content))
            return httpx.Response(200, headers={"location": "https://upload.example/s1"})
        if url == "https://upload.example/s1":
            self.bytes.append(request.content)
            return httpx.Response(200, json={"id": "vidRQ1"})
        return httpx.Response(404)


class Gate:
    def __init__(self, status: str = "held") -> None:
        self.status = status
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def __call__(self, action, params, meta, evidence):  # noqa: ANN001, ANN204
        self.calls.append((action, params, str(meta.idempotency_key)))
        extra: dict[str, Any] = {}
        if self.status == "held":
            extra = {"hold_reason": list(HoldReason)[0], "review_id": "rev_rvy"}
        return ExecuteDecision(execution_id="ex_rvy", grade="C", status=self.status, **extra)


class FakeLlm:
    seen: dict[str, Any] = {}

    async def structured(self, prompt, model, untrusted, context_id):  # noqa: ANN001, ANN201
        FakeLlm.seen = dict(untrusted)
        return model(title="AI 직원의 첫 출근", description="AXcellent AI 직원 소개", tags=["AI"])


def _draft(ctx: str = CTX, n: str = "a") -> sns.SnsPostDraft:
    caption = sns._compose(PLAN, "reel")  # noqa: SLF001
    return sns.SnsPostDraft(
        post_draft_id=f"spd_{n}",
        source_kind="brief",
        source_ref="prop_1",
        platform="instagram",
        media_kind="reel",
        caption=caption,
        hashtags=[],
        caption_sha256=sns._sha(caption),  # noqa: SLF001
        required_text=[PLAN, sns.DEMO_TEXT, sns.TERMS_TEXT],
        context_id=ctx,
    )


def _with_labeled_video(env: dict[str, Any], draft: sns.SnsPostDraft) -> Path:
    """생성 결과(meta.json)와 AI 라벨 입힌 파일을 만들고 초안에 연결한다(attach_label 뒤 모양)."""
    folder = env["media"] / "catbooks" / draft.context_id / "med_1"
    folder.mkdir(parents=True, exist_ok=True)
    labeled = folder / "labeled.mp4"
    labeled.write_bytes(LABELED)
    sha = sns_media.sha256_file(labeled)
    meta = {
        "media_id": "med_1",
        "media_kind": "video",
        "post_draft_id": draft.post_draft_id,
        "generated_at": "2026-11-23T01:00:00+00:00",
        "duration_s": 8,
        "prompt": "Scene 1: an android walks into the office. Scene 2: it files the ledger.",
        "ai_label": {"label_overlay": True, "labeled_sha256": sha, "labeled_file": "labeled.mp4"},
    }
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    env["sns"].update_meta(
        [
            draft.model_copy(
                update={"asset_ref": sns_media.asset_ref_for(labeled), "asset_sha256": sha}
            )
        ]
    )
    return labeled


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(media))
    for key in yt.KEYS:
        monkeypatch.setenv(key, f"DUMMY-{key}")
    monkeypatch.delenv("AX_SECRET_STORE", raising=False)
    # 회사 격리를 같은 파일로 확인하려고 파일 DB를 쓴다
    store = cr.CreativeRecords(str(tmp_path / "creative.db"))
    cr.set_store(store)
    sns_store = sns.SnsStore(str(tmp_path / "sns.db"))
    draft = _draft()
    sns_store.save(draft)
    google = Google()
    gate = Gate()
    monkeypatch.setattr(
        yt, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(google.handle))
    )
    monkeypatch.setattr(rvy, "execute", gate)
    yield {
        "store": store,
        "sns": sns_store,
        "draft": draft,
        "google": google,
        "gate": gate,
        "media": media.resolve(),
        "dir": tmp_path,
    }
    cr.set_store(None)


def _run(coro_fn):  # noqa: ANN001, ANN202
    async def go():  # noqa: ANN202
        out = await coro_fn()
        await yt.wait_idle()
        return out

    return asyncio.run(go())


def test_no_draft_without_a_labeled_video(env: dict[str, Any]) -> None:
    with pytest.raises(rvy.YoutubeError, match="video_not_ready"):
        _run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, FakeLlm, "ctx"))
    assert rvy.get(DRAFT_ID) == {}
    with pytest.raises(rvy.YoutubeError, match="not_found"):
        _run(lambda: rvy.write_draft(env["sns"], "spd_missing", FakeLlm, "ctx"))


def test_ai_draft_uses_plan_and_scenes_and_keeps_the_ai_notice(env: dict[str, Any]) -> None:
    _with_labeled_video(env, env["draft"])
    draft = _run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, FakeLlm, "ctx"))
    assert (draft["status"], draft["source"], draft["privacy"]) == ("draft", "ai", "unlisted")
    assert draft["request_id"] == "wr_abc123" and draft["part"] == "labeled_video"
    assert draft["title"] == "AI 직원의 첫 출근" and yt.AI_NOTICE in draft["description"]
    assert FakeLlm.seen["plan_text"] == PLAN and "Scene 2" in FakeLlm.seen["video_prompt"]
    edited = _run(
        lambda: rvy.write_draft(
            env["sns"], DRAFT_ID, FakeLlm, "ctx", edits={"title": "새 제목", "privacy": "public"}
        )
    )
    assert (edited["title"], edited["source"], edited["privacy"]) == (
        "새 제목",
        "human_edit",
        "public",
    )
    assert yt.AI_NOTICE in edited["description"]


def test_without_llm_the_plan_text_becomes_the_draft(env: dict[str, Any]) -> None:
    _with_labeled_video(env, env["draft"])
    draft = _run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, lambda: None, "ctx"))
    assert draft["source"] == "template"
    assert draft["title"].startswith("AI 직원이 첫 출근을 한다") and "AXcellent" in draft["title"]
    assert "재무 AI가 장부를 정리한다" in draft["description"]


def test_held_then_approved_uploads_the_labeled_file(env: dict[str, Any]) -> None:
    _with_labeled_video(env, env["draft"])
    _run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, FakeLlm, "ctx"))
    held = _run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    assert (held["status"], held["review_id"]) == ("awaiting_approval", "rev_rvy")
    action, params, key = env["gate"].calls[0]
    assert action == "publish_youtube" and params["request_id"] == "wr_abc123"
    assert params["ai_generated"] is True and params["ai_labeled"] is True
    assert params["video_sha256"] == sns_media.sha256_file(
        env["media"] / "catbooks" / CTX / "med_1" / "labeled.mp4"
    )
    assert env["google"].uploaded == []
    # 다시 요청해도 새 승인을 만들지 않는다
    again = _run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    assert again["status"] == "awaiting_approval" and len(env["gate"].calls) == 1

    env["gate"].status = "executed"
    _run(lambda: rvy.check_upload(META, env["sns"], DRAFT_ID))
    assert env["gate"].calls[-1][2] == key  # 같은 키로 다시 묻는다
    done = rvy.get(DRAFT_ID)
    assert (done["status"], done["video_id"], done["url"]) == (
        "done",
        "vidRQ1",
        "https://youtu.be/vidRQ1",
    )
    assert env["google"].bytes == [LABELED]  # AI 라벨 입힌 파일 그대로
    body = env["google"].uploaded[0]
    assert body["status"]["containsSyntheticMedia"] is True
    assert body["snippet"]["title"] == "AI 직원의 첫 출근"
    with pytest.raises(rvy.YoutubeError, match="already_requested"):
        _run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, FakeLlm, "ctx", regenerate=True))


def test_changed_file_after_approval_is_not_uploaded(env: dict[str, Any]) -> None:
    labeled = _with_labeled_video(env, env["draft"])
    _run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, FakeLlm, "ctx"))
    _run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    labeled.write_bytes(b"swapped-after-approval")
    env["gate"].status = "executed"
    _run(lambda: rvy.check_upload(META, env["sns"], DRAFT_ID))
    cur = rvy.get(DRAFT_ID)
    assert cur["status"] == "failed" and cur["error"] == "video_not_ready"
    assert env["google"].bytes == []


def test_request_needs_draft_and_connection(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_labeled_video(env, env["draft"])
    with pytest.raises(rvy.YoutubeError, match="draft_missing"):
        _run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    _run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, lambda: None, "ctx"))
    monkeypatch.delenv(yt.KEYS[2])
    with pytest.raises(rvy.YoutubeError, match="youtube_not_connected"):
        _run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    assert env["gate"].calls == []


def test_rejected_review_is_recorded(env: dict[str, Any]) -> None:
    _with_labeled_video(env, env["draft"])
    _run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, FakeLlm, "ctx"))
    _run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    env["gate"].status = "rejected"
    _run(lambda: rvy.check_upload(META, env["sns"], DRAFT_ID))
    cur = rvy.get(DRAFT_ID)
    assert (cur["status"], cur["error"]) == ("rejected", "gate_rejected")
    assert env["google"].uploaded == []


def test_other_company_cannot_see_or_upload(env: dict[str, Any]) -> None:
    _with_labeled_video(env, env["draft"])
    _run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, FakeLlm, "ctx"))
    # 같은 DB 파일, 다른 회사 칸
    other_sns = sns.SnsStore(str(env["dir"] / "sns.db"), company_id="axcellent")
    other_records = cr.CreativeRecords(str(env["dir"] / "creative.db"), company_id="axcellent")
    assert other_sns.get(DRAFT_ID) is None
    cr.set_store(other_records)
    assert rvy.get(DRAFT_ID) == {}
    assert other_records.request_youtube_all() == {}
    with pytest.raises(rvy.YoutubeError, match="not_found"):
        _run(lambda: rvy.write_draft(other_sns, DRAFT_ID, FakeLlm, "ctx"))
    with pytest.raises(rvy.YoutubeError, match="not_found"):
        _run(lambda: rvy.request_upload(META, other_sns, DRAFT_ID, "x"))
    assert env["gate"].calls == []


def test_same_store_keeps_companies_apart(tmp_path: Path) -> None:
    db = str(tmp_path / "creative.db")
    a = cr.CreativeRecords(db)
    b = cr.CreativeRecords(db, company_id="axcellent")
    a.set_request_youtube(DRAFT_ID, "wr_abc123", {"status": "draft", "title": "A"})
    assert a.get_request_youtube(DRAFT_ID)["title"] == "A"
    assert b.get_request_youtube(DRAFT_ID) == {} and b.request_youtube_all() == {}


def test_non_request_drafts_are_not_targets(env: dict[str, Any]) -> None:
    other = _draft(ctx="ctx_cmp_x", n="c")
    env["sns"].save(other)
    _with_labeled_video(env, other)
    with pytest.raises(rvy.YoutubeError, match="not_found"):
        _run(lambda: rvy.write_draft(env["sns"], "spd_c", FakeLlm, "ctx"))


def test_list_skill_attaches_youtube_state_and_checks_approval(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers.STATE, "sns", env["sns"])
    _with_labeled_video(env, env["draft"])
    _run(lambda: rvy.write_draft(env["sns"], DRAFT_ID, FakeLlm, "ctx"))
    _run(lambda: rvy.request_upload(META, env["sns"], DRAFT_ID, "태윤"))
    env["gate"].status = "executed"
    request = AgentRequest(meta=META, skill="list_request_videos", input={})
    result = _run(lambda: handlers.list_request_videos_skill(request))
    assert result.status == TaskStatus.COMPLETED
    item = result.output["items"][0]
    assert item["post_draft_id"] == DRAFT_ID and item["youtube"]["status"] in ("uploading", "done")
    assert rvy.get(DRAFT_ID)["status"] == "done"


def test_skills_return_contract_output(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers.STATE, "sns", env["sns"])
    monkeypatch.setattr(handlers, "YOUTUBE_LLM", FakeLlm)
    req = AgentRequest(
        meta=META, skill="request_video_youtube_draft", input={"post_draft_id": DRAFT_ID}
    )
    refused = _run(lambda: handlers.request_video_youtube_draft(req))
    assert (refused.status, refused.outcome) == (TaskStatus.FAILED, "video_not_ready")
    _with_labeled_video(env, env["draft"])
    ok = _run(lambda: handlers.request_video_youtube_draft(req))
    assert ok.status == TaskStatus.COMPLETED
    assert ok.output["request_id"] == "wr_abc123" and ok.output["youtube"]["status"] == "draft"
    up = AgentRequest(
        meta=META, skill="request_video_youtube_request", input={"post_draft_id": DRAFT_ID}
    )
    held = _run(lambda: handlers.request_video_youtube_request(up))
    assert held.output["youtube"]["status"] == "awaiting_approval"
    bad = AgentRequest(
        meta=META, skill="request_video_youtube_draft", input={"post_draft_id": "../x"}
    )
    assert _run(lambda: handlers.request_video_youtube_draft(bad)).outcome == "invalid_input"
