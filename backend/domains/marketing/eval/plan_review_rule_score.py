"""마케팅 제작안 확인(다른 인물 가까이 나옴) 낱말 규칙을 골든셋으로 채점한다.

이 파일은 새 규칙을 만들지 않는다. `ax_agent_template.plan_rules.close_other_person`을
그대로 불러온다(`marketing_agent.plan_review.check()`가 실제로 쓰는 것과 같은 호출:
`people` 인자 없이, 장면 글 낱말만 본다).

사용법:
    cd backend
    uv run python domains/marketing/eval/plan_review_rule_score.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "shared" / "agent-template" / "src"))

from ax_agent_template.plan_rules import close_other_person  # noqa: E402


@dataclass(frozen=True)
class Metrics:
    blocked: int
    blocked_hit: int
    links: int
    correct: int
    passable: int
    wrongly_blocked: int

    @property
    def recall(self) -> float | None:
        return self.blocked_hit / self.blocked if self.blocked else None

    @property
    def precision(self) -> float | None:
        return self.correct / self.links if self.links else None

    @property
    def false_block_rate(self) -> float | None:
        return self.wrongly_blocked / self.passable if self.passable else None


def evaluate(cases: list[dict], split: str) -> Metrics:
    blocked = blocked_hit = links = correct = passable = wrongly_blocked = 0
    for case in cases:
        if case["split"] != split:
            continue
        decided = close_other_person(case["body"], case["camera"])
        if decided:
            links += 1
        if case["label"] == "block":
            blocked += 1
            if decided:
                blocked_hit += 1
                correct += 1
        else:
            passable += 1
            if decided:
                wrongly_blocked += 1
    return Metrics(blocked, blocked_hit, links, correct, passable, wrongly_blocked)


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def main() -> None:
    cases = json.loads((HERE / "plan_review_cases.json").read_text(encoding="utf-8"))["cases"]
    print("| 집합 | 재현율 | 정밀도 | 오탐률(통과해야 할 장면을 막은 비율) |")
    print("| --- | --- | --- | --- |")
    for split in ("dev", "holdout"):
        m = evaluate(cases, split)
        print(f"| {split} | {_fmt(m.recall)} | {_fmt(m.precision)} | {_fmt(m.false_block_rate)} |")
    wrong = [
        c["id"]
        for c in cases
        if close_other_person(c["body"], c["camera"]) != (c["label"] == "block")
    ]
    if wrong:
        print(f"\n틀린 케이스: {', '.join(wrong)}")


if __name__ == "__main__":
    main()
