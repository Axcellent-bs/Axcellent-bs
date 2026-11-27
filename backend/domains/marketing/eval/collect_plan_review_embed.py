"""마케팅 제작안 확인(다른 인물 가까이 나옴) 낱말 규칙 대신 임베딩 유사도로 판정했을 때를 재는
1단계.

- `plan_review_cases.json`의 장면 글(`body`)과 `plan_review_anchors.json`의 "다른 인물이 가까이
  나오는" 대표 문장을 모두 임베딩하고, 장면마다 대표 문장들과의 코사인 유사도 중 가장 큰 값을
  남긴다. `camera`(와이드 샷 여부)는 이 1단계에서는 보지 않는다 — 2단계
  `score_plan_review_embed.py`가 유사도와 와이드 샷 낱말을 함께 본다(한계 참고).

사용법 (.env에 실제 EMBED_* 키가 있는 로컬에서):
    cd backend
    EMBED_MODEL=gemini-embedding-001 \
      uv run python domains/marketing/eval/collect_plan_review_embed.py gemini-embedding-001

라벨(인자)은 출력 파일 이름에만 쓴다: results/plan_review_embed_sims/sims_<라벨>.json
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[2]
sys.path.insert(0, str(BACKEND / "shared" / "agent-template" / "src"))

from ax_agent_template.llm import LLMClient  # noqa: E402


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        print("사용법: collect_plan_review_embed.py <결과 파일에 쓸 라벨>")
        return 2
    label = sys.argv[1]

    cases = json.loads((HERE / "plan_review_cases.json").read_text(encoding="utf-8"))["cases"]
    anchors = json.loads((HERE / "plan_review_anchors.json").read_text(encoding="utf-8"))["anchors"]

    case_texts = [c["body"] for c in cases]
    unique = sorted({*case_texts, *anchors})

    llm = LLMClient("marketing")
    vectors: dict[str, list[float]] = {}
    for start in range(0, len(unique), 50):
        chunk = unique[start : start + 50]
        for text, vec in zip(chunk, await llm.embed(chunk), strict=True):
            vectors[text] = vec

    model = llm.settings.embed_model
    out = {
        "model": model,
        "label": label,
        "collected_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "max_sim": {
            c["id"]: round(max(cosine(vectors[c["body"]], vectors[a]) for a in anchors), 4)
            for c in cases
        },
    }
    out_dir = HERE / "results" / "plan_review_embed_sims"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"sims_{label}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out_path} 에 저장 (model={model})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
