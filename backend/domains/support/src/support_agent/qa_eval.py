"""`qa_findings` 기준값을 실제 문의로 시험하는 도구 (읽기 전용, 운영자가 명령줄로 돌린다).

설계: docs/notes/support/2026-10-28-knowledge-gap-qa-design.md (9장 임계값 시험)

네 단계다. 앞의 둘은 서버에서, 뒤의 둘은 어디서든 돌릴 수 있다.

1. `export`: 문의·초안 기록·클레임 금액을 JSON 한 개로 내보낸다
   (`SUPPORT_PG_DSN` 필요, 읽기만 한다). 고객 이름·제목·초안 본문은 빼고 글은 개인정보
   마스킹(`guard.mask_pii`)을 거친다. 파일에 고객 글이 남아 있으니 **레포 밖에 두고
   커밋하지 않는다**.
2. `report`: 내보낸 파일에 규칙을 돌려 규칙별 적중 표와 `weak_grounding`·`heavy_edit`
   값의 분포를 낸다. 기준값이 너무 빡빡한지 느슨한지 보는 용도다.
3. `sheet`: 사람이 볼 시트를 만든다. 규칙에 걸린 문의와 대조군을 섞어 **어느 쪽인지
   숨긴** 채 `sheet.csv`(문의 번호·빈 판정 칸)와 `key.csv`(정답표)로 나눈다.
   고객 글은 들어가지 않는다.
4. `score`: 사람이 `verdict` 칸에 `y`(진짜 문제)·`n`을 채운 뒤 규칙별 정밀도와
   대조군 누락률을 낸다.

정답 표시는 사람이 한다. 이 도구는 판정을 지어내지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from support_agent import guard, languages, persistence, qa_rules
from support_agent.insights import parse_time
from support_agent.qa_schemas import QaReport
from support_agent.schemas import (
    Analysis,
    Classification,
    Conversation,
    Draft,
    Message,
    Source,
    Validation,
)
from support_agent.store import InquiryStore

FORMAT = "qa-eval-export/1"
TEST_SUBJECT_PREFIX = "[시험]"  # 시연·시험으로 넣은 문의는 실제 문의가 아니라 기본으로 뺀다
SHEET_SEED = 442  # 시트에서 줄 순서와 뽑을 문의를 같은 입력에 같은 결과로 만드는 고정 시드
PRECISION_BAR = 0.8  # 이 이상이면 flag로 올려도 된다고 권한다 (제안값, 팀이 정한다)
MIN_REVIEWED = 10  # 규칙당 이만큼은 봐야 정밀도를 말한다
YES = {"y", "yes", "예", "o", "문제"}
NO = {"n", "no", "아니오", "x"}
EDIT_BUCKETS = (0.0, 0.1, 0.25, 0.5, 0.75, 1.01)
COVERAGE_BUCKETS = (0.0, 0.05, 0.1, 0.2, 0.4, 1.01)
_NUMERIC_CLAIM_FIELDS = ("expected_refund", "shipping_refund", "points_back", "refund_paid")


# ---------------------------------------------------------------- 1. 내보내기
def _ints(value: Any) -> list[int]:
    """클레임 안에서 금액 같은 정수를 모은다 (글은 가져가지 않는다)."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _ints(v)]
    if isinstance(value, list):
        return [n for v in value for n in _ints(v)]
    return []


def _export_message(message: Message) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": message.id,
        "role": message.role,
        "text": guard.mask_pii(message.text)[0],
        "created_at": message.created_at,
        "analysis": None,
    }
    analysis = message.analysis
    if analysis is not None:
        classification = analysis.classification
        out["analysis"] = {
            "category": classification.category if classification else None,
            "language": classification.language if classification else None,
            "sources": [s.model_dump(mode="json") for s in analysis.sources or []],
            "drafts": [
                # 초안 본문은 점검에 쓰이지 않아 가져가지 않는다
                {"style": d.style, "validation": d.validation.model_dump(mode="json")}
                for d in analysis.drafts or []
            ],
            "validation": analysis.validation.model_dump(mode="json")
            if analysis.validation
            else None,
        }
    return out


def build_export(
    store: InquiryStore,
    claims: Sequence[Any],
    now: datetime,
    *,
    days: int = 14,
    include_test: bool = False,
) -> dict[str, Any]:
    since = now - timedelta(days=days)
    kept: list[Conversation] = []
    for conversation in store.all():
        if conversation.purged_at is not None:
            continue  # 원문을 지운 문의는 글자를 읽는 규칙을 돌릴 수 없다
        if not include_test and conversation.subject.startswith(TEST_SUBJECT_PREFIX):
            continue
        stamps = [m.created_at for m in conversation.messages] + [conversation.updated_at]
        if any(since < parse_time(s) <= now for s in stamps):
            kept.append(conversation)
    ids = {c.id for c in kept}
    event_keys = ("inquiry_id", "result", "edit_ratio", "occurred_at", "message_id", "guard_codes")
    return {
        "format": FORMAT,
        "exported_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": days,
        "include_test": include_test,
        "conversations": [
            {
                "id": c.id,
                "status": c.status,
                "updated_at": c.updated_at,
                "messages": [_export_message(m) for m in c.messages],
            }
            for c in sorted(kept, key=lambda c: c.id)
        ],
        "draft_events": [
            {k: e.get(k) for k in event_keys if k in e}
            for e in store.draft_events
            if e.get("inquiry_id") in ids
        ],
        "claims": [
            {
                "conversation_id": claim.conversation_id,
                "numbers": sorted(
                    {
                        *(getattr(claim, f) for f in _NUMERIC_CLAIM_FIELDS),
                        *_ints([d.model_dump() for d in claim.lines]),
                        *_ints([d.model_dump() for d in claim.deductions]),
                    }
                ),
            }
            for claim in claims
            if claim.conversation_id in ids
        ],
    }


# ---------------------------------------------------------------- 불러오기와 규칙 돌리기
def load_export(
    data: dict[str, Any],
) -> tuple[InquiryStore, dict[int, list[dict[str, Any]]], datetime]:
    if data.get("format") != FORMAT:
        raise ValueError(f"내보낸 파일 형식이 아니다 (format={data.get('format')!r})")
    store = InquiryStore()
    for raw in data["conversations"]:
        messages = []
        for m in raw["messages"]:
            a = m["analysis"]
            analysis = None
            if a is not None:
                classification = (
                    Classification(
                        intent_ko="-",
                        category=a["category"],
                        sentiment="calm",
                        urgency="medium",
                        search_query="-",
                        language=a["language"] or "ko",
                    )
                    if a["category"] is not None
                    else None
                )
                analysis = Analysis(
                    classification=classification,
                    sources=[Source(**s) for s in a["sources"]],
                    drafts=[
                        Draft(style=d["style"], reply="", validation=Validation(**d["validation"]))
                        for d in a["drafts"]
                    ],
                    validation=Validation(**a["validation"]) if a["validation"] else None,
                )
            messages.append(
                Message(
                    id=m["id"],
                    role=m["role"],
                    text=m["text"],
                    created_at=m["created_at"],
                    analysis=analysis,
                )
            )
        store.replace(
            Conversation(
                id=raw["id"],
                context_id="ctx_req_0000",
                customer_name="-",
                subject="-",
                status=raw["status"],
                updated_at=raw["updated_at"],
                preview="",
                awaiting_reply=False,
                messages=messages,
            )
        )
    store.draft_events.extend(data["draft_events"])
    claims: dict[int, list[dict[str, Any]]] = {}
    for claim in data["claims"]:
        claims.setdefault(claim["conversation_id"], []).append(claim)
    return store, claims, parse_time(data["exported_at"])


def run_rules(data: dict[str, Any], *, sample_size: int = 50) -> tuple[InquiryStore, QaReport]:
    store, claims, now = load_export(data)
    report = qa_rules.build_report(
        store,
        now,
        window_days=data["window_days"],
        sample_size=sample_size,
        claims_by_inquiry=claims,
    )
    return store, report


# ---------------------------------------------------------------- 2. 표
def _bucket_counts(values: list[float], edges: tuple[float, ...]) -> list[tuple[str, int]]:
    rows = []
    for low, high in zip(edges, edges[1:], strict=False):
        label = f"{low:.2f}~{high:.2f}" if high <= 1.0 else f"{low:.2f} 이상"
        rows.append((label, sum(1 for v in values if low <= v < high)))
    return rows


def coverage_values(store: InquiryStore, now: datetime, window_days: int) -> list[float]:
    """`weak_grounding`이 볼 수 있는 보낸 답변의 근거 조각 활용 비율을 모은다 (기준값 비교용)."""
    since = now - timedelta(days=window_days)
    out: list[float] = []
    for conversation in store.all():
        for index, message in enumerate(conversation.messages):
            if message.role != "agent" or not message.text.strip():
                continue
            if not since < parse_time(message.created_at) <= now:
                continue
            customer = qa_rules._previous_analysis(conversation, index)
            sources = (customer.analysis.sources or []) if customer and customer.analysis else []
            if not sources or languages.detect(message.text) != "ko":
                continue
            coverage = qa_rules.source_coverage(
                message.text, [f"{s.title} {s.section} {s.text}" for s in sources]
            )
            if coverage is not None:
                out.append(coverage)
    return out


def render_report(data: dict[str, Any], store: InquiryStore, report: QaReport) -> str:
    now = parse_time(data["exported_at"])
    window = data["window_days"]
    inquiries = len(store.all())
    lines = [
        f"# qa_findings 시험 표 ({data['exported_at']}, 최근 {window}일)",
        "",
        f"- 내보낸 문의 {inquiries}건, 점검한 보낸 답변 {report.replies_checked}건, "
        f"`flag` 걸린 답변 {report.replies_flagged}건 "
        f"(비율 {report.flagged_rate if report.flagged_rate is not None else '-'})",
        f"- 시험 문의 포함: {'예' if data.get('include_test') else '아니오'}",
    ]
    if inquiries < 100:
        lines.append(
            f"- **문의가 {inquiries}건이라 설계가 요구한 100~200건보다 적다.** "
            "기간을 늘리거나 시드 대화와 합치고, 어느 쪽인지 표에 적는다."
        )
    per_rule: dict[str, set[int]] = {}
    for f in report.findings:
        per_rule.setdefault(f.rule, set()).add(f.inquiry_id)
    lines += [
        "",
        "## 규칙별 적중",
        "",
        "| 규칙 | 심각도 | 건수 | 문의 수 | 문의 대비 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for rule in qa_rules.SEVERITY:
        n = report.rule_counts.get(rule, 0)
        share = f"{len(per_rule.get(rule, set())) / inquiries:.0%}" if inquiries else "-"
        hit_inquiries = len(per_rule.get(rule, set()))
        lines.append(f"| `{rule}` | {qa_rules.SEVERITY[rule]} | {n} | {hit_inquiries} | {share} |")
    coverage = coverage_values(store, now, window)
    lines += [
        "",
        f"## weak_grounding 근거 활용 비율 분포 (기준값 {qa_rules.WEAK_GROUNDING_COVERAGE}, "
        f"한국어 답변 중 근거가 있던 {len(coverage)}건)",
        "",
        "| 구간 | 답변 수 |",
        "| --- | --- |",
        *(f"| {label} | {n} |" for label, n in _bucket_counts(coverage, COVERAGE_BUCKETS)),
    ]
    ratios = [
        e["edit_ratio"]
        for e in data["draft_events"]
        if e.get("result") == "edited" and e.get("edit_ratio") is not None
    ]
    lines += [
        "",
        f"## heavy_edit 수정 정도 분포 (기준값 {qa_rules.HEAVY_EDIT_RATIO}, "
        f"수정한 초안 {len(ratios)}건)",
        "",
        "| 구간 | 초안 수 |",
        "| --- | --- |",
        *(f"| {label} | {n} |" for label, n in _bucket_counts(ratios, EDIT_BUCKETS)),
        "",
        "기준값 바로 위아래 구간에 건수가 몰려 있으면 시드가 조금만 바뀌어도 결과가 크게 달라진다.",
        "",
        "## 알아 둘 것",
        *(f"- {n}" for n in report.notes),
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 3. 시트
def build_sheet(
    report: QaReport, *, hits: int = 50, controls: int = 50
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(사람이 보는 시트 줄, 정답표 줄). 걸린 줄인지 대조군인지는 시트에 없다."""
    rng = random.Random(SHEET_SEED)
    by_target: dict[tuple[int, int | None], dict[str, Any]] = {}
    for f in report.findings:
        row = by_target.setdefault(
            (f.inquiry_id, f.message_id),
            {"inquiry_id": f.inquiry_id, "message_id": f.message_id, "rules": []},
        )
        row["rules"].append(f.rule)
    # 규칙마다 고르게 뽑는다 (흔한 규칙이 시트를 다 차지하지 않게)
    rules_present = sorted({r for row in by_target.values() for r in row["rules"]})
    chosen: dict[tuple[int, int | None], dict[str, Any]] = {}
    per_rule = max(1, hits // max(1, len(rules_present)))
    for rule in rules_present:
        pool = sorted(
            (k for k, row in by_target.items() if rule in row["rules"] and k not in chosen),
            key=lambda k: (k[0], k[1] or 0),
        )
        for key in rng.sample(pool, min(per_rule, len(pool))):
            chosen[key] = by_target[key]
    rows: list[dict[str, Any]] = [
        {
            "kind": "hit",
            "inquiry_id": r["inquiry_id"],
            "message_id": r["message_id"],
            "rules": "|".join(sorted(r["rules"])),
        }
        for r in chosen.values()
    ]
    for inquiry_id in report.human_review_sample[:controls]:
        rows.append({"kind": "control", "inquiry_id": inquiry_id, "message_id": None, "rules": ""})
    rng.shuffle(rows)
    key_rows = [{"row_id": n, **row} for n, row in enumerate(rows, 1)]
    sheet_rows = [
        {
            "row_id": r["row_id"],
            "inquiry_id": r["inquiry_id"],
            "message_id": r["message_id"] or "",
            "verdict": "",
            "note": "",
        }
        for r in key_rows
    ]
    return sheet_rows, key_rows


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


# ---------------------------------------------------------------- 4. 점수
def _verdict(value: str) -> bool | None:
    text = (value or "").strip().lower()
    return True if text in YES else False if text in NO else None


def score(sheet: list[dict[str, str]], key: list[dict[str, str]]) -> str:
    verdicts = {row["row_id"]: _verdict(row.get("verdict", "")) for row in sheet}
    total = Counter()
    yes = Counter()
    unlabeled = 0
    control_total = control_yes = 0
    for row in key:
        verdict = verdicts.get(row["row_id"])
        if verdict is None:
            unlabeled += 1
            continue
        if row["kind"] == "control":
            control_total += 1
            control_yes += verdict
            continue
        for rule in row["rules"].split("|"):
            total[rule] += 1
            yes[rule] += verdict
    lines = [
        "# qa_findings 판정 결과",
        "",
        f"- 아직 판정하지 않은 줄 {unlabeled}건 (제외)",
        "",
        "| 규칙 | 지금 심각도 | 본 줄 | 진짜 문제 | 정밀도 | 권고 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for rule, severity in qa_rules.SEVERITY.items():
        n = total[rule]
        if n == 0:
            lines.append(f"| `{rule}` | {severity} | 0 | 0 | - | 본 줄 없음 |")
            continue
        precision = yes[rule] / n
        if n < MIN_REVIEWED:
            advice = f"표본 부족 (최소 {MIN_REVIEWED}건)"
        elif precision >= PRECISION_BAR:
            advice = "flag로 올려도 됨" if severity == "info" else "flag 유지"
        else:
            advice = "기준값 조정 또는 info 유지"
        lines.append(f"| `{rule}` | {severity} | {n} | {yes[rule]} | {precision:.0%} | {advice} |")
    miss = f"{control_yes / control_total:.0%}" if control_total else "-"
    lines += [
        "",
        f"- 대조군 {control_total}건 중 사람이 문제를 찾은 것 {control_yes}건, 규칙 누락률 {miss}",
        f"- 권고 기준: 정밀도 {PRECISION_BAR:.0%} 이상, 규칙당 {MIN_REVIEWED}건 이상 "
        "(제안값, 팀이 정한다)",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 명령줄
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m support_agent.qa_eval",
        description="qa_findings 기준값을 실제 문의로 시험한다 (읽기 전용).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="문의·초안 기록·클레임 금액을 JSON으로 내보낸다")
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--days", type=int, default=14)
    export.add_argument("--include-test", action="store_true", help="[시험] 문의도 넣는다")
    report = sub.add_parser("report", help="규칙별 적중 표와 값 분포")
    report.add_argument("--in", dest="source", type=Path, required=True)
    report.add_argument("--out", type=Path)
    sheet = sub.add_parser("sheet", help="사람이 볼 시트와 정답표를 만든다")
    sheet.add_argument("--in", dest="source", type=Path, required=True)
    sheet.add_argument("--out-dir", type=Path, required=True)
    sheet.add_argument("--hits", type=int, default=50)
    sheet.add_argument("--controls", type=int, default=50)
    scoring = sub.add_parser("score", help="사람이 채운 시트로 정밀도를 낸다")
    scoring.add_argument("--sheet", type=Path, required=True)
    scoring.add_argument("--key", type=Path, required=True)
    return parser


def _emit(text: str, out: Path | None) -> None:
    if out is not None:
        out.write_text(text, encoding="utf-8")
        print(f"{out}에 썼다")
    else:
        sys.stdout.buffer.write(text.encode("utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # 윈도 콘솔 기본 인코딩에서 한글 안내가 깨지지 않게
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    if args.command == "export":
        if persistence.dsn() is None:
            print(
                f"{persistence.DSN_ENV}가 없다. 서비스가 쓰는 DB를 가리켜야 한다.", file=sys.stderr
            )
            return 2
        store = persistence.build_inquiry_store()
        claims = persistence.build_claim_store().list()
        data = build_export(
            store, claims, datetime.now(UTC), days=args.days, include_test=args.include_test
        )
        args.out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(
            f"문의 {len(data['conversations'])}건, 초안 기록 {len(data['draft_events'])}건을 "
            f"{args.out}에 썼다. 고객 글이 남아 있으니 레포 밖에 두고 커밋하지 않는다."
        )
        return 0
    if args.command == "score":
        _emit(score(read_csv(args.sheet), read_csv(args.key)), None)
        return 0
    data = json.loads(args.source.read_text(encoding="utf-8"))
    if args.command == "report":
        store, report = run_rules(data)
        _emit(render_report(data, store, report), args.out)
        return 0
    _, report = run_rules(data, sample_size=args.controls)
    sheet_rows, key_rows = build_sheet(report, hits=args.hits, controls=args.controls)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        args.out_dir / "sheet.csv",
        sheet_rows,
        ["row_id", "inquiry_id", "message_id", "verdict", "note"],
    )
    write_csv(
        args.out_dir / "key.csv", key_rows, ["row_id", "kind", "inquiry_id", "message_id", "rules"]
    )
    print(
        f"시트 {len(sheet_rows)}줄(걸린 것 {sum(r['kind'] == 'hit' for r in key_rows)}, "
        f"대조군 {sum(r['kind'] == 'control' for r in key_rows)})을 {args.out_dir}에 썼다. "
        "sheet.csv의 verdict 칸에 진짜 문제면 y, 아니면 n을 적는다. key.csv는 판정이 끝날 때까지 "
        "보지 않는다."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
