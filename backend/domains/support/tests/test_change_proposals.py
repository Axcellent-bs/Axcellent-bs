"""기획에 보낼 변경 요청 제안(#281 1단계): 급증에서 코드가 안만 만들고 아무것도 보내지 않는다."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent import guard, handlers, insights, proposal_rules
from support_agent.change_requests import ChangeRequestStore
from support_agent.insight_schemas import SurgeReport, TopicSurge
from support_agent.main import ALL_HANDLERS, app, load_card
from support_agent.schemas import Analysis, Classification, Triage
from support_agent.store import InquiryStore

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def add(store: InquiryStore, category: str, minutes_ago: float) -> int:
    conversation = store.create(
        context_id="ctx_req_0001",
        tenant_id="shop-demo",
        user_id=1,
        customer_name="김하늘",
        subject="문의 제목",
        order_id=None,
        order_no=None,
        first_message="고객이 쓴 본문",
        analysis=Analysis(
            classification=Classification(
                intent_ko="문의",
                category=category,
                sentiment="calm",
                urgency="medium",
                search_query=category,
            ),
            drafts=[],
        ),
    )
    conversation.messages[0].created_at = iso(minutes_ago)
    return conversation.id


def report(*surges: TopicSurge) -> SurgeReport:
    return SurgeReport(
        window_minutes=60,
        baseline_hours=24,
        evaluated_at="2026-10-22T12:00:00Z",
        total_in_window=sum(s.count for s in surges),
        surges=list(surges),
    )


def surge(topic: str = "shipping", ids: list[int] | None = None, **kw: Any) -> TopicSurge:
    return TopicSurge(
        topic_code=topic,
        count=kw.pop("count", 7),
        baseline_per_window=kw.pop("baseline_per_window", 1.4),
        ratio=kw.pop("ratio", 5.0),
        sample_inquiry_ids=[5, 3, 9] if ids is None else ids,
    )


def never(_: int) -> bool:
    return False


# ---------------------------------------------------------------- 규칙 (저장소 없이)
def test_no_surge_means_no_proposal() -> None:
    assert proposal_rules.build(report(), has_request=never).proposals == []


def test_one_proposal_per_surged_topic_with_the_oldest_inquiry_as_representative() -> None:
    out = proposal_rules.build(
        report(surge("shipping"), surge("payment", [4, 2])), has_request=never
    )
    shipping, payment = out.proposals
    assert (shipping.topic_code, shipping.inquiry_id) == ("shipping", 3)  # 샘플 중 가장 작은 번호
    assert shipping.evidence_ids == ["inq_5", "inq_9"]  # 대표 문의를 뺀 나머지 샘플
    assert (payment.inquiry_id, payment.evidence_ids) == (2, ["inq_4"])
    assert shipping.title == "배송 문의 급증 — 안내·화면 점검 검토"
    assert shipping.evidence == "최근 60분 배송 문의 7건 (평소 창당 1.4건의 5배)"
    assert (shipping.count, shipping.ratio) == (7, 5.0)


def test_the_baseline_is_stated_as_unknown_when_there_is_none() -> None:
    out = proposal_rules.build(
        report(surge(baseline_per_window=0.0, ratio=None)), has_request=never
    )
    assert out.proposals[0].evidence.endswith("(평소 기록 없음)")
    assert out.proposals[0].ratio is None


def test_an_unknown_topic_is_labelled_as_other_not_echoed() -> None:
    out = proposal_rules.build(report(surge("zzz-<script>")), has_request=never)
    assert out.proposals[0].title.startswith("기타 문의 급증")
    assert "zzz" not in out.proposals[0].title + out.proposals[0].evidence


def test_a_surge_without_sample_inquiries_gets_no_proposal() -> None:
    assert proposal_rules.build(report(surge(ids=[])), has_request=never).proposals == []


def test_already_requested_follows_the_representative_inquiry() -> None:
    out = proposal_rules.build(report(surge()), has_request=lambda i: i == 3)
    assert out.proposals[0].already_requested is True
    out = proposal_rules.build(report(surge()), has_request=lambda i: i == 5)  # 대표가 아니다
    assert out.proposals[0].already_requested is False


def test_titles_and_evidence_always_pass_the_request_change_pii_check() -> None:
    out = proposal_rules.build(
        report(*(surge(t) for t in proposal_rules.TOPIC_KO)), has_request=never
    )
    assert len(out.proposals) == len(proposal_rules.TOPIC_KO)
    for p in out.proposals:
        assert not guard.find_pii(p.title) and not guard.find_pii(p.evidence)
        assert len(p.title) <= 200 and len(p.evidence) <= 500 and len(p.evidence_ids) <= 20


def test_the_causes_the_agents_named_are_added_to_the_evidence() -> None:
    labels = {"shipping": [("택배사 연동 오류", 4), ("주소 입력 화면 오류", 2)]}
    out = proposal_rules.build(
        report(surge()), has_request=never, labels_for=lambda t: labels.get(t, [])
    )
    evidence = out.proposals[0].evidence
    assert evidence.startswith("최근 60분 배송 문의 7건")  # 기존 근거는 그대로 앞에 있다
    assert evidence.endswith("상담원이 정한 원인: 택배사 연동 오류 4건, 주소 입력 화면 오류 2건")


def test_without_named_causes_the_evidence_is_unchanged() -> None:
    plain = proposal_rules.build(report(surge()), has_request=never).proposals[0].evidence
    other = (
        proposal_rules.build(report(surge()), has_request=never, labels_for=lambda _t: [])
        .proposals[0]
        .evidence
    )
    assert other == plain and "상담원이 정한 원인" not in plain


def test_a_cause_name_that_would_break_the_limits_is_left_out_not_sent() -> None:
    too_long = [("가" * 200, 1), ("나" * 200, 1), ("다" * 200, 1)]
    out = proposal_rules.build(report(surge()), has_request=never, labels_for=lambda _t: too_long)
    assert "상담원이 정한 원인" not in out.proposals[0].evidence  # 500자를 넘으면 붙이지 않는다
    pii = proposal_rules.build(
        report(surge()), has_request=never, labels_for=lambda _t: [("010-1234-5678 번호", 1)]
    )
    assert "상담원이 정한 원인" not in pii.proposals[0].evidence  # 개인정보가 있으면 붙이지 않는다
    for p in (*out.proposals, *pii.proposals):
        assert len(p.evidence) <= 500 and not guard.find_pii(p.evidence)


def _name(store: InquiryStore, inquiry_id: int, label: str | None, state: str = "done") -> None:
    if label is None:
        return
    store.set_triage(
        inquiry_id,
        Triage(
            state=state,  # type: ignore[arg-type]
            reason="unclassified",
            marked_by="auto",
            marked_at=iso(1),
            label=label,
            decided_at=iso(1) if state == "done" else None,
        ),
    )


def test_cause_names_are_counted_like_the_surge_and_only_once_per_inquiry() -> None:
    store = InquiryStore()
    in_window = [add(store, "shipping", 5) for _ in range(4)]
    _name(store, in_window[0], "택배사 연동 오류")
    _name(store, in_window[1], "택배사 연동 오류")
    _name(store, in_window[2], "주소 입력 화면 오류")
    _name(store, in_window[3], "아직 정하는 중", state="needed")  # 마치지 않은 것은 세지 않는다
    old = add(store, "shipping", 600)  # 창 밖
    _name(store, old, "오래된 원인")
    other_topic = add(store, "payment", 5)  # 다른 분류
    _name(store, other_topic, "결제창 오류")
    got = insights.triage_label_counts(store, "shipping", NOW, window_minutes=60)
    assert got == [("택배사 연동 오류", 2), ("주소 입력 화면 오류", 1)]
    assert insights.triage_label_counts(store, "refund", NOW, window_minutes=60) == []


def test_only_the_top_causes_are_returned() -> None:
    store = InquiryStore()
    for n, label in enumerate(["가", "나", "다", "라", "마"]):
        for _ in range(5 - n):
            _name(store, add(store, "shipping", 5), label)
    got = insights.triage_label_counts(store, "shipping", NOW, window_minutes=60)
    assert [name for name, _ in got] == ["가", "나", "다"]  # 상위 3개, 많은 순


# ---------------------------------------------------------------- skill
@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(handlers, "CHANGES", ChangeRequestStore())
    return TestClient(app)


def call(client: TestClient, skill: str, input: dict[str, Any] | None = None) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_req_0001",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": input or {},
    }
    return client.post("/a2a", json=body).json()


def test_the_skill_is_declared_read_only_for_dashboard_and_orchestrator() -> None:
    card = {s.id: s for s in load_card().skills}
    assert "propose_change_requests" in ALL_HANDLERS
    skill = card["propose_change_requests"]
    assert skill.grade == "read"
    assert set(skill.allowed_requesters) == {"dashboard-api", "orchestrator"}


def test_the_skill_validates_its_input(client: TestClient) -> None:
    assert call(client, "propose_change_requests", {"ratio": 0.5})["status"] == "failed"
    ok = call(client, "propose_change_requests", {"window_minutes": 30})
    assert ok["output"]["window_minutes"] == 30 and ok["output"]["proposals"] == []


def test_the_skill_proposes_from_the_inquiry_records_without_original_text(
    client: TestClient,
) -> None:
    now = datetime.now(UTC)
    for _ in range(6):  # 평소(지난 24시간)는 거의 없고 최근 창에 몰린 배송 문의
        add(handlers.STORE, "shipping", 5)
    # `add`의 시각은 고정 NOW 기준이라 실제 지금 기준으로 다시 맞춘다
    for conversation in handlers.STORE.all():
        conversation.messages[0].created_at = (now - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    result = call(client, "propose_change_requests")
    proposals = result["output"]["proposals"]
    assert [p["topic_code"] for p in proposals] == ["shipping"]
    assert proposals[0]["inquiry_id"] == min(c.id for c in handlers.STORE.all())
    dumped = str(result["output"])
    assert "김하늘" not in dumped and "고객이 쓴 본문" not in dumped and "문의 제목" not in dumped


def test_the_skill_puts_the_named_causes_into_the_proposal_evidence(client: TestClient) -> None:
    now = datetime.now(UTC)
    ids = [add(handlers.STORE, "shipping", 5) for _ in range(6)]
    for conversation in handlers.STORE.all():
        conversation.messages[0].created_at = (now - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    for i in ids[:3]:
        _name(handlers.STORE, i, "택배사 연동 오류")
    result = call(client, "propose_change_requests")
    evidence = result["output"]["proposals"][0]["evidence"]
    assert evidence.endswith("상담원이 정한 원인: 택배사 연동 오류 3건")
    assert "김하늘" not in str(result["output"]) and "고객이 쓴 본문" not in str(result["output"])


def test_proposing_sends_and_stores_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("제안 단계에서는 기획으로 아무것도 보내지 않는다")

    monkeypatch.setattr(handlers, "SEND", forbidden)
    now = datetime.now(UTC)
    for _ in range(6):
        add(handlers.STORE, "shipping", 5)
    for conversation in handlers.STORE.all():
        conversation.messages[0].created_at = (now - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    before = dict(handlers.STORE.results)
    result = call(client, "propose_change_requests")
    assert result["status"] == "completed" and result["output"]["proposals"]
    assert handlers.CHANGES.all() == []  # 변경 요청 연결이 생기지 않았다
    assert handlers.STORE.results == before  # 멱등 결과도 남기지 않았다
