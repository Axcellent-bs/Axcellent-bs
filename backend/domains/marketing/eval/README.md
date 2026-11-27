# 마케팅 AI 평가

마케팅 AI(`domains/marketing`)의 코드 규칙을 골든셋으로 재고, 같은 골든셋으로 다른 판정
방식(임베딩 유사도)과 비교한다.

## 금지 표현·참여 유도 판정 낱말 규칙 vs 임베딩 비교 (태윤 제안, #소통 2026-11-09 15:56, 1순위)

"막아야 할 글을 놓치지 않으면서(재현율), 멀쩡한 글을 막지 않는지(정밀도, **먼저** 본다)"
평가. 지금은 `sns.py`의 `FORBIDDEN_PHRASES`·`ENGAGEMENT_WORDS`(참여 조건부 게시 판정)와
`notice_text.py`의 공지 금지 패턴(과장·확정 보장 표현)이 낱말 포함(또는 낱말+요구 표현 근접)
규칙으로 막는다. 문제: 설명 문장에 참여 낱말만 있어도 막혀서(#1667) 규칙을 좁혔지만, 오탐·
놓침이 둘 다 남는다. 막아야 할 글/통과해야 할 글로 골든셋을 만들어 지금 낱말 규칙과 임베딩
유사도(대표 금지 문장과 비교)를 같은 기준으로 비교한다.

- `forbidden_cases.json`: 합성 골든셋 23건(dev 15 · holdout 8), `sns`·`notice` 두 채널.
  - sns 채널: 참여 조건부 게시(좋아요·댓글·팔로우·공유 + 요구 표현)와 성과 과장 표현
  - notice 채널: 공지 금지 표현(보장·반드시·확정·완벽·100%·1위·최저가)
  - 지금 규칙이 실제로 틀리는 사례 3건을 넣었다(`m21`·`m22`·`m23`): 감사 인사인데 "공유"+
    "해주"가 근접해 **오탐**(`m21`), "구독하기"·"알림 설정"처럼 `ENGAGEMENT_WORDS`에 없는
    낱말로 참여를 유도해 **놓침**(`m22`·`m23`). 이 3건 덕에 지금 규칙의 dev 재현율은 0.89,
    holdout은 정밀도·재현율 모두 0.75로 — 비교에 실제 여지가 있다.
- `forbidden_rule_score.py`: 지금 production 함수(`sns.has_engagement_bait`·
  `has_forbidden_claim`, `notice_text.NOTICE_FORBIDDEN_PATTERNS`)를 그대로 불러와 합치기만
  한다. 새 규칙은 만들지 않는다.
  - 실행: `cd backend && uv run python domains/marketing/eval/forbidden_rule_score.py`
- `forbidden_anchors.json`: "대표 금지 문장" 11개(참여 조건부 4 · 과장 표현 3 · 공지 금지 4).
  사람이 미리 적었다(정답을 보고 거꾸로 고르지 않았다).
- `collect_embed_variant.py`: 골든셋 글과 대표 금지 문장을 모두 임베딩하고, 글마다 대표
  문장들과의 코사인 유사도 중 최댓값을 남긴다(`docs/notes/support/embed-compare`와 같은
  방식: 수집과 채점을 나눈다).
  - 실행: `EMBED_MODEL=<모델> uv run python domains/marketing/eval/collect_embed_variant.py <라벨>`
  - 결과: `results/embed_sims/sims_<라벨>.json`
- `score_embed_variant.py`: 유사도 임계값을 dev 집합에서만 그리드 탐색으로 고른다 —
  오탐률 0을 만족하는 임계값 중 재현율이 가장 높은 것(정밀도 우선, 태윤님 기준), 그런
  임계값이 없으면 정밀도가 가장 높은 것. holdout은 그 임계값으로 한 번만 잰다. 지금 규칙
  행(`forbidden_rule_score.py`와 같은 계산)을 항상 같이 찍어 한 표로 비교한다.
  - 실행: `uv run python domains/marketing/eval/score_embed_variant.py <라벨1> <라벨2> ...`

```bash
cd backend
uv run python domains/marketing/eval/forbidden_rule_score.py   # 지금 규칙만 (키 필요 없음)

EMBED_MODEL=gemini-embedding-001 \
  uv run python domains/marketing/eval/collect_embed_variant.py gemini-embedding-001
EMBED_MODEL=text-embedding-3-large EMBED_BASE_URL=https://api.openai.com/v1 EMBED_API_KEY=... \
  uv run python domains/marketing/eval/collect_embed_variant.py text-embedding-3-large
uv run python domains/marketing/eval/score_embed_variant.py gemini-embedding-001 text-embedding-3-large
```

**한계**
- 골든셋이 합성·소규모다(23건). 실제로 막힌·통과된 캡션·공지 기록으로 바꾸면 더 믿을 만하다.
- 정답·대표 금지 문장은 이 평가를 만들며 사람이 미리 적어 둔 것이다.
- 임베딩 쪽 "참여 조건부"와 "과장 표현"을 하나의 유사도 점수(대표 문장 전체 중 최댓값)로
  합쳐서 본다 — 어느 종류의 금지인지는 구분하지 못한다(지금 규칙은 이유 코드를 남긴다).
- 비용(토큰 수·호출 비용)은 이 표에 아직 없다. 모델별 비교 때 `LLMClient`의 사용량 기록을
  같이 보고 채워야 한다.
- 이 세션(클라우드 샌드박스)에는 임베딩 키가 없어, 손으로 만든 가짜 유사도 값으로 채점
  함수(`score_embed_variant.select`·`evaluate`)만 검증했다(정답을 완벽히 가르는 값 →
  dev·holdout 모두 정밀도·재현율 1.00으로 지금 규칙을 앞서는 것, 변별력 없는 값 → 임계값이
  의미 없이 뽑히는 것). 실제 비교 숫자는 키가 있는 로컬 PC에서 위 명령으로 내야 한다.
