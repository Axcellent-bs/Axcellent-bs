"""마케팅 금지 표현·참여 유도 판정: 낱말 규칙 대신 임베딩 유사도로 판정했을 때를 재는 1단계.

- `forbidden_cases.json`의 글과 `forbidden_anchors.json`의 "대표 금지 문장"을 모두 임베딩하고,
  글마다 대표 문장들과의 코사인 유사도 중 가장 큰 값을 남긴다(`support`의 `embed-compare` 방식과
  같다: 판정은 2단계 `score_embed_variant.py`가 한다).
- 정답(`label`)은 이 평가를 만들며 사람이 미리 적었다. 글을 보고 임계값을 거꾸로 맞추지 않았다.

사용법 (.env에 실제 EMBED_* 키가 있는 로컬에서):
    cd backend
    EMBED_MODEL=gemini-embedding-001 \
      uv run python domains/marketing/eval/collect_embed_variant.py gemini-embedding-001

라벨(인자)은 출력 파일 이름에만 쓴다: results/embed_sims/sims_<라벨>.json
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
        print("사용법: collect_embed_variant.py <결과 파일에 쓸 라벨, 예: gemini-embedding-001>")
        return 2
    label = sys.argv[1]

    cases = json.loads((HERE / "forbidden_cases.json").read_text(encoding="utf-8"))["cases"]
    anchors = json.loads((HERE / "forbidden_anchors.json").read_text(encoding="utf-8"))["anchors"]

    case_texts = [c["text"] for c in cases]
    unique = sorted({*case_texts, *anchors})

    llm = LLMClient("marketing")  # MARKETING_EMBED_MODEL 우선, 없으면 EMBED_MODEL
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
            c["id"]: round(max(cosine(vectors[c["text"]], vectors[a]) for a in anchors), 4)
            for c in cases
        },
    }
    out_dir = HERE / "results" / "embed_sims"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"sims_{label}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out_path} 에 저장 (model={model})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
