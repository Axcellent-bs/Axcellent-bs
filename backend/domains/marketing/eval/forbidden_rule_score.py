"""마케팅 금지 표현·참여 유도 판정: 지금 쓰는 낱말 규칙을 골든셋으로 채점한다.

이 파일은 새 규칙을 만들지 않는다. `marketing_agent.sns.has_engagement_bait`·
`has_forbidden_claim`과 `marketing_agent.notice_text.NOTICE_FORBIDDEN_PATTERNS`를
그대로 불러와 "이 글을 막는가"만 합친다 (sns 채널은 production의 `check_sns_post`/
`draft_sns_post`가 막는 조건과 같고, notice 채널은 `check_text`의 `forbidden_claim`
이유 한 줄과 같다 — 그 함수의 다른 이유(길이·사실 불일치 등)는 이 평가 대상이 아니다).

사용법:
    cd backend
    uv run python domains/marketing/eval/forbidden_rule_score.py
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "domains" / "marketing" / "src"))

from marketing_agent.notice_text import NOTICE_FORBIDDEN_PATTERNS  # noqa: E402
from marketing_agent.sns import has_engagement_bait, has_forbidden_claim  # noqa: E402


def rule_blocks(text: str, channel: str) -> bool:
    """지금 production이 이 글을 막는가 (낱말 규칙만, 그대로 불러옴)."""
    if has_forbidden_claim(text):
        return True
    if channel == "sns":
        return has_engagement_bait(text)
    squeezed = re.sub(r"\s+", "", text)
    return any(re.search(p, squeezed) for p in NOTICE_FORBIDDEN_PATTERNS)


@dataclass(frozen=True)
class Metrics:
    blocked: int  # 정답이 block인 글 수
    blocked_hit: int  # 그중 규칙이 막은 수
    links: int  # 규칙이 막은 글 수 (전체)
    correct: int  # 규칙이 막았고 정답도 block
    passable: int  # 정답이 pass인 글 수
    wrongly_blocked: int  # 정답은 pass인데 규칙이 막음

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
        decided = rule_blocks(case["text"], case["channel"])
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
    cases = json.loads((HERE / "forbidden_cases.json").read_text(encoding="utf-8"))["cases"]
    print("| 집합 | 정밀도 | 재현율 | 오탐률(통과해야 할 글을 막은 비율) |")
    print("| --- | --- | --- | --- |")
    for split in ("dev", "holdout"):
        m = evaluate(cases, split)
        print(f"| {split} | {_fmt(m.precision)} | {_fmt(m.recall)} | {_fmt(m.false_block_rate)} |")
    wrong = [
        c["id"]
        for c in cases
        if rule_blocks(c["text"], c["channel"]) != (c["label"] == "block")
    ]
    if wrong:
        print(f"\n틀린 케이스: {', '.join(wrong)}")


if __name__ == "__main__":
    main()
