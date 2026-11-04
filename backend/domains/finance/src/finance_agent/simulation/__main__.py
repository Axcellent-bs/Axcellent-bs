"""시뮬레이션 실행: `python -m finance_agent.simulation [--scenario a|b|all] [--out 폴더]`."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from finance_agent.simulation.engine import SCENARIOS, render_report, run_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description="재무 관점 전 부서 시뮬레이션")
    parser.add_argument("--scenario", choices=[*SCENARIOS, "all"], default="all")
    parser.add_argument("--out", default=".data/simulation", help="결과를 쓸 폴더")
    args = parser.parse_args()
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results = [asyncio.run(run_scenario(name)) for name in names]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for result in results:
        text = json.dumps(result, ensure_ascii=False, indent=2)
        (out / f"scenario-{result['id']}.json").write_text(text, encoding="utf-8")
    report = render_report(results)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
