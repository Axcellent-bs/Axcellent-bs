# 기획 A2A holdout 평가

기획 서비스가 받는 A2A skill을 **실제 A2A 경로**(기획 서버 `POST /a2a`, 요청 부서 토큰)로 보내고,
결과를 **계약·에이전트 카드에 적힌 규칙**으로 따로 계산한 정답과 비교한다. 기획 판정 함수는 정답 계산에 쓰지 않는다.

- 실행: `cd backend && uv run python domains/planning/eval/holdout.py [--n 200] [--seed 20261110_02] [--fails]`
- LLM을 부르지 않는다 (요청 내용 묶기·제안 초안·대화방 LLM은 꺼서 규칙 경로만 잰다). 약 10초.
- 하네스는 dev seed `20261110_01`로만 고치고, holdout seed `20261110_02`는 한 번 돌려 숫자만 본다.
- 안전 관문(execute)은 통과로 두고, 개발에 보내는 배포 지시는 가짜로 받아 센다(실제로 보내지 않음).

## 영역과 정답 규칙

| 영역 | 정답 규칙 (문서) |
|---|---|
| access | 카드의 `allowed_requesters`에 없는 요청자는 `FORBIDDEN_SKILL`로 거부 |
| create_ticket | 끝나지 않은(완료·기각 아님) 같은 제목 요청이 있으면 `linked_duplicate` + 원래 REQ·ctx_req_, 아니면 `created` + 새 ctx_req_. 같은 멱등성 키는 같은 결과, 키가 없으면 `missing_idempotency_key` |
| request_release_check | 연결 규칙(`spec_unknown`·`spec_not_confirmed`·`open_question`·`para_without_task`·`task_not_merged`·`pr_outside_spec`·`linked_incident_open`·`paused_by_human`) 중 하나라도 걸리면 `hold`, 통과하고 검수가 모두 match면 `ready`(배포 지시 보냄), 아니면 `needs_human`. 문단별 상태·내용까지 비교 |
| receive_insight | facts가 없으면 `invalid_input`, 같은 insight_id 재전송은 `duplicate`(같은 PROP), 아니면 `created`. 수치는 facts 그대로, 근거·제목·목표에 없는 숫자가 설명에 있으면 경고 |
| receive_campaign_update | 모르는 제안은 `unknown_proposal`, 두 번째 게시·같은 성과 재전송은 `duplicate`, 아니면 `applied`와 상태(진행 중·성과 확인) |
| receive_planning_answer | 열린 질문에 맞는 요청이면 `applied`(질문 닫힘), 두 번째 답은 `duplicate`, 모르는 질문·다른 요청은 `unknown_question` |
| receive_dev_result | 요청 상태·작업·PR이 받은 값으로 바뀜, 같은 멱등성 키는 한 번만 적용 |

`planning_chat_*`는 권한(거부)만 잰다. 허용된 요청자로 보내면 LLM을 부를 수 있어서다.

## 결과 (2026-11-10)

| 영역 | dev seed 고치기 전 (40개씩) | dev seed (200개씩) | holdout (200개씩) |
|---|---|---|---|
| 요청자 권한 | 100% | 100% | 100% |
| 변경 요청 접수·중복 묶기 | 100% | 100% | 100% |
| 배포 전 기획서 대조 | 100% | 100% | 100% |
| 인사이트 → 제안·숫자 경고 | **12.5%** | 100% | 100% |
| 캠페인 진행·성과 받기 | **10.0%** | 100% | 100% |
| 되물은 질문 답 받기 | 100% | 100% | 100% |
| 개발 결과 받기·멱등성 | 100% | 100% | 100% |
| 전체 | 74.6% (209/280) | 100% (1400/1400) | **100% (1400/1400)** |

- 찾은 오류: `receive_insight` 핸들러가 계약에 없는 `inp.ref`를 읽어 A2A로 받으면 제안은 만들어지는데 응답이
  `handler_error`(failed)로 나갔다(11/4 #553부터). 기획이 직접 받아오는 경로(`pull_insights`)는 핸들러를 거치지 않아
  화면에서는 안 보였다. 캠페인 영역은 앞에서 제안을 A2A로 만들지 못해 같이 틀렸다. 근거 id를 `insight:<insight_id>`로 바꿔 고쳤다.
- 하네스 민감도: 규칙 4곳을 일부러 깨면(검수 none을 match로 봄, 끝난 요청에도 묶음, 설명 숫자 검사 뺌, 게시 중복 검사 약화)
  dev seed 점수가 63~96%로 떨어졌다. 확인 뒤 되돌렸다.
- 한계: LLM 판단(요청 내용 묶기, 제안 할 일·이유 초안)은 재지 않았다. 안전 관문은 통과로 두었다.
- 결과 파일: `results/holdout_20261110_02.json`

## 요청 관계 판단 LLM 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 1순위)

위 holdout은 LLM을 꺼서 돌기 때문에, `related.py`(`same`·`conflict`·`update`·`related` 4분류
판단)는 재지 못한다. 평가 데이터가 없어서(사용자 우선순위표 "만들 것") 새로 만들었다.

- `related_cases.json`: 합성 골든셋 28건(관계당 6건 + 관계없음 4건). 정답은 `ax_workflow.related`의
  `JUDGE_PROMPT` 정의(같은 일=same·둘 다 진행 중인 상충=conflict·끝난 요청을 다시 바꿈=update·
  같은 화면이지만 따로 처리 가능=related)에 맞춰 사람이 직접 표시했다(검수 전).
- `related_eval.py`: 케이스마다 `Request`·`State`를 실제로 만들어 `Relater(llm=실제 LLMClient)._judge()`
  를 그대로 부른다(검색 단계는 건너뛰고 "후보가 주어졌을 때 판단만" 잰다). 채점(같은 same 정밀도·재현율,
  4분류 정확도, none 정확도, 혼동표)은 이 스크립트에서 계산한다(다른 곳에 채점 로직이 없어서).
  - 실행: `cd backend && uv run python domains/planning/eval/related_eval.py`
  - 모델은 `PLANNING_LLM_MODEL` → 없으면 `LLM_MODEL`(`LLMSettings.from_env`와 동일)
- 모델 비교: `model_compare_related.py <라벨>`로 모델별 결과를 `results/model_compare/related_<라벨>.json`에
  쌓고, `model_compare_related_report.py <라벨1> <라벨2> ...`로 비교표를 뽑는다.

```bash
cd backend
PLANNING_LLM_MODEL=claude-haiku-5-5 uv run python domains/planning/eval/model_compare_related.py haiku
PLANNING_LLM_MODEL=claude-sonnet-5-5 uv run python domains/planning/eval/model_compare_related.py sonnet
uv run python domains/planning/eval/model_compare_related_report.py haiku sonnet
```

**한계**
- 골든셋 28건은 전부 합성이고 사람 검수 전이다(병환 님 holdout과 같은 한계). 표본이 작아 비율의
  오차가 크다 — 재현님 의견(위 다른 부서 적용 요청 2 다음 섹션, `embed-compare/README.md`)과 같은
  이유로, 실제로 모델을 바꾸기 전에는 더 늘리는 쪽이 먼저다.
- 검색(임베딩으로 후보 5개 찾기)은 이 평가에 안 들어간다. "비슷한 요청 찾기" 자체의 재현율·정밀도는
  우선순위표의 "비고" 항목이고, `related.py`의 `_vectors`/`MIN_SCORE`로 따로 재야 한다(아직 안 함).
- 이 세션(클라우드 샌드박스)에는 키가 없어 실행만 확인했다(배선 확인, 가짜 LLM으로 채점 로직
  검증 완료). 실제 비교 숫자는 키가 있는 로컬 PC에서 위 명령으로 내야 한다.
