"""성과 결과 → 기획 보내기 채점기: 케이스 정답(`make_cases`)과 실행 결과를 비교.

지표 (평가 정의 PR에서는 관찰만, 관문은 코드 PR에서 켠다):
- `campaign_update.send_decision_accuracy`: 보내기 판정(보냄·대기·안 보냄과 이유), 기획 응답 처리
  (호출 수·마지막 상태·실패 수·이유), 플래그 꺼짐(호출 0회·상태 off)이 정답과 같은 케이스 비율
- `campaign_update.contract_valid_rate`: 만든 기획 입력이 실제 `ReceiveCampaignUpdateInput`으로,
  재무 기록용 값에 마케팅 몫(이름·채널·집행액)을 더한 것이 실제 `CampaignResultInput`으로 검증되고,
  재무 기록용 값에 집행액 칸이 없는 비율 (실행 쪽이 검증 결과를 `valid`로 낸다)
- `campaign_update.mapping_accuracy`: direction·summary·facts(순서·값·마이너스 그대로·네 칸 따로),
  재무 기록용 값(0으로 채우지 않음·음수 보존)이 정답과 같은 케이스 비율
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

METRICS = (
    "campaign_update.send_decision_accuracy",
    "campaign_update.contract_valid_rate",
    "campaign_update.mapping_accuracy",
)


def score(cases: list[dict], answers: Mapping[str, Mapping[str, Any] | None]) -> dict[str, float]:
    hits = {m: [0, 0] for m in METRICS}

    def count(metric: str, ok: bool) -> None:
        hits[metric][0] += bool(ok)
        hits[metric][1] += 1

    for case in cases:
        want = case["expected"]
        got = answers.get(case["name"]) or {}
        kind = case["kind"]
        if kind in ("decide", "reply", "flag_off"):
            count(METRICS[0], bool(got) and all(got.get(k) == v for k, v in want.items()))
        elif kind == "payload":
            count(METRICS[1], got.get("valid") is True)
            count(METRICS[2], bool(got) and all(got.get(k) == want[k]
                                                for k in ("direction", "summary", "facts")))
        elif kind == "finance":
            count(METRICS[1], got.get("valid") is True)
            record = got.get("record") or {}
            count(METRICS[2], bool(record) and all(record.get(k) == v for k, v in want.items()))
    return {m: (round(h / n, 4) if n else 0.0) for m, (h, n) in hits.items()}
