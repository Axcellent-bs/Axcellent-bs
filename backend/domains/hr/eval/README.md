# 인사 AI 검색 평가 (합성 골든 셋)

카드 검색 방식(Dice, Sparse, Dense, Hybrid)을 같은 문제집으로 비교한다. **평가 데이터는 모두 합성**이다
(실제 카드 1장 + 합성 카드). 운영 데이터가 아니고 화면에서 쓰지 않는다.

## 최종 선택
Dense(임베딩), 카드 620장(골든 120 + 방해 500) 기준. 근거와 한계는 `compare-rules.md`와 아티팩트 참고.
사전에 정한 규칙의 결과(Sparse)와 최종 선택(Dense)이 다르고, 최종 선택은 사후 판단이다.

## 파일
| 파일 | 내용 |
|---|---|
| `design.md` | 골든 셋 설계 (기능 3곳 × 군집, 질의 유형, 분할) |
| `compare-rules.md` | 비교와 선택 규칙(결과 전에 정함), 결과, 최종 선택 |
| `cards.json` | 골든 카드 120장 (실제 1장 + 합성 119장) |
| `cards_distractors.json` | 방해 카드 500장 (합성). 평가의 기본 |
| `cards_distractors_880.json` | 규모 비교 실험용 880장 (500장 포함). 1000장 평가에만 쓴다 |
| `queries.json` | 질의 100개와 정답 카드 id, 개발용/평가용 분할 |
| `run_eval.py` | 방식 하나를 평가한다: `python run_eval.py <dice\|sparse\|dense\|hybrid> [카드 수]` |
| `compare.py` | 방식 간 비교와 선택 규칙 적용: `python compare.py [카드 수]` |
| `split_eval.py` | 개발:평가 비율(5:5, 4:6)과 규모별 "올바른 개수" |
| `make_distractors.py` | 방해 카드 생성기 (시드 고정, 기본 500장) |
| `audit_distractors.py` | 정답보다 위에 올라온 방해 카드 점검 |
| `refine-rules.md` | 8-2 Dense 보완: 시험 방법과 채택 기준(결과 전에 정함), 결과, 판정 |
| `refine_eval.py` | 8-2 변형 V0~V3 평가: `python refine_eval.py <dev\|test\|holdout>` (V3는 Claude 호출) |
| `make_holdout.py`, `queries_holdout.json` | 8-2 검증용 질의 40개(마지막에 한 번만 평가) |
| `run_explain.py` | `check_plan` 끝에서 끝까지 평가 (실제 Gemini·Claude 호출) |
| `results/` | 위 스크립트의 결과 |

## 실행
`dense`·`hybrid`는 임베딩 API를 쓴다. `backend/.env`의 `MEMORY_LLM_*`·`MEMORY_EMBED_MODEL`(없으면
`LLM_*`·`EMBED_MODEL`)을 읽고, 만든 벡터는 `backend/.data/eval_vectors.db`에 저장해 다시 쓴다.
**무료 임베딩은 하루 1000건 한도가 있다.** 620장 평가는 새로 받는 임베딩이 약 700건이고, 이미 받은
벡터가 있으면 0건이다. `run_all.sh`의 초기화 옵션은 `.data/*.db`를 지우므로 벡터도 사라진다.

```bash
cd backend
uv run python domains/hr/eval/run_eval.py dice 620
uv run python domains/hr/eval/compare.py 620
```

## 규모
1000장 평가는 규모를 정하려는 용도로 한 번만 돌렸다(620장과 사실상 같은 결과). 이후 평가는 620장까지만 쓴다.

## 반복 장애 적중 평가 (`repeat_eval.py`)

같은 유형의 장애가 다시 났을 때 `find_similar_cases`가 같은 유형의 과거 사건을 찾는지 본다. 결제 실패,
결제 지연, 환불 남용 3유형의 과거 사건 15장(해결 12 + 미해결 3)을 쌓고 질의 19개(총괄이 실제로 보내는
글 3, 사람이 쓴 말투 12, 상관없는 글 4)를 보냈다. 운영 설정 그대로이고(코사인 0.71, LLM 검증 없음) 이 평가로
값을 조정하지 않았다. 임베딩은 카드 15장과 질의 19개만 불렀다.

| 항목 | 결과 |
|---|---|
| 같은 유형이 1위 (유형이 있는 질의 15개) | 14개 (결과가 나온 14개 중 14개 모두 정답) |
| 결과가 없었던 질의 | 1개 ("결제가 서버 오류로 자꾸 실패해요", 기준 0.71 미만) |
| 상위 3개에 다른 유형이 섞인 질의 | 0개 |
| 총괄이 보내는 글 3개 | 3개 모두 같은 유형 1위 (유사도 0.74~0.85) |
| 상관없는 질의 4개 | 4개 모두 결과 없음 |

- 이 데이터는 합성이고 질의의 정답 유형은 작성자(Claude)가 정했다(사용자 확인 전). 질의가 15+4개뿐이라
  비율은 참고 수준이며 운영 성능이 아니다.
- 총괄이 보내는 글은 제목과 부품뿐이라 유형 구분은 지표 이름(`error_rate_5xx`, `p95_latency_ms`)에 기댄다.
- 결과: `results/repeat_eval.json`.

