# services/settlement — 판매자 정산

포트 8126 · 업무 로직 `domains/support` · 화면 서비스 id `settlement` (화면은 클레임·보상 메뉴의 `/settlements`)

| skill | 등급 |
| --- | --- |
| `seller_settlement` | read |
| `seller_settlement_weeks` | read |
| `seller_settlement_batches` | read |
| `seller_settlement_deadlines` | read |
| `request_seller_settlement` | A |

- 실행: `uv run --package ax-svc-settlement uvicorn svc_settlement.main:app --port 8126` (backend/ 에서, 목 모드 `AGENT_TEMPLATE_MOCK=1`)
- 다른 에이전트가 `support`로 불러도 위 skill이면 이 서비스로 온다 (`route_target`). 재무(`settlement_feed.py`)·dashboard-api(`settlements.py`)는 `support`로 부른다.
- 켠 서비스 목록(`AX_ENABLED_SERVICES`)에 `claims`가 있으면 이 서비스도 켠 것으로 본다 (예전에 claims 안에 있었다).
- 판매 서버 연결 키(`AX_SHOP_CLIENT_KEYS`)에 `settlement:` 키가 없으면 쿠폰 목록 읽기에 `claims` 키를 쓴다 (읽기만 한다).

판매자 정산(2026-11-12 재무에서 옮김)은 처음에 클레임(claims) 서비스에 있었다. 주문 30만 건 장부를 읽고 판매자별로 계산하는 일이 같은 프로세스의 클레임 처리를 느리게 해서 이 서비스로 나눴다.

- 조회는 저장된 장부와 계산 결과로 바로 답한다. 장부 새로 읽기는 뒤에서 하나만 돈다(최근 3일 주문은 30초 뒤 조회 때, 전체는 10분마다).
- 서버를 켜면 뒤에서 첫 장부를 모으고 주별 정산서를 미리 계산한다. 그 전에 온 조회는 `settlement_preparing`으로 바로 답한다(화면은 503 "준비 중").
- 지급 승인 요청은 60초 안에 모은 장부로 새로 계산한다(오래됐으면 최근 주문만 다시 읽는다).

자세한 것은 `domains/support/README.md`의 "판매자 정산" 절.
