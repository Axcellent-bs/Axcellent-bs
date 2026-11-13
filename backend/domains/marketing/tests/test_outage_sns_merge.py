"""장애 사과·보상 초안 유사 병합 (`merge_outage_sns_drafts`, 규칙 기반 1단계)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from marketing_agent import handlers, outage_sns, sns
from marketing_agent.main import app, load_card
from marketing_agent.sns_publish import pending as pend
from marketing_agent.sns_publish import publisher

CTX = "ctx_inc_0001"
client = TestClient(app)
SEOUL = outage_sns.KST


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()
    monkeypatch.setattr(publisher.RT, "store", pend.PendingStore(":memory:"))


def _call(skill: str, input: dict, token: str = "mock:dashboard-api") -> dict:
    body = {
        "meta": {
            "context_id": CTX,
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": input,
    }
    return client.post("/a2a", json=body).json()


def _notice_id(kind: str = "incident_resolved") -> str:
    orch = "mock:orchestrator"
    draft = _call("draft_notice", {"kind": kind, "facts": {"component": "payment"}}, orch)["output"]
    return _call("publish_notice", {"draft_id": draft["draft_id"]}, orch)["output"]["notice_id"]


def _draft(**over) -> sns.SnsPostDraft:
    """초안 한 건을 만들어 저장소에 둔다(받은 입력으로 `draft_outage_sns_post`)."""
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


def _variant(base: sns.SnsPostDraft, suffix: str, caption: str | None = None) -> sns.SnsPostDraft:
    """같은 글에 끝 공백만 달리 붙인 다른 id의 초안(필수 문구는 그대로라 점검을 통과한다)."""
    text = caption if caption is not None else base.caption + " \n"
    draft = base.model_copy(update={"post_draft_id": f"spd_{suffix}", "caption": text})
    handlers.STATE.sns.save(draft)
    return draft


def _stamp(draft_id: str, when: datetime) -> None:
    store = handlers.STATE.sns
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sns_drafts SET created_at = ? WHERE post_draft_id = ?",
            (when.isoformat(), draft_id),
        )


NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _merge(**input) -> dict:
    result = _call("merge_outage_sns_drafts", input)
    assert result["status"] == "completed", result
    return result["output"]


def _list(**input) -> dict:
    return _call("list_outage_sns_drafts", input, token="mock:shop-api")["output"]


# --- 유사 판정 ---------------------------------------------------------------------------


def test_threshold_is_pinned_and_conservative() -> None:
    assert outage_sns.SIMILARITY_THRESHOLD == 0.9
    assert (outage_sns.GROUP_MIN, outage_sns.GROUP_MAX) == (2, 10)


def test_whitespace_punctuation_and_label_spacing_are_normalized() -> None:
    base = _draft()
    noisy = base.caption.replace("\n", "\r\n\r\n").replace(". ", " .  ").replace("!", "！")
    assert outage_sns.similar_captions(base.caption, noisy)
    assert outage_sns.similar_captions(base.caption, f"  {base.caption}  \n\n")


def test_different_key_sentences_are_never_similar() -> None:
    confirmed = _draft().caption
    unknown = _draft(cause_code="unknown", action_code="restored").caption
    no_fix = _draft(cause_code="unknown", action_code="none").caption
    assert not outage_sns.similar_captions(confirmed, unknown)
    assert not outage_sns.similar_captions(unknown, no_fix)
    comp = _call(
        "draft_outage_sns_post",
        {"trigger": "compensation", "component": "payment", "compensation_issued": True},
        token="mock:orchestrator",
    )["output"]["caption"]
    comp_paid = _call(
        "draft_outage_sns_post",
        {
            "trigger": "compensation",
            "component": "payment",
            "compensation_issued": True,
            "recovered": True,
            "action_code": "restored",
        },
        token="mock:orchestrator",
    )["output"]["caption"]
    assert not outage_sns.similar_captions(comp, comp_paid)
    # 보상 문장만 없앤 글도 다른 글이다
    assert not outage_sns.similar_captions(
        comp_paid, comp_paid.replace(outage_sns.COMPENSATION_TEXT, "")
    )


def test_near_match_needs_threshold_and_known_key_sentences() -> None:
    base = _draft().caption
    # 핵심 문장은 같고 어절 하나만 더한 글: 겹침이 임계값을 넘는다
    assert outage_sns.similar_captions(base, base + " 감사합니다")
    # 많이 덧붙인 글은 겹침 비율이 낮아 다른 글이다
    assert not outage_sns.similar_captions(
        base, base + " " + " ".join(f"추가{i}" for i in range(20))
    )


# --- 병합 ----------------------------------------------------------------------------------


def _three_dupes() -> tuple[sns.SnsPostDraft, list[sns.SnsPostDraft]]:
    first = _draft()
    old = _variant(first, "old")
    mid = _variant(first, "mid", first.caption + "\n")
    _stamp(old.post_draft_id, NOW - timedelta(hours=3))
    _stamp(mid.post_draft_id, NOW - timedelta(hours=2))
    _stamp(first.post_draft_id, NOW - timedelta(hours=1))
    return first, [mid, old]


def test_dry_run_previews_without_saving() -> None:
    first, others = _three_dupes()
    before = _list(include_merged=True)
    out = _merge(dry_run=True)
    assert out["dry_run"] is True
    assert len(out["groups"]) == 1
    group = out["groups"][0]
    assert group["representative"] == first.post_draft_id  # 가장 최근
    assert group["merged_from"] == [o.post_draft_id for o in others]
    assert out["merged_count"] == 2 and out["remaining_count"] == 1
    assert _list(include_merged=True) == before  # 저장 없음
    assert handlers.STATE.sns.get(first.post_draft_id).merged_from == []


def test_merge_marks_originals_and_keeps_captions_and_times() -> None:
    first, others = _three_dupes()
    captions = {d.post_draft_id: d.caption for d in [first, *others]}
    out = _merge(dry_run=False)
    assert out["merged_count"] == 2
    rep = handlers.STATE.sns.get(first.post_draft_id)
    assert rep.status == "draft" and sorted(rep.merged_from) == sorted(
        o.post_draft_id for o in others
    )
    for other in others:
        saved = handlers.STATE.sns.get(other.post_draft_id)
        assert (saved.status, saved.merged_into) == ("merged", first.post_draft_id)
        assert saved.merged_at is not None
    for pid, caption in captions.items():  # 새 문장을 만들지 않는다
        assert handlers.STATE.sns.get(pid).caption == caption
    # 저장 시각(목록 순서의 근거)은 그대로다
    shown = _list(include_merged=True)["items"]
    assert [i["post_draft_id"] for i in shown] == [
        first.post_draft_id,
        others[0].post_draft_id,
        others[1].post_draft_id,
    ]


def test_merge_is_idempotent() -> None:
    first, _ = _three_dupes()
    _merge(dry_run=False)
    snapshot = _list(include_merged=True)
    again = _merge(dry_run=False)
    assert again["groups"] == [] and again["merged_count"] == 0
    assert _list(include_merged=True) == snapshot
    assert _merge(dry_run=True)["groups"] == []
    assert handlers.STATE.sns.get(first.post_draft_id) is not None


def test_list_hides_merged_by_default_and_toggle_shows_them() -> None:
    first, others = _three_dupes()
    _merge(dry_run=False)
    hidden = _list()
    assert [i["post_draft_id"] for i in hidden["items"]] == [first.post_draft_id]
    assert (hidden["total"], hidden["merged_total"]) == (1, 2)
    assert hidden["items"][0]["merged_from"] == sorted(o.post_draft_id for o in others)
    shown = _list(include_merged=True)
    assert shown["total"] == 3 and len(shown["items"]) == 3
    merged = [i for i in shown["items"] if i["status"] == "merged"]
    assert {i["merged_into"] for i in merged} == {first.post_draft_id}


def test_recreating_same_draft_keeps_merge_marks() -> None:
    first, others = _three_dupes()
    _merge(dry_run=False)
    handlers.STATE.sns.save(others[0].model_copy(update={"status": "draft", "merged_into": None}))
    assert handlers.STATE.sns.get(others[0].post_draft_id).status == "merged"
    handlers.STATE.sns.save(first.model_copy(update={"merged_from": []}))
    assert len(handlers.STATE.sns.get(first.post_draft_id).merged_from) == 2


def test_newer_draft_takes_over_and_children_follow() -> None:
    first, others = _three_dupes()
    _merge(dry_run=False)
    newest = _variant(first, "newest", first.caption + "\n\n")
    _stamp(newest.post_draft_id, NOW)
    out = _merge(dry_run=False)
    assert out["groups"][0]["representative"] == newest.post_draft_id
    rep = handlers.STATE.sns.get(newest.post_draft_id)
    assert sorted(rep.merged_from) == sorted(
        [first.post_draft_id, *(o.post_draft_id for o in others)]
    )
    for pid in rep.merged_from:
        assert handlers.STATE.sns.get(pid).merged_into == newest.post_draft_id
    assert handlers.STATE.sns.get(first.post_draft_id).merged_from == []


# --- 후보에서 빠지는 것 ----------------------------------------------------------------------


def test_different_key_sentences_do_not_group() -> None:
    a = _draft()
    b = _draft(cause_code="unknown", action_code="restored", notice_id=a.source_ref)
    _stamp(a.post_draft_id, NOW)
    _stamp(b.post_draft_id, NOW - timedelta(hours=1))
    assert _merge()["groups"] == []


def test_requires_same_trigger_notice_kind_and_media_kind() -> None:
    base = _draft()
    other_trigger = _variant(base, "trig")
    handlers.STATE.sns.save(other_trigger.model_copy(update={"outage_trigger": "compensation"}))
    reel = _variant(base, "reel")
    handlers.STATE.sns.save(reel.model_copy(update={"media_kind": "reel"}))
    kind = _variant(base, "kind")
    handlers.STATE.sns.save(kind.model_copy(update={"notice_kind": "incident_opened"}))
    for draft in (base, other_trigger, reel, kind):
        _stamp(draft.post_draft_id, NOW)
    assert _merge()["groups"] == []


def test_needs_same_source_or_same_korean_date() -> None:
    base = _draft()
    other = _variant(base, "other")
    other = other.model_copy(update={"source_ref": "ntc_another"})
    handlers.STATE.sns.save(other)
    # 한국 날짜가 다르고(UTC 14:59 = KST 23:59 / UTC 15:00 = 다음 날 00:00) 출처도 다르다
    _stamp(base.post_draft_id, datetime(2026, 11, 13, 14, 59, tzinfo=UTC))
    _stamp(other.post_draft_id, datetime(2026, 11, 13, 15, 0, tzinfo=UTC))
    assert _merge()["groups"] == []
    # 같은 한국 날짜면 출처가 달라도 묶인다
    _stamp(other.post_draft_id, datetime(2026, 11, 13, 1, 0, tzinfo=UTC))
    assert len(_merge()["groups"]) == 1
    # 한국 날짜가 달라도 같은 출처면 묶인다
    handlers.STATE.sns.update_meta([other.model_copy(update={"source_ref": base.source_ref})])
    _stamp(other.post_draft_id, datetime(2026, 11, 20, 1, 0, tzinfo=UTC))
    assert len(_merge()["groups"]) == 1


def test_media_attached_and_pending_publish_are_left_alone() -> None:
    base = _draft()
    media = _variant(base, "media")
    handlers.STATE.sns.save(media.model_copy(update={"asset_ref": "a/labeled.jpg"}))
    held = _variant(base, "held")
    publisher.store().create(
        pend.PendingPost(
            idempotency_key="k1",
            tenant_id="t",
            context_id=CTX,
            post_draft_id=held.post_draft_id,
            content_sha="x",
            execution_id=None,
            review_id="rev_1",
            status=pend.HELD,
            params={},
        )
    )
    for draft in (base, media, held):
        _stamp(draft.post_draft_id, NOW)
    out = _merge(dry_run=False)
    assert out["groups"] == []
    assert {"id": held.post_draft_id, "reason": "publish_in_progress_or_done"} in out["skipped"]
    assert handlers.STATE.sns.get(held.post_draft_id).status == "draft"
    assert handlers.STATE.sns.get(media.post_draft_id).status == "draft"


def test_publish_state_unreadable_means_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _draft()
    twin = _variant(base, "twin")

    def boom(_: str) -> list:
        raise pend.PendingStorageError("down")

    monkeypatch.setattr(publisher.store(), "with_draft", boom)
    out = _merge(dry_run=False)
    assert out["groups"] == [] and out["merged_count"] == 0
    assert {s["reason"] for s in out["skipped"]} == {"publish_state_unknown"}
    assert handlers.STATE.sns.get(twin.post_draft_id).status == "draft"


def test_failed_check_drafts_are_not_merged() -> None:
    base = _draft()
    bad = _variant(base, "bad", base.caption.replace(sns.AI_LABEL_TEXT, ""))
    for draft in (base, bad):
        _stamp(draft.post_draft_id, NOW)
    out = _merge(dry_run=False)
    assert out["groups"] == []
    assert {"id": bad.post_draft_id, "reason": "check_failed"} in out["skipped"]
    assert handlers.STATE.sns.get(bad.post_draft_id).status == "draft"


def test_group_size_is_capped_at_ten() -> None:
    base = _draft()
    _stamp(base.post_draft_id, NOW)
    for i in range(11):
        extra = _variant(base, f"x{i:02d}", base.caption + "\n" * (i + 1))
        _stamp(extra.post_draft_id, NOW - timedelta(minutes=i + 1))
    out = _merge(dry_run=True)
    assert [len(g["merged_from"]) + 1 for g in out["groups"]] == [10, 2]


def test_draft_ids_limit_the_pool_and_report_skips() -> None:
    first, others = _three_dupes()
    out = _merge(
        dry_run=False, draft_ids=[first.post_draft_id, others[0].post_draft_id, "spd_nope"]
    )
    assert out["groups"][0]["merged_from"] == [others[0].post_draft_id]
    assert {"id": "spd_nope", "reason": "not_found"} in out["skipped"]
    assert handlers.STATE.sns.get(others[1].post_draft_id).status == "draft"
    again = _merge(dry_run=False, draft_ids=[others[0].post_draft_id, first.post_draft_id])
    assert again["groups"] == []
    assert {"id": others[0].post_draft_id, "reason": "already_merged"} in again["skipped"]


def test_old_records_without_new_fields_still_read() -> None:
    base = _draft()
    store = handlers.STATE.sns
    legacy = base.model_dump(mode="json")
    for key in ("status", "merged_into", "merged_from", "merged_at"):
        legacy.pop(key)
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sns_drafts SET payload = ? WHERE post_draft_id = ?",
            (json.dumps(legacy, ensure_ascii=False), base.post_draft_id),
        )
    loaded = store.get(base.post_draft_id)
    assert (loaded.status, loaded.merged_into, loaded.merged_from) == ("draft", None, [])
    assert _list()["total"] == 1


def test_publish_records_and_captions_untouched_by_merge() -> None:
    first, _ = _three_dupes()
    caption_hash = first.caption_sha256
    _merge(dry_run=False)
    saved = handlers.STATE.sns.get(first.post_draft_id)
    assert saved.caption_sha256 == caption_hash and saved.caption == first.caption
    assert publisher.store().with_status(pend.HELD) == []


# --- 호출 권한·입력 ---------------------------------------------------------------------------


def test_card_grade_and_requesters() -> None:
    card = {s.id: s for s in load_card().skills}
    assert card["merge_outage_sns_drafts"].grade == "read"
    assert card["merge_outage_sns_drafts"].allowed_requesters == ["dashboard-api"]
    for caller in ("orchestrator", "shop-api", "planning"):
        denied = _call("merge_outage_sns_drafts", {}, token=f"mock:{caller}")
        assert (denied["status"], denied["outcome"]) == ("rejected", "FORBIDDEN_SKILL"), caller


@pytest.mark.parametrize("payload", [{"dry_run": "maybe"}, {"draft_ids": "x"}, {"extra": 1}])
def test_invalid_input(payload: dict) -> None:
    result = _call("merge_outage_sns_drafts", payload)
    assert (result["status"], result["outcome"]) == ("failed", "invalid_input")


def test_default_is_preview_only() -> None:
    _three_dupes()
    out = _merge()
    assert out["dry_run"] is True and out["merged_count"] == 2
    assert _list()["merged_total"] == 0
