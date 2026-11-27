"""범위 정확도(dev_agent.coder) LLM 모델 비교 평가 (예진 제안, #소통 2026-11-27 15:46, 개발 2순위).

무엇을 재나
- "기대 파일·기대 줄만 바꾼 비율". 평가 데이터가 없어 새로 만들었다. task_success_eval.py와
  같은 production 경로(`dev_agent.coder.run`)를 돌리되, 여기서는 완료 기준 충족이 아니라
  **얼마나 좁게 고쳤는지**(file precision·line precision)를 잰다.
- 저장소마다 이름이 비슷한 미끼 파일(decoy, 예: `BannerOld.tsx`·`QtyButtonsLegacy.tsx`)을 더
  심어, 모델이 작업과 무관한 파일을 집지 않는지도 같이 본다.

어떻게 재나
- 파일 범위: 실제로 고친 파일 집합이 정답(golden) 파일 집합과 정확히 같은가(추가로 집은 미끼
  파일이 있으면 틀림).
- 줄 범위: 병합된 최종 글이 정답(golden) 글과 줄 단위로 정확히 같은가. task_success_eval.py의
  "완료 기준 충족"(정답의 더해진 줄이 최종 글에 들어 있나, 더 있어도 괜찮음 — 재현율 쪽)과
  다르게, 여기는 더 얹은 줄이 하나라도 있으면 틀린 것으로 본다(정밀도 쪽) — "기대 줄만"의
  "만"을 재는 것이라 자기 할 일만 하고 다른 줄은 안 건드렸는지를 본다. 둘 다 production
  판정이 아니라 이 평가 전용 근사치다(정답과 글자 그대로 같은지만 본다, 뜻이 같은 다른
  표현은 틀린 것으로 셀 수 있다 — 아래 한계 참고).
- 나머지 흐름(목 저장소·`DevService`·자동 머지)은 task_success_eval.py와 같다.
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


def extra_line_count(golden: str, final: str) -> int:
    """정답에 없는데 최종 글에 더 들어간 줄 수 (정답 쪽에서 봤을 때 없는 줄만 센다).

    난이도를 낮춰 잡기 쉽게: 두 글을 줄 단위로 맞춰 보고 `replace`·`insert`로 새로 생긴
    줄 수만 더한다(`delete`는 정답에서 뺀 줄이라 범위 밖 "수정"이 아니다)."""
    matcher = difflib.SequenceMatcher(None, golden.splitlines(), final.splitlines())
    return sum(
        j2 - j1 for tag, _i1, _i2, j1, j2 in matcher.get_opcodes() if tag in ("replace", "insert")
    )


def line_scope(golden: str, final: str) -> tuple[bool, int]:
    """(기대 줄만 바꿨나 — 최종 글이 정답과 줄 단위로 정확히 같은가, 범위 밖에 더 들어간 줄 수)."""
    extra = extra_line_count(golden, final)
    return final == golden, extra


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
        row.update(status="exception", merged=False, file_scope_ok=None, line_scope_ok=None)
        row["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return row

    task = next((t for t in dev.state.tasks if t.id == "T-D1"), None)
    cr = task.coding if task else None
    status = cr.status if cr else "no_task"
    row["status"] = status
    row["merged"] = status == "merged"
    if not row["merged"]:
        row["file_scope_ok"] = None
        row["line_scope_ok"] = None
        return row

    changed_paths = {f.path for f in cr.files} if cr else set()
    expected_paths = set(case["golden"])
    row["file_scope_ok"] = changed_paths == expected_paths
    row["extra_files"] = sorted(changed_paths - expected_paths)
    row["missing_files"] = sorted(expected_paths - changed_paths)

    line_ok = True
    extra_total = 0
    for path, golden in case["golden"].items():
        if path not in changed_paths:
            line_ok = False
            continue
        final = await adapter.read_file(path)
        ok, extra = line_scope(golden, final)
        line_ok = line_ok and ok
        extra_total += extra
    row["line_scope_ok"] = line_ok
    row["extra_lines"] = extra_total
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
    scoped = [r for r in merged if r["file_scope_ok"] and r["line_scope_ok"]]
    file_ok = [r for r in merged if r["file_scope_ok"]]
    line_ok = [r for r in merged if r["line_scope_ok"]]
    extra_files = sum(len(r.get("extra_files") or []) for r in merged)
    extra_lines = sum(r.get("extra_lines") or 0 for r in merged)
    statuses = sorted({r["status"] for r in rows})
    return {
        "n": n,
        "merge_rate": len(merged) / n if n else None,
        "scope_accuracy": (len(scoped) / len(merged)) if merged else None,  # 기대 파일·줄만
        "file_scope_rate": (len(file_ok) / len(merged)) if merged else None,
        "line_scope_rate": (len(line_ok) / len(merged)) if merged else None,
        "extra_files_total": extra_files,
        "extra_lines_total": extra_lines,
        "by_status": {s: sum(1 for r in rows if r["status"] == s) for s in statuses},
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "results" / "scope_accuracy_eval.json"))
    args = parser.parse_args()
    cases = json.loads((HERE / "scope_accuracy_cases.json").read_text(encoding="utf-8"))["cases"]
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
