# 인사 부서 평가 설계 (장애 대응 시간) — 0단계 확인 결과

> 상태: 계획·확인 단계. 이 문서는 코드를 읽은 결과와 1~4단계에서 만들 필드·흐름을 정리한 것이고, 아직 만든 코드는 없다.
> 기준값은 모두 임시값(v1)이다. 실제 장애 기록이 쌓이면 조정한다.

## 1. 목적

장애 대응 때 부서 AI가 맡은 단계의 처리 시간을 인사가 기준값과 비교해, 부서별 지연과 반복 장애를 보여 준다.
판정은 시간 비교 계산으로만 하고 LLM은 쓰지 않는다. 최종 판단은 사람이 한다.

## 2. 코드에서 확인한 것

| 질문 | 확인 결과 | 위치 |
|---|---|---|
| 단계의 시작·끝 시각이 저장되는가 | 저장됨. `StepRun.started_at`·`finished_at`·`attempts`는 저장 형식(`step_to_dict`)에 들어 있다 | `ax_orchestrator/models.py`, `store.py` |
| 승인 대기 시각이 있는가 | `Hold.started_at`(보류에 올린 시각)이 있다. 그러나 `Hold.decide()`가 결정 시각을 남기지 않는다 | `engine.py` `Hold` |
| 승인 대기가 저장되는가 | 저장되지 않는다. `holds`·`handoffs`는 저장 형식에서 제외돼 있어 재시작하면 사라진다 | `store.py` `incident_to_dict` |
| 승인 대기가 단계 시간에 섞이는가 | 섞인다. `run.started_at`은 단계 시작에 찍히고, 승인 뒤 재개한 호출이 끝나야 `finished_at`이 찍힌다 | `engine.py` `_run_step`, `_wait_human` |
| 단계 마감(30초)은 보류 중에 멈추는가 | 마감은 호출 하나마다 건다(`deadline = now + (step.timeout or step_timeout)`). 승인 기다림은 호출 밖에서 `hold_timeout`(기본 600초)으로 기다린다. 승인 뒤 재개 호출은 새 마감을 받는다 | `engine.py` `_request`, `_wait_human` |
| 재시도 횟수가 저장되는가 | 저장됨(`attempts`). 실패 재시도 최대 2, 마감 초과 재시도 최대 1. 승인 뒤 재개 호출은 `attempts`에 안 센다 | `engine.py` `_call_and_handle` |
| 사람 할 일함 대기 시각이 있는가 | `HandoffWait.started_at`(넘긴 시각)·`finished_at`(사람이 끝낸 시각)이 있다. 처리를 시작한 시각은 없다. 이것도 저장되지 않는다 | `engine.py` `HandoffWait` |
| 외부 대기(shop-api 등)를 구분할 수 있는가 | 구분할 수 없다. 총괄은 부서에 요청을 보내고 답이 올 때까지의 시간만 안다. 부서 안에서 무엇을 기다렸는지는 모른다 | `engine.py` `_send` |
| 안전 관문이 승인 시각을 주는가 | 준다. `Review`에 `review_id`·`context_id`·`created_at`·`decided_at`·`status`(pending/approved/rejected/expired)·`expires_at`이 있고 `GET /api/safety/reviews`로 읽는다. `Hold.review_id`가 이 `review_id`와 같다 | `ax_safety/store.py`, `main.py` |
| 인사가 받는 시각 계약 | `RecordEventTimesInput`(`EventTimeline` 상속): `detected_at`, `cause_candidate_at`, `human_work_started_at`, `human_work_done_at`, `recovered_at`, `prior_case_id`. 이미 값이 있는 시각은 덮어쓰지 않고 비어 있던 것만 채운다 | `ax_contracts/schemas/hr.py`, `hr_agent/case_memory/service.py` |
| 인사가 이미 나누는 구간 | 감지→원인 후보(AI 처리), 사람 작업 시작→완료, 완료→정상 복귀(복구 대기). 부서별·단계별 구분은 없다 | `ax_contracts/schemas/hr.py` |
| 처리 시간 요약(`kpi_summary`) | 사건별 감지~복구 시간(`mttr_seconds`), 자동 처리율, 등급별 건수 등을 계산하는 순수 함수(LLM 없음). 부서별 단계 시간은 계산하지 않는다 | `data_agent/metrics/kpi.py` |

## 3. 확인으로 바뀌는 것

1. **외부 대기는 v1에서 뺀다.** 총괄은 구분할 수 없다. 외부 때문에 늦어진 것은 일한 시간에 그대로 들어가므로, 화면에 "외부 요인은 구분하지 않음"이라고 적는다.
2. **전달 대기의 뜻을 "사람 할 일함 대기"로 바꾼다.** 처리 시작 시각이 없어서 "넘긴 뒤 처리 시작까지"는 잴 수 없다. 잴 수 있는 것은 "넘긴 시각 → 사람이 끝낸 시각"이다.
3. **승인 대기는 총괄이 직접 기록한다.** `Hold.decide()`에서 결정 시각을 남기고, 승인 대기 초를 단계 기록에 저장한다. 안전 관문 `decided_at`은 같은 값을 맞춰 보는 용도다.
4. **복구 검증은 결과 지표로 둔다.** 복구까지 걸린 시간은 `kpi_summary`의 `mttr_seconds`를 쓸 수 있다.

## 4. 1단계에서 더할 필드 (총괄, 모두 추가만)

`StepRun`(저장 형식에 선택 값으로 추가)

| 필드 | 뜻 |
|---|---|
| `approval_wait_seconds` | 보류에 올린 시각부터 사람이 결정한 시각까지 |
| `human_wait_seconds` | 사람 할 일함으로 넘긴 시각부터 사람이 끝낸 시각까지 |
| `work_seconds` | `finished_at − started_at − approval_wait − human_wait` (0 미만이면 0) |

- `Hold`에 `decided_at`을 더하고, 결정 때 승인 대기 초를 단계에 옮긴다.
- `HandoffWait.finished_at`은 이미 있으니 끝날 때 단계에 옮긴다.
- 인사로 보내는 단계별 값:

```
{ step_id, dept, status, work_seconds, approval_wait_seconds,
  human_wait_seconds, attempts, reason_code }
```

## 5. 인사로 보내는 방법 (결정 필요)

| 안 | 내용 | 장단점 |
|---|---|---|
| A (추천) | `RecordEventTimesInput`에 선택 필드 `step_timings`를 추가(계약은 추가만) | 이미 있는 총괄→인사 호출·멱등성·카드 연결을 그대로 쓴다. 새 skill 등록(Agent Card·안전 등록부)이 필요 없다. 단 "이미 있는 값은 덮어쓰지 않음" 규칙과 단계 시간 갱신 방식을 정해야 한다 |
| B | 새 skill `record_step_timings`를 만든다 | 규칙이 깔끔하지만 계약·Agent Card·안전 등록부까지 늘어 범위가 커진다 |

1단계 크기는 **중간**으로 본다(총괄 필드 + 저장 + 전송 + 계약 + 시험).

## 6. 아직 확인하지 못한 것

- 안전 관문 `Review` 저장소가 재시작 뒤에도 남는지 (총괄 기록으로 대체하므로 필수는 아님)
- 인사 카드가 `step_timings`를 어디에 어떤 모양으로 저장할지 (카드 데이터 JSON 확장 여부)
- 고객 입력 대기(보류) 상태(`engine.py`의 고객 입력 대기)의 시간을 어느 대기로 셀지
- 단계 이름·부서 배정표에서 `link_incident`가 아직 `planning`으로 남아 있는 것 (개발로 집계)

## 7. 이후 단계 요약

| 단계 | 내용 |
|---|---|
| 1 | 총괄: 위 필드를 기록하고 인사로 보낸다 (PR 1) |
| 2 | 인사: 받아서 저장, 기준값 설정(v1), 단계-묶음 배정, 판정 (PR 2) |
| 3 | 인사: 부서별 집계와 "부서 평가" 화면 (PR 3) |
| 4 | 사람 확인(의견), 용어 점검, 시연 영상 (PR 4) |

기준값(임시 v1)

| 묶음 | 정상 | 지연 | 확인 필요 |
|---|---|---|---|
| 조회·분석 | 30초 이하 | 30초~2분 | 2분 초과 |
| LLM 분석 | 1분 이하 | 1~2분 | 2분 초과 |
| 조치 | 2분 이하 | 2~5분 | 5분 초과 |
| 공지·기록 | 30초 이하 | 30초~2분 | 2분 초과 |
| 되돌리기 교차검증 | 30초 이하 | 없음 | 30초 초과 |

- 평가 제외: 기획 AI, 복구 검증(결과 지표)
- 시간이 아닌 상태: 기록 없음 / 실패·막힘 / 승인 만료 (외부 대기는 v1 제외)
