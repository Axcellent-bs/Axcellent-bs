"""작업 성공률(dev_agent.coder) LLM 모델 비교 평가 (예진 제안, #소통 2026-11-27 15:46, 개발 1순위).

무엇을 재나
- "완료 기준을 모두 채운 PR/작업 수" · "정답 diff 있는 작업만". 평가 데이터가 아예 없어서
  새로 만들었다(합성 코딩 작업 8건, `task_success_cases.json`). 실제로는 11/24~26 운영 시험
  요청이 후보지만, 이 샌드박스에는 그 기록이 없어 합성 데이터로 하네스만 먼저 둔다.
- holdout.py(규칙 skill 6개, A2A 질의·응답)와는 다른 평가다: 여기는 LLM이 실제로 코드를
  쓰는 `dev_agent.coder.run()`(파일 고르기 → 쓰기 → 리뷰 → PR → 자동 머지)을 그대로 돌려,
  병합된 최종 코드가 완료 기준을 채우는지 잰다.

어떻게 재나
- 작업마다 작은 목 저장소(files)를 `MockVcsAdapter`에 심고, 실제 `DevService.create_dev_task`
  → `coder.run()`(production 그대로, CI는 목 모드라 검사 없는 레포로 바로 통과) → 자동 머지까지
  돌린다. 사람이 미리 써 둔 정답 최종 글(golden)과 원래 글을 difflib로 비교해 "더해진 줄"을
  뽑고, 병합된 최종 글에 그 줄들이 다 들어 있으면 완료 기준을 채운 것으로 본다(평가 전용 근사치,
  production 판정 아님 — done 문장을 사람이 보고 판정하는 게 아니라 정답 diff의 핵심 줄을
  코드로 맞춰 보는 것이다).
- 작업당 서비스를 새로 띄운다(예산·ID 번호가 섞이지 않게).
"""

from __future__ import annotations

import asyncio
import difflib
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

coder.CI_POLL_SECONDS = 0.0  # 시험과 같다: CI 기다리지 않는다
coder.NO_CI_GRACE_SECONDS = 0.0


async def _no_handoff(*_: Any, **__: Any) -> str:
    return "보냄 (평가: 버림)"


def added_lines(original: str, golden: str) -> list[str]:
    """정답 diff에서 더해진 줄만 (앞뒤 공백만 다른 줄은 기존 줄로 본다)."""
    a = [x.strip() for x in original.splitlines()]
    b = golden.splitlines()
    b_stripped = [x.strip() for x in b]
    matcher = difflib.SequenceMatcher(None, a, b_stripped)
    out: list[str] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "insert"):
            out += [b[j].strip() for j in range(j1, j2) if b[j].strip()]
    return out


def criteria_met(original: str, golden: str, final: str) -> bool:
    """정답의 더해진 줄이 모두 최종 글에 들어 있나 (줄 단위 부분 일치, 평가 전용)."""
    need = added_lines(original, golden)
    if not need:
        return final == golden
    have = {line.strip() for line in final.splitlines()}
    return all(line in have for line in need)


async def run_case(llm: Any, case: dict) -> dict:
    clear_state()
    config.set_agent_name("planning")
    workflow_base.send_to_service = _no_handoff  # type: ignore[assignment]
    vcs._screen = None
    adapter = MockVcsAdapter()
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
        row.update(status="exception", merged=False, criteria_met=False, attempts=None)
        row["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return row

    task = next((t for t in dev.state.tasks if t.id == "T-D1"), None)
    cr = task.coding if task else None
    status = cr.status if cr else "no_task"
    row["status"] = status
    row["attempts"] = cr.attempts if cr else None
    row["merged"] = status == "merged"
    if not row["merged"]:
        row["criteria_met"] = False
        return row
    ok = True
    for path, golden in case["golden"].items():
        original = case["files"].get(path, "")
        final = await adapter.read_file(path)
        if not criteria_met(original, golden, final):
            ok = False
    row["criteria_met"] = ok
    row["review_verdict"] = cr.review.verdict if cr.review else None
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
    merged = [r for r in rows if r["merged"]]
    success = [r for r in rows if r["merged"] and r["criteria_met"]]
    attempts = [r["attempts"] for r in merged if r["attempts"] is not None]
    statuses = sorted({r["status"] for r in rows})
    by_status = {s: sum(1 for r in rows if r["status"] == s) for s in statuses}
    return {
        "n": n,
        "merge_rate": len(merged) / n if n else None,
        "task_success_rate": len(success) / n if n else None,  # 완료 기준을 모두 채운 PR/작업 수
        "criteria_met_given_merged": (len(success) / len(merged)) if merged else None,
        "avg_attempts": sum(attempts) / len(attempts) if attempts else None,
        "by_status": by_status,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "results" / "task_success_eval.json"))
    args = parser.parse_args()
    cases = json.loads((HERE / "task_success_cases.json").read_text(encoding="utf-8"))["cases"]
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
