# services/handover — 인수인계

포트 8104 · 업무 로직 `domains/hr` · 화면 서비스 id `handover`

| skill | 등급 |
| --- | --- |
| `get_roster` | read |
| `start_handover` | A |
| `answer_handover_question` | A |
| `confirm_handover` | B |
| `list_handovers` | read |
| `list_open_questions` | read |

- 실행: `uv run --package ax-svc-handover uvicorn svc_handover.main:app --port 8104` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `hr`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
