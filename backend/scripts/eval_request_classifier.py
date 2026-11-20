"""총괄 의뢰 분류 정확도 시험 (의뢰 자동 넘김 20번). 실제 LLM을 문장 샘플 수만큼 부른다.

사용법 (backend 폴더에서):
    uv run python scripts/eval_request_classifier.py [--env-file 경로] [--out 결과.json]

- 문장 샘플과 정답은 `services/orchestrator/tests/request_samples.json`이다(고객 문장·정답).
- LLM 설정은 평소 총괄과 같다(`LLMSettings.from_env`). 키가 환경변수에 없으면 `--env-file`의
  `KEY=VALUE` 줄을 읽는다. 키 값은 출력하지 않는다.
- 결과는 정답 대비 정확도, 틀린 곳, 확신도 기준값(`CONFIDENCE_MIN`)별 영향을 표로 보여 준다.
  분류기가 돌려준 확신도로 기준값을 올리거나 내렸을 때를 다시 계산하므로 LLM은 한 번만 부른다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[1] / "services/orchestrator/tests/request_samples.json"
THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)


def load_env(path: str | None) -> None:
    if not path:
        return
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def verdict(sample: dict, outcome: str, task: str | None) -> str:  # sample["task"]가 정답 일
    """정답 대비: ok · safe(되묻거나 사람에게 감) · wrong(엉뚱한 일로 보냄·지나침)."""
    expect = sample["expect"]
    if expect == "task":
        if outcome == "task" and task == sample["task"]:
            return "ok"
        if outcome in ("clarify", "unknown", "question"):
            return "safe"  # 일을 못 찾았을 뿐 엉뚱한 부서로 보내지는 않았다
        return "wrong"  # 다른 일로 보냈거나 거절했다
    if outcome == expect:
        return "ok"
    if outcome == "task":
        return "wrong"  # 의뢰가 아닌 글을 일로 보냈다
    return "safe"


def at_threshold(row: dict, threshold: float) -> tuple[str, str | None]:
    """기준값을 바꿔 본 결과: 일이면서 확신이 모자라면 되묻기(clarify)가 된다."""
    if row["outcome"] == "task" and row["confidence"] < threshold:
        return "clarify", row["got_task"]
    return row["outcome"], row["got_task"]


async def run(env_file: str | None) -> list[dict]:
    load_env(env_file)
    from ax_agent_template.llm import LLMClient, LLMSettings
    from ax_orchestrator.request_classifier import classify

    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    llm = LLMClient("orchestrator", settings=LLMSettings.from_env("orchestrator"))
    rows: list[dict] = []
    for sample in samples:
        started = time.time()
        decision = None
        for _ in range(4):  # 호출 한도(429)면 잠깐 쉬고 다시
            decision = await classify(llm, sample["text"])
            if decision.outcome != "unavailable" or "429" not in (decision.problem or ""):
                break
            await asyncio.sleep(20)
        assert decision is not None
        # 기준값은 `decide()`가 이미 적용한 결과이므로 낮은 확신 일은 clarify로 와 있을 수 있다.
        # 일은 정해졌는데 확신이 모자란 경우(clarify + task)는 원래 일로 되돌려 기록한다
        task = decision.task.name if decision.task else None
        outcome = "task" if decision.outcome == "clarify" and task else decision.outcome
        rows.append(
            {
                **sample,
                "task_expected": sample.get("task"),
                "outcome": outcome,
                "got_task": task,
                "confidence": decision.confidence,
                "asked": decision.outcome == "clarify" and task is not None,
                "reason": decision.reason,
                "seconds": round(time.time() - started, 1),
            }
        )
        mark = "." if verdict(sample, outcome, task) == "ok" else "x"
        print(
            f"{mark} {outcome:9} {str(task):20} {decision.confidence:.2f} | {sample['text'][:44]}",
            flush=True,
        )
        await asyncio.sleep(1.0)
    return rows


def report(rows: list[dict]) -> str:
    lines = []
    total = len(rows)
    counts = {"ok": 0, "safe": 0, "wrong": 0}
    for row in rows:
        row["verdict"] = verdict(row, row["outcome"], row["got_task"])
        counts[row["verdict"]] += 1
    lines.append(
        f"문장 {total}개: 정답 {counts['ok']}, 안전하게 빗나감 {counts['safe']}, "
        f"엉뚱하게 보냄 {counts['wrong']}"
    )
    tasks = [r for r in rows if r["expect"] == "task"]
    hit = sum(1 for r in tasks if r["verdict"] == "ok")
    lines.append(f"일 의뢰 {len(tasks)}개 중 일까지 맞힘 {hit}")
    for group in ("question", "clarify", "rejected", "unknown"):
        part = [r for r in rows if r["expect"] == group]
        lines.append(f"{group}: {sum(1 for r in part if r['verdict'] == 'ok')}/{len(part)}")
    lines.append("")
    lines.append("틀린 곳:")
    for row in rows:
        if row["verdict"] != "ok":
            want = row["expect"] + (f":{row['task_expected']}" if row.get("task_expected") else "")
            lines.append(
                f"  [{row['verdict']}] 정답={want} 결과={row['outcome']}:{row['got_task']} "
                f"확신={row['confidence']:.2f} | {row['text']}"
            )
    lines.append("")
    lines.append("확신도 기준값별 (일로 정한 것 중 기준보다 낮으면 되묻기가 됨):")
    lines.append("기준  정답  안전  엉뚱  되물음")
    for threshold in THRESHOLDS:
        c = {"ok": 0, "safe": 0, "wrong": 0}
        asked = 0
        for row in rows:
            outcome, task = at_threshold(row, threshold)
            if outcome == "clarify" and row["outcome"] == "task":
                asked += 1
            c[verdict(row, outcome, task)] += 1
        lines.append(f"{threshold:.1f}   {c['ok']:>4}  {c['safe']:>4}  {c['wrong']:>4}  {asked:>5}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file")
    parser.add_argument("--out", default="request_eval_results.json")
    args = parser.parse_args()
    rows = asyncio.run(run(args.env_file))
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print(report(rows))


if __name__ == "__main__":
    main()
