# workflow-core (`ax_workflow`)

기획(planning, 8101)과 개발(dev, 8111) 서비스가 같이 쓰는 뼈대예요. 분리 1·2단계에서 `domains/planning`에서 옮겼어요.

| 모듈 | 내용 |
|---|---|
| `models.py` | 상태 모델 (요청·기획서·작업·PR·대화·활동 등, 화면 State와 같은 모양) |
| `clock.py` | 주입하는 시계 (테스트 고정용 `StepClock`, 운영 `system_clock`) |
| `store.py` | 상태 파일 이어 쓰기 (`PLANNING_STATE_DIR`) |
| `core.py` | `StateService`: 상태·구독·적용 틀(`_apply` + `_mark`·`_settle`·`_after` 훅)·실행 관문(`_execute`)·활동 기록(`_act`) |
| `related.py`·`request_index.py` | 비슷한·충돌하는 요청 찾기, memory request 카드 색인 (기획: 새 요청, 개발: 대화 미리 보기·경로 겹침) |
| `rules.py`·`autocheck.py`·`handover.py`·`trace.py`·`metrics.py` | 코드로 계산하는 규칙(간단 요청·자동 확정·머지·인수인계·역추적·지표) |
| `seed.py` | 초기 데이터 (두 서비스 같은 시드) |
| `dev_handoff.py`·`result_notify.py`·`cause_inbox.py` | 기획 ↔ 개발 넘기기, 요청 부서 결과 통지, 받은 요청(할 일함) 올리기 |
| `auth.py` | 쓰기 API 사람·관리자 토큰 확인 |

- 도메인끼리는 서로 import하지 않아요(`.importlinter`). 두 서비스가 같이 쓰는 것만 여기에 둬요.
- `planning_agent`의 옛 모듈(`domain.models`·`domain.clock`·`state_store`·`related` 등)은 예전 import 경로를 위해 여기를 다시 내보내요.
