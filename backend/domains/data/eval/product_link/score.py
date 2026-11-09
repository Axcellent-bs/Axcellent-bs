"""소재 ↔ 상품 연결 보정셋 채점·기준 고르기.

저장된 코사인(`sims/`)과 `cases.json`만 읽는다. 네트워크 없음.

    cd backend
    uv run python domains/data/eval/product_link/score.py            # 기준표 (모든 글 모양)
    uv run python domains/data/eval/product_link/score.py --form rich

연결 규칙(`decide`): 유사도 높은 순으로 최대 `limit`개를 보며, 유사도 ≥ `min_sim`이고
여유(유사도 − 기준) ≥ `min_margin`인 상품만 잇는다. 기준은 여유 종류마다 다르다.
- none: 여유를 보지 않는다(지금 content_recommend 방식)
- top2: 그 상품을 뺀 나머지 중 가장 높은 유사도 (= 1등이면 1등 − 2등. 1등만 이을 수 있다)
- mean: 그 상품을 뺀 나머지의 평균

기준 고르기(`select`, README "기준 고르는 법"): dev에서 무관 소재 오연결률 0, 연결 정밀도 ≥ 0.9인
설정 중 관련 질의 재현율이 가장 높은 것. 같으면 여유 기준이 큰 것, 그다음 유사도 기준이 큰 것,
그다음 여유 종류 none → top2 → mean 순. holdout은 고른 설정의 숫자만 본다.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARGIN_KINDS: tuple[str, ...] = ("none", "top2", "mean")
MIN_SIM_GRID: tuple[float, ...] = tuple(round(0.30 + 0.05 * i, 2) for i in range(11))  # 0.30~0.80
MIN_MARGIN_GRID: tuple[float, ...] = (0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15)
MIN_PRECISION = 0.9
LIMIT = 3  # content_recommend `max_products`와 같은 값
CURRENT = ("none", 0.6, 0.0)  # 지금 설정 (trends.toml [recommend] min_similarity 0.6, 여유 없음)


@dataclass(frozen=True)
class Params:
    margin_kind: str
    min_sim: float
    min_margin: float


@dataclass(frozen=True)
class Metrics:
    related: int  # 관련 질의 수
    related_hit: int  # 맞는 상품을 하나 이상 이은 관련 질의 수
    links: int  # 이은 상품 수 (전체)
    correct: int  # 그중 정답
    unrelated: int  # 무관 소재 수
    unrelated_linked: int  # 하나라도 이은 무관 소재 수

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
    """이을 상품의 위치(유사도 높은 순)."""
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
            raise ValueError(f"모르는 여유 종류: {p.margin_kind}")
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


def select(cases: list[dict], sims: dict, kinds: tuple[str, ...] = MARGIN_KINDS) -> Params | None:
    """dev 기준 고르기. 조건을 만족하는 설정이 없으면 None."""
    best: tuple[tuple, Params] | None = None
    for p in grid():
        if p.margin_kind not in kinds:
            continue
        m = evaluate(cases, sims, p, "dev")
        if not acceptable(m) or not m.related_hit:
            continue
        key = (m.recall or 0.0, p.min_margin, p.min_sim, -MARGIN_KINDS.index(p.margin_kind))
        if best is None or key > best[0]:
            best = (key, p)
    return best[1] if best else None


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def row(label: str, p: Params | None, cases: list[dict], sims: dict) -> str:
    if p is None:
        return f"| {label} | 조건을 만족하는 설정 없음 | | | | | | |"
    d, h = evaluate(cases, sims, p, "dev"), evaluate(cases, sims, p, "holdout")
    setting = f"{p.margin_kind} / 유사도 ≥ {p.min_sim:.2f}" + (
        f" / 여유 ≥ {p.min_margin:.2f}" if p.margin_kind != "none" else ""
    )
    return (
        f"| {label} | {setting} | {_fmt(d.recall)} | {_fmt(d.precision)} | {_fmt(d.false_rate)}"
        f" | {_fmt(h.recall)} | {_fmt(h.precision)} | {_fmt(h.false_rate)} |"
    )


def load(form: str) -> tuple[list[dict], dict]:
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]
    sims = json.loads((HERE / "sims" / f"{form}.json").read_text(encoding="utf-8"))
    return cases, sims


def report(form: str) -> list[str]:
    cases, sims = load(form)
    lines = [
        f"### {form} (모델 {sims['model']}, {sims['collected_at']})",
        "",
        "| 설정 | 기준 | dev 재현율 | dev 정밀도 | dev 무관 오연결률"
        " | holdout 재현율 | holdout 정밀도 | holdout 무관 오연결률 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        row("지금 설정", Params(*CURRENT), cases, sims),
    ]
    for kind in MARGIN_KINDS:
        lines.append(row(f"고른 설정 ({kind}만)", select(cases, sims, (kind,)), cases, sims))
    lines.append(row("**고른 설정 (전체)**", select(cases, sims), cases, sims))
    lines += ["", spread(cases, sims)]
    return lines


def spread(cases: list[dict], sims: dict) -> str:
    """참고 한 줄 (전체).

    관련 질의의 정답 상품 최고 유사도 최솟값, 무관 소재의 최고 유사도 최댓값.
    """
    products: list[str] = sims["products"]
    related, unrelated = [], []
    for case in cases:
        row_ = sims["sims"][case["id"]]
        if case["relevant"]:
            related.append(max(row_[products.index(p)] for p in case["relevant"]))
        else:
            unrelated.append(max(row_))
    return (
        f"참고: 관련 질의 {len(related)}개의 정답 상품 유사도 최솟값 {min(related):.3f}, "
        f"무관 소재 {len(unrelated)}개의 최고 유사도 최댓값 {max(unrelated):.3f}"
    )


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="소재 ↔ 상품 연결 보정셋 채점")
    parser.add_argument("--form", choices=("basic", "rich", "all"), default="all")
    args = parser.parse_args(argv)
    forms = ("basic", "rich") if args.form == "all" else (args.form,)
    for form in forms:
        print("\n".join(report(form)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
