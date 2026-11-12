"""캐러셀(이미지 2~10장) 게시: 초안·점검(장마다 라벨) → publish_sns(C등급 승인) → 목 Instagram.

실제 Graph API·Gemini는 부르지 않는다(목 어댑터·가짜 관문). 라벨 입힌 이미지는 규격 검사가 읽는
헤더만 맞춘 합성 JPEG + 실제 XMP·meta.json 기록이다(오버레이 자체는 test_sns_label_position).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import (
    AgentRequest,
    ExecuteDecision,
    HoldReason,
    RequestMeta,
    TaskStatus,
)
from marketing_agent import handlers, sns, sns_label, sns_media
from marketing_agent.main import app
from marketing_agent.sns_publish import instagram, pending, publisher
from marketing_agent.sns_publish.instagram_mock import MockInstagramAdapter

CTX = "ctx_car_0001"
TENANT = "shop-demo"
client = TestClient(app)


class FakeGate:
    """safety 관문 대역: 같은 키는 이전 판정을 돌려주고, 승인하면 executed가 된다."""

    def __init__(self) -> None:
        self.by_key: dict[str, ExecuteDecision] = {}
        self.params: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.n = 0

    async def execute(self, action: str, params: dict, meta: RequestMeta, evidence: Any):
        assert action == "publish_sns"
        key = meta.idempotency_key or ""
        self.calls.append(key)
        self.params.append(params)
        if key not in self.by_key:
            self.n += 1
            self.by_key[key] = ExecuteDecision(
                execution_id=f"exe_{self.n}",
                grade="C",
                status="held",
                hold_reason=HoldReason.OVER_THRESHOLD,
                review_id=f"rev_{self.n}",
            )
        return self.by_key[key]

    def approve_all(self) -> None:
        for key, d in self.by_key.items():
            self.by_key[key] = d.model_copy(update={"status": "executed"})


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(media))
    for name in ("MARKETING_SNS_PROVIDER", "MARKETING_SNS_PENDING_DB", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    events.reset_mock_bus()
    handlers.reset_state()
    publisher.reset_runtime()
    instagram.reset_adapter()

    async def no_sleep(_: float) -> None:
        return None

    publisher.RT.sleep = no_sleep
    yield
    publisher.reset_runtime()
    instagram.reset_adapter()
    events.reset_mock_bus()


@pytest.fixture
def adapter() -> MockInstagramAdapter:
    fake = MockInstagramAdapter()
    instagram.set_adapter(fake)
    return fake


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> FakeGate:
    fake = FakeGate()
    monkeypatch.setattr(publisher, "execute", fake.execute)
    return fake


# --- 재료 ---


def _make_jpeg(path: Path, width: int, height: int, pad: int) -> None:
    sof = b"\xff\xc0" + (11).to_bytes(2, "big") + b"\x08"
    sof += height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8" + sof + b"\xff\xd9" + bytes([pad % 250 + 1]) * (pad + 1))


def _item(index: int, *, overlay: bool = True, mark: bool = True) -> str:
    """장마다 자기 폴더에 `labeled.jpg`·`meta.json`을 둔다. asset_ref를 돌려준다."""
    root = sns_media.media_root()
    assert root is not None
    folder = root / "t1" / "c1" / f"med_{index:02d}"
    path = folder / "labeled.jpg"
    _make_jpeg(path, 1080, 1350, index)
    if mark:
        sns_label.inject_xmp(path, sns_label.machine_mark("test-model", datetime.now(UTC)))
    if overlay:
        meta = {
            "ai_label": {
                "label_overlay": True,
                "labeled_file": "labeled.jpg",
                "labeled_sha256": sns_media.sha256_file(path),
            }
        }
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return sns_media.asset_ref_for(path)


def _coupon() -> dict:
    return {
        "campaign_id": "cmp_1",
        "unit_value": 2000,
        "constraints": [
            {"code": code, "text": f"{label} 안내 {i}"}
            for i, (code, label) in enumerate(sns.CONSTRAINT_LABELS.items(), 1)
        ],
    }


def _meta() -> dict:
    return {
        "context_id": CTX,
        "token": "mock:orchestrator",
        "path": [],
        "deadline": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
    }


def _call(skill: str, data: dict) -> dict:
    return client.post("/a2a", json={"meta": _meta(), "skill": skill, "input": data}).json()


def _draft(refs: list[str], **extra: Any) -> dict:
    result = _call(
        "draft_sns_post",
        {"coupon": _coupon(), "media_kind": "carousel", "asset_paths": refs} | extra,
    )
    assert result["status"] == "completed", result
    return result["output"]


def _check(draft: dict) -> dict:
    out = _call(
        "check_sns_post",
        {
            "post_draft_id": draft["post_draft_id"],
            "caption": draft["caption"],
            "is_ai_generated": True,
        },
    )
    return out["output"]


def _publish_request(draft: dict, **over: Any) -> AgentRequest:
    body = {
        "post_draft_id": draft["post_draft_id"],
        "media_kind": "carousel",
        "asset_refs": [a["asset_ref"] for a in draft["assets"]],
        "asset_sha256s": [a["asset_sha256"] for a in draft["assets"]],
        "caption": draft["caption"],
    } | over
    return AgentRequest(
        meta=RequestMeta(
            context_id=CTX,
            token="mock:orchestrator",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        skill="publish_sns",
        input=body,
    )


def _ready(count: int) -> dict:
    draft = _draft([_item(i) for i in range(1, count + 1)])
    check = _check(draft)
    assert check["verdict"] == "approved" and check["publishable"] is True, check["problems"]
    return draft


def _row() -> pending.PendingPost:
    rows = publisher.store()._query("")  # noqa: SLF001
    assert len(rows) == 1
    return rows[0]


# --- 초안·점검: 2장·10장 경계, 1장·11장 거절 ---


@pytest.mark.parametrize("count", [2, 10])
def test_carousel_boundaries_pass_and_every_item_is_listed(count: int) -> None:
    draft = _ready(count)
    assert draft["media_kind"] == "carousel" and len(draft["assets"]) == count
    assert sns.ai_label_text("carousel") in draft["caption"]
    check = _check(draft)
    assert check["media_details"]["item_count"] == count
    assert (check["ai_label_missing_count"], check["text_mismatch_count"]) == (0, 0)


@pytest.mark.parametrize("count", [1, 11])
def test_draft_rejects_out_of_range_counts(count: int) -> None:
    refs = [_item(i) for i in range(1, count + 1)]
    out = _call(
        "draft_sns_post", {"coupon": _coupon(), "media_kind": "carousel", "asset_paths": refs}
    )
    assert out["status"] == "failed" and out["outcome"] == "invalid_input"


def test_draft_rejects_duplicates_and_stray_asset_paths() -> None:
    ref = _item(1)
    dup = _call(
        "draft_sns_post",
        {"coupon": _coupon(), "media_kind": "carousel", "asset_paths": [ref, ref]},
    )
    assert dup["outcome"] == "invalid_input"
    single = _call(
        "draft_sns_post",
        {"coupon": _coupon(), "media_kind": "image", "asset_paths": [ref, _item(2)]},
    )
    assert single["outcome"] == "invalid_input"


def test_item_without_overlay_record_rejects_whole_carousel() -> None:
    refs = [_item(1), _item(2, overlay=False), _item(3)]
    check = _check(_draft(refs))
    assert check["verdict"] == "rejected" and check["publishable"] is False
    assert "item_2:ai_label_overlay_missing" in check["problems"]
    assert check["ai_label_missing_count"] >= 1
    assert not any(p.startswith(("item_1:", "item_3:")) for p in check["problems"])


def test_item_without_machine_mark_rejects_whole_carousel() -> None:
    check = _check(_draft([_item(1), _item(2, mark=False)]))
    assert check["verdict"] == "rejected" and "item_2:machine_mark_missing" in check["problems"]


def test_item_changed_after_draft_is_rejected() -> None:
    draft = _ready(3)
    target = sns_media.resolve_asset(draft["assets"][1]["asset_ref"])
    target.write_bytes(target.read_bytes() + b"x")
    check = _check(draft)
    assert check["verdict"] == "rejected" and check["publishable"] is False
    assert check["text_mismatch_count"] >= 1


def test_item_with_bad_spec_is_rejected_per_item() -> None:
    bad = _item(2)
    _make_jpeg(sns_media.resolve_asset(bad), 240, 300, 2)  # 너비 320 미만(비율은 같다)
    check = _check(_draft([_item(1), bad]))
    assert check["verdict"] == "rejected"
    assert "item_2:image_width_out_of_range" in check["problems"]


def test_different_item_order_or_set_changes_draft_id() -> None:
    a, b = _item(1), _item(2)
    assert _draft([a, b])["post_draft_id"] != _draft([b, a])["post_draft_id"]
    assert _draft([a, b])["asset_sha256"] == sns_media.carousel_sha256(
        [x["asset_sha256"] for x in _draft([a, b])["assets"]]
    )


# --- publish_sns: 입력 경계 ---


@pytest.mark.parametrize("count", [1, 11])
@pytest.mark.asyncio
async def test_publish_rejects_out_of_range_counts(count: int, gate: FakeGate) -> None:
    sha = hashlib.sha256(b"x").hexdigest()
    request = _publish_request(
        {"post_draft_id": "spd_x", "assets": [], "caption": "c"},
        asset_refs=[f"a{i}.jpg" for i in range(count)],
        asset_sha256s=[sha] * count,
    )
    result = await publisher.publish_sns(request, handlers.STATE.sns)
    assert result.outcome == "invalid_input" and gate.calls == []


@pytest.mark.asyncio
async def test_publish_input_shape_rules(gate: FakeGate) -> None:
    draft = _ready(2)
    both = await publisher.publish_sns(
        _publish_request(draft, asset_ref="x.jpg", asset_sha256="0" * 64), handlers.STATE.sns
    )
    assert both.outcome == "invalid_input"
    mismatch = await publisher.publish_sns(
        _publish_request(draft, asset_sha256s=["0" * 64]), handlers.STATE.sns
    )
    assert mismatch.outcome == "invalid_input"
    image_with_list = await publisher.publish_sns(
        _publish_request(draft, media_kind="image"), handlers.STATE.sns
    )
    assert image_with_list.outcome == "invalid_input" and gate.calls == []


# --- publish_sns: 승인 대기 큐에 장수·참조, 그리고 승인 전에는 호출 0회 ---


@pytest.mark.asyncio
async def test_held_queue_shows_item_count_and_every_ref_and_nothing_is_called(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    draft = _ready(3)
    held = await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    assert held.status == TaskStatus.INPUT_REQUIRED and held.outcome == "held"
    assert held.output["grade"] == "C"
    assert held.output["item_count"] == 3
    assert held.output["asset_refs"] == [a["asset_ref"] for a in draft["assets"]]
    sent = gate.params[0]
    assert sent["media_kind"] == "carousel" and sent["item_count"] == 3
    assert sent["asset_refs"] == held.output["asset_refs"]
    assert "3장" in sent["summary"] and "01" in sent["summary"] and "03" in sent["summary"]
    assert (sent["ai_label_missing_count"], sent["text_mismatch_count"]) == (0, 0)
    assert sum(adapter.calls.values()) == 0  # 승인 전에는 Instagram 호출 0회
    assert _row().status == pending.HELD


@pytest.mark.asyncio
async def test_approval_publishes_children_then_parent_then_publish_once(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    draft = _ready(4)
    await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    gate.approve_all()
    done = await publisher.publish_sns(
        _publish_request(draft), handlers.STATE.sns
    )  # 다시 보내면 승인 확인
    assert done.outcome == "published", done
    row = _row()
    assert row.status == pending.PUBLISHED
    assert adapter.calls["create_carousel_item"] == 4
    assert adapter.calls["create_carousel_container"] == 1
    assert adapter.calls["media_publish"] == 1
    assert adapter.calls["create_container"] == 0  # 단일 컨테이너 경로를 타지 않았다
    assert adapter.carousel_item_refs == [a["asset_ref"] for a in draft["assets"]]  # 순서 그대로
    parent = adapter.carousel_requests[0]
    assert parent.is_ai_generated is True and len(parent.child_ids) == 4  # AI 표시는 부모에만
    assert row.container_id and row.container_id.startswith("mock_carousel_")
    assert len(row.result["children"]) == 4 and row.result["item_count"] == 4
    assert row.result["permalink"].startswith("https://")
    again = await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    assert again.outcome == "published" and adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_changed_item_after_hold_needs_new_approval(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    draft = _ready(2)
    held = await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    review_id = held.output["review_id"]
    target = sns_media.resolve_asset(draft["assets"][0]["asset_ref"])
    target.write_bytes(target.read_bytes() + b"x")  # 승인 대기 중에 한 장이 바뀌었다
    gate.approve_all()
    row = _row()
    await publisher.apply_decision(row, "approved", "event")
    assert publisher.store().by_review(review_id).failure_code == "content_changed"  # type: ignore[union-attr]
    assert sum(adapter.calls.values()) == 0


# --- publish_sns: 승인 전 거절(fail-closed) ---


@pytest.mark.asyncio
async def test_publish_refuses_carousel_with_unlabeled_item_before_asking_a_human(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    draft = _draft([_item(1), _item(2, overlay=False)])
    result = await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    assert result.status == TaskStatus.FAILED and result.outcome == "carousel_not_publishable"
    assert any("item_2:ai_label_overlay_missing" in p for p in result.output["problems"])
    assert gate.calls == [] and sum(adapter.calls.values()) == 0


@pytest.mark.asyncio
async def test_publish_refuses_without_matching_draft(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    draft = _ready(2)
    other = _ready(3)
    no_store = await publisher.publish_sns(_publish_request(draft), None)
    assert no_store.outcome == "carousel_not_publishable"
    swapped = await publisher.publish_sns(
        _publish_request(
            draft,
            asset_refs=[a["asset_ref"] for a in other["assets"][1:]],
            asset_sha256s=[a["asset_sha256"] for a in other["assets"][1:]],
        ),
        handlers.STATE.sns,
    )
    assert swapped.outcome == "carousel_not_publishable"
    assert gate.calls == [] and sum(adapter.calls.values()) == 0


@pytest.mark.asyncio
async def test_adapter_without_carousel_support_is_refused_before_approval(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.supports_carousel = False
    result = await publisher.publish_sns(_publish_request(_ready(2)), handlers.STATE.sns)
    assert result.outcome == "carousel_unsupported" and gate.calls == []


@pytest.mark.asyncio
async def test_real_graph_adapter_keeps_carousel_off_until_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_agent.sns_publish.instagram_graph import GraphInstagramAdapter

    monkeypatch.setenv("MARKETING_IG_ACCESS_TOKEN", "t-placeholder")
    monkeypatch.setenv("MARKETING_IG_USER_ID", "u-placeholder")
    monkeypatch.delenv("MARKETING_IG_CAROUSEL_ENABLED", raising=False)
    assert GraphInstagramAdapter.from_env().supports_carousel is False
    monkeypatch.setenv("MARKETING_IG_CAROUSEL_ENABLED", "true")
    assert GraphInstagramAdapter.from_env().supports_carousel is True


# --- 중간 실패·재시작 멱등 ---


async def _approved(gate: FakeGate, draft: dict) -> None:
    await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    gate.approve_all()


@pytest.mark.asyncio
async def test_fatal_child_error_fails_without_parent_or_publish(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.fail_on("create_carousel_item", 4, 2207051)  # 스팸 의심: 즉시 실패
    draft = _ready(3)
    await _approved(gate, draft)
    await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    row = _row()
    assert row.status == pending.FAILED and row.failure_code == "ig_spam_suspected"
    assert adapter.calls["create_carousel_container"] == 0 and adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_transient_child_error_is_retried_then_publishes_once(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.fail_on("create_carousel_item", -1, 2207001, times=1)  # 일시 오류 1회
    draft = _ready(2)
    await _approved(gate, draft)
    await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    assert _row().status == pending.PUBLISHED and adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_lost_publish_response_is_resolved_by_parent_status_not_republish(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.lose_response_on("media_publish", processed=True)
    draft = _ready(3)
    await _approved(gate, draft)
    await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    row = _row()
    assert row.status == pending.PUBLISHED and row.media_id
    assert adapter.calls["media_publish"] == 1
    assert adapter.calls["create_carousel_container"] == 1


def _interrupted(sent: bool, container: str | None, children: list[str]) -> None:
    row = pending.PendingPost(
        idempotency_key="k1",
        tenant_id=TENANT,
        context_id=CTX,
        post_draft_id="spd_car",
        content_sha="s",
        execution_id="exe_1",
        review_id="rev_1",
        status=pending.PUBLISHING,
        params={"post_draft_id": "spd_car", "media_kind": "carousel", "asset_refs": ["a", "b"]},
    )
    assert publisher.store().create(row)
    publisher.store().update(
        "k1", container_id=container, publish_sent=sent, result={"children": children}
    )


async def _build_carousel(adapter: MockInstagramAdapter) -> tuple[str, list[str]]:
    kids = [await adapter.create_carousel_item(f"ref{i}") for i in range(2)]
    for kid in kids:
        await adapter.get_container_status(kid)  # FINISHED가 된 뒤에만 부모를 만들 수 있다
    parent = await adapter.create_carousel_container(
        instagram.CarouselRequest(tuple(kids), "c", True)
    )
    await adapter.get_container_status(parent)
    return parent, kids


@pytest.mark.asyncio
async def test_restart_after_publish_sent_does_not_republish_published_carousel(
    adapter: MockInstagramAdapter,
) -> None:
    parent, kids = await _build_carousel(adapter)
    media_id = await adapter.media_publish(parent)  # 게시는 됐는데 서버가 죽었다
    _interrupted(True, parent, kids)
    await publisher.recover_interrupted()
    row = publisher.store().get("k1")
    assert row is not None and row.status == pending.PUBLISHED and row.media_id == media_id
    assert adapter.calls["media_publish"] == 1  # 두 번 올리지 않았다
    assert adapter.calls["create_carousel_item"] == 2  # 자식도 다시 만들지 않았다


@pytest.mark.asyncio
async def test_restart_before_publish_sent_fails_and_never_publishes(
    adapter: MockInstagramAdapter,
) -> None:
    parent, kids = await _build_carousel(adapter)
    _interrupted(False, parent, kids)  # 자식·부모는 만들었지만 게시 전에 멈춤
    await publisher.recover_interrupted()
    row = publisher.store().get("k1")
    assert row is not None and row.failure_code == "interrupted_before_publish"
    assert adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_restart_with_unconfirmed_parent_fails_without_republish(
    adapter: MockInstagramAdapter,
) -> None:
    parent, kids = await _build_carousel(adapter)
    _interrupted(True, parent, kids)  # 보냈다고 기록했지만 부모는 게시 상태가 아니다
    await publisher.recover_interrupted()
    assert publisher.store().get("k1").failure_code == "publish_unconfirmed"  # type: ignore[union-attr]
    assert adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_restart_with_approved_unstarted_carousel_publishes_once(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    draft = _ready(2)
    await _approved(gate, draft)
    row = _row()
    publisher.store().transition(row.idempotency_key, pending.HELD, pending.APPROVED)
    assert await publisher.recover_interrupted() == 1
    assert _row().status == pending.PUBLISHED and adapter.calls["media_publish"] == 1
    assert await publisher.recover_interrupted() == 0  # 다시 돌려도 더 올리지 않는다
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_real_safety_gate_holds_carousel_for_a_human_and_shows_count(
    adapter: MockInstagramAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ax_contracts.schemas import ExecuteRequest
    from ax_safety.gate import Gate
    from ax_safety.rules import load_registry, load_thresholds
    from ax_safety.store import Store
    from ax_safety.verify import publish_safety_event

    real = Gate(load_registry(), load_thresholds(), Store(), None, publish_safety_event)  # type: ignore[arg-type]

    async def through_gate(action: str, params: dict, meta: RequestMeta, evidence: Any):
        return await real.execute(
            ExecuteRequest(
                action=action,
                requester="marketing",
                context_id=meta.context_id,
                tenant_id=meta.tenant_id,
                idempotency_key=meta.idempotency_key or "",
                params=params,
                evidence=evidence,
            )
        )

    monkeypatch.setattr(publisher, "execute", through_gate)
    publisher.setup_subscriber()
    draft = _ready(3)
    held = await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    assert held.output["grade"] == "C" and held.output["item_count"] == 3
    review = real.store.get_review(held.output["review_id"])
    assert review is not None
    assert "3장" in review.report["summary"]  # 승인 대기 큐 요약에 장수
    assert sum(adapter.calls.values()) == 0

    await real.decide_review(held.output["review_id"], "approved", "taeyoon", None)  # 사람 승인
    assert _row().status == pending.PUBLISHED
    assert adapter.calls["media_publish"] == 1 and adapter.calls["create_carousel_item"] == 3


def test_mock_rejects_out_of_range_parent_and_unknown_children() -> None:
    import asyncio

    mock = MockInstagramAdapter()

    async def go() -> None:
        kid = await mock.create_carousel_item("a")
        with pytest.raises(instagram.IgApiError):
            await mock.create_carousel_container(instagram.CarouselRequest((kid,), "c", True))
        with pytest.raises(instagram.IgApiError):
            await mock.create_carousel_container(
                instagram.CarouselRequest((kid, "nope"), "c", True)
            )

    asyncio.run(go())


# --- 같은 비율 강제(첫 장 비율로 잘린다) / 부모 FINISHED 대기 / 건수 ---


def _resize(ref: str, width: int, height: int) -> None:
    _make_jpeg(sns_media.resolve_asset(ref), width, height, 7)


def test_draft_rejects_different_aspect_ratios() -> None:
    a, b = _item(1), _item(2)
    _resize(b, 1080, 566)  # 1.91:1 (규격 안이지만 첫 장 4:5와 다르다)
    out = _call(
        "draft_sns_post", {"coupon": _coupon(), "media_kind": "carousel", "asset_paths": [a, b]}
    )
    assert out["status"] == "failed" and out["outcome"] == "carousel_aspect_mismatch"


def test_draft_accepts_same_ratio_with_small_tolerance_and_different_pixels() -> None:
    a, b = _item(1), _item(2)
    _resize(b, 864, 1080)  # 4:5, 크기만 다르다
    assert _draft([a, b])["media_kind"] == "carousel"
    c = _item(3)
    _resize(c, 1080, 1352)  # 0.15% 차이는 허용 오차 안
    assert _draft([a, c])["media_kind"] == "carousel"


def test_check_rejects_aspect_change_after_draft() -> None:
    draft = _ready(2)
    _resize(draft["assets"][1]["asset_ref"], 1080, 566)  # 초안 뒤에 비율이 달라졌다
    check = _check(draft)
    assert check["verdict"] == "rejected" and check["publishable"] is False
    assert "carousel_aspect_mismatch" in check["problems"]
    assert "carousel_aspect_differs" in check["warnings"]  # 경고 코드는 그대로 남는다


@pytest.mark.asyncio
async def test_publish_refuses_mixed_aspect_before_approval(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    draft = _ready(2)
    _resize(draft["assets"][1]["asset_ref"], 1080, 566)
    out = await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    assert out.outcome == "carousel_not_publishable"
    assert "carousel_aspect_mismatch" in out.output["problems"]
    assert gate.calls == [] and sum(adapter.calls.values()) == 0


@pytest.mark.asyncio
async def test_children_and_parent_must_be_finished_before_publish(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.in_progress_polls = 1  # 모든 컨테이너가 처음 한 번은 IN_PROGRESS
    draft = _ready(3)
    await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    gate.approve_all()
    done = await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    assert done.outcome == "published", done
    # 자식 3 + 부모 1 컨테이너가 각각 IN_PROGRESS 1회 + FINISHED 1회
    assert adapter.calls["get_container_status"] == 8
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_child_error_stops_before_parent(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.container_error(1, None)
    draft = _ready(2)
    await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    gate.approve_all()
    await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    row = _row()
    assert (row.status, row.failure_code) == (pending.FAILED, "ig_container_error")
    assert adapter.calls["create_carousel_container"] == 0
    assert adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_carousel_counts_as_one_post_for_the_daily_limit(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    store = publisher.store()
    stamp = datetime.now(UTC).isoformat()
    for i in range(44):
        key = f"seed-{i}"
        row = pending.PendingPost(
            key, TENANT, CTX, f"s{i}", "a" * 64, None, None, pending.PUBLISHED, {}
        )
        assert store.create(row)
        store._conn.execute(  # noqa: SLF001
            "UPDATE sns_pending SET updated_at = ?, publish_sent = 1 WHERE idempotency_key = ?",
            (stamp, key),
        )
    draft = _ready(10)  # 10장이어도 1건
    await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    gate.approve_all()
    done = await publisher.publish_sns(_publish_request(draft), handlers.STATE.sns)
    assert done.outcome == "published"
    assert store.count_posts_since(datetime.now(UTC) - timedelta(hours=24)) == 45
    refused, _ = await publisher._pre_gate(adapter)  # noqa: SLF001
    assert refused == "daily_limit_reached"
