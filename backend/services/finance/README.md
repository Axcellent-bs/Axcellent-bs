# services/finance — 재무 관리

포트 8105 · 업무 로직 `domains/finance` · 화면 서비스 id `finance`

| skill | 등급 |
| --- | --- |
| `seller_review` | B |
| `settlement_hold` | B |
| `closing_report` | read |
| `audit_report` | read |
| `record_support_cost` | read |

- 실행: `uv run --package ax-svc-finance uvicorn svc_finance.main:app --port 8105` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `finance`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
