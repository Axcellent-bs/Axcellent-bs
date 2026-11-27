"""`collect_bank_pg_memo_embed.py`가 만든 `results/bank_pg_memo_embed_sims/sims_<라벨>.json`을
채점하고, 지금 쓰는 부분 문자열 규칙(`bank_pg_memo_rule_score.py`)과 한 표로 비교한다.

태윤님 기준: "오연결률(false-link rate)은 0이어야 한다" — 취향이 아니라 꼭 지켜야 할 조건으로
받아들였다. 그래서 dev 집합에서 오연결률이 정확히 0인 임계값들 중에서만 재현율이 가장 높은
것을 고른다. 그런 임계값이 하나도 없으면 "0을 만드는 임계값 없음"이라고 그대로 보고하지,
0이 아닌 임계값을 대신 고르지 않는다.

사용법:
    cd backend
    uv run python domains/finance/eval/score_bank_pg_memo_embed.py gemini-embedding-001 ...
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bank_pg_memo_rule_score import evaluate as rule_evaluate  # noqa: E402

SIM_GRID: tuple[float, ...] = tuple(round(0.50 + 0.02 * i, 2) for i in range(26))  # 0.50~1.00


@dataclass(frozen=True)
class Metrics:
    matched: int
    matched_hit: int
    links: int
    correct: int
    no_match: int
    false_links: int

    @property
    def recall(self) -> float | None:
        return self.matched_hit / self.matched if self.matched else None

    @property
    def precision(self) -> float | None:
        return self.correct / self.links if self.links else None

    @property
    def false_link_rate(self) -> float | None:
        return self.false_links / self.no_match if self.no_match else None


def evaluate(cases: list[dict], sims: dict[str, float], threshold: float, split: str) -> Metrics:
    matched = matched_hit = links = correct = no_match = false_links = 0
    for case in cases:
        if case["split"] != split:
            continue
        decided = sims[case["id"]] >= threshold
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


def select(cases: list[dict], sims: dict[str, float]) -> float | None:
    """dev 집합에서 오연결률이 정확히 0인 임계값들 중 재현율 최댓값을 고른다.

    0을 만드는 임계값이 없으면 None (0이 아닌 값을 대신 고르지 않는다 — 태윤님 기준은
    취향이 아니라 꼭 지켜야 할 조건)."""
    rows = [(t, evaluate(cases, sims, t, "dev")) for t in SIM_GRID]
    zero_fl = [(t, m) for t, m in rows if m.false_link_rate == 0]
    if not zero_fl:
        return None
    best_t, _ = max(zero_fl, key=lambda tm: (tm[1].recall or 0.0))
    return best_t


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    labels = argv[1:]
    if not labels:
        print(
            "사용법: score_bank_pg_memo_embed.py <라벨1> <라벨2> ...  "
            "(collect_bank_pg_memo_embed.py와 같은 라벨)"
        )
        return 2

    data = json.loads((HERE / "bank_pg_memo_cases.json").read_text(encoding="utf-8"))
    cases, pg_memo = data["cases"], data["pg_memo_placeholder"]

    print(
        "| 방식 | 고른 임계값 | dev 재현율 | dev 정밀도 | dev 오연결률"
        " | holdout 재현율 | holdout 정밀도 | holdout 오연결률 |"
    )
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")

    rd, rh = rule_evaluate(cases, pg_memo, "dev"), rule_evaluate(cases, pg_memo, "holdout")
    print(
        f"| 부분 문자열 (지금) | - | {_fmt(rd.recall)} | {_fmt(rd.precision)}"
        f" | {_fmt(rd.false_link_rate)} | {_fmt(rh.recall)} | {_fmt(rh.precision)}"
        f" | {_fmt(rh.false_link_rate)} |"
    )

    for label in labels:
        path = HERE / "results" / "bank_pg_memo_embed_sims" / f"sims_{label}.json"
        if not path.exists():
            note = f"(결과 없음: 먼저 collect_bank_pg_memo_embed.py {label} 실행)"
            print(f"| 임베딩 {label} | {note} | | | | | | |")
            continue
        sims_data = json.loads(path.read_text(encoding="utf-8"))
        sims = sims_data["max_sim"]
        t = select(cases, sims)
        if t is None:
            print(
                f"| 임베딩 {label} ({sims_data['model']}) | dev 오연결률 0을 만드는 "
                "임계값 없음 | | | | | | |"
            )
            continue
        d, h = evaluate(cases, sims, t, "dev"), evaluate(cases, sims, t, "holdout")
        print(
            f"| 임베딩 {label} ({sims_data['model']}) | 유사도≥{t:.2f} | {_fmt(d.recall)}"
            f" | {_fmt(d.precision)} | {_fmt(d.false_link_rate)} | {_fmt(h.recall)}"
            f" | {_fmt(h.precision)} | {_fmt(h.false_link_rate)} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
