# 사이트 변환기 PR1 (연결 종류·읽기 변환·빈 응답) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 회사 연결 파일에 `kind=adapter`를 두면 AI들이 부르는 shop-api 주소가 dashboard-api 안의 변환기로 가고, 변환기가 고객사 자기 API(누비투어 `/api/v1`)를 짝대로 읽어 shop-api 모양으로 돌려준다.

**Architecture:** `ax_agent_template.company_connection`에 `kind`·`adapter` 필드를 추가하고 `agent_url("shop-api")`가 `kind=adapter`면 `<dashboard-api>/site-adapter`를 준다. dashboard-api에 `site_adapter/` 패키지(짝 파일 읽기, 고객사 API 호출, 자원 변환, 라우터)를 둔다. 라우터는 `/site-adapter/api/shop/...`와 `/site-adapter/health`를 받는다. 사이트에 없는 자료는 빈 정상 응답, 고객지원 접수 동기화 쓰기는 변환기 저장소에 기록만 한다.

**Tech Stack:** Python 3.12, FastAPI, httpx(`MockTransport`로 시험), pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-11-18-site-adapter-design.md`

**이번 PR에 넣지 않는 것 (다음 PR):** ② 사람 반영 쓰기(환불·쿠폰·배너 → 할 일함), 짝 제안·확인 화면, 인증 값의 보관함 저장, 문의함·신청함 짝(`/admin/inquiries`·`/admin/claims` 읽기, 고객지원 접수 항목 모양 조사 필요). 이 PR에서 ② 주소와 문의·신청 읽기는 아래 "빈 응답"이 아닌 404 `NOT_MAPPED`로 남겨 둔다(아직 안 만든 것과 사이트에 없는 것을 섞지 않는다).

## Global Constraints

- 계약은 추가만 한다. 연결 파일 필드는 추가(`kind` 기본 `shop_api`, 없으면 예전 파일 그대로)만 한다.
- 비밀값(고객사 API 키)은 `.env`(`AX_SITE_API_TOKEN`)에서만 읽는다. 응답·로그·오류 문구에 넣지 않는다.
- 고객사 API 주소는 기존 주소 정책 `company_connection.check_host`를 지난다.
- 숫자를 지어내지 않는다. 사이트에 없는 자료는 빈 목록·빈 값이고 `meta.source = "site_adapter"`를 싣는다.
- LLM을 쓰지 않는다(짝은 파일).
- 로컬 테스트는 고친 패키지만: `cd backend && uv run pytest shared/agent-template shared/dashboard-api -q`.
- 커밋: `git -c user.name="노예진" -c user.email="kidjin36@gmail.com" commit -F <파일>`. 브랜치 `feat/site-adapter-reads`, 작업 폴더 `.wt/feat-site-adapter-reads`.

## Review Focus

1. 고객사 API가 다음 쪽 커서를 계속 주는데 `since`보다 오래된 주문만 남은 경우: 변환기가 끝없이 읽지 않고 `AX_SITE_ADAPTER_MAX_PAGES`(기본 50)에서 멈춰 `meta.truncated = true`를 준다 → Task 4 시험.
2. 고객사 API 401·403: 503 `UPSTREAM_AUTH`, 키 값은 문구에 없음 → Task 4 시험.
3. 짝의 필수 항목이 응답에 없음(예: `code` 없음): 502 `MAPPING_BROKEN`, 문구에 자원·항목 이름 → Task 3 시험.
4. 예약 인원 0명(성인·아동 0)인 주문: `qty`가 0이라 `unit_price` 나눗셈에서 죽지 않고 `unit_price = 0` → Task 3 시험.
5. `kind=adapter`인데 `enabled=false`: 내부 데이터 모드라 변환기로 가지 않는다(지금 규칙 유지) → Task 1 시험.

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `backend/shared/agent-template/src/ax_agent_template/company_connection.py` (수정) | `Connection.kind`, `Connection.adapter_base_url`, `shop_api_override()`가 adapter면 변환기 주소 |
| `backend/shared/dashboard-api/src/ax_dashboard_api/site_adapter/__init__.py` | 패키지 설명 |
| `.../site_adapter/mapping.py` | 짝 파일 모델·읽기·항목 꺼내기 |
| `.../site_adapter/upstream.py` | 고객사 API 호출(인증 헤더, 페이지 넘기기, 오류 바꾸기) |
| `.../site_adapter/resources.py` | 자원 변환: 주문·상품·분류·고객·판매 합계 |
| `.../site_adapter/bookkeeping.py` | 접수 동기화 기록 저장 |
| `.../site_adapter/router.py` | `/site-adapter` 라우터: ① 변환, ③ 빈 응답, 동기화 기록, 그 밖 404 |
| `backend/shared/dashboard-api/src/ax_dashboard_api/main.py` (수정) | 라우터 붙이기 |
| `backend/shared/dashboard-api/tests/site_adapter/fixtures/nubi_*.json` | 누비투어 응답 녹화(시드 형식, 가린 개인정보) |
| `backend/shared/dashboard-api/tests/site_adapter/fixtures/nubi_mapping.json` | 손으로 쓴 누비투어 짝 |
| `backend/shared/dashboard-api/tests/site_adapter/test_*.py` | 시험 |
| `backend/shared/dashboard-api/README.md` (수정) | 변환기 절 추가 |

---

### Task 1: 연결 파일 `kind`·`adapter` 필드와 주소 바꾸기

**Files:**
- Modify: `backend/shared/agent-template/src/ax_agent_template/company_connection.py` (`Connection`, `_parse`, `shop_api_override`, `save_connection`)
- Test: `backend/shared/agent-template/tests/test_company_connection_kind.py`

**Interfaces:**
- Produces:
  - `Connection.kind: Literal["shop_api", "adapter"] = "shop_api"`
  - `Connection.adapter: dict[str, str] | None = None` — `{"base_url": "<고객사 API 주소>"}`
  - `as_dict()`에 `kind`·`adapter` 추가
  - `ADAPTER_PATH = "/site-adapter"`
  - `shop_api_override() -> str | None`: `enabled`이고 `kind == "adapter"`면 `agent_url("dashboard-api") + ADAPTER_PATH` (순환 import는 함수 안 import로 피한다), `kind == "shop_api"`면 지금처럼 `shop_api_url`
  - `save_connection(url, *, verified_at, updated_by, enabled=True, kind="shop_api", adapter=None)` — `kind="adapter"`면 `adapter["base_url"]`에 `validate_shop_url`·`check_host`를 적용, `shop_api_url`은 같은 값으로 둔다(화면·기록 호환)

- [ ] **Step 1: 실패하는 시험 쓰기**

```python
def test_old_file_without_kind_is_shop_api(conn_file): ...
    # {"shop_api_url": "http://10.0.0.5:8010", "enabled": true} → read_connection().kind == "shop_api"
    # shop_api_override() == "http://10.0.0.5:8010"

def test_adapter_kind_points_to_dashboard_site_adapter(conn_file, monkeypatch):
    # monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash:8020")
    # 파일 {"shop_api_url": "http://10.0.0.9:3000", "enabled": true, "kind": "adapter", "adapter": {"base_url": "http://10.0.0.9:3000"}}
    # shop_api_override() == "http://dash:8020/site-adapter"

def test_adapter_disabled_uses_no_override(conn_file):
    # 같은 파일에 "enabled": false → shop_api_override() is None

def test_unknown_kind_is_rejected(conn_file):
    # "kind": "db" → read_connection() is None (깨진 파일 규칙: 경고 한 번, 없는 것으로)

def test_save_adapter_checks_host(conn_file):
    # save_connection("http://169.254.169.254", ..., kind="adapter", adapter={"base_url": "http://169.254.169.254"}) → ValueError
```

- [ ] **Step 2: 실패 확인** — Run: `cd backend && uv run pytest shared/agent-template/tests/test_company_connection_kind.py -q` / Expected: FAIL (`kind` 없음)
- [ ] **Step 3: 구현** — `_parse`에서 `kind`는 없으면 `"shop_api"`, 두 값 밖이면 `ValueError`. `adapter`는 `kind=="adapter"`일 때 `base_url` 문자열 필수.
- [ ] **Step 4: 통과 확인** — 같은 명령 + `uv run pytest shared/agent-template -q` 전체 PASS
- [ ] **Step 5: 커밋** — `feat(agent-template): 회사 연결에 kind(shop_api|adapter)를 더하고 adapter면 변환기 주소를 준다`

---

### Task 2: 짝 파일 모델과 누비투어 짝·녹화

**Files:**
- Create: `.../site_adapter/__init__.py`, `.../site_adapter/mapping.py`
- Create: `tests/site_adapter/fixtures/nubi_mapping.json`, `nubi_tours.json`, `nubi_bookings_p1.json`, `nubi_bookings_p2.json`
- Test: `tests/site_adapter/test_mapping.py`

**Interfaces:**
- Produces:
  - `MAPPING_DIR_ENV = "AX_SITE_ADAPTER_DIR"`, 기본 `backend/.data/site_adapter`, 짝 파일 `<dir>/mapping.json`
  - `class Paging(BaseModel)`: `items_field="data"`, `next_field="next_cursor"`, `cursor_param="cursor"`, `since_param: str | None = "updated_since"`, `limit_param="limit"`, `limit=200`
  - `class ResourceMap(BaseModel)`: `path: str`, `paging: Paging`, `fields: dict[str, str]`, `values: dict[str, dict[str, str]] = {}`
  - `class SiteMapping(BaseModel)`: `version: Literal[1]`, `auth_header: str = "Authorization"`, `auth_scheme: str = "Bearer"`, `resources: dict[Literal["orders","products"], ResourceMap]`
  - `load_mapping() -> SiteMapping | None` (없으면 None, 깨졌으면 `MappingError`)
  - `pick(row: dict, expr: str) -> Any`: `expr`는 점 경로(`contact.name`), `sum:a,b`(정수 합), `const:<값>`. 경로가 없으면 `MissingField(expr)`
  - `class MappingError(Exception)`, `class MissingField(MappingError)`

누비투어 짝(`nubi_mapping.json`)에 적을 값:
- orders: `path=/api/v1/bookings`, fields `order_id=code`, `status=status`, `created_at=created_at`, `buyer_id=member_id`, `product_id=product_code`, `qty=sum:adults,children`, `total_amount=total_amount`, `paid_amount=paid_amount`; values `status`: `pending_deposit→pending_payment`, `pending_full→pending_payment`, `deposit_paid→paid`, `fully_paid→paid`, `completed→paid`, `cancel_requested→paid`, `cancelled→cancelled`, `expired→cancelled`
- products: `path=/api/v1/tours`, fields `product_id=code`, `name=name`, `price=base_price`, `category_code=region`, `active=on_sale`

녹화 파일은 누비투어 `serialize.ts`의 `bookingV1`·`tourV1` 모양 그대로(가린 이름·전화), 예약 3건(상태 `deposit_paid`·`cancelled`·`pending_deposit`, 그중 하나는 `member_id: null`), 상품 2건. `nubi_bookings_p1.json`은 `next_cursor`가 있고 p2는 `null`.

- [ ] **Step 1: 시험** — `test_pick_dot_path`, `test_pick_sum`, `test_pick_missing_raises`(`MissingField("code")`), `test_load_nubi_mapping`(fixture를 `AX_SITE_ADAPTER_DIR`에 복사 → `resources["orders"].values["status"]["fully_paid"] == "paid"`), `test_broken_mapping_raises`
- [ ] **Step 2: 실패 확인** — `uv run pytest shared/dashboard-api/tests/site_adapter/test_mapping.py -q` FAIL
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 커밋** — `feat(dashboard-api): 사이트 변환기 짝 파일 모델과 누비투어 짝·녹화`

---

### Task 3: 자원 변환 (주문·상품·분류·고객·판매 합계)

**Files:**
- Create: `.../site_adapter/resources.py`
- Test: `tests/site_adapter/test_resources.py`

**Interfaces:**
- Consumes: `SiteMapping`, `ResourceMap`, `pick`, `MissingField` (Task 2)
- Produces (모두 순수 함수, 고객사 원본 행 목록을 받는다):
  - `to_order(row: dict, m: ResourceMap) -> dict` → `{"order_id", "status", "created_at", "buyer_id", "total_amount", "refunded_amount": 0, "items": [{"product_id", "qty", "unit_price", "paid_amount"}], "payments": [{"amount", "status": "succeeded", "created_at"}]}` — `payments`는 `paid_amount > 0`일 때 한 건, `unit_price = total_amount // qty`(qty 0이면 0), 상태는 `values["status"]`, 표에 없는 값은 원래 값 그대로
  - `to_product(row, m) -> dict` → `{"product_id","name","price","category_code","active"}`
  - `categories_from(products: list[dict]) -> list[dict]` → `{"category_code","name"}`(이름은 코드), 중복 없이 정렬
  - `users_from(orders: list[dict]) -> list[dict]` → `{"buyer_id","display_name": None,"points": 0}`, `buyer_id`가 None인 주문은 뺀다
  - `sales_summary(orders: list[dict], group_by: str) -> list[dict]` → `group_by=product`면 `{"product_id","gross_qty","gross_amount"}`, 상태 `cancelled` 주문 제외
  - `MissingField`는 `MappingBroken(resource, field)`로 바꿔 올린다

- [ ] **Step 1: 시험** (녹화 fixture 사용)
  - `test_order_from_nubi_booking`: `deposit_paid` 예약 → `status == "paid"`, `items[0]["qty"] == adults+children`, `payments[0]["amount"] == paid_amount`
  - `test_pending_booking_has_no_payment`
  - `test_zero_qty_does_not_divide` (Review Focus 4): adults·children 0 → `unit_price == 0`
  - `test_missing_code_is_mapping_broken` (Review Focus 3): `code` 지운 행 → `MappingBroken`, `str(e)`에 `orders`·`code`
  - `test_users_skip_guest_orders`, `test_categories_unique`, `test_sales_summary_skips_cancelled`
- [ ] **Step 2: 실패 확인** — `uv run pytest shared/dashboard-api/tests/site_adapter/test_resources.py -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 커밋** — `feat(dashboard-api): 사이트 변환기 자원 변환(주문·상품·분류·고객·판매 합계)`

---

### Task 4: 고객사 API 호출 (인증·페이지·오류)

**Files:**
- Create: `.../site_adapter/upstream.py`
- Test: `tests/site_adapter/test_upstream.py`

**Interfaces:**
- Consumes: `SiteMapping`, `ResourceMap` (Task 2), `company_connection.read_connection()` (Task 1)
- Produces:
  - `TOKEN_ENV = "AX_SITE_API_TOKEN"`, `MAX_PAGES_ENV = "AX_SITE_ADAPTER_MAX_PAGES"`(기본 50), `TIMEOUT = 10.0`
  - `TRANSPORT: httpx.AsyncBaseTransport | None = None` (시험이 바꾼다, `company_connection` 라우트와 같은 방식)
  - `async def fetch_all(m: SiteMapping, res: ResourceMap, *, since: str | None = None) -> tuple[list[dict], bool]` → (원본 행들, truncated). `since`가 있으면 `paging.since_param`으로 보낸다. 커서가 `None`이 될 때까지 또는 최대 쪽 수까지 읽는다
  - `async def ping() -> bool` (고객사 base_url GET, 2xx면 True)
  - `class UpstreamError(Exception)`: `.status: int`, `.code: str` — 401·403 → `(503, "UPSTREAM_AUTH")`, 연결 실패·타임아웃·5xx → `(503, "UPSTREAM_UNAVAILABLE")`, 응답 JSON에 `items_field` 없음 → `(502, "MAPPING_BROKEN")`
  - 인증 헤더: `{m.auth_header: f"{m.auth_scheme} {token}".strip()}`, 토큰 없으면 헤더 없이 보낸다

- [ ] **Step 1: 시험** (`httpx.MockTransport`)
  - `test_reads_two_pages`: p1·p2 녹화 → 3행, `truncated is False`, 두 번째 요청에 `cursor` 쿼리
  - `test_sends_since_and_token`: 요청 헤더 `Authorization == "Bearer tok"`, 쿼리 `updated_since`
  - `test_stops_at_max_pages` (Review Focus 1): 늘 `next_cursor`를 주는 응답 + `AX_SITE_ADAPTER_MAX_PAGES=3` → 요청 3번, `truncated is True`
  - `test_auth_error_hides_token` (Review Focus 2): 401 → `UpstreamError(503, "UPSTREAM_AUTH")`, `str(err)`에 `"tok"` 없음
  - `test_connect_error_is_unavailable`
- [ ] **Step 2: 실패 확인** — `uv run pytest shared/dashboard-api/tests/site_adapter/test_upstream.py -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 커밋** — `feat(dashboard-api): 사이트 변환기 고객사 API 호출(인증·페이지·오류)`

---

### Task 5: 라우터 (① 변환 주소, ③ 빈 응답, 동기화 기록, 그 밖 404)

**Files:**
- Create: `.../site_adapter/bookkeeping.py`, `.../site_adapter/router.py`
- Modify: `backend/shared/dashboard-api/src/ax_dashboard_api/main.py` (`app.include_router(site_adapter.router)`)
- Modify: `backend/shared/dashboard-api/README.md` (변환기 절: 켜는 법 `kind=adapter`, 짝 파일 위치, `AX_SITE_API_TOKEN`, 주소 표)
- Test: `tests/site_adapter/test_router.py`

**Interfaces:**
- Consumes: Task 2~4 전부
- Produces: `router = APIRouter(prefix="/site-adapter")`
  - ① `GET /api/shop/orders` (`since`,`until`,`limit`,`cursor`,`buyer_id`): `fetch_all(orders, since=since)` → `to_order` → `created_at`이 `[since, until)`이고 `buyer_id` 맞는 것 → `created_at, order_id` 순 정렬 → `limit`(기본 100, 최대 1000)씩 자르고 `cursor`는 다음 시작 위치 정수 문자열. 응답 `{"data": [...], "meta": {"next_cursor": str|None, "source": "site_adapter", "truncated": bool}}`
  - ① `GET /api/shop/orders/{order_id}`: 없으면 404 `{"error": {"code": "NOT_FOUND", ...}}`
  - ① `GET /api/shop/products`, `/api/shop/categories`, `/api/shop/users`, `/api/shop/sales-summary?from&to&group_by=product`, `/api/shop/sales-export`, `/api/shop/customers-export` (export 둘은 `data` 목록 + `meta.next_cursor: null`)
  - ① `GET /health` → `{"data": {"status": "ok"|"degraded"}}`(`ping()` 결과)
  - ③ 빈 응답 (GET): `/api/shop/metric-series` → `{"data": {"points": []}}`; `/api/shop/failed-orders`·`/api/shop/orders/attributed` → `{"data": {"orders": [], "truncated": false}}`; `/api/shop/sellers`·`/api/shop/coupons`·`/api/shop/banners`·`/api/shop/admin/intake/stuck`·`/api/shop/admin/feedback`·`/api/shop/admin/ideas`·`/api/shop/admin/returns`·`/api/shop/admin/orchestrator/{rest:path}`·`/api/shop/admin/marketing/{rest:path}` → `{"data": []}`; `/api/shop/sellers/{sid}/profile`·`/api/shop/admin/faults`·`/api/shop/admin/payment-version` → `{"data": {}}`. 모두 `meta = {"source": "site_adapter", "total": 0}` (목록형만 `total`)
  - 동기화 기록 (POST): `/api/shop/admin/inquiries/{seq}/{action}` (`synced|failed|view|feedback-synced|gone`), `/api/shop/admin/inquiries/by-support/{sid}/gone`, `/api/shop/admin/claims/{seq}/failed`, `/api/shop/admin/ideas/{seq}/synced`, `/api/shop/admin/returns/{cid}/synced`, `/api/shop/admin/{kind}/{seq}/retry` → `bookkeeping.record(kind, key, action, body)` 후 `{"data": {"ok": true}}`
  - 그 밖 모든 경로·메서드: 404 `{"error": {"code": "NOT_MAPPED", "message": "변환기가 아직 받지 않는 주소예요: <METHOD> <path>"}}` + 경로별 경고 로그 한 번
  - 짝 파일이 없으면 ①은 503 `{"error": {"code": "NOT_CONFIGURED"}}`, `MappingBroken` → 502 `MAPPING_BROKEN`, `UpstreamError` → 그 상태·코드
  - 쓰기(동기화 기록)는 에이전트 토큰 필요: dashboard-api의 기존 에이전트 토큰 확인 방식을 쓴다(없으면 401)
- `bookkeeping.record(kind: str, key: str, action: str, body: dict) -> None`: `<AX_SITE_ADAPTER_DIR>/bookkeeping.jsonl`에 한 줄(시각·kind·key·action, `body`는 키 이름만)

- [ ] **Step 1: 시험** (`TestClient(app)`, `upstream.TRANSPORT`를 녹화 MockTransport로)
  - `test_orders_filtered_and_paged`: `since`·`until`로 2건, `limit=1` → `next_cursor == "1"`
  - `test_order_detail_not_found`
  - `test_products_and_categories`
  - `test_empty_endpoints_return_shapes`: 위 ③ 목록을 매개변수로 돌려 200·`meta.source == "site_adapter"`
  - `test_sync_write_records`: POST `/site-adapter/api/shop/admin/inquiries/7/synced` → 200, `bookkeeping.jsonl`에 한 줄
  - `test_unmapped_is_404`: `POST /site-adapter/api/shop/coupons` → 404 `NOT_MAPPED` (② 다음 PR)
  - `test_not_configured_is_503`: 짝 파일 없음
  - `test_upstream_auth_maps_503`
  - 끝까지: `test_data_connector_reads_through_adapter` — `kind=adapter` 연결 파일 + `AX_DASHBOARD_API_URL`을 TestClient로 돌리는 transport로 놓고 `agent_url("shop-api")`가 `/site-adapter`로 끝나는지만 본다(도메인 import 금지 규칙 때문에 도메인 클라이언트는 부르지 않는다)
- [ ] **Step 2: 실패 확인** — `uv run pytest shared/dashboard-api/tests/site_adapter -q`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `uv run pytest shared/agent-template shared/dashboard-api -q` 전체 PASS, `uv run ruff check shared/dashboard-api shared/agent-template`
- [ ] **Step 5: 커밋** — `feat(dashboard-api): 사이트 변환기 라우터(읽기 변환·빈 응답·접수 동기화 기록)`
- [ ] **Step 6: PR** — `git push origin feat/site-adapter-reads` 한 줄, `gh pr create --body-file`, `gh pr merge <n> --auto --squash` 한 줄
