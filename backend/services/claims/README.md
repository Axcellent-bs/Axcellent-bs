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
| `list_incident_compensation` | read |
| `propose_incident_compensation` | A |
| `decide_incident_compensation` | A |
| `list_incident_queue` | read |

- 실행: `uv run --package ax-svc-claims uvicorn svc_claims.main:app --port 8112` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `support`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`).
- 이 폴더는 `scripts/split_services.py`가 만든다. 손으로 고치지 말고 대응표를 고친다.

클레임 기록의 보유기간 삭제 skill `purge_expired_claims`(팀 기준 5년, 기본 dry-run)는 클레임을 가진 이 서비스에 둔다. 자세한 것은 `domains/support/README.md`의 "개인정보 삭제" 절.

판매자 정산(2026-11-12 재무에서 옮김)은 처음에 이 서비스에 있었는데, 주문 30만 건 장부를 읽고 계산하는 동안 클레임 처리가 느려져 따로 띄운다: `services/settlement` (포트 8126). 자세한 것은 `domains/support/README.md`의 "판매자 정산" 절.
