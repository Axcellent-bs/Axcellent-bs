"""의뢰 영상: 생성 직후 AI 라벨·초안 연결(멱등·실패 시 미기록)과 의뢰 영상 목록.

외부 호출 없음. 변환·라벨·점검은 가짜로 바꿔 ffmpeg 없이 돈다(`list_request_videos`의 라벨 확인은
`meta.json` 기록과 파일 해시를 본다).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from marketing_agent import request_video, sns, sns_label, sns_media
from marketing_agent.sns_prepare import Converted, PrepareError

CTX = "ctx_wr_abc123"
TENANT = "shop-demo"


@pytest.fixture
def media(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(root))
    return root.resolve()


def _meta(ctx: str = CTX) -> RequestMeta:
    return RequestMeta(
        context_id=ctx,
        token="mock:orchestrator",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


def _draft(
    ctx: str = CTX, kind: str = "reel", source: str = "brief", n: str = "a"
) -> sns.SnsPostDraft:
    caption = f"캡션 {n} AI 생성"
    return sns.SnsPostDraft(
        post_draft_id=f"spd_{n}",
        source_kind=source,
        source_ref="prop_1",
        platform="instagram",
        media_kind=kind,
        caption=caption,
        hashtags=[],
        caption_sha256=sns._sha(caption),  # noqa: SLF001
        required_text=[],
        context_id=ctx,
    )


def _generated(
    media: Path, draft_id: str, ctx: str = CTX, med: str = "med_1"
) -> tuple[AgentRequest, AgentResult]:
    folder = media / TENANT / ctx / med
    folder.mkdir(parents=True, exist_ok=True)
    asset = folder / "asset.mp4"
    asset.write_bytes(b"raw-video-" + med.encode())
    out = {
        "media_id": med,
        "media_kind": "video",
        "asset_ref": str(asset),
        "model_id": "mock-video",
        "duration_s": 15,
        "post_draft_id": draft_id,
        "generated_at": "2026-11-20T01:00:00+00:00",
        "ai_generated": True,
    }
    (folder / "meta.json").write_text(json.dumps(out), encoding="utf-8")
    request = AgentRequest(
        meta=_meta(ctx),
        skill="generate_media",
        input={"post_draft_id": draft_id, "media_kind": "video", "scene_template_id": "t"},
    )
    return request, AgentResult(
        meta=request.meta, status=TaskStatus.COMPLETED, outcome="generated", output=out
    )


class Proc:
    """`_convert_and_label` 가짜. 라벨 파일과 `meta.json`의 ai_label 기록을 같이 남긴다."""

    def __init__(self, fail: str | None = None) -> None:
        self.calls = 0
        self.fail = fail

    def __call__(
        self, asset: Path, out_dir: Path, kind: str, model: str, origin_sha: str, runner: Any
    ):
        self.calls += 1
        if self.fail:
            raise PrepareError("label", self.fail)
        labeled = out_dir / "labeled.mp4"
        labeled.write_bytes(b"labeled-" + origin_sha.encode())
        sha = sns_media.sha256_file(labeled)
        meta_path = out_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["ai_label"] = {
            "label_overlay": True,
            "labeled_sha256": sha,
            "labeled_file": "labeled.mp4",
        }
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        result = sns_label.LabelResult(
            labeled, sha, asset, origin_sha, meta_path, kind, {}, sns.ai_label_text("reel")
        )
        return Converted(asset, {}), result, False


class Check:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls = 0

    async def __call__(self, request: AgentRequest, store: sns.SnsStore) -> AgentResult:
        self.calls += 1
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            output={
                "verdict": "approved" if self.ok else "rejected",
                "publishable": self.ok,
                "problems": [],
            },
        )


def _attach(store, request, result, proc, check=None, preflight=lambda: None):
    return asyncio.run(
        request_video.attach_label_after_generate(
            request, result, store=store, process=proc, preflight=preflight, check=check or Check()
        )
    )


def test_label_is_applied_and_draft_is_connected(media: Path) -> None:
    store = sns.SnsStore(":memory:")
    store.save(_draft())
    request, result = _generated(media, "spd_a")
    out = _attach(store, request, result, Proc())
    assert out.output["label"] == "ok" and out.output["draft_stage"] == "draft_ready"
    assert out.output["asset_ref"] == result.output["asset_ref"]  # 생성 결과 필드는 그대로
    draft = store.get("spd_a")
    assert draft.asset_ref == f"{TENANT}/{CTX}/med_1/labeled.mp4"
    assert draft.asset_sha256 == sns_media.sha256_file(media / draft.asset_ref)
    prepared = draft.media_flow["prepared"]
    assert prepared["asset_ref"] == draft.asset_ref and prepared["stage"] == "draft_ready"
    assert prepared["asset_sha256"] == draft.asset_sha256
    assert draft.post_draft_id == "spd_a"  # id는 그대로


def test_second_run_is_idempotent(media: Path) -> None:
    store = sns.SnsStore(":memory:")
    store.save(_draft())
    request, result = _generated(media, "spd_a")
    proc, check = Proc(), Check()
    _attach(store, request, result, proc, check)
    again = _attach(store, request, result, proc, check)
    assert again.output["label"] == "reused"
    assert proc.calls == 1 and check.calls == 1  # 라벨·점검을 되풀이하지 않는다


def test_label_failure_keeps_generation_and_records_nothing(media: Path) -> None:
    store = sns.SnsStore(":memory:")
    store.save(_draft())
    request, result = _generated(media, "spd_a")
    out = _attach(store, request, result, Proc(fail="font_glyph_missing"))
    assert out.outcome == "generated" and out.status == TaskStatus.COMPLETED
    assert out.output["label"] == "failed" and out.output["label_reason"] == "font_glyph_missing"
    assert out.output["media_id"] == "med_1"
    draft = store.get("spd_a")
    assert draft.asset_ref is None and draft.asset_sha256 is None and draft.media_flow is None
    out2 = _attach(store, request, result, Proc(), preflight=lambda: "ffmpeg")  # 도구가 없을 때
    assert out2.output["label"] == "failed" and store.get("spd_a").media_flow is None


def test_other_drafts_and_failed_results_are_left_alone(media: Path) -> None:
    store = sns.SnsStore(":memory:")
    store.save(_draft(kind="image", n="img"))
    store.save(_draft(source="outage", n="out"))
    proc = Proc()
    for draft_id in ("spd_img", "spd_out", "spd_none"):
        request, result = _generated(media, draft_id, med=f"med_{draft_id}")
        assert _attach(store, request, result, proc).output == result.output
    request, result = _generated(media, "spd_img")
    failed = result.model_copy(update={"outcome": "provider_failed"})
    assert _attach(store, request, failed, proc) is failed
    assert proc.calls == 0


def test_check_rejection_still_connects_label_but_marks_stage(media: Path) -> None:
    store = sns.SnsStore(":memory:")
    store.save(_draft())
    request, result = _generated(media, "spd_a")
    out = _attach(store, request, result, Proc(), Check(ok=False))
    assert out.output["label"] == "ok" and out.output["draft_stage"] == "check_rejected"


def _list(store, **input: Any) -> list[dict]:
    result = asyncio.run(
        request_video.list_request_videos(
            AgentRequest(meta=_meta(), skill="list_request_videos", input=input), store
        )
    )
    assert result.status == TaskStatus.COMPLETED, result
    return result.output["items"]


def test_list_filters_by_request_and_reports_labeled(media: Path) -> None:
    store = sns.SnsStore(":memory:")
    store.save(_draft(n="a"))
    store.save(_draft(ctx="ctx_wr_other", n="b"))
    store.save(_draft(ctx="ctx_cmp_x", n="c"))  # 의뢰가 아닌 context
    store.save(_draft(n="nomedia"))  # 영상이 아직 없는 초안
    req_a, res_a = _generated(media, "spd_a")
    _generated(media, "spd_b", ctx="ctx_wr_other")
    _generated(media, "spd_c", ctx="ctx_cmp_x")
    items = _list(store)
    assert {i["post_draft_id"] for i in items} == {"spd_a", "spd_b"}
    assert all(i["labeled"] is False and i["prepared"] is None for i in items)
    assert all(i["status"] == "label_missing" for i in items)

    _attach(store, req_a, res_a, Proc())
    only = _list(store, request_id="wr_abc123")
    assert [i["post_draft_id"] for i in only] == ["spd_a"]
    item = only[0]
    assert item["labeled"] is True and item["status"] == "draft_ready"
    assert item["media_id"] == "med_1" and item["duration_s"] == 15
    assert item["request_id"] == "wr_abc123" and item["caption_ko"].startswith("캡션")
    assert item["prepared"]["asset_ref"].endswith("labeled.mp4")
    assert _list(store, post_draft_id="spd_b")[0]["labeled"] is False


def test_list_does_not_trust_a_tampered_label(media: Path) -> None:
    store = sns.SnsStore(":memory:")
    store.save(_draft())
    request, result = _generated(media, "spd_a")
    _attach(store, request, result, Proc())
    (media / TENANT / CTX / "med_1" / "labeled.mp4").write_bytes(b"changed")
    item = _list(store)[0]
    assert item["labeled"] is False and item["prepared"] is None


def test_list_rejects_bad_input_and_empty_without_media_dir(
    media: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = sns.SnsStore(":memory:")
    bad = asyncio.run(
        request_video.list_request_videos(
            AgentRequest(meta=_meta(), skill="list_request_videos", input={"request_id": "x"}),
            store,
        )
    )
    assert bad.status == TaskStatus.FAILED and bad.outcome == "invalid_input"
    monkeypatch.delenv(sns_media.MEDIA_DIR_ENV)
    assert _list(store) == []
