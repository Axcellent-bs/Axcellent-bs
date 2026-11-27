"""`collect_plan_review_embed.py`가 만든 `results/plan_review_embed_sims/sims_<라벨>.json`을
채점하고, 지금 쓰는 낱말 규칙(`plan_review_rule_score.py`)과 한 표로 비교한다.

- 와이드 샷 낱말(`WIDE_WORDS`)은 지금 규칙을 그대로 재사용한다 — 이 평가가 바꾸려는 것은
  "다른 인물이 가까이 나오는가"를 어떻게 판정하는지(낱말 vs 임베딩)이지, 샷 폭 판정이 아니다.
- 임계값(유사도 기준)은 dev 집합에서만 그리드 탐색으로 고른다. 태윤님 기준: "재현율(빠뜨리면
  영상에 다른 인물이 나옴) 우선, 그다음 정밀도" — item 1(금지 표현)과 우선순위가 반대다.

사용법:
    cd backend
    uv run python domains/marketing/eval/score_plan_review_embed.py gemini-embedding-001 ...
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
sys.path.insert(0, str(BACKEND / "shared" / "agent-template" / "src"))
sys.path.insert(0, str(HERE))

from plan_review_rule_score import evaluate as rule_evaluate  # noqa: E402

from ax_agent_template.plan_rules import WIDE_WORDS, has_word  # noqa: E402

SIM_GRID: tuple[float, ...] = tuple(round(0.50 + 0.02 * i, 2) for i in range(26))  # 0.50~1.00


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


def decide(case: dict, sims: dict[str, float], threshold: float) -> bool:
    if has_word(case["camera"], WIDE_WORDS):
        return False
    return sims[case["id"]] >= threshold


def evaluate(cases: list[dict], sims: dict[str, float], threshold: float, split: str) -> Metrics:
    blocked = blocked_hit = links = correct = passable = wrongly_blocked = 0
    for case in cases:
        if case["split"] != split:
            continue
        decided = decide(case, sims, threshold)
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


def select(cases: list[dict], sims: dict[str, float]) -> float | None:
    """dev 집합에서 재현율 우선, 그 다음 정밀도로 가장 좋은 임계값을 고른다.

    (item 1의 `score_embed_variant.select`와 우선순위가 반대 — 여기는 태윤님이 재현율을
    먼저 보라고 한 항목이다.) 재현율이 가장 높은 임계값들 중 정밀도가 가장 높은 것."""
    rows = [(t, evaluate(cases, sims, t, "dev")) for t in SIM_GRID]
    pool = [(t, m) for t, m in rows if m.blocked_hit or m.links]
    if not pool:
        return None
    best_t, _ = max(pool, key=lambda tm: ((tm[1].recall or 0.0), (tm[1].precision or 0.0)))
    return best_t


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    labels = argv[1:]
    if not labels:
        print(
            "사용법: score_plan_review_embed.py <라벨1> <라벨2> ...  "
            "(collect_plan_review_embed.py와 같은 라벨)"
        )
        return 2

    cases = json.loads((HERE / "plan_review_cases.json").read_text(encoding="utf-8"))["cases"]

    print(
        "| 방식 | 고른 임계값 | dev 재현율 | dev 정밀도 | dev 오탐률"
        " | holdout 재현율 | holdout 정밀도 | holdout 오탐률 |"
    )
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")

    rd, rh = rule_evaluate(cases, "dev"), rule_evaluate(cases, "holdout")
    print(
        f"| 낱말 규칙 (지금) | - | {_fmt(rd.recall)} | {_fmt(rd.precision)}"
        f" | {_fmt(rd.false_block_rate)} | {_fmt(rh.recall)} | {_fmt(rh.precision)}"
        f" | {_fmt(rh.false_block_rate)} |"
    )

    for label in labels:
        path = HERE / "results" / "plan_review_embed_sims" / f"sims_{label}.json"
        if not path.exists():
            note = f"(결과 없음: 먼저 collect_plan_review_embed.py {label} 실행)"
            print(f"| 임베딩 {label} | {note} | | | | | | |")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        sims = data["max_sim"]
        t = select(cases, sims)
        if t is None:
            print(f"| 임베딩 {label} ({data['model']}) | 쓸 만한 임계값 없음 | | | | | | |")
            continue
        d, h = evaluate(cases, sims, t, "dev"), evaluate(cases, sims, t, "holdout")
        print(
            f"| 임베딩 {label} ({data['model']}) | 유사도≥{t:.2f} | {_fmt(d.recall)}"
            f" | {_fmt(d.precision)} | {_fmt(d.false_block_rate)} | {_fmt(h.recall)}"
            f" | {_fmt(h.precision)} | {_fmt(h.false_block_rate)} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
