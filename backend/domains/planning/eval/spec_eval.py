"""기획서 초안(spec_writer) LLM 모델 비교 평가 (예진 제안, #소통 2026-11-27 15:46, 3순위).

변경 요청 한 건(제목·근거·보낸 부서)마다 실제 planning_agent.spec_writer.SpecWriter.draft()를
그대로 부른다. 채점 로직은 두 갈래다.

1. spec_writer가 이미 하는 일을 그대로 잰다 (새로 만들지 않음):
   - 성공/실패(규칙 초안으로 떨어짐) 여부, 걸린 시간, 30초 타임아웃으로 떨어졌는지
   - "코드가 뺀 문장 수": LLM이 낸 원래 문장 수와 spec_writer._clean()이 거른 뒤 문장 수의 차이.
     _clean()은 가져와서 그대로 쓰고(새 로직 아님), 원본 문장 수를 보려고 LLM 클라이언트를
     얇게 감싸 draft() 안에서 실제로 받은 answer를 옆에서 기록만 한다(동작은 바꾸지 않음).

2. spec_writer에는 아직 없는 검사라서 이 평가용으로 새로 만든 규칙(투명하게 적는다. production
   코드가 아니라 근사치다):
   - invented_number: 초안 문장 숫자가 근거 문장 어디에도 없으면 "근거에 없는 관찰 값"으로 센다
   - scope_widening: 결제·개인정보 범위를 넓히는 낱말(카드번호 저장·주민등록번호 등)이 나오면 센다
   - axcellent_term: 이 레포 실제 화면 용어(AXcellent·btn-primary 등)가 나오면 센다(합성 근거에는
     없는 말이라 나오면 지어낸 것)
   제안이 말한 "요구사항이 개발 작업 하나로 바로 되는 비율"의 정확한 판정(실제로 작업 하나로
   끝나는지)은 개발 쪽에서 실행해봐야 알 수 있어 여기서는 재지 않는다. 이 세 검사가 모두 0인
   비율을 근사치로 쓴다(clean_requirement_rate).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
sys.path.insert(0, str(HERE))


def load_env() -> dict[str, str]:
    """backend/.env를 읽어 os.environ에 없는 값만 보탠다 (domains끼리 import하지 않는다 규칙이라
    domains/archive/eval/run_eval.py의 load_env와 같은 내용을 여기도 둔다)."""
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

from ax_agent_template.llm import LLMClient, LLMSettings  # noqa: E402
from planning_agent.spec_writer import SPEC_TIMEOUT_SECONDS, SpecWriter, _clean  # noqa: E402

TIMEOUT_MARGIN = 0.95  # 타임아웃 근처(>=95%)에서 실패하면 시간 초과로 본다
NUMBER = re.compile(r"\d{2,}")

SCOPE_WORDS = (
    "카드번호 저장", "카드 번호 저장", "카드 정보 저장", "주민등록번호",
    "계좌번호 저장", "비밀번호 저장", "비밀번호 평문", "개인정보 저장", "개인정보를 저장",
)  # fmt: skip
AXCELLENT_TERMS = (
    "AXcellent", "axcellent", "btn-primary", "btn-ghost", "dashboard-api", "frontend/src",
)  # fmt: skip


class _RecordingLLM:
    """실제 LLM 클라이언트를 그대로 부르고, draft()가 받는 원본 답만 옆에서 기록한다
    (동작은 바꾸지 않는다 - 순수 통과 래퍼)."""

    def __init__(self, inner: LLMClient):
        self._inner = inner
        self.last_raw = None

    async def structured(self, *args, **kwargs):
        got = await self._inner.structured(*args, **kwargs)
        self.last_raw = got
        return got

    @property
    def settings(self):
        return getattr(self._inner, "settings", None)


def _numbers(text: str) -> set[str]:
    return set(NUMBER.findall(text or ""))


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in (text or "") for w in words)


def score_draft(draft_texts: list[str], evidence: str) -> dict:
    """invented_number·scope_widening·axcellent_term 세 검사 (production 코드 아님, 평가 전용)."""
    joined = " ".join(draft_texts)
    evidence_numbers = _numbers(evidence)
    draft_numbers = _numbers(joined)
    invented = sorted(draft_numbers - evidence_numbers)
    return {
        "invented_number": len(invented) > 0,
        "invented_number_values": invented,
        "scope_widening": _has(joined, SCOPE_WORDS),
        "axcellent_term": _has(joined, AXCELLENT_TERMS),
    }


async def run_case(llm: LLMClient, case: dict) -> dict:
    recorder = _RecordingLLM(llm)
    writer = SpecWriter(recorder, timeout=SPEC_TIMEOUT_SECONDS)
    context_id = f"ctx_req_{case['id']}"
    started = time.monotonic()
    draft = await writer.draft(
        title=case["title"], evidence=case["evidence"], requester=case["requester"],
        context_id=context_id,
    )
    elapsed = time.monotonic() - started

    row: dict = {"id": case["id"], "elapsed": round(elapsed, 2), "ok": draft is not None}

    if draft is None:
        row["timed_out"] = elapsed >= SPEC_TIMEOUT_SECONDS * TIMEOUT_MARGIN
        row["dropped_sentences"] = None
        row["clean"] = None
        row.update(
            {"invented_number": None, "scope_widening": None, "axcellent_term": None}
        )
        return row

    row["timed_out"] = False
    texts = [draft.problem, draft.goal, *draft.requirements, *draft.done_criteria]
    checks = score_draft(texts, case["evidence"])
    row.update(checks)
    violated = checks["invented_number"] or checks["scope_widening"] or checks["axcellent_term"]
    row["clean"] = not violated

    raw = recorder.last_raw
    if raw is not None:
        raw_n = len(raw.requirements) + len(raw.done_criteria)
        clean_n = len(_clean(raw.requirements)) + len(_clean(raw.done_criteria))
        row["dropped_sentences"] = max(0, raw_n - clean_n)
    else:
        row["dropped_sentences"] = None
    return row


async def run_all(cases: list[dict]) -> list[dict]:
    settings = LLMSettings.from_env("planning")
    if not settings.model:
        raise SystemExit("LLM 설정이 없다(backend/.env, PLANNING_LLM_MODEL 또는 LLM_MODEL)")
    llm = LLMClient("planning", settings)
    return [await run_case(llm, case) for case in cases]


def score(rows: list[dict]) -> dict:
    n = len(rows)
    ok_rows = [r for r in rows if r["ok"]]
    timeouts = [r for r in rows if not r["ok"] and r["timed_out"]]
    dropped = [r["dropped_sentences"] for r in ok_rows if r["dropped_sentences"] is not None]
    return {
        "n": n,
        "draft_rate": len(ok_rows) / n if n else None,
        "fallback_rate": (n - len(ok_rows)) / n if n else None,
        "timeout_rate": len(timeouts) / n if n else None,
        "clean_requirement_rate": (
            sum(1 for r in ok_rows if r["clean"]) / len(ok_rows) if ok_rows else None
        ),
        "invented_number_hits": sum(1 for r in ok_rows if r["invented_number"]),
        "scope_widening_hits": sum(1 for r in ok_rows if r["scope_widening"]),
        "axcellent_term_hits": sum(1 for r in ok_rows if r["axcellent_term"]),
        "avg_dropped_sentences": sum(dropped) / len(dropped) if dropped else None,
        "avg_elapsed": sum(r["elapsed"] for r in rows) / n if n else None,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "results" / "spec_eval.json"))
    args = parser.parse_args()
    cases = json.loads((HERE / "spec_cases.json").read_text(encoding="utf-8"))["cases"]
    rows = asyncio.run(run_all(cases))
    result = {
        "model": os.environ.get("PLANNING_LLM_MODEL") or os.environ.get("LLM_MODEL"),
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
