# 은행 입금 적요 ↔ PG 정산금 판정: 부분 문자열 규칙 vs 임베딩 비교 (태윤 제안, #소통 2026-11-09 15:56, 3순위)

`bank.match()`는 은행 거래내역의 입금 적요(memo)에 `pg_memo` 글자가 있는 줄만 "PG 정산금"으로
센다(부분 문자열, `not memo or memo in r["memo"]`). 날짜·금액 대조(PG가 주기로 한 날 들어왔나)는
이 평가의 범위 밖이다 — 이 평가는 "이 입금 줄이 PG 정산금인가"라는 적요 판정 한 단계만 본다.

실제 `finance_policy.toml`의 `pg_memo`는 비어 있다("비우면 모든 입금" — 아직 실제 은행 적요
표본이 없다, 태윤님 본인 메모). 그래서 이 평가는 `bank_pg_memo_cases.json`의
`pg_memo_placeholder`(기존 bank 테스트 fixture와 같은 '토스')로 잰다 — 실제 적요 표본이 아니다.

태윤님 기준: **오연결률(false-link rate, PG 정산금이 아닌데 정산금으로 잘못 센 비율)은 0이어야
한다.** 취향이 아니라 꼭 지켜야 할 조건으로 받아들였다 — `score_bank_pg_memo_embed.py`는 dev
집합에서 오연결률 0을 만드는 임계값이 없으면 "없음"을 그대로 보고하지, 0이 아닌 값을 대신
고르지 않는다.

- `bank_pg_memo_cases.json`: 합성 골든셋 22건(dev 15 · holdout 7). 지금 규칙이 실제로 틀리는
  사례 4건을 넣었다:
  - **놓침** 2건(`f11`·`f12`): "(주)비바리퍼블리카"(토스페이먼츠 운영사의 법인명 — 은행
    적요에 브랜드명 대신 법인명이 찍히는 실제 사례)와 "TOSSPAYMENTS SETTLEMENT"(영문 표기)는
    '토스'와 글자가 안 겹쳐 지금 규칙이 PG 정산금을 놓친다.
  - **오연결** 2건(`f13`·`f14`): "토스뱅크 이자"·"토스증권 배당금"은 같은 '토스' 브랜드의
    다른 상품(통장 이자·증권 배당)인데 '토스' 글자가 겹쳐 지금 규칙이 PG 정산금으로 잘못
    센다 — 태윤님이 0이어야 한다고 한 바로 그 오연결이, dev 집합에서 지금 **0.25**로 나온다.
  - 다른 PG(네이버페이·카카오페이 정산)도 넣어 "아무 정산 글자나 다 섞지 않는지" 확인한다.
- `bank_pg_memo_rule_score.py`: `bank.match()`의 적요 판정 줄을 그대로 불러와 합치기만 한다.
  새 규칙은 만들지 않는다.
  - 실행: `cd backend && uv run python domains/finance/eval/bank_pg_memo_rule_score.py`
- `bank_pg_memo_anchors.json`: "실제 PG 정산금 적요" 대표 문장 4개(사람이 미리 적음).
- `collect_bank_pg_memo_embed.py`/`score_bank_pg_memo_embed.py`: item 1·2와 같은 수집·채점
  분리 방식. 임계값은 dev에서 **오연결률이 정확히 0인 것들 중 재현율 최댓값**(0을 만드는
  임계값이 없으면 고르지 않고 "없음"이라고 보고).
  - 실행: `EMBED_MODEL=<모델> uv run python domains/finance/eval/collect_bank_pg_memo_embed.py <라벨>`
  - 비교: `uv run python domains/finance/eval/score_bank_pg_memo_embed.py <라벨1> <라벨2> ...`

```bash
cd backend
uv run python domains/finance/eval/bank_pg_memo_rule_score.py   # 지금 규칙만 (키 필요 없음)

EMBED_MODEL=gemini-embedding-001 \
  uv run python domains/finance/eval/collect_bank_pg_memo_embed.py gemini-embedding-001
uv run python domains/finance/eval/score_bank_pg_memo_embed.py gemini-embedding-001
```

**한계**
- **실제 은행 적요 표본이 없다.** 이 평가는 전부 사람이 미리 쓴 합성 적요다. 태윤님이 말한
  대로, 실제 적용은 측정 결과를 같이 보고 정할 일이지 이 평가가 정할 일이 아니다.
- 골든셋이 22건으로 작다.
- `pg_memo`가 여러 PG를 동시에 보는 설정(예: 토스페이먼츠+카카오페이 둘 다 정산금으로 침)은
  다루지 않는다. 지금 설정(단일 문자열 하나)과 같은 범위로만 쟀다.
- 날짜·금액 대조(`bank.match()`의 나머지 로직)는 그대로 둔다. 이 평가가 재는 것은 적요 판정
  한 단계뿐이다.
- 비용은 이 표에 아직 없다(item 1·2와 같은 한계).
- 이 세션에는 임베딩 키가 없어 배선 확인(키 없이 멈춤)과 가짜 유사도 값(완벽히 가르는 값·
  변별력 없는 값 — 변별력 없는 값에서는 "오연결률 0을 만드는 임계값 없음"이 사실 그대로
  나오는 것까지 확인)으로 채점 함수만 검증했다. 실제 비교 숫자는 키가 있는 로컬 PC에서
  위 명령으로 내야 한다.
