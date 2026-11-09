"""qa_eval: qa_findings 기준값 시험 도구 (내보내기·표·시트·점수). 모두 합성 데이터로 시험한다."""

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from support_agent import persistence, qa_eval, qa_rules
from support_agent.claim_schemas import Claim
from support_agent.schemas import Analysis, Classification, Draft, Finding, Source, Validation
from support_agent.store import InquiryStore

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
POLICY = "수령 후 7일 이내 단순 변심 환불은 왕복 배송비를 고객이 부담한다."
SOURCE = Source(
    id="refund-1",
    doc_id="refund",
    category="refund",
    title="환불 기준",
    section="수령 후 7일",
    text=POLICY,
    score=0.8,
)
GOOD = "수령 후 7일 이내 단순 변심 환불은 왕복 배송비를 고객이 부담합니다."
WEAK = "안녕하세요 확인해 보겠습니다 잠시만 기다려 주세요"
SECRET_NAME = "김하늘"
PHONE = "010-1234-5678"
DRAFT_BODY = "초안본문비밀"


def stamp(hours_ago: float = 1.0) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def analysis(*, sources: bool = True, findings: list[str] | None = None) -> Analysis:
    found = [Finding(code=c, level="error", message="x") for c in findings or []]
    return Analysis(
        classification=Classification(
            intent_ko="문의",
            category="refund",
            sentiment="calm",
            urgency="medium",
            search_query="환불",
            language="ko",
        ),
        sources=[SOURCE] if sources else [],
        drafts=[
            Draft(
                style="concise",
                reply=DRAFT_BODY,
                validation=Validation(passed=not found, findings=found),
            )
        ],
    )


def add(
    store: InquiryStore,
    reply: str | None = GOOD,
    *,
    subject: str = "환불 문의",
    customer_text: str = f"환불하고 싶어요 {PHONE}로 연락주세요",
    hours_ago: float = 1.0,
    sources: bool = True,
    findings: list[str] | None = None,
) -> int:
    conversation = store.create(
        context_id="ctx_req_0001",
        tenant_id="shop-demo",
        user_id=1,
        customer_name=SECRET_NAME,
        subject=subject,
        order_id=None,
        order_no=None,
        first_message=customer_text,
        analysis=analysis(sources=sources, findings=findings),
    )
    if reply is not None:
        store.add_agent_message(conversation.id, reply)
    for message in conversation.messages:
        message.created_at = stamp(hours_ago)
    conversation.updated_at = stamp(hours_ago)
    return conversation.id


def claim_for(conversation_id: int) -> Claim:
    return Claim(
        id=1,
        context_id="ctx_req_0001",
        order_id="ord_0001",
        order_no="ORD-1",
        buyer_id="buy_0001",
        customer=SECRET_NAME,
        type="cancel",
        reason="change_of_mind",
        status="completed",
        created_at=stamp(),
        updated_at=stamp(),
        outcome="refunded",
        resolution="환불",
        requires_return=False,
        conversation_id=conversation_id,
        expected_refund=4500,
        shipping_refund=3000,
        agent_note="상담원메모비밀",
    )


def sample_store() -> InquiryStore:
    store = InquiryStore()
    for _ in range(6):
        add(store)  # 문제 없는 문의
    add(store, sources=False)  # no_evidence_reply
    add(store, WEAK)  # weak_grounding
    add(store, GOOD + " 환불 99일 안에 처리합니다.")  # unsupported_number
    add(store, None, findings=["promise_refund"])  # guard_hit (보낸 답변 없음)
    heavy = add(store, None)
    store.draft_events.append(
        {"inquiry_id": heavy, "result": "edited", "edit_ratio": 0.8, "occurred_at": stamp()}
    )
    return store


# ---------------------------------------------------------------- 내보내기
def test_export_drops_names_subjects_and_draft_bodies_and_masks_pii() -> None:
    store = InquiryStore()
    cid = add(store)
    data = qa_eval.build_export(store, [claim_for(cid)], NOW)
    dumped = json.dumps(data, ensure_ascii=False)
    for secret in (SECRET_NAME, PHONE, DRAFT_BODY, "환불 문의", "상담원메모비밀"):
        assert secret not in dumped
    first = data["conversations"][0]["messages"][0]
    assert "환불하고 싶어요" in first["text"]  # 규칙이 글을 읽어야 해서 마스킹만 한다
    assert data["claims"] == [{"conversation_id": cid, "numbers": [0, 3000, 4500]}]
    assert data["format"] == qa_eval.FORMAT


def test_export_skips_test_inquiries_purged_ones_and_the_ones_outside_the_window() -> None:
    store = InquiryStore()
    real = add(store)
    add(store, subject="[시험] 영어 문의")
    purged = add(store)
    store.get(purged).purged_at = stamp()  # type: ignore[union-attr]
    add(store, hours_ago=24 * 30)
    data = qa_eval.build_export(store, [], NOW, days=14)
    assert [c["id"] for c in data["conversations"]] == [real]
    with_test = qa_eval.build_export(store, [], NOW, days=14, include_test=True)
    assert len(with_test["conversations"]) == 2 and with_test["include_test"] is True


def test_export_keeps_only_the_events_and_claims_of_exported_inquiries() -> None:
    store = InquiryStore()
    kept = add(store)
    dropped = add(store, subject="[시험] 문의")
    store.draft_events.append({"inquiry_id": kept, "result": "edited", "edit_ratio": 0.6})
    store.draft_events.append({"inquiry_id": dropped, "result": "rejected", "extra": "x"})
    data = qa_eval.build_export(store, [claim_for(dropped)], NOW)
    assert [e["inquiry_id"] for e in data["draft_events"]] == [kept]
    assert data["claims"] == []


def test_a_round_trip_gives_the_same_findings_as_running_the_rules_directly() -> None:
    store = sample_store()
    cid = add(store, GOOD + " 환불 4500원을 처리했습니다.")
    claim = claim_for(cid)
    direct = qa_rules.build_report(
        store,
        NOW,
        window_days=14,
        sample_size=50,
        claims_by_inquiry={cid: [{"numbers": [4500, 3000]}]},
    )
    data = json.loads(json.dumps(qa_eval.build_export(store, [claim], NOW, days=14)))
    _, report = qa_eval.run_rules(data)
    assert [f.model_dump() for f in report.findings] == [f.model_dump() for f in direct.findings]
    assert report.human_review_sample == direct.human_review_sample
    assert "unsupported_number" not in {f.rule for f in report.findings if f.inquiry_id == cid}


def test_load_export_rejects_other_formats() -> None:
    with pytest.raises(ValueError, match="형식"):
        qa_eval.load_export({"format": "something-else"})


# ---------------------------------------------------------------- 표
def test_report_lists_rules_distributions_and_warns_when_there_are_too_few_inquiries() -> None:
    data = qa_eval.build_export(sample_store(), [], NOW, days=14)
    store, report = qa_eval.run_rules(data)
    text = qa_eval.render_report(data, store, report)
    for rule in qa_rules.SEVERITY:
        assert f"`{rule}`" in text
    assert "weak_grounding 근거 활용 비율 분포" in text and "heavy_edit 수정 정도 분포" in text
    assert "100~200건보다 적다" in text  # 합성 문의는 11건뿐이다
    assert SECRET_NAME not in text and PHONE not in text
    coverage = qa_eval.coverage_values(store, NOW, 14)
    assert len(coverage) >= 7 and min(coverage) < qa_rules.WEAK_GROUNDING_COVERAGE


def test_buckets_cover_every_value_once() -> None:
    values = [0.0, 0.049, 0.05, 0.1, 0.39, 0.4, 1.0]
    rows = qa_eval._bucket_counts(values, qa_eval.COVERAGE_BUCKETS)
    assert sum(n for _, n in rows) == len(values)
    assert dict(rows)["0.00~0.05"] == 2
    assert rows[-1][0] == "0.40 이상" and rows[-1][1] == 2


# ---------------------------------------------------------------- 시트
def make_sheet() -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any]:
    data = qa_eval.build_export(sample_store(), [], NOW, days=14)
    _, report = qa_eval.run_rules(data)
    sheet, key = qa_eval.build_sheet(report, hits=10, controls=3)
    return sheet, key, report


def test_the_sheet_hides_which_rows_are_hits_and_never_carries_text() -> None:
    sheet, key, _ = make_sheet()
    assert set(sheet[0]) == {"row_id", "inquiry_id", "message_id", "verdict", "note"}
    assert all(row["verdict"] == "" for row in sheet)
    dumped = json.dumps(sheet + key, ensure_ascii=False)
    for secret in (SECRET_NAME, PHONE, DRAFT_BODY, "환불하고"):
        assert secret not in dumped
    assert [r["row_id"] for r in sheet] == [r["row_id"] for r in key]
    kinds = {r["kind"] for r in key}
    assert kinds == {"hit", "control"}
    assert [r["kind"] for r in key] != sorted(r["kind"] for r in key)  # 섞여 있다


def test_hits_cover_each_rule_and_controls_are_clean_inquiries() -> None:
    _, key, report = make_sheet()
    hit_rules = {rule for r in key if r["kind"] == "hit" for rule in r["rules"].split("|")}
    assert {"no_evidence_reply", "weak_grounding", "unsupported_number"} <= hit_rules
    flagged = {f.inquiry_id for f in report.findings}
    controls = [r for r in key if r["kind"] == "control"]
    assert len(controls) == 3 and not {r["inquiry_id"] for r in controls} & flagged


def test_the_sheet_is_repeatable() -> None:
    first = make_sheet()[:2]
    second = make_sheet()[:2]
    assert first == second


# ---------------------------------------------------------------- 점수
def labeled(key: list[dict[str, Any]], answers: dict[str, str]) -> list[dict[str, str]]:
    """규칙 이름(또는 control)별로 정해진 판정을 시트 줄에 채운다."""
    out = []
    for row in key:
        who = "control" if row["kind"] == "control" else row["rules"].split("|")[0]
        out.append(
            {
                "row_id": str(row["row_id"]),
                "verdict": answers.get(who, ""),
                "inquiry_id": str(row["inquiry_id"]),
                "message_id": "",
                "note": "",
            }
        )
    return out


def fake_key(rule: str, n: int, start: int = 1) -> list[dict[str, str]]:
    return [
        {
            "row_id": str(start + i),
            "kind": "hit",
            "inquiry_id": "1",
            "message_id": "",
            "rules": rule,
        }
        for i in range(n)
    ]


def test_score_computes_precision_advice_and_the_miss_rate() -> None:
    key = fake_key("no_evidence_reply", 10) + fake_key("weak_grounding", 10, 11)
    key += [
        {"row_id": str(21 + i), "kind": "control", "inquiry_id": "9", "message_id": "", "rules": ""}
        for i in range(4)
    ]
    verdicts = ["y"] * 9 + ["n"] + ["y"] * 3 + ["n"] * 7 + ["n", "n", "n", "y"]
    sheet = [
        {"row_id": k["row_id"], "verdict": v, "inquiry_id": "1", "message_id": "", "note": ""}
        for k, v in zip(key, verdicts, strict=True)
    ]
    text = qa_eval.score(sheet, key)
    assert "| `no_evidence_reply` | flag | 10 | 9 | 90% | flag 유지 |" in text
    assert "| `weak_grounding` | info | 10 | 3 | 30% | 기준값 조정 또는 info 유지 |" in text
    assert "대조군 4건 중 사람이 문제를 찾은 것 1건, 규칙 누락률 25%" in text
    assert "| `heavy_edit` | info | 0 | 0 | - | 본 줄 없음 |" in text


def test_score_promotes_info_rules_only_with_enough_reviewed_rows_and_skips_blanks() -> None:
    key = fake_key("unsupported_number", 12) + fake_key("heavy_edit", 5, 13)
    sheet = [
        {"row_id": k["row_id"], "verdict": "예", "inquiry_id": "1", "message_id": "", "note": ""}
        for k in key
    ]
    sheet[0]["verdict"] = ""  # 판정하지 않은 줄은 빠진다
    text = qa_eval.score(sheet, key)
    assert "| `unsupported_number` | info | 11 | 11 | 100% | flag로 올려도 됨 |" in text
    assert "| `heavy_edit` | info | 5 | 5 | 100% | 표본 부족 (최소 10건) |" in text
    assert "아직 판정하지 않은 줄 1건" in text


# ---------------------------------------------------------------- 명령줄
def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_cli_report_sheet_and_score_work_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export = tmp_path / "export.json"
    export.write_text(
        json.dumps(qa_eval.build_export(sample_store(), [], NOW, days=14), ensure_ascii=False),
        encoding="utf-8",
    )
    assert qa_eval.main(["report", "--in", str(export), "--out", str(tmp_path / "r.md")]) == 0
    assert "규칙별 적중" in (tmp_path / "r.md").read_text(encoding="utf-8")

    out = tmp_path / "sheet"
    assert qa_eval.main(["sheet", "--in", str(export), "--out-dir", str(out), "--hits", "8"]) == 0
    sheet, key = read_rows(out / "sheet.csv"), read_rows(out / "key.csv")
    assert len(sheet) == len(key) > 0
    for row in sheet:  # 사람이 채우는 흉내: 걸린 줄은 모두 y, 대조군은 모두 n
        kind = next(k["kind"] for k in key if k["row_id"] == row["row_id"])
        row["verdict"] = "y" if kind == "hit" else "n"
    with (out / "sheet.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(sheet[0]))
        writer.writeheader()
        writer.writerows(sheet)
    capsys.readouterr()
    assert (
        qa_eval.main(["score", "--sheet", str(out / "sheet.csv"), "--key", str(out / "key.csv")])
        == 0
    )
    printed = capsys.readouterr().out
    assert "판정 결과" in printed and "규칙 누락률 0%" in printed


def test_cli_export_needs_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(persistence.DSN_ENV, raising=False)
    assert qa_eval.main(["export", "--out", str(tmp_path / "x.json")]) == 2
    assert persistence.DSN_ENV in capsys.readouterr().err
    assert not (tmp_path / "x.json").exists()


def test_cli_export_writes_a_file_from_the_configured_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InquiryStore()
    cid = add(store, hours_ago=0.5)
    # 시험 시각이 아니라 실제 지금 기준으로 잘리므로 방금 온 문의로 바꿔 둔다
    recent = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for message in store.get(cid).messages:  # type: ignore[union-attr]
        message.created_at = recent
    store.get(cid).updated_at = recent  # type: ignore[union-attr]
    monkeypatch.setenv(persistence.DSN_ENV, "postgresql://unused")
    monkeypatch.setattr(persistence, "build_inquiry_store", lambda: store)

    class _Claims:
        def list(self) -> list[Claim]:
            return [claim_for(cid)]

    monkeypatch.setattr(persistence, "build_claim_store", lambda: _Claims())
    out = tmp_path / "x.json"
    assert qa_eval.main(["export", "--out", str(out), "--days", "7"]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [c["id"] for c in data["conversations"]] == [cid]
    assert SECRET_NAME not in out.read_text(encoding="utf-8")
