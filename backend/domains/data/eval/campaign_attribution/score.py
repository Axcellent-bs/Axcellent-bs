"""campaign_effect 유입경로 기준 채점기 (#648). `cases/`·`holdout/`으로 판정 함수 하나를 채점한다.

    cd backend
    uv run python domains/data/eval/campaign_attribution/score.py --judge <모듈>:<함수>

판정 함수는 인자로 받는다(skill이 아직 없어도 돈다). 모양:

    judge(request: dict, transport: httpx.MockTransport) -> dict   (async여도 된다)

- `request`: `{"campaign_id", "window_from", "window_to"[, "products"]}`
- `transport`: 이 케이스 데이터로 만든 shop-api 응답 (`/orders/attributed`·`/orders`·`/products`
  ·`/metric-series` 등, `eval_world.World.transport`)
- 돌려줄 답 모양(`Answer`):
  - `attribution`: 알 때 `{"status": "known", "orders": int, "gross_krw": int,
    "by_source": {출처: {"orders", "gross_krw"}}, "tagged_share": 0~1}`, 모를 때
    `{"status": "unknown", "unknown_reason": "..."}` (0으로 채우지 않는다)
  - `overlap`(관련 상품을 요청했을 때): `{"status", "orders", "units", "gross_krw"}`

지표 (README, 평가 정의 PR에서는 관찰만 하고 관문은 skill PR에서 켠다):
- `campaign_attribution.count_match`: known 케이스에서 주문 수·판매액·출처별 값이 센 값과 같은 비율
- `campaign_attribution.share_error`: 유입 표시가 있는 주문 비율의 오차 (평균 절대값)
- `campaign_attribution.no_tag_unknown_rate`: 유입 표시 없는 케이스를 unknown + "유입 기록 없음"으로
- `campaign_attribution.other_campaign_excluded_rate`: 다른 캠페인 주문이 있는 케이스에서 그 주문을
  세지 않은 비율 (주문 수가 센 값과 같음)
- `campaign_attribution.overlap_match`: 유입 + 관련 상품 겹침 칸이 센 값과 같은 비율
  (unknown 정답 포함)
- holdout: `holdout.campaign_attribution.*` (화면에는 틀린 개수만)
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
NO_TAG_WORD = "유입 기록 없음"
SHARE_TOLERANCE = 0.0005
METRICS = (
    "campaign_attribution.count_match",
    "campaign_attribution.share_error",
    "campaign_attribution.no_tag_unknown_rate",
    "campaign_attribution.other_campaign_excluded_rate",
    "campaign_attribution.overlap_match",
)

Judge = Callable[[dict[str, Any], httpx.MockTransport], "dict | Awaitable[dict]"]


class _Row(BaseModel):
    model_config = ConfigDict(extra="ignore")
    orders: int
    gross_krw: int


class _Attribution(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["known", "unknown"]
    unknown_reason: str | None = None
    orders: int | None = None
    gross_krw: int | None = None
    by_source: dict[str, _Row] = Field(default_factory=dict)
    tagged_share: float | None = None


class _Overlap(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["known", "unknown"]
    unknown_reason: str | None = None
    orders: int | None = None
    units: int | None = None
    gross_krw: int | None = None


class Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    attribution: _Attribution
    overlap: _Overlap | None = None


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


def score(cases: list[dict[str, Any]], answers: dict[str, Answer | None]) -> dict[str, Any]:
    count = [0, 0]
    share: list[float] = []
    no_tag = [0, 0]
    other = [0, 0]
    overlap = [0, 0]
    wrong: list[dict[str, str]] = []

    def miss(case_id: str, item: str, want: str, got: str) -> None:
        wrong.append({"case": case_id, "item": item, "expected": want, "got": got})

    for case in cases:
        cid, want, counted = case["id"], case["expected"], case["counted"]
        answer = answers.get(cid)
        got = answer.attribution if answer else None
        if want["attribution"]["status"] == "unknown":
            no_tag[1] += 1
            if got and got.status == "unknown" and NO_TAG_WORD in (got.unknown_reason or ""):
                no_tag[0] += 1
            else:
                miss(cid, "no_tags", f"unknown({NO_TAG_WORD})", got.status if got else MISSING)
        else:
            count[1] += 1
            truth = {k: _Row(**v) for k, v in counted["by_source"].items()}
            same = bool(
                got and got.status == "known" and got.orders == counted["orders"]
                and got.gross_krw == counted["gross_krw"] and got.by_source == truth
            )  # fmt: skip
            count[0] += same
            if not same:
                miss(cid, "count", f"{counted['orders']}건 {counted['gross_krw']}원",
                     f"{got.orders}건 {got.gross_krw}원" if got else MISSING)  # fmt: skip
            if got and got.status == "known" and got.tagged_share is not None:
                share.append(abs(got.tagged_share - counted["tagged_share"]))
            if counted["other_campaign_orders"]:
                other[1] += 1
                if got and got.status == "known" and got.orders == counted["orders"]:
                    other[0] += 1
                else:
                    miss(cid, "other_campaign", f"{counted['orders']}건",
                         str(got.orders) if got else MISSING)  # fmt: skip
        if "overlap" in want:
            overlap[1] += 1
            ov = answer.overlap if answer else None
            if want["overlap"]["status"] == "unknown":
                ok = bool(ov and ov.status == "unknown")
            else:
                truth_ov = counted["overlap"]
                ok = bool(
                    ov and ov.status == "known" and ov.orders == truth_ov["orders"]
                    and ov.units == truth_ov["units"] and ov.gross_krw == truth_ov["gross_krw"]
                )  # fmt: skip
            overlap[0] += ok
            if not ok:
                miss(cid, "overlap", want["overlap"]["status"], ov.status if ov else MISSING)

    return {
        "metrics": {
            "campaign_attribution.count_match": _rate(*count),
            "campaign_attribution.share_error": (
                round(sum(share) / len(share), 4) if share else None
            ),
            "campaign_attribution.no_tag_unknown_rate": _rate(*no_tag),
            "campaign_attribution.other_campaign_excluded_rate": _rate(*other),
            "campaign_attribution.overlap_match": _rate(*overlap),
        },
        "counts": {
            "known_cases": count[1],
            "share_answers": len(share),
            "no_tag_cases": no_tag[1],
            "other_campaign_cases": other[1],
            "overlap_cases": overlap[1],
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
    parser = argparse.ArgumentParser(description="campaign_effect 유입경로 채점")
    parser.add_argument("--judge", required=True, help="<모듈>:<함수>")
    args = parser.parse_args()
    print(render(asyncio.run(run(_load_judge(args.judge)))))


if __name__ == "__main__":
    main()
