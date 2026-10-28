# services/planning — 요청·기획서

포트 8101 · 업무 로직 `domains/planning` · 화면 서비스 id `planning`

| skill | 등급 |
| --- | --- |
| `create_ticket` | A |
| `receive_dev_result` | A |

- 실행: `uv run --package ax-svc-planning uvicorn svc_planning.main:app --port 8101` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `planning`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
