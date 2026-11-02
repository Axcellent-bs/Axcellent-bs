"""trend_scan 채점기. `cases/`·`holdout/`을 읽어 판정 함수 하나를 채점한다. 네이버를 부르지 않는다.

    cd backend
    uv run python domains/data/eval/trend/score.py --judge <모듈>:<함수>

판정 함수는 인자로 받는다(skill이 아직 없어도 돈다). 모양:

    judge(query: TrendScanInput, response: TrendResponse) -> TrendScanOutput  (async여도 된다)

- `query`: 케이스 요청 (63일 = 기준선 56일 + 판정 구간 7일, 하루 단위)
- `response`: 커넥터(`connectors.naver_datalab`)가 돌려주는 모양 그대로.
  케이스에 저장된 값으로 만든다
- 판정 구간은 요청의 마지막 7일이다. trend_scan은 최근 56일 대비 급상승을 본다
  (계절 반복 구분은 범위 밖)

지표 (README "채점"):
- 본 지표(계절·실제 사례 제외): `trend.detection_rate`, `trend.false_positive_rate`,
  `trend.unknown_handled_rate`, `trend.group_isolation`
- 따로 보고: `trend.seasonal_false_positive_rate`, `trend.real_detection_rate`
- holdout: `holdout.trend.*` (화면에는 틀린 개수만)
판정 함수가 오류를 내거나 그룹 결과를 빠뜨리면 그 그룹은 틀린 것(`missing`)으로 센다.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

from ax_contracts.schemas.data import TrendScanInput, TrendScanOutput
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"
HOLDOUT = HERE / "holdout"

Judge = Callable[[TrendScanInput, TrendResponse], "TrendScanOutput | Awaitable[TrendScanOutput]"]

MAIN_KINDS = frozenset({"injected", "past_spike", "joint", "none", "unknown"})
SEASONAL_KINDS = frozenset({"seasonal"})
REAL_KINDS = frozenset({"real_event"})
MISSING = "missing"  # 판정 함수가 오류를 냈거나 그룹을 빠뜨림
MAIN_METRICS = (
    "trend.detection_rate",
    "trend.false_positive_rate",
    "trend.unknown_handled_rate",
    "trend.group_isolation",
)


def load(folder: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def to_request(case: dict[str, Any]) -> tuple[TrendScanInput, TrendResponse]:
    """케이스 → (요청, 커넥터 응답). 저장된 값만 쓴다."""
    query = TrendScanInput.model_validate(case["input"])
    series = tuple(
        TrendSeries(
            group.group_name,
            tuple(group.keywords),
            tuple((date.fromisoformat(d), float(v)) for d, v in case["series"][group.group_name]),
        )
        for group in query.keyword_groups
    )
    return query, TrendResponse(query.start_date, query.end_date, query.time_unit, series)


async def judge_case(judge: Judge, case: dict[str, Any]) -> dict[str, str]:
    """그룹 → 판정 상태. 오류·빠진 그룹은 `missing`."""
    query, response = to_request(case)
    groups = [g.group_name for g in query.keyword_groups]
    try:
        result = judge(query, response)
        if inspect.isawaitable(result):
            result = await result
        output = TrendScanOutput.model_validate(result)
    except Exception:  # noqa: BLE001 - 판정 함수 오류는 그 케이스를 틀린 것으로 센다
        return dict.fromkeys(groups, MISSING)
    got = {v.group_name: v.status for v in output.results}
    return {g: got.get(g, MISSING) for g in groups}


def _rate(hit: int, total: int) -> float | None:
    return round(hit / total, 4) if total else None


def score(cases: list[dict[str, Any]], answers: dict[str, dict[str, str]]) -> dict[str, Any]:
    """케이스와 판정 결과로 지표를 낸다. `answers`: 케이스 id → 그룹 → 판정 상태."""
    rising = [0, 0]  # 잡음, 정답 rising 그룹 수
    normal = [0, 0]  # 오탐(rising), 정답 normal 그룹 수
    unknown = [0, 0]  # unknown으로 답함, 정답 unknown 그룹 수
    isolation = [0, 0]  # 넣은 그룹만 잡은 케이스, joint 케이스 수
    seasonal = [0, 0]  # rising으로 답함, 계절 그룹 수
    real = [0, 0]  # rising으로 답함, 실제 사례 그룹 수
    wrong: list[dict[str, str]] = []
    for case in cases:
        kind, got = case["kind"], answers[case["id"]]
        for group, expected in case["expected"].items():
            want, answer = expected["status"], got[group]
            if kind in SEASONAL_KINDS:
                seasonal[0] += answer == "rising"
                seasonal[1] += 1
            elif kind in REAL_KINDS:
                real[0] += answer == "rising"
                real[1] += 1
            elif want == "rising":
                rising[0] += answer == "rising"
                rising[1] += 1
            elif want == "normal":
                normal[0] += answer == "rising"
                normal[1] += 1
            elif want == "unknown":
                unknown[0] += answer == "unknown"
                unknown[1] += 1
            if kind in MAIN_KINDS and answer != want:
                wrong.append({"case": case["id"], "group": group, "expected": want, "got": answer})
        if kind == "joint":
            isolation[0] += all(
                (got[g] == "rising") == (e["status"] == "rising")
                for g, e in case["expected"].items()
            )
            isolation[1] += 1
    return {
        "metrics": {
            "trend.detection_rate": _rate(*rising),
            "trend.false_positive_rate": _rate(*normal),
            "trend.unknown_handled_rate": _rate(*unknown),
            "trend.group_isolation": _rate(*isolation),
            "trend.seasonal_false_positive_rate": _rate(*seasonal),
            "trend.real_detection_rate": _rate(*real),
        },
        "counts": {
            "rising_groups": rising[1],
            "normal_groups": normal[1],
            "unknown_groups": unknown[1],
            "joint_cases": isolation[1],
            "seasonal_groups": seasonal[1],
            "real_groups": real[1],
        },
        "wrong": wrong,
    }


async def run(judge: Judge, cases_dir: Path = CASES, holdout_dir: Path = HOLDOUT) -> dict[str, Any]:
    """`cases/`와 `holdout/`을 채점한다. holdout은 지표와 틀린 개수만 남긴다(케이스 이름 없음)."""
    cases = load(cases_dir)
    seen = score(cases, {c["id"]: await judge_case(judge, c) for c in cases})
    held_cases = load(holdout_dir)
    held = score(held_cases, {c["id"]: await judge_case(judge, c) for c in held_cases})
    held_metrics = {
        f"holdout.{name}": value
        for name, value in held["metrics"].items()
        if name in MAIN_METRICS  # holdout은 주입·평범만 있어 unknown·isolation은 None이다
    }
    return {
        "metrics": {**seen["metrics"], **held_metrics},
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
    lines.append(f"틀린 그룹 (cases/): {len(report['wrong'])}")
    lines.extend(f"  - {w['case']} [{w['group']}] 정답 {w['expected']}, 답 {w['got']}"
                 for w in report["wrong"])  # fmt: skip
    held = report["holdout"]
    lines.append(
        f"holdout (seed {held['seed']}, {held['cases']}개): 틀린 그룹 {held['wrong_count']}"
    )
    return "\n".join(lines)


def _load_judge(spec: str) -> Judge:
    module, _, name = spec.partition(":")
    if not module or not name:
        raise SystemExit(
            "--judge 는 <모듈>:<함수> 모양이다 (예: data_agent.skills.trend_scan:judge)"
        )
    return getattr(importlib.import_module(module), name)


def main() -> None:
    parser = argparse.ArgumentParser()
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
