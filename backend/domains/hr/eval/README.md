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
