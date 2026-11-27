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

## 영상 제작안 LLM 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 2순위)

"첫 답이 코드 검사를 통과한 비율(1판·2판)"·"금칙어·지어낸 숫자·실명 0" 평가. 이것도 평가
데이터가 없어서 새로 만들었다.

- `video_plan_cases.json`: 합성 제안(Proposal) 10건. 보통 제안 8건 + 의뢰 주제 후보에 실존
  인물 이름을 넣은 제안 2건(v04·v10, `실명` 검사가 실제로 걸릴 조건을 만든 것)
- `video_plan_eval.py`: 케이스마다 실제 `video_plan.write(llm, p, context_id)`를 그대로 부른다.
  채점 로직을 새로 만들지 않고, `write()`가 이미 `VideoPlan`에 남기는 값(`written_by`·
  `check_log`·`fallback_reason`·`fallback_detail`)만 읽어 지표로 바꾼다 — 금칙어
  (`text_problems`)·지어낸 숫자(`invented_number`)·실명(`has_real_name`) 검사 자체는
  `write()` 안에서 이미 돈 것이고 이 스크립트는 그 결과(통과했는지·어느 검사에 걸렸는지)만 센다.
  - 실행: `cd backend && uv run python domains/planning/eval/video_plan_eval.py`
  - 모델은 `PLANNING_LLM_MODEL` → 없으면 `LLM_MODEL`
  - 지표: `first_draft_pass_rate`(1판 통과율)·`rewrite_rate`(다시 쓰기 비율)·
    `second_draft_pass_rate`(다시 쓴 뒤 통과율)·`llm_plan_rate`(최종 LLM 제작안 비율, 1 -
    이 값 = 규칙 제작안으로 떨어진 비율)·`watched_check_hits`(marketing_word·
    invented_offer_number·real_name_in_scene 각각 몇 건 걸렸는지)
- 모델 비교: `model_compare_video_plan.py <라벨>` → `results/model_compare/video_plan_<라벨>.json`,
  `model_compare_video_plan_report.py <라벨1> <라벨2> ...`로 비교표

```bash
cd backend
PLANNING_LLM_MODEL=claude-haiku-5-5 uv run python domains/planning/eval/model_compare_video_plan.py haiku
PLANNING_LLM_MODEL=claude-sonnet-5-5 uv run python domains/planning/eval/model_compare_video_plan.py sonnet
uv run python domains/planning/eval/model_compare_video_plan_report.py haiku sonnet
```

**한계**
- 제안 10건은 합성이고, 실명 검사를 걸기 위한 2건을 뺀 8건은 평범한 입력이라 LLM이 정상적으로
  쓰면 보통 1판에서 통과한다 — "모델이 얼마나 자주 위반을 시도하는가"를 보려면 의도적으로 애매한
  입력(상충하는 할인율·여러 실존 이름 후보 등)을 더 늘려야 한다.
- 콘티 SVG 호출(`storyboard_svg.draw`)은 제작안과 같은 LLM으로 돌지만 이 평가의 지표에는 넣지
  않았다(제작안 자체의 통과율만 본다).
- 이 세션(클라우드 샌드박스)에는 키가 없어 실행만 확인했다(배선 확인). 가짜 LLM으로 (1) 깨끗한
  답은 10건 전부 1판 통과, (2) 의도적으로 실명을 섞은 답은 그 2건만 `real_name_in_scene`으로
  거절되는지 직접 검증했다. 실제 비교 숫자는 키가 있는 로컬 PC에서 위 명령으로 내야 한다.

## 기획서 초안 LLM 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 3순위)

"요구사항이 개발 작업 하나로 바로 되는 비율" 평가. 이것도 평가 데이터가 없어서 새로 만들었다.

- `spec_cases.json`: 합성 변경 요청 10건(제목·근거·보낸 부서). 평범한 요청 8건 + 결제·개인정보
  쪽으로 범위를 넓히게 유혹하는 2건(s04 카드사 재청구, s05 생년월일·주소 수집)
- `spec_eval.py`: 케이스마다 실제 `planning_agent.spec_writer.SpecWriter.draft()`를 그대로
  부른다. 두 갈래로 잰다:
  1. spec_writer가 이미 하는 일: 성공/실패(규칙 초안으로 떨어짐)·걸린 시간·30초 타임아웃 여부,
     "코드가 뺀 문장 수"(LLM이 낸 원본 문장 수와 spec_writer의 실제 `_clean()`이 거른 뒤 문장
     수의 차이 — `_clean()`을 그대로 가져와 쓰고, 원본을 보려고 LLM 클라이언트를 얇게 감싸
     draft() 안에서 실제로 받은 답만 옆에서 기록한다. 동작은 바꾸지 않는다)
  2. spec_writer에는 아직 없는 검사라서 이 평가 전용으로 새로 만든 근사치 세 가지(투명하게
     적는다, production 코드 아님): `invented_number`(초안 문장 숫자가 근거 어디에도 없음)·
     `scope_widening`(카드번호 저장·주민등록번호 등 결제·개인정보 범위 넓히는 낱말)·
     `axcellent_term`(합성 근거에는 없을 이 레포 실제 화면 용어, 나오면 지어낸 것)
  "요구사항이 개발 작업 하나로 바로 되는지"의 정확한 판정(실제로 작업 하나로 끝나는지)은
  개발 쪽에서 실행해봐야 알 수 있어 여기서는 재지 않는다. 이 세 검사가 모두 0인 비율
  (`clean_requirement_rate`)을 근사치로 쓴다
  - 실행: `cd backend && uv run python domains/planning/eval/spec_eval.py`
  - 모델은 `PLANNING_LLM_MODEL` → 없으면 `LLM_MODEL`
  - 지표: `draft_rate`(초안 성공률)·`fallback_rate`(규칙 초안으로 떨어진 비율)·
    `timeout_rate`(30초 초과 비율)·`clean_requirement_rate`(세 근사 검사 모두 통과한 비율)·
    `invented_number_hits`·`scope_widening_hits`·`axcellent_term_hits`(각 검사 걸린 건수)·
    `avg_dropped_sentences`(평균 뺀 문장 수)
- 모델 비교: `model_compare_spec.py <라벨>` → `results/model_compare/spec_<라벨>.json`,
  `model_compare_spec_report.py <라벨1> <라벨2> ...`로 비교표

```bash
cd backend
PLANNING_LLM_MODEL=claude-haiku-5-5 uv run python domains/planning/eval/model_compare_spec.py haiku
PLANNING_LLM_MODEL=claude-sonnet-5-5 uv run python domains/planning/eval/model_compare_spec.py sonnet
uv run python domains/planning/eval/model_compare_spec_report.py haiku sonnet
```

**한계**
- `invented_number`·`scope_widening`·`axcellent_term`은 production 코드가 아니라 이 평가를
  위해 새로 만든 근사치다. 특히 "요구사항 하나 = 개발 작업 하나"는 실제로 개발에 넘겨 실행해
  봐야 확실해서, 여기 지표는 그 대리 신호(지어낸 숫자·범위 넓히기·엉뚱한 화면 용어가 없는지)일
  뿐이다.
- 합성 요청 10건 중 범위 넓히기 유혹 2건(s04·s05)을 빼면 나머지는 평범한 입력이라 LLM이
  정상적으로 쓰면 보통 통과한다 — 더 애매한 입력(모호한 근거·상충하는 요청)을 늘리면 모델별
  차이가 더 잘 드러날 것이다.
- 이 세션(클라우드 샌드박스)에는 키가 없어 실행만 확인했다(배선 확인, 가짜 LLM으로 채점 로직
  검증 완료 — 깨끗한 답·범위 넓히기/지어낸 숫자/화면 용어가 섞인 답·검증 실패·타임아웃 네
  경로 모두). 실제 비교 숫자는 키가 있는 로컬 PC에서 위 명령으로 내야 한다.

## 제안 초안 LLM 모델 비교 (예진 제안, #소통 2026-11-27 15:46, 4순위)

"형식 통과율"·"근거에 없는 숫자 0" 평가. 기존 holdout.py(위 "영역과 정답 규칙"의
`receive_insight`)는 LLM을 꺼서 규칙 경로만 쟀다(결과 1400/1400) — LLM 경로는 이 평가가
처음 잰다.

- `proposal_cases.json`: 합성 인사이트 10건(제목·요약·근거 facts, 데이터분석이 보내는 모양
  그대로)
- `proposal_eval.py`: 케이스마다 실제 `planning_agent.proposals.write()`를 그대로 부른다.
  `write()`는 production 파이프라인 전체다 — LLM(`ProposalWriter.draft`) → 실패·시간 초과면
  규칙 초안(`rule_draft`) → 두 경로 모두 숫자 검사(`number_warnings`, 근거·목표에 없는 숫자를
  경고). 이 평가는 새 검사를 하나도 만들지 않았다 — `write()`가 돌려준 `written_by`·`warnings`만
  읽어 지표로 바꾼다
  - 실행: `cd backend && uv run python domains/planning/eval/proposal_eval.py`
  - 모델은 `PLANNING_LLM_MODEL` → 없으면 `LLM_MODEL`
  - 지표: `format_pass_rate`(`written_by == "llm"` 비율, 즉 규칙 초안으로 안 떨어진 비율)·
    `llm_zero_warning_rate`(LLM 경로 중 숫자 경고 0건 비율)·`zero_warning_rate`(전체 중 숫자
    경고 0건 비율)·`total_warnings`·`avg_warnings`(평균 경고 수)
- 모델 비교: `model_compare_proposal.py <라벨>` → `results/model_compare/proposal_<라벨>.json`,
  `model_compare_proposal_report.py <라벨1> <라벨2> ...`로 비교표

```bash
cd backend
PLANNING_LLM_MODEL=claude-haiku-5-5 uv run python domains/planning/eval/model_compare_proposal.py haiku
PLANNING_LLM_MODEL=claude-sonnet-5-5 uv run python domains/planning/eval/model_compare_proposal.py sonnet
uv run python domains/planning/eval/model_compare_proposal_report.py haiku sonnet
```

**한계**
- 합성 인사이트 10건은 근거 facts가 이미 깔끔해서(데이터분석이 보내는 구조화된 값), LLM이
  정상적으로 쓰면 숫자 경고가 잘 안 나올 수 있다 — 근거가 모호하거나 여러 숫자가 섞인 케이스를
  늘리면 모델별 차이가 더 잘 드러날 것이다.
- `draft_for_angle`(방향을 고른 뒤 다시 쓰는 경로, 11/18)는 이 평가에 넣지 않았다. 기본
  `write()` 경로만 잰다.
- 이 세션(클라우드 샌드박스)에는 키가 없어 실행만 확인했다(배선 확인, 가짜 LLM으로 깨끗한
  답·숫자 경고가 걸리는 답·검증 실패로 규칙 초안에 떨어지는 경로 모두 검증 완료). 실제 비교
  숫자는 키가 있는 로컬 PC에서 위 명령으로 내야 한다.
