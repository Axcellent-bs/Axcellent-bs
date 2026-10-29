# services/inquiry — 고객 문의

포트 8102 · 업무 로직 `domains/support` · 화면 서비스 id `inquiry`

| skill | 등급 |
| --- | --- |
| `list_inquiries` | read |
| `get_inquiry` | read |
| `check_reply` | read |
| `summarize_inquiry` | read |
| `create_inquiry` | A |
| `add_customer_message` | A |
| `send_reply` | A |
| `reject_draft` | A |
| `set_inquiry_status` | A |
| `inquiry_overview` | read |
| `detect_inquiry_surge` | read |
| `request_change` | A |
| `list_change_requests` | read |
| `receive_change_result` | A |
| `diagnose_payment` | read |
| `prepare_handoff` | read |
| `assess_deletion_request` | read |
| `analyze_receipt` | read |

- 실행: `uv run --package ax-svc-inquiry uvicorn svc_inquiry.main:app --port 8102` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `support`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.
