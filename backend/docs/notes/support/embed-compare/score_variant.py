"""임베딩 모델 비교: `collect_variant.py`가 만든 `sims/basic_<라벨>.json` 여러 개를
data 부서의 기존 `cases.json`(정답, 읽기 전용) 기준으로 한 표에 채점한다.

연결 규칙·지표·기준 고르는 법은 `domains/data/eval/product_link/score.py`(data 부서 소유)와
완전히 같다 — 모델 간 비교가 공정하려면 같은 채점 방식을 써야 하므로, 그 파일의 로직을
그대로 옮겨왔다(파일은 읽기만 하고 쓰지 않는다).

사용법:
    cd backend
    uv run python docs/notes/support/embed-compare/score_variant.py \
      gemini-embedding-001 text-embedding-3-large text-embedding-3-small solar-embedding-1-large
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_EVAL = HERE.parent.parent.parent.parent / "domains" / "data" / "eval" / "product_link"

MARGIN_KINDS: tuple[str, ...] = ("none", "top2", "mean")
MIN_SIM_GRID: tuple[float, ...] = tuple(round(0.30 + 0.05 * i, 2) for i in range(11))
MIN_MARGIN_GRID: tuple[float, ...] = (0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15)
MIN_PRECISION = 0.9
LIMIT = 3


@dataclass(frozen=True)
class Params:
    margin_kind: str
    min_sim: float
    min_margin: float


@dataclass(frozen=True)
class Metrics:
    related: int
    related_hit: int
    links: int
    correct: int
    unrelated: int
    unrelated_linked: int

    @property
    def recall(self) -> float | None:
        return self.related_hit / self.related if self.related else None

    @property
    def precision(self) -> float | None:
        return self.correct / self.links if self.links else None

    @property
    def false_rate(self) -> float | None:
        return self.unrelated_linked / self.unrelated if self.unrelated else None


def decide(sims: list[float], p: Params, limit: int = LIMIT) -> list[int]:
    order = sorted(range(len(sims)), key=lambda i: (-sims[i], i))
    out: list[int] = []
    for i in order[:limit]:
        s = sims[i]
        if s < p.min_sim:
            break
        others = [sims[j] for j in range(len(sims)) if j != i]
        if p.margin_kind == "none" or not others:
            ref = float("-inf")
        elif p.margin_kind == "top2":
            ref = max(others)
        elif p.margin_kind == "mean":
            ref = sum(others) / len(others)
        else:
            raise ValueError(p.margin_kind)
        if s - ref >= p.min_margin - 1e-9:
            out.append(i)
    return out


def evaluate(cases: list[dict], sims: dict, p: Params, split: str) -> Metrics:
    products: list[str] = sims["products"]
    related = related_hit = links = correct = unrelated = unrelated_linked = 0
    for case in cases:
        if case["split"] != split:
            continue
        linked = {products[i] for i in decide(sims["sims"][case["id"]], p)}
        relevant = set(case["relevant"])
        links += len(linked)
        correct += len(linked & relevant)
        if relevant:
            related += 1
            related_hit += bool(linked & relevant)
        else:
            unrelated += 1
            unrelated_linked += bool(linked)
    return Metrics(related, related_hit, links, correct, unrelated, unrelated_linked)


def grid() -> list[Params]:
    out = [Params("none", s, 0.0) for s in MIN_SIM_GRID]
    for kind in ("top2", "mean"):
        out += [Params(kind, s, m) for s in MIN_SIM_GRID for m in MIN_MARGIN_GRID]
    return out


def acceptable(m: Metrics) -> bool:
    return m.false_rate == 0 and (m.precision is None or m.precision >= MIN_PRECISION)


def select(cases: list[dict], sims: dict) -> Params | None:
    best: tuple[tuple, Params] | None = None
    for p in grid():
        m = evaluate(cases, sims, p, "dev")
        if not acceptable(m) or not m.related_hit:
            continue
        key = (m.recall or 0.0, p.min_margin, p.min_sim, -MARGIN_KINDS.index(p.margin_kind))
        if best is None or key > best[0]:
            best = (key, p)
    return best[1] if best else None


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    labels = argv[1:]
    if not labels:
        print("사용법: score_variant.py <라벨1> <라벨2> ...  (collect_variant.py에 준 라벨과 같게)")
        return 2

    cases = json.loads((DATA_EVAL / "cases.json").read_text(encoding="utf-8"))["cases"]

    print("| 모델 | 고른 설정 | dev 재현율 | dev 정밀도 | dev 오연결률"
          " | holdout 재현율 | holdout 정밀도 | holdout 오연결률 |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for label in labels:
        path = HERE / "sims" / f"basic_{label}.json"
        if not path.exists():
            note = f"(sims 파일 없음: 먼저 collect_variant.py {label} 실행)"
            print(f"| {label} | {note} | | | | | | |")
            continue
        sims = json.loads(path.read_text(encoding="utf-8"))
        p = select(cases, sims)
        if p is None:
            note = "조건(오연결률 0, 정밀도 ≥0.9) 만족 설정 없음"
            print(f"| {label} ({sims['model']}) | {note} | | | | | | |")
            continue
        d, h = evaluate(cases, sims, p, "dev"), evaluate(cases, sims, p, "holdout")
        setting = f"{p.margin_kind} / 유사도≥{p.min_sim:.2f}" + (
            f" / 여유≥{p.min_margin:.2f}" if p.margin_kind != "none" else ""
        )
        print(
            f"| {label} ({sims['model']}) | {setting} | {_fmt(d.recall)}"
            f" | {_fmt(d.precision)} | {_fmt(d.false_rate)}"
            f" | {_fmt(h.recall)} | {_fmt(h.precision)} | {_fmt(h.false_rate)} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
