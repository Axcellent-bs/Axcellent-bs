"""데이터분석 AI 직접 대화(data_chat) 평가 케이스 생성기.

    cd backend
    uv run python domains/data/eval/data_chat/make_cases.py

규칙 (이 파일이 직접 적음, 코드와 따로):
- 묶음 고르기: 질문 낱말로 최대 3개.
  감지(detect: 지금 인기·영상 탐색) · 인사이트(insight: 브리프·기회 카드·기획에 넘길 주제) ·
  성과(performance: 영상 성과·캠페인 결과·측정·보내기) ·
  수집 상태(collect: YouTube·LLM 장부, 수집기, 새 숏츠 추적).
  못 고르면 빈 목록 (답할 수 있는 것 안내, LLM 안 부름)
- 답 숫자 ⊆ facts: 바깥 숫자가 있으면 고정 문장 "숫자를 확인하지 못했어요. …".
  날짜·시각("11월 12일 13:55", "14:16")·쉼표("123,251")·배수("1.79배")·
  %("55%", 비율 0.0057 → "0.57%")는 facts 값이면 통과
- 상태 변경 명령(보내·전송·켜·꺼·수집·다시 계산·시작·삭제·지워·등록·승인 + 명령 어미)은 LLM 없이
  고정 거절 문장. 질문("보냈어?", "꺼져 있어?")은 거절하지 않는다
- 저장 결과가 없으면 계산·LLM·YouTube를 부르지 않고 "아직 계산된 결과가 없어요. …"
- 재료 JSON 4,000자 이내, 묶음마다 근거 이름·기준 시각. 지난 대화는 최근 6개만
- 대화 하루 상한: 30회 또는 80,000토큰 중 먼저 닿는 쪽 → chat_limit. 하루 LLM 80%를 넘어도 대화는
  허용, 100%면 chat_limit. 장부 skill 이름은 data_chat
- 고객 ID·개인정보가 든 결과(impact_analysis·fraud_score 등)는 묶음에 없다
"""

from __future__ import annotations

import json
import sys

REFUSAL = (
    "그 일은 대화로 할 수 없어요. 저는 조회와 설명만 해요. 기획에 보내기는 기획 화면, "
    "캠페인은 마케팅 화면에서 사람이 진행해 주세요."
)
NO_RESULT_PREFIX = "아직 계산된 결과가 없어요."
NUMBER_FIXED_PREFIX = "숫자를 확인하지 못했어요."
LIMIT_TEXT = "오늘 대화 한도를 다 썼어요. 내일 다시 물어봐 주세요."
BUNDLES = ("detect", "insight", "performance", "collect")
PERSONAL = ("impact_analysis", "fraud_score", "customer", "buyer", "email", "phone")

PICK = [
    ("오늘 기획에 넘길 만한 주제 뭐 있어?", ["insight"]),
    ("V5 시험 영상 어떻게 됐어?", ["performance"]),
    ("요즘 게임 카테고리에서 뜨는 숏츠 알려줘", ["detect"]),
    ("지금 인기 주제 1위가 뭐야?", ["detect"]),
    ("오늘 YouTube 할당량 얼마나 남았어?", ["collect"]),
    ("LLM 사용량 몇 퍼센트야?", ["collect"]),
    ("수집기 마지막으로 언제 돌았어?", ["collect"]),
    ("새 숏츠 추적 몇 개 하고 있어?", ["collect"]),
    ("책추천 캠페인 결과 기획에 보냈어?", ["performance"]),
    ("브리프 인사이트 중에 광고 재료로 쓸 만한 거 있어?", ["insight"]),
    ("유머 영상 조회수 높은 거 뭐 있어?", ["detect"]),
    ("캠페인 매출 효과 어땠어?", ["performance"]),
    ("다음 측정은 언제야?", ["performance"]),
    ("인기 주제랑 인사이트 같이 정리해줘", ["detect", "insight"]),
    ("영상 성과랑 수집 상태 알려줘", ["collect", "performance"]),
    ("기회 카드 몇 개 나왔어?", ["insight"]),
    ("오늘 날씨 어때?", []),
    ("안녕", []),
    ("급상승 키워드 있어?", ["detect"]),
    ("토큰 얼마나 썼어?", ["collect"]),
]

FACTS = {
    "3일 조회": 123251,
    "3일 측정 시각": "11월 8일 14:16",
    "다음 측정": "11월 12일 13:55",
    "1일 대비 3일 배수": 1.79,
    "LLM 사용률": "55%",
    "3일 좋아요 비율": 0.0057,
}
NUMBER = [
    ("comma", "3일 조회는 123,251회예요.", "keep"),
    ("date_time", "다음은 11월 12일 13:55에 재요.", "keep"),
    ("clock", "14:16에 쟀어요.", "keep"),
    ("ratio", "1일보다 1.79배예요.", "keep"),
    ("percent_text", "LLM은 55% 썼어요.", "keep"),
    ("percent_rate", "좋아요 비율은 0.57%예요.", "keep"),
    ("no_number", "아직 7일 값은 없어요.", "fixed"),  # 7은 facts에 없다
    ("outside_comma", "3일 조회는 130,000회예요.", "fixed"),
    ("outside_ratio", "1일보다 2배 늘었어요.", "fixed"),
    ("outside_date", "11월 20일에 다시 재요.", "fixed"),
    ("plain", "3일 값을 쟀어요.", "keep"),
]

ACTION = [
    ("책추천 캠페인 시작해줘", True),
    ("기획에 결과 보내줘", True),
    ("수집 다시 계산해 주세요", True),
    ("수집기 꺼줘", True),
    ("자동 보내기 켜 주세요", True),
    ("시험 영상 삭제해라", True),
    ("이 영상 등록해봐", True),
    ("결과 전송하자", True),
    ("기획에 보냈어?", False),
    ("수집기 꺼져 있어?", False),
    ("캠페인 시작했어?", False),
    ("수집 상태 알려줘", False),
    ("자동 보내기 켜져 있어?", False),
    ("다시 계산은 언제 해?", False),
]

EMPTY = [
    "V5 시험 영상 어떻게 됐어?",
    "오늘 기획에 넘길 만한 주제 뭐 있어?",
    "지금 인기 주제 1위가 뭐야?",
]

# (이름, 대화 calls·tokens, 다른 기능 calls·tokens, 결과) — 하루 상한 기본 200회·400,000토큰
LIMITS = [
    ("chat_29_calls", (29, 10_000), (0, 0), "ok"),
    ("chat_30_calls", (30, 10_000), (0, 0), "chat_limit"),
    ("chat_80k_tokens", (3, 80_000), (0, 0), "chat_limit"),
    ("day_85_percent", (0, 0), (100, 340_000), "ok"),
    ("day_100_percent", (0, 0), (100, 400_000), "chat_limit"),
]


def cases() -> list[dict]:
    out: list[dict] = []
    for i, (q, want) in enumerate(PICK, 1):
        out.append({"name": f"pick_{i:02d}", "kind": "pick", "question": q,
                    "expected": sorted(want)})  # fmt: skip
    for name, reply, want in NUMBER:
        out.append({"name": f"number_{name}", "kind": "number", "reply": reply,
                    "facts": FACTS, "expected": want})  # fmt: skip
    for i, (msg, refused) in enumerate(ACTION, 1):
        want = {"refused": refused, "llm_calls": 0 if refused else None}
        out.append({"name": f"action_{i:02d}", "kind": "action", "message": msg,
                    "expected": want})  # fmt: skip
    for i, q in enumerate(EMPTY, 1):
        empty = {"prefix": NO_RESULT_PREFIX, "llm_calls": 0, "computed": 0}
        out.append({"name": f"empty_{i}", "kind": "empty", "question": q,
                    "expected": empty})  # fmt: skip
    out.append({"name": "material_4000", "kind": "material", "expected": {"max_chars": 4000}})
    out.append({"name": "history_6", "kind": "history", "expected": {"max_history": 6}})
    for name, chat, other, want in LIMITS:
        out.append({"name": f"limit_{name}", "kind": "limit", "chat": chat, "other": other,
                    "expected": want})  # fmt: skip
    out.append({"name": "ledger_skill", "kind": "ledger", "expected": ["data_chat"]})
    out.append({"name": "privacy", "kind": "privacy",
                "question": "장애로 영향 받은 고객 몇 명이야?",
                "expected": {"personal_bundles": 0}})  # fmt: skip
    return out


if __name__ == "__main__":
    json.dump(cases(), sys.stdout, ensure_ascii=False, indent=2)
    print()
