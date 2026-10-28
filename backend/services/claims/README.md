# services/claims — 클레임·보상

포트 8112 · 업무 로직 `domains/support` · 화면 서비스 id `claims`

| skill | 등급 |
| --- | --- |
| `request_compensation` | A |
| `list_claims` | read |
| `get_claim` | read |
| `claim_stats` | read |
| `claim_queue` | read |
| `create_claim` | A |
| `decide_claim` | A |
| `receive_return` | A |
| `inspect_return` | A |
| `process_claim_queue` | A |
| `suggest_compensation` | read |
| `request_incident_compensation` | A |
| `analyze_receipt` | read |
| `analyze_claim_request` | read |

- 실행: `uv run --package ax-svc-claims uvicorn svc_claims.main:app --port 8112` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `support`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
