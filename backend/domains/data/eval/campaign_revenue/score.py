"""campaign_effect 매출(증분 판매액) 채점기. `cases/`·`holdout/`을 읽어 판정 함수 하나를 채점한다.

    cd backend
    uv run python domains/data/eval/campaign_revenue/score.py --judge <모듈>:<함수>

판정 함수는 인자로 받는다(skill이 아직 없어도 돈다). 모양:

    judge(request: dict, transport: httpx.MockTransport) -> dict   (async여도 된다)

- `request`: 케이스 입력 `{"campaign_id": str | None, "window_from": ISO, "window_to": ISO}`
- `transport`: 이 케이스 데이터로 만든 shop-api 응답(`eval_world.World.transport`:
  `/api/shop/metric-series`·`/coupons`·`/orders`). skill은 실제처럼 shop-api를 읽는다
- 돌려줄 답 모양(`Answer`): `{"sales": ...}`
  - 알 때: `{"status": "known", "direction": "up"|"down"|"uncertain",
    "incremental_gross_krw": int, "direct_attributed_krw": int | None}`
    (주문 금액 = 상품가 × 수량, 할인 전. 결제 완료 주문만)
  - 모를 때: `{"status": "unknown", "unknown_reason": "..."}` (0으로 채우지 않는다)

지표 (README):
- `campaign_revenue.direction_rate`: 정답 방향이 있는 케이스 중 맞힌 비율. 정답 none은
  "uncertain"(확실하지 않음)도 맞다고 본다. up·down은 그 방향이어야 맞다
- `campaign_revenue.excess_error`: 증분 판매액 오차율 = |답 − 센 값| ÷ 기준선 판매액 (평균)
- `campaign_revenue.gap_unknown_rate`: 구간 결제 기록 공백 케이스를
  unknown + "결제 기록 공백"으로 답한 비율
- `campaign_revenue.direct_match`: 쿠폰 귀속 케이스에서 쿠폰 주문 판매액 합이 센 값과 같은 비율
- holdout: `holdout.campaign_revenue.*` (화면에는 틀린 개수만)
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
from pydantic import BaseModel, ConfigDict, ValidationError

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import eval_world  # noqa: E402 - 결제·캠페인 평가와 같은 생성기

CASES = HERE / "cases"
HOLDOUT = HERE / "holdout"
MISSING = "missing"
GAP_WORD = "결제 기록 공백"  # 공백 케이스 unknown 이유에 있어야 하는 말
METRICS = (
    "campaign_revenue.direction_rate",
    "campaign_revenue.excess_error",
    "campaign_revenue.gap_unknown_rate",
    "campaign_revenue.direct_match",
)

Judge = Callable[[dict[str, Any], httpx.MockTransport], "dict | Awaitable[dict]"]


class _Sales(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["known", "unknown"]
    unknown_reason: str | None = None
    direction: Literal["up", "down", "uncertain", "none"] | None = None
    incremental_gross_krw: int | None = None
    direct_attributed_krw: int | None = None


class Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sales: _Sales


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


def score(cases: list[dict[str, Any]], answers: dict[str, Answer | None]) -> dict[str, Any]:
    direction = [0, 0]
    errors: list[float] = []
    gap = [0, 0]
    direct = [0, 0]
    wrong: list[dict[str, str]] = []

    def miss(case_id: str, item: str, want: str, got: str) -> None:
        wrong.append({"case": case_id, "item": item, "expected": want, "got": got})

    for case in cases:
        cid, want, counted = case["id"], case["expected"]["sales"], case["counted"]
        answer = answers.get(cid)
        got = answer.sales if answer else None
        if want["status"] == "unknown":
            gap[1] += 1
            if got and got.status == "unknown" and GAP_WORD in (got.unknown_reason or ""):
                gap[0] += 1
            else:
                miss(cid, "gap", f"unknown({GAP_WORD})", got.status if got else MISSING)
            continue
        direction[1] += 1
        if got and got.status == "known" and direction_ok(want["direction"], got.direction):
            direction[0] += 1
        else:
            miss(cid, "direction", want["direction"],
                 (got.direction or got.status) if got else MISSING)  # fmt: skip
        base = counted.get("gross_baseline")
        if got and got.status == "known" and got.incremental_gross_krw is not None and base:
            errors.append(abs(got.incremental_gross_krw - counted["gross_excess"]) / base)
        if want.get("direct_attributed"):
            direct[1] += 1
            truth = counted["direct_attributed"]
            if got and got.status == "known" and got.direct_attributed_krw == truth:
                direct[0] += 1
            else:
                miss(cid, "direct", str(truth),
                     str(got.direct_attributed_krw) if got else MISSING)  # fmt: skip

    return {
        "metrics": {
            "campaign_revenue.direction_rate": _rate(*direction),
            "campaign_revenue.excess_error": (
                round(sum(errors) / len(errors), 4) if errors else None
            ),
            "campaign_revenue.gap_unknown_rate": _rate(*gap),
            "campaign_revenue.direct_match": _rate(*direct),
        },
        "counts": {
            "direction_cases": direction[1],
            "excess_answers": len(errors),
            "gap_cases": gap[1],
            "direct_cases": direct[1],
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
    parser = argparse.ArgumentParser(description="campaign_effect 매출 채점")
    parser.add_argument("--judge", required=True, help="<모듈>:<함수>")
    args = parser.parse_args()
    print(render(asyncio.run(run(_load_judge(args.judge)))))


if __name__ == "__main__":
    main()
