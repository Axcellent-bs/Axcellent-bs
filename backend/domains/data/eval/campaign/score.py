"""campaign_effect 채점기. `cases/`·`holdout/`을 읽어 판정 함수 하나를 채점한다.

    cd backend
    uv run python domains/data/eval/campaign/score.py --judge <모듈>:<함수>

판정 함수는 인자로 받는다(skill이 아직 없어도 돈다). 모양:

    judge(request: dict, transport: httpx.MockTransport) -> dict   (async여도 된다)

- `request`: 케이스 입력 `{"campaign_id": str | None, "window_from": ISO, "window_to": ISO}`
- `transport`: 이 케이스 데이터로 만든 shop-api 응답(`eval_world.World.transport`:
  `/api/shop/metric-series`·`/failed-orders`·`/coupons`). skill은 실제처럼 shop-api를 읽는다
- 돌려줄 답 모양(`Answer`): `orders`·`revenue`·`coupon` 각각
  - 알 때: `{"status": "known", ...}`. 주문·매출은 `direction`(up·none·down)과
    `excess`(실제 − 기대),
    쿠폰은 `use_rate`(조회 시점 누적 사용률, 0~1)와 `note`(설명 문장)
  - 모를 때: `{"status": "unknown", "unknown_reason": "..."}` (0으로 채우지 않는다)
  skill의 계약 출력(skill PR에서 추가)을 이 모양으로 옮기는 작은 함수를 skill PR에서 만든다.

지표 (README "채점"):
- 본 지표: `campaign.order_direction_rate`, `campaign.order_excess_error`,
  `campaign.unknown_handled_rate`, `campaign.coupon_usage_match`
- 따로 보고: `campaign.revenue_*` (shop-api에 매출 지표가 아직 없어 "확인할 수 없음"이 지금 정상)
- holdout: `holdout.campaign.*` (화면에는 틀린 개수만)
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

import eval_world  # noqa: E402 - 결제 평가와 같은 생성기

CASES = HERE / "cases"
HOLDOUT = HERE / "holdout"
MISSING = "missing"
COUPON_RATE_TOLERANCE = 0.005  # 사용률은 소수 셋째 자리에서 반올림한 값까지 맞다고 본다
# 쿠폰 설명에 있어야 하는 말: 조회 시점 누적이라는 것 + 캠페인 뒤 사용분이 섞인다는 것
NOTE_MUST_SAY = (("누적",), ("캠페인 뒤", "캠페인 이후", "캠페인이 끝난 뒤"))
MAIN_METRICS = (
    "campaign.order_direction_rate",
    "campaign.order_excess_error",
    "campaign.unknown_handled_rate",
    "campaign.coupon_usage_match",
)

Judge = Callable[[dict[str, Any], httpx.MockTransport], "dict | Awaitable[dict]"]


# ---------------------------------------------------------------- 답 모양


class _Part(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["known", "unknown"]
    unknown_reason: str | None = None
    direction: Literal["up", "none", "down"] | None = None
    excess: float | None = None  # 실제 − 기대 (주문 건수, 매출 원)
    use_rate: float | None = Field(default=None, ge=0)  # 쿠폰 조회 시점 누적 사용률
    note: str | None = None


class Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    orders: _Part
    revenue: _Part
    coupon: _Part


def load(folder: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


_worlds: dict[str, eval_world.World] = {}


def world_of(case: dict[str, Any]) -> eval_world.World:
    """케이스 데이터. 같은 명세는 늘 같은 데이터라 한 프로세스에서 한 번만 만든다."""
    key = json.dumps(case["spec"], sort_keys=True)
    if key not in _worlds:
        _worlds[key] = eval_world.build(case["spec"])
    return _worlds[key]


async def judge_case(judge: Judge, case: dict[str, Any]) -> Answer | None:
    """케이스 하나를 판정한다. 오류·모양이 틀리면 None (= 모든 항목 missing)."""
    world = world_of(case)
    try:
        result = judge(dict(case["input"]), world.transport())
        if inspect.isawaitable(result):
            result = await result
        return Answer.model_validate(result)
    except (ValidationError, Exception):  # noqa: BLE001 - 판정 함수 오류는 틀린 것으로 센다
        return None


# ---------------------------------------------------------------- 채점


def _rate(hit: int, total: int) -> float | None:
    return round(hit / total, 4) if total else None


def note_ok(note: str | None) -> bool:
    return bool(note) and all(any(word in note for word in words) for words in NOTE_MUST_SAY)


def score(cases: list[dict[str, Any]], answers: dict[str, Answer | None]) -> dict[str, Any]:
    """케이스와 답으로 지표를 낸다. `answers`: 케이스 id → 답(None이면 판정 실패)."""
    direction = [0, 0]  # 맞힘, 정답 방향이 있는 케이스
    errors: list[float] = []  # 주문 증분 절대 오차 (답이 증분을 낸 케이스만)
    unknown = [0, 0]  # unknown으로 답함, 정답 unknown 항목 (주문·쿠폰. 매출은 따로)
    coupon = [0, 0]  # 사용률·설명 맞음, 정답 쿠폰 known 케이스
    revenue_direction = [0, 0]
    revenue_unknown = [0, 0]  # 매출을 unknown으로 답함, 정답 매출 known 케이스
    wrong: list[dict[str, str]] = []

    def miss(case_id: str, item: str, want: str, got: str) -> None:
        wrong.append({"case": case_id, "item": item, "expected": want, "got": got})

    for case in cases:
        cid, exp, counted = case["id"], case["expected"], case["counted"]
        answer = answers.get(cid)
        # 주문
        want = exp["orders"]
        got = answer.orders if answer else None
        if want["status"] == "unknown":
            unknown[1] += 1
            if got and got.status == "unknown":
                unknown[0] += 1
            else:
                miss(cid, "orders", "unknown", got.status if got else MISSING)
        else:
            direction[1] += 1
            if got and got.status == "known" and got.direction == want["direction"]:
                direction[0] += 1
            else:
                miss(
                    cid,
                    "orders",
                    want["direction"],
                    (got.direction or got.status) if got else MISSING,
                )
            if got and got.status == "known" and got.excess is not None:
                truth = counted["orders_actual"] - counted["orders_baseline"]
                errors.append(abs(got.excess - truth))
        # 쿠폰
        want = exp["coupon"]
        got = answer.coupon if answer else None
        if want["status"] == "unknown":
            unknown[1] += 1
            if got and got.status == "unknown":
                unknown[0] += 1
            else:
                miss(cid, "coupon", "unknown", got.status if got else MISSING)
        else:
            coupon[1] += 1
            truth_rate = counted["coupon_use_rate"]
            rate_ok = bool(
                got and got.status == "known" and got.use_rate is not None
                and abs(got.use_rate - truth_rate) <= COUPON_RATE_TOLERANCE
            )  # fmt: skip
            if rate_ok and got and note_ok(got.note):
                coupon[0] += 1
            else:
                why = MISSING if not got else got.status if got.status != "known" else (
                    "사용률 다름" if not rate_ok else "설명 없음(누적·캠페인 뒤 섞임)"
                )  # fmt: skip
                miss(cid, "coupon", f"사용률 {truth_rate}", why)
        # 매출 (본 지표에서 뺀다)
        want = exp["revenue"]
        got = answer.revenue if answer else None
        if want["status"] == "known":
            revenue_direction[1] += 1
            revenue_unknown[1] += 1
            if got and got.status == "known" and got.direction == want["direction"]:
                revenue_direction[0] += 1
            if got and got.status == "unknown":
                revenue_unknown[0] += 1

    return {
        "metrics": {
            "campaign.order_direction_rate": _rate(*direction),
            "campaign.order_excess_error": round(sum(errors) / len(errors), 2) if errors else None,
            "campaign.unknown_handled_rate": _rate(*unknown),
            "campaign.coupon_usage_match": _rate(*coupon),
            "campaign.revenue_direction_rate": _rate(*revenue_direction),
            "campaign.revenue_unknown_rate": _rate(*revenue_unknown),
        },
        "counts": {
            "direction_cases": direction[1],
            "excess_answers": len(errors),
            "unknown_items": unknown[1],
            "coupon_cases": coupon[1],
            "revenue_cases": revenue_direction[1],
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
            **{f"holdout.{k}": v for k, v in held["metrics"].items() if k in MAIN_METRICS},
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
    parser = argparse.ArgumentParser(description="campaign_effect 채점")
    parser.add_argument("--judge", required=True, help="<모듈>:<함수>")
    parser.add_argument("--out", type=Path, default=None, help="결과 JSON 경로 (없으면 화면만)")
    args = parser.parse_args()
    report = asyncio.run(run(_load_judge(args.judge)))
    print(render(report))
    if args.out:
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
