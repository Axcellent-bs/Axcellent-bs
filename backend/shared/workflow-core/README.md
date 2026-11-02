# workflow-core (`ax_workflow`)

기획(planning, 8101)과 개발(dev, 8111) 서비스가 같이 쓰는 뼈대예요. 분리 1단계에서 `domains/planning`에서 옮겼어요.

| 모듈 | 내용 |
|---|---|
| `models.py` | 상태 모델 (요청·기획서·작업·PR·대화·활동 등, 화면 State와 같은 모양) |
| `clock.py` | 주입하는 시계 (테스트 고정용 `StepClock`, 운영 `system_clock`) |
| `store.py` | 상태 파일 이어 쓰기 (`PLANNING_STATE_DIR`) |
| `core.py` | `StateService`: 상태·구독·적용 틀(`_apply` + `_mark`·`_settle`·`_after` 훅)·실행 관문(`_execute`)·활동 기록(`_act`) |

- 도메인끼리는 서로 import하지 않아요(`.importlinter`). 두 서비스가 같이 쓰는 것만 여기에 둬요.
- `planning_agent.domain.models`·`domain.clock`·`state_store`는 예전 import 경로를 위해 여기를 다시 내보내요.
