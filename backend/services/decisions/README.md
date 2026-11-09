# services/decisions — 결정 기록

포트 8114 · 업무 로직 `domains/hr` · 화면 서비스 id `decisions`

| skill | 등급 |
| --- | --- |
| `record_case` | A |
| `check_plan` | read |
| `list_candidate_cards` | read |
| `get_audit_timeline` | read |
| `decide_candidate_card` | A |

- 실행: `uv run --package ax-svc-decisions uvicorn svc_decisions.main:app --port 8114` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `hr`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
