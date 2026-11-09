# services/dev — 개발·배포

포트 8111 · 업무 로직 `domains/planning` · 화면 서비스 id `dev`

| skill | 등급 |
| --- | --- |
| `find_related_prs` | read |
| `link_incident` | A |
| `verify_rollback_target` | read |
| `list_in_progress` | read |
| `verify_handover` | read |
| `start_dev` | A |

- 실행: `uv run --package ax-svc-dev uvicorn svc_dev.main:app --port 8111` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `planning`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
