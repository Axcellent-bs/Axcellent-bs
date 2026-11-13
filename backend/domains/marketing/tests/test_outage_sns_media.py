"""장애 사과·보상 초안의 이미지 붙이기·게시 요청.

`prepare_outage_sns_media`·`request_outage_sns_publish`.

외부 호출은 없다: 공급자는 목, 안전 관문은 가짜(`Gate`·`PublishGate`), Instagram은 목 어댑터.
연결·거절·멱등 시험은 변환·라벨·점검을 가짜로 바꿔 ffmpeg 없이 돈다(실제 변환 시험은 도구가
없으면 건너뜀).
승인은 어디서도 하지 않는다: 요청만으로 어댑터가 불리지 않는지(승인 전 게시 0회)를 본다.
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    ExecuteDecision,
    HoldReason,
    RequestMeta,
    TaskStatus,
)
from marketing_agent import handlers, outage_sns_media, sns, sns_label, sns_media
from marketing_agent.main import app
from marketing_agent.media import prompts, skill
from marketing_agent.media.config import MediaSettings
from marketing_agent.sns_prepare import Converted, PrepareError
from marketing_agent.sns_publish import instagram, pending, publisher
from marketing_agent.sns_publish.instagram_mock import MockInstagramAdapter

client = TestClient(app)
CTX = "ctx_inc_0001"
FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/malgun.ttf",
]
ANY_FONT = next((f for f in FONTS if Path(f).is_file()), None)
needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe") and ANY_FONT),
    reason="ffmpeg·ffprobe 또는 글꼴이 없어 실제 변환 시험을 건너뜀",
)
MEDIA_ENV = (
    "MARKETING_IMAGE_PROVIDER",
    "MARKETING_IMAGE_MODEL",
    "MARKETING_VIDEO_PROVIDER",
    "MARKETING_VIDEO_MODEL",
    "MARKETING_AUDIO_MODE",
    "MARKETING_FX_USD_KRW",
    "MARKETING_FX_AS_OF",
    "MARKETING_FX_MAX_AGE_DAYS",
    "MARKETING_COST_SAFETY_MARGIN_PCT",
    "MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM",
    "MARKETING_MEDIA_MONTHLY_BUDGET_KRW",
    "MARKETING_SNS_PROVIDER",
    "MARKETING_SNS_PENDING_DB",
    "REDIS_URL",
    sns_label.CONFIG_ENV,
    sns_label.FONT_ENV,
)


class Gate:
    """생성(B등급) 안전 관문 가짜. 받은 매개변수를 기록한다."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.params: list[dict] = []
        self.status = "executed"

    async def __call__(self, action, params, context, evidence):  # noqa: ANN001
        if action == "generate_media":  # 공지 게시 등 다른 관문 호출은 세지 않는다
            self.seen.append(action)
            self.params.append(params)
        extra = (
            {"review_id": "rev_9", "hold_reason": "over_threshold"}
            if self.status == "held" and action == "generate_media"
            else {}
        )
        return ExecuteDecision(execution_id="exe_b1", grade="B", status=self.status, **extra)


class PublishGate:
    """게시(C등급) 관문 가짜: 새 키는 항상 보류(승인 대기)."""

    def __init__(self) -> None:
        self.by_key: dict[str, ExecuteDecision] = {}
        self.calls: list[str] = []

    async def execute(self, action: str, params: dict, meta: RequestMeta, evidence: Any):
        assert action == "publish_sns"
        key = meta.idempotency_key or ""
        self.calls.append(key)
        if key not in self.by_key:
            n = len(self.by_key) + 1
            self.by_key[key] = ExecuteDecision(
                execution_id=f"exe_{n}",
                grade="C",
                status="held",
                hold_reason=HoldReason.OVER_THRESHOLD,
                review_id=f"rev_{n}",
            )
        return self.by_key[key]


class Env:
    def __init__(self, gate: Gate, pub: PublishGate, adapter: MockInstagramAdapter) -> None:
        self.gate, self.pub, self.adapter = gate, pub, adapter
        self.processed = 0
        self.verdict = "approved"

    def published(self) -> int:
        calls = self.adapter.calls
        return calls.get("create_container", 0) + calls.get("media_publish", 0)


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Env:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    for name in MEDIA_ENV:
        monkeypatch.delenv(name, raising=False)
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(media))
    if ANY_FONT:
        monkeypatch.setenv(sns_label.FONT_ENV, ANY_FONT)
    events.reset_mock_bus()
    handlers.reset_state()
    publisher.reset_runtime()
    instagram.reset_adapter()
    adapter = MockInstagramAdapter()
    instagram.set_adapter(adapter)
    gate, pub = Gate(), PublishGate()
    monkeypatch.setattr(skill, "execute", gate)
    monkeypatch.setattr(handlers, "execute", gate)
    monkeypatch.setattr(publisher, "execute", pub.execute)
    monkeypatch.setattr(publisher.RT, "store", pending.PendingStore(":memory:"))

    async def review_info(row, client=None):  # noqa: ANN001
        return None

    monkeypatch.setattr(publisher, "_review_info", review_info)
    yield Env(gate, pub, adapter)
    publisher.reset_runtime()
    instagram.reset_adapter()
    events.reset_mock_bus()


def _meta() -> RequestMeta:
    return RequestMeta(
        context_id=CTX,
        token="mock:dashboard-api",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


def _req(skill_id: str, **input: Any) -> AgentRequest:
    return AgentRequest(meta=_meta(), skill=skill_id, input=input)


def _call(skill_id: str, input: dict, token: str = "mock:dashboard-api") -> dict:
    body = {
        "meta": {
            "context_id": CTX,
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill_id,
        "input": input,
    }
    return client.post("/a2a", json=body).json()


def _notice_id() -> str:
    orch = "mock:orchestrator"
    draft = _call(
        "draft_notice", {"kind": "incident_resolved", "facts": {"component": "payment"}}, orch
    )["output"]
    return _call("publish_notice", {"draft_id": draft["draft_id"]}, orch)["output"]["notice_id"]


def _draft(**over: Any) -> sns.SnsPostDraft:
    payload = {
        "trigger": "apology_notice",
        "cause_code": "recent_update",
        "action_code": "reverted_update",
        "recovered": True,
    } | over
    if payload["trigger"] == "apology_notice" and "notice_id" not in payload:
        payload["notice_id"] = _notice_id()
    result = _call("draft_outage_sns_post", payload, token="mock:orchestrator")
    assert result["status"] == "completed", result
    found = handlers.STATE.sns.get(result["output"]["post_draft_id"])
    assert found is not None
    return found


def _fake_process(env: Env):
    def process(asset: Path, out_dir: Path, kind: str, model: str, origin_sha: str, runner: Any):
        env.processed += 1
        labeled = out_dir / "labeled.jpg"
        labeled.write_bytes(b"labeled-" + origin_sha.encode())
        result = sns_label.LabelResult(
            labeled,
            sns_media.sha256_file(labeled),
            asset,
            origin_sha,
            out_dir / "meta.json",
            kind,
            {},
            sns.ai_label_text("image"),
        )
        return Converted(asset, {}), result, False

    return process


def _fake_check(env: Env):
    async def check(request: AgentRequest, store: sns.SnsStore) -> AgentResult:
        ok = env.verdict == "approved"
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            output={
                "verdict": env.verdict,
                "publishable": ok,
                "problems": [] if ok else ["ai_label_overlay_missing"],
            },
        )

    return check


def _prepare(env: Env, draft_id: str, **over: Any) -> AgentResult:
    args: dict[str, Any] = {
        "process": _fake_process(env),
        "preflight": lambda: None,
        "check": _fake_check(env),
    } | over
    return asyncio.run(
        outage_sns_media.prepare_outage_sns_media(
            _req("prepare_outage_sns_media", post_draft_id=draft_id),
            store=handlers.STATE.sns,
            ledger=handlers.STATE.media_ledger,
            from_decision=handlers._from_decision,
            **args,
        )
    )


def _publish(env: Env, draft_id: str, **extra: Any) -> AgentResult:
    return asyncio.run(
        outage_sns_media.request_outage_sns_publish(
            _req("request_outage_sns_publish", post_draft_id=draft_id, **extra),
            store=handlers.STATE.sns,
            publish=publisher.publish_sns,
            check=_fake_check(env),
        )
    )


def _state(draft_id: str) -> dict:
    origin = handlers.STATE.sns.get(draft_id)
    assert origin is not None
    return outage_sns_media.flow_state(
        _req("x"), origin, handlers.STATE.sns, ledger=handlers.STATE.media_ledger
    )


# --- 템플릿: 코드 고정값·안전 규칙 ------------------------------------------------------


def test_apology_template_is_fixed_and_follows_the_rules() -> None:
    template = prompts.get_template(outage_sns_media.TEMPLATE_ID)
    assert template.kind == "image" and template.fields == ("color_theme",)
    prompt = prompts.render_prompt(outage_sns_media.TEMPLATE_ID, outage_sns_media.TEMPLATE_FIELDS)
    for fixed in (
        "No text, no letters, no numbers, no logos",
        "no people, no children, no real people, no real brands",
    ):
        assert fixed in prompt
    assert "soft pastel" in prompt and "apolog" in prompt.lower()
    # 화이트리스트 밖의 필드·값은 거절된다
    with pytest.raises(prompts.PromptRejected):
        prompts.render_prompt(outage_sns_media.TEMPLATE_ID, {"color_theme": "neon"})
    with pytest.raises(prompts.PromptRejected):
        prompts.render_prompt(
            outage_sns_media.TEMPLATE_ID, {"color_theme": "soft pastel", "category": "x"}
        )


# --- 이미지 붙이기 ------------------------------------------------------------------------


def test_prepare_attaches_image_keeps_caption_and_never_publishes(env: Env) -> None:
    origin = _draft()
    out = _prepare(env, origin.post_draft_id)
    assert out.status == TaskStatus.COMPLETED and out.output["stage"] == "draft_ready", out
    assert env.gate.seen == ["generate_media"] and env.processed == 1
    sent = env.gate.params[0]
    assert sent["scene_template_id"] == "cat_apology_image" and sent["media_kind"] == "image"
    assert sent["ai_generated"] is True
    # 캡션은 그대로, 이미지는 사본에만 붙는다
    kept = handlers.STATE.sns.get(origin.post_draft_id)
    assert kept is not None and kept.asset_ref is None and kept.caption == origin.caption
    derived = handlers.STATE.sns.get(out.output["media_draft_id"])
    assert derived is not None and derived.caption == origin.caption
    assert derived.media_from == origin.post_draft_id and derived.asset_ref
    assert derived.post_draft_id != origin.post_draft_id
    assert derived.media_check["verdict"] == "approved"
    # 게시 요청은 만들지 않았다
    assert publisher.store()._query("") == []  # noqa: SLF001
    assert env.published() == 0
    flow = out.output["media_flow"]
    assert flow["stage"] == "draft_ready" and flow["publish"]["status"] == "not_requested"


def test_second_call_is_replayed_without_new_cost(env: Env) -> None:
    origin = _draft()
    _prepare(env, origin.post_draft_id)
    again = _prepare(env, origin.post_draft_id)
    assert again.outcome == "already_prepared" and env.gate.seen == ["generate_media"]
    assert env.processed == 1


def test_check_rejected_is_not_ready_and_retry_replays_generation(env: Env) -> None:
    origin = _draft()
    env.verdict = "rejected"
    first = _prepare(env, origin.post_draft_id)
    assert first.outcome == "rejected_by_check" and first.output["stage"] == "check_rejected"
    assert _state(origin.post_draft_id)["stage"] == "check_rejected"
    assert _publish(env, origin.post_draft_id).outcome == "check_not_passed"
    env.verdict = "approved"
    second = _prepare(env, origin.post_draft_id)
    assert second.output["stage"] == "draft_ready"
    assert second.output["generation"]["replayed"] is True  # 비용이 다시 나지 않는다
    assert len(handlers.STATE.sns.list_by_source("outage", None)[0]) == 2  # 원본 + 사본 1개


def test_caller_in_other_context_gets_the_same_ledger_key(env: Env) -> None:
    origin = _draft()
    _prepare(env, origin.post_draft_id)
    other = AgentRequest(
        meta=_meta().model_copy(update={"context_id": "ctx_someone_else"}),
        skill="prepare_outage_sns_media",
        input={"post_draft_id": origin.post_draft_id},
    )
    out = asyncio.run(
        outage_sns_media.prepare_outage_sns_media(
            other,
            store=handlers.STATE.sns,
            ledger=handlers.STATE.media_ledger,
            from_decision=handlers._from_decision,
            process=_fake_process(env),
            preflight=lambda: None,
            check=_fake_check(env),
        )
    )
    assert out.outcome == "already_prepared" and env.gate.seen == ["generate_media"]


def test_held_gate_returns_review_and_attaches_nothing(env: Env) -> None:
    origin = _draft()
    env.gate.status = "held"
    out = _prepare(env, origin.post_draft_id)
    assert out.status == TaskStatus.INPUT_REQUIRED and out.output["stage"] == "generate_media"
    assert handlers.STATE.sns.find_derived(origin.post_draft_id) is None
    assert env.processed == 0


def test_conversion_failure_keeps_cost_note_and_retry_replays(env: Env) -> None:
    origin = _draft()

    def broken(*args: Any) -> Any:
        raise PrepareError("convert", "ffmpeg_failed")

    out = _prepare(env, origin.post_draft_id, process=broken)
    assert out.outcome == "convert_failed" and "ffmpeg" not in str(out.output["note"])
    assert handlers.STATE.sns.find_derived(origin.post_draft_id) is None
    ok = _prepare(env, origin.post_draft_id)
    assert ok.output["generation"]["replayed"] is True and env.gate.seen == ["generate_media"]


# --- 비용 전에 막는 것 ---------------------------------------------------------------------


def test_refusals_happen_before_any_generation(env: Env) -> None:
    origin = _draft()
    # 병합된 초안
    merged = origin.model_copy(update={"status": "merged", "merged_into": "spd_other"})
    handlers.STATE.sns.update_meta([merged])
    assert _prepare(env, origin.post_draft_id).outcome == "already_merged"
    handlers.STATE.sns.update_meta([origin])
    # 검사를 통과하지 못한 초안(AI 표시가 빠진 글)
    bare = origin.caption.replace(sns.ai_label_text("image"), "")
    bad = origin.model_copy(update={"caption": bare})
    handlers.STATE.sns.update_meta([bad])
    assert _prepare(env, origin.post_draft_id).outcome == "check_failed"
    handlers.STATE.sns.update_meta([origin])
    # 영상 초안
    reel = _draft(media_kind="reel", cause_code="unknown", action_code="none", recovered=False)
    assert _prepare(env, reel.post_draft_id).outcome == "video_not_supported"
    # 없는 초안·잘못된 id·장애 초안이 아닌 것
    assert _prepare(env, "spd_none").outcome == "draft_not_found"
    assert _prepare(env, "a b").outcome == "invalid_input"
    other = origin.model_copy(update={"post_draft_id": "spd_notoutage", "source_kind": "notice"})
    handlers.STATE.sns.save(other)
    assert _prepare(env, "spd_notoutage").outcome == "not_outage_draft"
    assert env.gate.seen == [] and env.processed == 0


def test_draft_with_publish_history_is_refused(env: Env) -> None:
    origin = _draft()
    row = pending.PendingPost(
        idempotency_key="k1",
        tenant_id="shop-demo",
        context_id=CTX,
        post_draft_id=origin.post_draft_id,
        content_sha="s" * 64,
        execution_id="exe_1",
        review_id="rev_1",
        status=pending.HELD,
        params={},
    )
    assert publisher.store().create(row)
    out = _prepare(env, origin.post_draft_id)
    assert out.outcome == "publish_history" and env.gate.seen == []
    state = _state(origin.post_draft_id)
    assert state["can_proceed"] is False and state["reason_code"] == "publish_history"


def test_missing_tools_or_media_dir_stop_before_cost(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = _draft()
    out = _prepare(env, origin.post_draft_id, preflight=lambda: "ffmpeg_not_found")
    assert out.outcome == "post_tools_unavailable" and env.gate.seen == []
    monkeypatch.delenv(sns_media.MEDIA_DIR_ENV)
    assert _prepare(env, origin.post_draft_id).outcome == "media_dir_not_configured"
    assert env.gate.seen == []


def test_merge_skips_a_draft_that_already_has_an_image_copy(env: Env) -> None:
    origin = _draft()
    _prepare(env, origin.post_draft_id)
    merge = _call("merge_outage_sns_drafts", {"dry_run": True, "draft_ids": [origin.post_draft_id]})
    assert merge["output"]["skipped"] == [{"id": origin.post_draft_id, "reason": "media_attached"}]


# --- 카드 상태·목록 ------------------------------------------------------------------------


def test_flow_state_shows_estimate_before_click_and_list_hides_copies(env: Env) -> None:
    origin = _draft()
    state = _state(origin.post_draft_id)
    assert state["stage"] == "not_started" and state["can_proceed"] is True
    est = state["estimate"]
    assert est["status"] == "ok" and est["cost_krw"] and est["settled"] is False
    assert est["month_total_krw"] == 0 and "per_item_limit_krw" in est
    _prepare(env, origin.post_draft_id)
    listed = _call("list_outage_sns_drafts", {})["output"]
    assert [i["post_draft_id"] for i in listed["items"]] == [origin.post_draft_id]
    assert listed["total"] == 1 and listed["items"][0]["media_flow"]["stage"] == "draft_ready"
    one = _call("list_outage_sns_drafts", {"post_draft_id": origin.post_draft_id})["output"]
    assert len(one["items"]) == 1
    after = _state(origin.post_draft_id)
    assert after["can_proceed"] is False and after["estimate"] is None


# --- 게시 요청 -----------------------------------------------------------------------------


def test_publish_request_makes_only_a_held_item(env: Env) -> None:
    origin = _draft()
    assert _publish(env, origin.post_draft_id).outcome == "not_prepared"
    _prepare(env, origin.post_draft_id)
    derived = handlers.STATE.sns.find_derived(origin.post_draft_id)
    out = _publish(env, origin.post_draft_id)
    assert out.status == TaskStatus.COMPLETED and out.outcome == "requested", out
    held = out.output["publish"]
    assert held["status"] == "held" and held["review_id"] == out.output["review_id"] == "rev_1"
    assert held["caption"] == origin.caption and held["ai_label"] == sns.ai_label_text("image")
    assert held["preview_kind"] == "image" and held["proposal_id"] == origin.post_draft_id
    assert held["preview_url"].endswith(f"/outage-sns-drafts/{origin.post_draft_id}/media/preview")
    row = publisher.store().by_review("rev_1")
    assert row is not None and row.status == pending.HELD
    assert row.post_draft_id == derived.post_draft_id and row.params["caption"] == origin.caption
    assert row.params["asset_sha256"] == derived.asset_sha256 and row.params["is_ai_generated"]
    assert row.context_id == f"ctx_outage_{origin.post_draft_id}"
    assert env.published() == 0  # 승인 전 게시 0회, 이 흐름에는 승인 호출이 없다
    # 같은 요청은 다시 만들지 않는다
    again = _publish(env, origin.post_draft_id)
    assert again.outcome == "already_requested" and len(env.pub.calls) == 1
    assert _state(origin.post_draft_id)["publish"]["status"] == "held"
    # 이미 게시 요청이 있으니 새 이미지 만들기는 거절 대신 '이미 준비됨'
    assert _prepare(env, origin.post_draft_id).outcome == "already_prepared"


def test_caller_cannot_choose_content_and_only_dashboard_api_may_call(env: Env) -> None:
    origin = _draft()
    _prepare(env, origin.post_draft_id)
    for extra in ({"caption": "바꾼 글"}, {"asset_ref": "x"}, {"template": "cat_bookstore_image"}):
        bad = _call("request_outage_sns_publish", {"post_draft_id": origin.post_draft_id} | extra)
        assert bad["outcome"] == "invalid_input"
        bad = _call("prepare_outage_sns_media", {"post_draft_id": origin.post_draft_id} | extra)
        assert bad["outcome"] == "invalid_input"
    for name in ("prepare_outage_sns_media", "request_outage_sns_publish"):
        for who in ("mock:orchestrator", "mock:planning", "mock:marketing"):
            out = _call(name, {"post_draft_id": origin.post_draft_id}, token=who)
            assert out["status"] == "rejected", (name, who)
    assert publisher.store()._query("") == []  # noqa: SLF001


def test_changed_caption_or_failed_fresh_check_is_refused(env: Env) -> None:
    origin = _draft()
    _prepare(env, origin.post_draft_id)
    derived = handlers.STATE.sns.find_derived(origin.post_draft_id)
    env.verdict = "rejected"  # 요청 직전 점검이 통과하지 못하면 승인을 받으러 가지 않는다
    assert _publish(env, origin.post_draft_id).outcome == "check_not_passed"
    env.verdict = "approved"
    tampered = derived.model_copy(update={"caption": derived.caption + " 추가"})
    handlers.STATE.sns.update_meta([tampered])
    assert _publish(env, origin.post_draft_id).outcome == "draft_changed"
    assert env.pub.calls == []


def _expire(review_id: str) -> None:
    row = publisher.store().by_review(review_id)
    assert row is not None
    assert publisher._close(  # noqa: SLF001
        row, pending.HELD, pending.REJECTED, publisher.REVIEW_TIMEOUT
    )


def test_expired_request_can_be_retried_only_by_a_human_and_stays_held(env: Env) -> None:
    origin = _draft()
    _prepare(env, origin.post_draft_id)
    assert _publish(env, origin.post_draft_id, retry=True).outcome == "retry_not_expired"
    _publish(env, origin.post_draft_id)
    plain_before = _publish(env, origin.post_draft_id)
    assert plain_before.outcome == "already_requested"
    _expire("rev_1")
    state = _state(origin.post_draft_id)["publish"]
    assert state["status"] == "expired" and state["can_retry"] is True
    assert _publish(env, origin.post_draft_id).outcome == "already_requested"  # 자동 재요청 없음
    out = _publish(env, origin.post_draft_id, retry=True)
    assert out.outcome == "requested" and env.pub.calls[1].endswith(":r1")
    assert _state(origin.post_draft_id)["publish"]["status"] == "held"
    assert env.published() == 0
    assert _publish(env, origin.post_draft_id, retry=True).outcome == "retry_other_held"


def test_human_rejected_request_is_not_retried(env: Env) -> None:
    origin = _draft()
    _prepare(env, origin.post_draft_id)
    _publish(env, origin.post_draft_id)
    row = publisher.store().by_review("rev_1")
    assert row is not None
    publisher._close(  # noqa: SLF001
        row, pending.HELD, pending.REJECTED, "review_rejected", result={"cause": "human"}
    )
    state = _state(origin.post_draft_id)["publish"]
    assert state["status"] == "rejected" and state["can_retry"] is False
    assert _publish(env, origin.post_draft_id, retry=True).outcome == "retry_rejected_by_human"


def test_no_adapter_is_refused_before_approval(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing():  # noqa: ANN202
        raise instagram.AdapterUnavailable("none")

    origin = _draft()
    _prepare(env, origin.post_draft_id)
    monkeypatch.setattr(publisher, "get_adapter", missing)
    out = _publish(env, origin.post_draft_id)
    assert out.outcome == "adapter_unavailable" and env.pub.calls == []


# --- 실제 변환(목 공급자) -------------------------------------------------------------------


@needs_ffmpeg
def test_end_to_end_with_mock_provider_and_real_conversion(env: Env) -> None:
    origin = _draft()
    out = asyncio.run(
        outage_sns_media.prepare_outage_sns_media(
            _req("prepare_outage_sns_media", post_draft_id=origin.post_draft_id),
            store=handlers.STATE.sns,
            ledger=handlers.STATE.media_ledger,
            from_decision=handlers._from_decision,
            settings=MediaSettings.from_env(),
        )
    )
    assert out.output["stage"] == "draft_ready", out.output
    derived = handlers.STATE.sns.find_derived(origin.post_draft_id)
    assert derived is not None and derived.asset_ref.endswith("labeled.jpg")
    requested = asyncio.run(
        outage_sns_media.request_outage_sns_publish(
            _req("request_outage_sns_publish", post_draft_id=origin.post_draft_id),
            store=handlers.STATE.sns,
            publish=publisher.publish_sns,
        )
    )
    assert requested.outcome == "requested" and env.published() == 0
