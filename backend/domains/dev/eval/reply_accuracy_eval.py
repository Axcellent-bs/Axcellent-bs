"""대화 답 정확도(dev_agent.dev_chat) 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 5순위).

무엇을 재나
- "바꿀 파일·완료 기준, CI 원인 파일·줄 맞힌 비율" (같은 질문 묶음). 평가 데이터가 없어 새로
  만들었다(`reply_accuracy_cases.json`, 8건 — files_done 4건 · ci_cause 4건).
- files_done: 개발팀원이 할 일을 한 번에 또렷이 말했을 때, `dev_chat`이 세우는 계획의
  `plan.paths`(바꿀 파일)·`plan.done`(완료 기준)가 정답과 맞는지.
- ci_cause: 대화가 가리킨 PR의 CI 로그(`pr_ci`)를 읽고, 실패 원인 파일·줄을 답에 올바르게
  짚는지. 로그는 실제 타입체크·린트·테스트 실패 형식으로 만들었다.

어떻게 재나
- production 함수 `dev_chat.turn()`을 그대로 돌린다 (한 번 보내고 답까지 기다리는 경로, `send()`
  + 답 기다리기와 같다). `_reply`·`_ask_llm`·`pr_ci`·`SYSTEM_PROMPT`는 손대지 않는다.
- 새로 만든 건 합성 케이스뿐: files_done은 목 형상관리(MockVcsAdapter)에 파일을 미리 넣어
  대화의 파일 목록(`files`)에 보이게 하고, ci_cause는 `adapter.seed()`로 PR 번호를 만들고
  `adapter.set_logs()`로 실패 로그를 넣은 뒤 `pr={"number": n}`으로 그 PR을 가리키게 한다.
- 정답 판정은 평가 전용 근사치: plan.paths에 정답 경로가 모두 있는지(정확한 문자열), plan.done
  글 전체에 정답 낱말이 있는지(부분 문자열), 답 글에 정답 파일 이름·줄 번호가 있는지(부분
  문자열)만 본다. 뜻을 제대로 짚었는지까지는 보지 못한다 (README 한계 참고).
- 작업당 서비스를 새로 띄운다 (대화 상태가 섞이지 않게).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
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
os.environ["AGENT_TEMPLATE_MOCK"] = "1"  # 실행 관문은 목 모드 (판정은 대화 답 쪽이 평가 대상)

from ax_agent_template import config  # noqa: E402
from ax_agent_template.testing import clear_state  # noqa: E402
from ax_workflow import base as workflow_base  # noqa: E402
from dev_agent import dev_chat, vcs  # noqa: E402
from dev_agent import runtime as dev_runtime  # noqa: E402
from dev_agent.service import DevService  # noqa: E402
from dev_agent.vcs import MockVcsAdapter  # noqa: E402
from dev_agent.vcs.base import CheckRun  # noqa: E402


async def _no_handoff(*_: Any, **__: Any) -> str:
    return "보냄 (평가: 버림)"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _grade_files_done(th_plan: Any, case: dict) -> dict:
    paths = list(th_plan.paths) if th_plan is not None else []
    done_text = _norm(" ".join(th_plan.done)) if th_plan is not None else ""
    paths_ok = all(p in paths for p in case["expected_paths"])
    done_ok = all(kw.lower() in done_text for kw in case["expected_done_keywords"])
    return {
        "has_plan": th_plan is not None,
        "paths_correct": bool(th_plan is not None and paths_ok),
        "done_correct": bool(th_plan is not None and done_ok),
        "both_correct": bool(th_plan is not None and paths_ok and done_ok),
    }


def _grade_ci_cause(reply: str, case: dict) -> dict:
    low = _norm(reply)
    file_hit = case["expected_file"].lower() in low
    line_hit = bool(re.search(rf"(?<!\d){case['expected_line']}(?!\d)", low))
    return {
        "file_correct": file_hit,
        "line_correct": line_hit,
        "both_correct": bool(file_hit and line_hit),
    }


async def run_case(llm: Any, case: dict) -> dict:
    clear_state()
    config.set_agent_name("planning")
    workflow_base.send_to_service = _no_handoff  # type: ignore[assignment]
    vcs._screen = None
    adapter = MockVcsAdapter()
    vcs.set_vcs(adapter)
    dev = dev_runtime.set_service(DevService(dev_sender=_no_handoff))
    dev_chat.set_llm(llm)

    row: dict[str, Any] = {"id": case["id"], "kind": case["kind"]}
    try:
        if case["kind"] == "files_done":
            adapter.seed_files(case["repo_files"])
            th = await dev_chat.turn(dev, None, case["message"])
            plan = next(
                (m.plan for m in reversed(th.messages) if m.role == "assistant" and m.plan),
                None,
            )
            row["reply"] = next(
                (m.text for m in reversed(th.messages) if m.role == "assistant"), ""
            )[:300]
            row.update(_grade_files_done(plan, case))
        else:
            number = adapter.seed(
                title=case["id"], checks=[CheckRun(case["check_name"], "failed")]
            )
            adapter.set_logs(number, {case["check_name"]: case["ci_log"]})
            th = await dev_chat.turn(dev, None, case["message"], pr={"number": number})
            reply = next(
                (m.text for m in reversed(th.messages) if m.role == "assistant"), ""
            )
            row["reply"] = reply[:300]
            row.update(_grade_ci_cause(reply, case))
    except Exception as exc:  # noqa: BLE001 - 평가는 멈추지 않고 실패로 기록
        row.update(both_correct=False)
        row["error"] = f"{type(exc).__name__}: {exc}"[:200]
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
    def _rate(kind: str, key: str) -> float | None:
        subset = [r for r in rows if r["kind"] == kind]
        return (sum(1 for r in subset if r.get(key)) / len(subset)) if subset else None

    files_done = [r for r in rows if r["kind"] == "files_done"]
    ci_cause = [r for r in rows if r["kind"] == "ci_cause"]
    return {
        "n": len(rows),
        "files_done_accuracy": _rate("files_done", "both_correct"),  # 바꿀 파일·완료 기준 다 맞힘
        "paths_accuracy": _rate("files_done", "paths_correct"),
        "done_accuracy": _rate("files_done", "done_correct"),
        "ci_cause_accuracy": _rate("ci_cause", "both_correct"),  # CI 원인 파일·줄 다 맞힘
        "ci_file_accuracy": _rate("ci_cause", "file_correct"),
        "ci_line_accuracy": _rate("ci_cause", "line_correct"),
        "n_files_done": len(files_done),
        "n_ci_cause": len(ci_cause),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "results" / "reply_accuracy_eval.json"))
    args = parser.parse_args()
    cases = json.loads((HERE / "reply_accuracy_cases.json").read_text(encoding="utf-8"))["cases"]
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
