# domains/dev (`dev_agent`)
담당: 예진 · 포트: 8111 · 서비스: `services/dev`

## 1. 역할
개발·배포 에이전트. 기획 서비스(`domains/planning`, 8101)가 넘긴 확정 기획서(`start_dev`)로 PR을 만들고 검수·머지·배포까지 잇고, 결과를 기획에 돌려준다(`receive_dev_result`). 2026-11-02 기획·개발 분리 5단계에서 `planning_agent`에서 나왔다.

- 넘겨받은 기획서는 사람 클릭 없이 PR 만들기 → 검수 → 불일치면 수정 후 재검수 → 자동 머지 검사까지 간다. 규칙에 걸리면 `auto_review` 대기열(교차검증 대기·사람 확인).
- 자동 처리 범위(`auto_policy`)의 주인이다. 바뀌면 `receive_dev_result`에 실어 기획에 맞춘다.
- 코딩 에이전트(`coder.py`)·개발 AI 대화(`dev_chat.py`)·개발자 본인 Claude 일감(`work_queue.py`·`work_api.py`, MCP)·저장소 작업 공간(`repo_api.py`)·형상관리 연결(`vcs/`·`vcs_bridge.py`·`repo_routing.py`)·배포 기록(`deploy_store.py`·`deploy_signals.py`·`deploy.py`)·머지 교차검증 근거(`cross_check.py`)가 여기 있다.
- 장애 사건에 관련 PR 후보(`find_related_prs`)·롤백 대상 확인(`verify_rollback_target`)·사건 연결(`link_incident`), 인사 인수인계용 진행 중 항목(`list_in_progress`)·인계 확인(`verify_handover`)을 맡는다.

## 2. 구성
| 모듈 | 내용 |
|---|---|
| `service.py` | `DevService(PlanningCore)`. 공용 뼈대는 `ax_workflow.base` |
| `api.py` | 화면 REST `/api/planning/*` (공용 라우트 `ax_workflow.api` + 개발 라우트) |
| `handlers.py` | A2A skill 6개 (`DEV_HANDLERS`) |
| `main.py`·`runtime.py` | 서버 진입점, 프로세스 안 서비스 하나 |

- 기획 도메인(`planning_agent`)은 import하지 않는다(`.importlinter`). 둘이 같이 쓰는 상태 모델·규칙·넘기기는 `shared/workflow-core`(`ax_workflow`).
- 환경 변수 이름은 그대로 `PLANNING_*`(LLM·형상관리·상태 파일). 카드는 `DEV_AGENT_CARD`(옛 `PLANNING_AGENT_CARD`도 읽음).
- 시험: `domains/dev/tests`. 기획·개발을 함께 도는 시험 도우미는 `shared/workflow-testing`(`ax_workflow_testing`).
