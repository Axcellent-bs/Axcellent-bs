# 의뢰 접수·저장·이어가기 설계 (총괄 의뢰 자동 넘김, 0단계)

고객사 일반회원이 AI 대화방에서 한 의뢰를 총괄이 받아 알맞은 부서로 넘긴다. 이 문서는 그중 0단계
(저장과 재시작 이어가기)의 설계다. 접수(대화)·분류(LLM)·부서에 시키기는 이후 단계에서 이 기록 위에 붙는다.
조사한 일 목록은 `request-catalog.md`.

## 원칙

- LLM은 "의뢰인지 질문인지 가르고, 어떤 일인지 고르기"만 한다. 접수·저장·전달·검사는 코드가 한다.
- 흐름의 **첫 작업 부서부터** 시키고, 부서 사이 전달은 총괄이 **결과 번호**(`result_ref`)만 넘긴다.
  큰 결과는 부서 저장소에 두고 총괄 기록에는 번호만 둔다.
- 장애 대응 기존 동작은 바꾸지 않는다.

## 저장 항목 (`work_requests` 표, `work_requests.py`)

의뢰 하나가 한 줄(JSON)이다.

| 칸 | 내용 |
| --- | --- |
| `request_id`, `account`, `text` | 의뢰 번호, 의뢰자, 문장 원문(2000자까지) |
| `status` | `received` → `classified` → `running` ⇄ `waiting` → `done`·`failed`·`rejected`·`needs_human` |
| `task`, `confidence`, `reason`, `alternatives` | 고른 일, 확신도, 한 줄 이유, 차선 후보 (분류가 채움) |
| `plan` | 단계 목록 `{step_id, dept, skill, label, after[]}`. `after`는 앞서 끝나야 하는 단계 |
| `steps` | 단계별 진행: `status`, `attempts`, `grade`, `result_ref`, `review_id`, `handoff_task_id`, `async_task_id`, 시각 |
| `log`, `context_id` | 진행 로그, 연결된 이슈 번호 |

일반회원은 자기 의뢰만 본다(`list(account)`). 닫힌 의뢰는 오래된 것부터 1000건을 넘으면 지운다(열린 의뢰는 지우지 않는다).

## 재시작 이어가기

장애 이벤트는 재시작하면 진행 중이던 단계를 "중단됨"으로 닫는다. 의뢰는 닫지 않고 단계 상태별로 이어 간다.

| 단계 상태 | 재시작 뒤 |
| --- | --- |
| `completed`·`failed`·`skipped`·`rejected_by_human` | 그대로 |
| `pending` | 그대로 (선행 단계가 끝났으면 시킬 수 있음: `ready_steps`) |
| `held`·`waiting_human`·`waiting_async` | 그대로. 저장해 둔 번호(`review_id` 등)로 계속 기다림 |
| `running` (부서에 보내던 중) | 읽기(`grade == read`)면 `pending`으로 되돌려 다시 시킴. 그 밖(A·B·C, 등급을 모르는 것)은 이미 처리됐을 수 있어 **다시 보내지 않고** `waiting_human`(이유 `restart_unconfirmed`)으로 사람 확인 |

닫힌 의뢰(`done` 등)는 건드리지 않는다. 정리한 결과는 바로 저장돼 한 번 더 켜도 같은 상태다.
`IncidentManager.restore()`가 `resumed_requests`(복원·열림·다시 시킬 단계·확인 필요 단계 수)를 남긴다.

## 이 단계에서 하지 않는 것

- 실제로 이어서 시키는 일: 의뢰 실행(2단계)이 `ready_steps`를 보고 한다. 이 단계는 기록과 정책까지다.
- 의뢰 접수 대화, 분류 LLM, 화면, API: 이후 단계.
