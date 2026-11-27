"""은행 입금 적요 ↔ PG 정산금 판정 낱말 규칙(부분 문자열)을 골든셋으로 채점한다.

`bank.match()`가 실제로 쓰는 조건을 그대로 가져온다(`r["amount"] > 0 and (not memo or memo in
r["memo"])` 줄, `finance_agent/bank.py`). 새 규칙은 만들지 않는다.

실제 `finance_policy.toml`의 `pg_memo`는 비어 있다("아직 실제 적요 표본이 없다" — 태윤님 본인
메모). 이 평가는 `bank_pg_memo_cases.json`에 적힌 placeholder 값('토스')으로 재지, 회사의
실제 설정값이나 실제 적요 표본으로 재지 않는다 (README 한계 참고).

사용법:
    cd backend
    uv run python domains/finance/eval/bank_pg_memo_rule_score.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


def rule_matches(memo_text: str, pg_memo: str) -> bool:
    """bank.match()의 적요 판정 줄 그대로: pg_memo가 비었으면 모든 입금, 아니면 부분 일치."""
    return not pg_memo or pg_memo in memo_text


@dataclass(frozen=True)
class Metrics:
    matched: int  # 정답이 match인 적요 수
    matched_hit: int  # 그중 규칙이 맞힌 수
    links: int  # 규칙이 match로 센 적요 수(전체)
    correct: int  # 규칙이 match로 셌고 정답도 match
    no_match: int  # 정답이 no-match인 적요 수
    false_links: int  # 정답은 no-match인데 규칙이 match로 셈 (PG 정산금 아닌데 섞임)

    @property
    def recall(self) -> float | None:
        return self.matched_hit / self.matched if self.matched else None

    @property
    def precision(self) -> float | None:
        return self.correct / self.links if self.links else None

    @property
    def false_link_rate(self) -> float | None:
        return self.false_links / self.no_match if self.no_match else None


def evaluate(cases: list[dict], pg_memo: str, split: str) -> Metrics:
    matched = matched_hit = links = correct = no_match = false_links = 0
    for case in cases:
        if case["split"] != split:
            continue
        decided = rule_matches(case["memo"], pg_memo)
        if decided:
            links += 1
        if case["label"] == "match":
            matched += 1
            if decided:
                matched_hit += 1
                correct += 1
        else:
            no_match += 1
            if decided:
                false_links += 1
    return Metrics(matched, matched_hit, links, correct, no_match, false_links)


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def main() -> None:
    data = json.loads((HERE / "bank_pg_memo_cases.json").read_text(encoding="utf-8"))
    cases, pg_memo = data["cases"], data["pg_memo_placeholder"]
    print(f"pg_memo placeholder: {pg_memo!r} ({data['note_ko']})\n")
    print("| 집합 | 재현율 | 정밀도 | 오연결률(PG 정산금 아닌데 섞은 비율) |")
    print("| --- | --- | --- | --- |")
    for split in ("dev", "holdout"):
        m = evaluate(cases, pg_memo, split)
        print(f"| {split} | {_fmt(m.recall)} | {_fmt(m.precision)} | {_fmt(m.false_link_rate)} |")
    wrong = [c["id"] for c in cases if rule_matches(c["memo"], pg_memo) != (c["label"] == "match")]
    if wrong:
        print(f"\n틀린 케이스: {', '.join(wrong)}")


if __name__ == "__main__":
    main()
