"""CI 자동 수정 성공률(dev_agent.coder) 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 3순위).

무엇을 재나
- "실패 → 한 번 고친 뒤 통과 비율". 평가 데이터가 없어 일부러 깬 PR 케이스를 새로 만들었다
  (`ci_fix_cases.json`, 8건). task_success_eval.py·scope_accuracy_eval.py와 같은 production
  경로(`dev_agent.coder.run`)를 돌리되, 여기서는 첫 PR의 CI를 목에서 일부러 실패시켜
  `coder._fix_ci`(로그를 보고 한 번 고쳐 같은 PR에 다시 올리는 길)가 실제로 동작하게 한다.

어떻게 재나
- `CiFixAdapter`(MockVcsAdapter 상속)가 처음 CI 확인에는 항상 실패를 주고, 실패 이유를
  정해 둔 로그(`ci_log`, 실제 타입체크·린트 오류 같은 형식)로 돌려준다. `coder._fix_ci`가
  이 로그를 데이터로 넣어 LLM에게 다시 쓰게 하고 같은 PR 브랜치에 커밋하면(`update_change`),
  그 다음 CI 확인부터는 **커밋된 글이 정답 기준(`fix_must_contain`·`fix_must_not_contain`)을
  채우는지 직접 본다** — 스크립트로 미리 정한 pass/fail이 아니라 실제로 고쳤는지를 판정이 거쳐
  가게 한 것(이 또한 production 판정이 아니라 평가 전용 근사치: 정답 문자열 포함 여부만 본다).
- 작업당 서비스를 새로 띄운다. 나머지 흐름(목 저장소·`DevService`·자동 머지)은 앞선 두
  평가(작업 성공률·범위 정확도)와 같다.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
sys.path.insert(0, str(HERE))


def load_env() -> dict[str, str]:
    """backend/.env를 읽어 os.environ에 없는 값만 보탠다 (DEV_LLM_* 모델 설정)."""
    env = dict(os.environ)
    path = BACKEND / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.split(" #")[0].strip().strip('"')
            if value:
                env[key.strip()] = value
    return env


os.environ.update(load_env())
os.environ["AGENT_TEMPLATE_MOCK"] = "1"  # 실행 관문은 목 모드 (판정은 safety 쪽 평가 대상)

from ax_agent_template import config  # noqa: E402
from ax_agent_template.testing import clear_state  # noqa: E402
from ax_workflow import base as workflow_base  # noqa: E402
from dev_agent import coder, vcs  # noqa: E402
from dev_agent import runtime as dev_runtime  # noqa: E402
from dev_agent.service import DevService  # noqa: E402
from dev_agent.vcs import MockVcsAdapter  # noqa: E402
from dev_agent.vcs.base import CheckLog, CheckRun, combine_checks  # noqa: E402

coder.CI_POLL_SECONDS = 0.0  # 시험과 같다: CI 기다리지 않는다
coder.NO_CI_GRACE_SECONDS = 0.0


class CiFixAdapter(MockVcsAdapter):
    """첫 CI 확인은 항상 실패, 로그로 정해 둔 글. 커밋(update_change)이 한 번 오면
    그 글이 정답 기준을 채우는지 직접 보고 통과·실패를 정한다 (스크립트 pass/fail 아님)."""

    def __init__(
        self, check_name: str, log: str, fix_path: str, must: list[str], must_not: list[str]
    ):
        super().__init__()
        self.check_name = check_name
        self.log = log
        self.fix_path = fix_path
        self.must = must
        self.must_not = must_not
        self.fix_attempted = False

    async def update_change(self, number: int, files: dict[str, str], message: str):
        result = await super().update_change(number, files, message)
        self.fix_attempted = True
        return result

    async def get_checks(self, number: int):
        if not self.fix_attempted:
            return combine_checks([CheckRun(self.check_name, "failed")])
        content = self.contents(number).get(self.fix_path, "")
        ok = all(s in content for s in self.must) and not any(s in content for s in self.must_not)
        return combine_checks([CheckRun(self.check_name, "passed" if ok else "failed")])

    async def get_failed_logs(self, number: int) -> list[CheckLog]:
        return [CheckLog(name=self.check_name, text=self.log)]


async def _no_handoff(*_: Any, **__: Any) -> str:
    return "보냄 (평가: 버림)"


async def run_case(llm: Any, case: dict) -> dict:
    clear_state()
    config.set_agent_name("planning")
    workflow_base.send_to_service = _no_handoff  # type: ignore[assignment]
    vcs._screen = None
    fix_path = next(iter(case["files"]))
    adapter = CiFixAdapter(
        check_name=case["check_name"],
        log=case["ci_log"],
        fix_path=fix_path,
        must=case["fix_must_contain"],
        must_not=case["fix_must_not_contain"],
    )
    adapter.seed_files(dict(case["files"]))
    vcs.set_vcs(adapter)
    dev = dev_runtime.set_service(DevService(dev_sender=_no_handoff))
    coder.set_llm(llm)

    row: dict[str, Any] = {"id": case["id"]}
    try:
        await dev.create_dev_task(
            title=case["title"], plan=case["plan"], done=case["done"], paths=case.get("paths")
        )
        await dev.wait_coding()
    except Exception as exc:  # noqa: BLE001 - 평가는 멈추지 않고 실패로 기록
        row.update(status="exception", merged=False, fix_attempted=False, ci_fix_success=False)
        row["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return row

    task = next((t for t in dev.state.tasks if t.id == "T-D1"), None)
    cr = task.coding if task else None
    status = cr.status if cr else "no_task"
    row["status"] = status
    row["merged"] = status == "merged"
    row["fix_attempted"] = adapter.fix_attempted
    final = await adapter.read_file(fix_path) if adapter.fix_attempted else ""
    ok = all(s in final for s in case["fix_must_contain"]) and not any(
        s in final for s in case["fix_must_not_contain"]
    )
    row["ci_fix_success"] = bool(adapter.fix_attempted and row["merged"] and ok)
    return row


async def run_all(cases: list[dict]) -> list[dict]:
    from ax_agent_template.llm import LLMClient
    from dev_agent import llm_env, llm_key

    settings = llm_key.apply(llm_env.settings())
    if not settings.model:
        raise SystemExit("LLM 설정이 없다(backend/.env, DEV_LLM_MODEL 또는 LLM_MODEL)")
    llm = LLMClient(llm_env.DEPT, settings=settings, timeout=180.0)
    return [await run_case(llm, case) for case in cases]


def score(rows: list[dict]) -> dict:
    n = len(rows)
    attempted = [r for r in rows if r["fix_attempted"]]
    success = [r for r in rows if r["ci_fix_success"]]
    statuses = sorted({r["status"] for r in rows})
    by_status = {s: sum(1 for r in rows if r["status"] == s) for s in statuses}
    return {
        "n": n,
        "fix_attempt_rate": len(attempted) / n if n else None,  # 로그를 읽고 한 번 고치려 했나
        "ci_fix_success_rate": len(success) / n if n else None,  # 고친 뒤 통과(전체 중)
        "success_given_attempted": (len(success) / len(attempted)) if attempted else None,
        "by_status": by_status,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "results" / "ci_fix_eval.json"))
    args = parser.parse_args()
    cases = json.loads((HERE / "ci_fix_cases.json").read_text(encoding="utf-8"))["cases"]
    rows = asyncio.run(run_all(cases))
    result = {
        "model": os.environ.get("DEV_LLM_MODEL") or os.environ.get("LLM_MODEL"),
        "rows": rows,
        "metrics": score(rows),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"저장: {out_path}")


if __name__ == "__main__":
    main()
