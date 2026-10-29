"""평가용 LLM 클라이언트. 네트워크 없이 늘 같은 답을 내서 CI에서도 결과가 같다.

시나리오의 `llm` 값으로 행동을 고른다.
- grounded: 받은 사실·숫자만 쓴다.
  - 설명 문장(`_Summary`): facts의 값을 그대로 나열한다.
  - 원인 후보(`_Candidates`): 받은 사실 순서대로 사실마다 후보 하나(확신도 mid)를 낸다.
    시간 순서를 따져 다시 줄 세우지 않는다(LLM이 순서를 그대로 믿는 경우).
- hallucinate: grounded 답에 지어낸 것을 섞는다. 설명에는 사실에 없는 숫자를 붙이고, 원인 후보에는
  없는 사실 id를 쓴 후보와 지어낸 숫자를 쓴 후보를 앞에 넣는다(`planted_ungrounded`로 센다).
- fail: 늘 실패한다(LLM 미설정·시간 초과와 같다). 코드가 템플릿·규칙으로 대체해야 한다.
"""

from __future__ import annotations

import ast
from typing import Any

from ax_agent_template.llm import LLMResponseError

MADE_UP_SUMMARY = " 추정 손실 9,876,543원"
MADE_UP_CAUSE_NUMBER = "결제 실패율이 47%까지 올랐을 수 있음"


def _facts_in(prompt: str) -> dict[str, Any]:
    """`explain` 프롬프트 끝의 `facts: {...}`를 읽는다."""
    _, _, tail = prompt.rpartition("facts: ")
    try:
        value = ast.literal_eval(tail.strip())
    except (ValueError, SyntaxError):
        return {}
    return value if isinstance(value, dict) else {}


class ScriptedLLM:
    def __init__(self, mode: str = "grounded") -> None:
        if mode not in ("grounded", "hallucinate", "fail"):
            raise ValueError(f"unknown llm mode: {mode}")
        self.mode = mode
        self.calls = 0
        self.planted_ungrounded = 0  # 원인 후보에 일부러 넣은 근거 없는 후보 수

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.calls += 1
        if self.mode == "fail":
            raise LLMResponseError("평가용 LLM: 응답 없음")
        name = schema.__name__
        if name == "_Summary":
            facts = _facts_in(prompt)
            text = ", ".join(f"{key} {value}" for key, value in facts.items()) or "값 없음"
            if self.mode == "hallucinate":
                text += MADE_UP_SUMMARY
            return schema(summary=text)
        if name == "_Candidates":
            facts = (untrusted or {}).get("facts") or []
            usable = [f for f in facts if f.get("kind") != "anomaly"]
            candidates = [
                {
                    "explanation": f"{f['summary']} 이후 이상이 생겼을 수 있음",
                    "fact_ids": [f["fact_id"]],
                    "confidence": "mid",
                }
                for f in usable
            ]
            if self.mode == "hallucinate":
                planted = [
                    {
                        "explanation": "DB 과부하일 수 있음",
                        "fact_ids": ["f99"],
                        "confidence": "high",
                    }
                ]
                if usable:
                    planted.append(
                        {
                            "explanation": MADE_UP_CAUSE_NUMBER,
                            "fact_ids": [usable[0]["fact_id"]],
                            "confidence": "high",
                        }
                    )
                self.planted_ungrounded += len(planted)
                candidates = planted + candidates
            return schema(hypotheses=candidates)
        raise LLMResponseError(f"평가용 LLM이 모르는 스키마: {name}")
