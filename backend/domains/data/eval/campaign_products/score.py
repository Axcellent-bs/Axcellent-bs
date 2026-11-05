"""campaign_effect 상품 기준 채점기 (#646). `cases/`·`holdout/`을 읽어 판정 함수 하나를 채점한다.

    cd backend
    uv run python domains/data/eval/campaign_products/score.py --judge <모듈>:<함수>

판정 함수는 인자로 받는다(skill이 아직 없어도 돈다). 모양:

    judge(request: dict, transport: httpx.MockTransport) -> dict   (async여도 된다)

- `request`: 케이스 입력 `{"campaign_id": None, "window_from": ISO, "window_to": ISO,
  "products": [상품 id 또는 이름]}`
- `transport`: 이 케이스 데이터로 만든 shop-api 응답(`eval_world.World.transport`:
  `/api/shop/metric-series`·`/orders`·`/products`). skill은 실제처럼 shop-api를 읽는다
- 돌려줄 답 모양(`Answer`): `{"products": ...}`
  - 늘: `matched`(맞은 상품 id), `unmatched`(못 맞춘 요청 값)
  - 알 때: `{"status": "known", "orders"|"units"|"gross": {"direction": "up"|"down"|"uncertain",
    "incremental": 숫자}}` (결제 완료 주문만. gross = 그 상품 단가 × 수량, 할인 전)
  - 모를 때: `{"status": "unknown", "unknown_reason": "..."}` (0으로 채우지 않는다)

지표 (README):
- `campaign_products.direction_rate`: 정답 방향이 있는 케이스 × 세 칸(주문 수·수량·판매액) 중
  맞힌 비율. 정답 none은 "uncertain"(확실하지 않음)도 맞다. up·down은 그 방향이어야 맞다
- `campaign_products.other_product_none_rate`: 다른 상품만 늘린 케이스에서 세 칸을 모두
  none·uncertain으로 답한 비율 (가게나 다른 상품 증가를 이 상품 성과로 세지 않는지)
- `campaign_products.excess_error`: 판매액 증분 오차율 = |답 − 센 값| ÷ 기준선 판매액 (평균)
- `campaign_products.match_rate`: 모든 케이스에서 matched·unmatched가 정답과 같은 비율
- `campaign_products.no_match_unknown_rate`: 맞는 상품이 없는 케이스를 unknown + "맞는 상품 없음"
- `campaign_products.gap_unknown_rate`: 구간 결제 공백 케이스를 unknown + "결제 기록 공백"
- `campaign_products.short_baseline_unknown_rate`: 기준선 부족 케이스를 unknown + "기준선 부족"
- holdout: `holdout.campaign_products.*` (화면에는 틀린 개수만)
판정 함수가 오류를 내거나 모양이 틀리면 그 케이스의 모든 항목은 틀린 것(`missing`)으로 센다.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import eval_world  # noqa: E402 - 결제·캠페인 평가와 같은 생성기

CASES = HERE / "cases"
HOLDOUT = HERE / "holdout"
MISSING = "missing"
PARTS = ("orders", "units", "gross")
WORDS = {
    "payment_gap": "결제 기록 공백",
    "baseline_short": "기준선 부족",
    "no_match": "맞는 상품 없음",
}
METRICS = (
    "campaign_products.direction_rate",
    "campaign_products.other_product_none_rate",
    "campaign_products.excess_error",
    "campaign_products.match_rate",
    "campaign_products.no_match_unknown_rate",
    "campaign_products.gap_unknown_rate",
    "campaign_products.short_baseline_unknown_rate",
)

Judge = Callable[[dict[str, Any], httpx.MockTransport], "dict | Awaitable[dict]"]


class _Part(BaseModel):
    model_config = ConfigDict(extra="ignore")
    direction: Literal["up", "down", "uncertain", "none"] | None = None
    incremental: float | None = None


class _Products(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["known", "unknown"]
    unknown_reason: str | None = None
    matched: list[str] = Field(default_factory=list)
    unmatched: list[str] = Field(default_factory=list)
    orders: _Part | None = None
    units: _Part | None = None
    gross: _Part | None = None


class Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    products: _Products


def load(folder: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


_worlds: dict[str, eval_world.World] = {}


def world_of(case: dict[str, Any]) -> eval_world.World:
    key = json.dumps(case["spec"], sort_keys=True)
    if key not in _worlds:
        _worlds[key] = eval_world.build(case["spec"])
    return _worlds[key]


async def judge_case(judge: Judge, case: dict[str, Any]) -> Answer | None:
    world = world_of(case)
    try:
        result = judge(dict(case["input"]), world.transport())
        if inspect.isawaitable(result):
            result = await result
        return Answer.model_validate(result)
    except (ValidationError, Exception):  # noqa: BLE001 - 판정 함수 오류는 틀린 것으로 센다
        return None


def _rate(hit: int, total: int) -> float | None:
    return round(hit / total, 4) if total else None


def direction_ok(want: str, got: str | None) -> bool:
    if want == "none":
        return got in ("none", "uncertain")
    return got == want


def _other_product(case: dict[str, Any]) -> bool:
    """다른 상품만 늘린 케이스 (요청 상품은 가게 배수 그대로 1배)."""
    boost = case["spec"].get("product_boost")
    want = case["expected"]["products"]
    return bool(
        boost and want["status"] == "known" and boost["product_id"] not in want["matched"]
        and want.get("lift") == 1.0
    )  # fmt: skip


def score(cases: list[dict[str, Any]], answers: dict[str, Answer | None]) -> dict[str, Any]:
    direction = [0, 0]
    other = [0, 0]
    errors: list[float] = []
    matched = [0, 0]
    unknown = {k: [0, 0] for k in WORDS}
    wrong: list[dict[str, str]] = []

    def miss(case_id: str, item: str, want: str, got: str) -> None:
        wrong.append({"case": case_id, "item": item, "expected": want, "got": got})

    for case in cases:
        cid, want, counted = case["id"], case["expected"]["products"], case["counted"]
        answer = answers.get(cid)
        got = answer.products if answer else None
        matched[1] += 1
        if got and got.matched == want["matched"] and got.unmatched == want["unmatched"]:
            matched[0] += 1
        else:
            miss(cid, "match", f"{want['matched']} / {want['unmatched']}",
                 f"{got.matched} / {got.unmatched}" if got else MISSING)  # fmt: skip
        if want["status"] == "unknown":
            kind = want["unknown_reason"]
            tally, word = unknown[kind], WORDS[kind]
            tally[1] += 1
            if got and got.status == "unknown" and word in (got.unknown_reason or ""):
                tally[0] += 1
            else:
                miss(cid, kind, f"unknown({word})", got.status if got else MISSING)
            continue
        all_none = True
        for part in PARTS:
            direction[1] += 1
            part_got = getattr(got, part) if got and got.status == "known" else None
            ok = bool(part_got and direction_ok(want[part], part_got.direction))
            direction[0] += ok
            all_none = all_none and bool(part_got and direction_ok("none", part_got.direction))
            if not ok:
                said = got.status if got else MISSING
                miss(cid, part, want[part], (part_got.direction or "없음") if part_got else said)
        if _other_product(case):
            other[1] += 1
            other[0] += all_none
        gross = got.gross if got and got.status == "known" else None
        base = counted.get("gross_baseline")
        if gross is not None and gross.incremental is not None and base:
            errors.append(abs(gross.incremental - counted["gross_excess"]) / base)

    return {
        "metrics": {
            "campaign_products.direction_rate": _rate(*direction),
            "campaign_products.other_product_none_rate": _rate(*other),
            "campaign_products.excess_error": (
                round(sum(errors) / len(errors), 4) if errors else None
            ),
            "campaign_products.match_rate": _rate(*matched),
            "campaign_products.no_match_unknown_rate": _rate(*unknown["no_match"]),
            "campaign_products.gap_unknown_rate": _rate(*unknown["payment_gap"]),
            "campaign_products.short_baseline_unknown_rate": _rate(*unknown["baseline_short"]),
        },
        "counts": {
            "direction_items": direction[1],
            "other_product_cases": other[1],
            "excess_answers": len(errors),
            "match_cases": matched[1],
            **{f"{k}_cases": v[1] for k, v in unknown.items()},
        },
        "wrong": wrong,
    }


async def _answers(judge: Judge, cases: list[dict[str, Any]]) -> dict[str, Answer | None]:
    return {c["id"]: await judge_case(judge, c) for c in cases}


async def run(judge: Judge, cases_dir: Path = CASES, holdout_dir: Path = HOLDOUT) -> dict[str, Any]:
    """`cases/`와 `holdout/`을 채점한다. holdout은 지표와 틀린 개수만 남긴다(케이스 이름 없음)."""
    cases = load(cases_dir)
    seen = score(cases, await _answers(judge, cases))
    held_cases = load(holdout_dir)
    held = score(held_cases, await _answers(judge, held_cases))
    return {
        "metrics": {
            **seen["metrics"],
            **{f"holdout.{k}": v for k, v in held["metrics"].items() if k in METRICS},
        },
        "counts": seen["counts"],
        "wrong": seen["wrong"],
        "holdout": {
            "seed": held_cases[0]["holdout"]["seed"] if held_cases else None,
            "cases": len(held_cases),
            "wrong_count": len(held["wrong"]),
        },
    }


def render(report: dict[str, Any]) -> str:
    lines = [f"{name}: {value}" for name, value in report["metrics"].items()]
    lines.append(f"틀린 항목 (cases/): {len(report['wrong'])}")
    lines.extend(
        f"  - {w['case']} [{w['item']}] 정답 {w['expected']}, 답 {w['got']}"
        for w in report["wrong"]
    )
    held = report["holdout"]
    lines.append(
        f"holdout (seed {held['seed']}, {held['cases']}개): 틀린 항목 {held['wrong_count']}"
    )
    return "\n".join(lines)


def _load_judge(spec: str) -> Judge:
    module, _, name = spec.partition(":")
    if not module or not name:
        raise SystemExit("--judge 는 <모듈>:<함수> 모양이다")
    return getattr(importlib.import_module(module), name)


def main() -> None:
    parser = argparse.ArgumentParser(description="campaign_effect 상품 기준 채점")
    parser.add_argument("--judge", required=True, help="<모듈>:<함수>")
    args = parser.parse_args()
    print(render(asyncio.run(run(_load_judge(args.judge)))))


if __name__ == "__main__":
    main()
