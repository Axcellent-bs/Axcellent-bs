# 사이트 변환기 (다른 형식 API·플랫폼 사이트 연결) 설계

2026-11-18, 예진 님과 정함. 회사 연결(`/system/connect`)의 "준비 중"을 정리한다.

## 배경과 목표

- 지금 AI들은 고객사 사이트를 **AXcellent 규격(shop-api)** 으로만 읽고 쓴다. 규격을 그대로 쓰는 사이트(캣북스)만 연결된다.
- AXcellent를 모르는 사이트(가상 고객사 2호 누비투어, `Catbooks-demo/nubi-tour` `/api/v1`)도 붙여야 한다. 사이트 코드는 고치지 않는다.
- 서비스는 모두 필수다(#1273). 연결 방식 때문에 AI마다 "건너뛰는 흐름"이 생기지 않게 한다.

## 정한 것 (예진 님)

1. 회사 연결의 **읽기 전용 DB**는 없앤다(고객 DB 계정 보관·사내망 개방 부담).
2. **다른 형식의 API**를 만든다. 범위는 **읽기만 변환**하고, AI가 사이트에 쓰려는 일은 **안전 관문 승인 뒤 사람 할 일("사이트에 직접 반영")** 로 보낸다.
3. **플랫폼으로 만든 사이트**도 연다. 플랫폼 관리 API 주소와 발급 토큰을 넣으면 같은 변환기로 연결한다. 플랫폼 앱 등록·OAuth·웹훅 자동 등록은 사업자 등록이 필요해 계속 뺀다(11/16 보류 유지).
4. 변환기 위치는 새 서버가 아니라 **dashboard-api 안**(컨테이너 줄이기 작업과 부딪히지 않게).

## 방법 비교

- **A. 변환기 (고름)**: shop-api와 같은 모양으로 답하는 변환기를 두고, 연결 주소가 변환기를 가리키게 한다. AI 코드는 거의 그대로다.
- B. AI마다 변환: 데이터·마케팅·고객지원·재무·인프라 클라이언트마다 갈래가 생긴다.
- C. 주기 복사: 고객 데이터 복사본이 하나 더 생기고 늦다.

## 구조

```
AI들 ── agent_url("shop-api") ──▶ (kind=shop_api) 고객사 shop-api            [지금 그대로]
                               └▶ (kind=adapter)  dashboard-api /site-adapter ──▶ 고객사 자기 API
                                                     │  짝(mapping)대로 바꿔 읽기
                                                     │  쓰기 → 사람 할 일함(handoffs)
                                                     └  사이트에 없는 자료 → 빈 정상 응답
```

- 회사 연결 파일(`company_connection.py`)에 **추가 필드** `kind`(`shop_api` 기본 | `adapter`)를 둔다. 없으면 `shop_api`(예전 파일 그대로).
- `kind=adapter`면 `agent_url("shop-api")`는 `http://<dashboard-api>/site-adapter`를 준다. 고객사 API 주소는 변환기만 안다.
- 고객사 API 주소는 기존 주소 정책(`check_host`)을 그대로 지난다.
- 인증 값(헤더 이름 + 키·토큰)은 **도구 연결 보관함**(`ax_agent_template.tool_connections`)에 새 종류 `site_api`로 넣는다. 응답·로그·오류 문구에 비밀을 넣지 않는다.
- 변환기 상태(짝, 접수 동기화 기록, 반영 대기)는 `AX_SITE_ADAPTER_DIR`(기본 `backend/.data/site_adapter/`)의 JSON 파일에 둔다.

## 변환기가 받는 shop-api 주소 (11/18 전수 조사 기준)

AI가 부르는 주소 약 45개를 셋으로 나눈다. 목록에 없는 주소는 404 `NOT_MAPPED`로 답하고 경고 로그를 한 번 남긴다(새 호출을 놓치지 않게).

### ① 짝 맞춰 읽기 (고객사 API를 불러 바꾼다)

| shop-api | 짝 자원 | 비고 |
|---|---|---|
| `GET /api/shop/products` | 상품 | `product_id`·`name`·`price`·`category_code` |
| `GET /api/shop/categories` | 분류 | 상품의 분류 값에서 만들 수도 있다 |
| `GET /api/shop/users` | 고객 | 사이트에 고객 목록이 없으면 주문의 고객 id로 만든다. 가린 이름은 그대로 |
| `GET /api/shop/orders` (`since`·`until`·`limit`·`cursor`·`buyer_id`) | 주문 | 고객사 페이지 방식(커서·`updated_since`·쪽 번호)을 짝에 적는다 |
| `GET /api/shop/orders/{id}` | 주문 하나 | 결제·환불 목록을 같이 짝 |
| `GET /api/shop/sales-summary`, `/sales-export` | 주문에서 계산 | 상품별 수량·금액 합 |
| `GET /api/shop/customers-export` | 고객 | |
| `GET /api/shop/admin/inquiries` (`state=new`), `/admin/inquiries/open` | 문의 | 고객지원 접수함. 상태 값 짝(`open`→`new` 등) |
| `GET /api/shop/admin/claims` | 취소·환불 신청 | 사이트에 있으면 짝, 없으면 ③ |
| `GET /health` | 고객사 API 상태 | 짝 확인용 |

상태 값은 짝에 **값 표**로 적는다(예: 누비투어 `deposit_paid`·`fully_paid` → 결제 완료, `cancelled`·`expired` → 취소). 금액·시각 형식(원 단위 정수, ISO 시각)은 변환기가 맞춘다.

### ② 사람 반영 (쓰기)

| shop-api | 할 일함에 넣는 일 |
|---|---|
| `POST /api/shop/payments/{id}/refund` | "사이트에서 환불 처리" (금액·주문·근거) |
| `POST /api/shop/coupons` | "사이트에 쿠폰 발급" |
| `POST /api/shop/banners`, `DELETE /api/shop/banners/{id}` | "사이트 공지·배너 게시/내림" (문구·기간) |
| `POST .../admin/claims/{seq}/result` | "신청 처리 결과를 사이트에 반영" |
| `DELETE /api/shop/admin/faults`, `POST .../payment-version/rollback` | "사이트 운영자에게 조치 요청" |

- 이 요청들은 AI 쪽에서 이미 안전 관문(`execute()`) 승인을 지난 뒤 온다(조사 확인). 변환기는 승인을 다시 묻지 않는다.
- 변환기는 `HandoffCreate`(to_service = 보낸 AI, skill = `site_apply_<동작>`, `idempotency_key` = 요청의 멱등 키)로 넣고, shop-api 성공 모양으로 답하며 **추가 필드** `applied: "pending_human"`, `handoff_task_id`를 싣는다. 쿠폰은 `coupon_id`를 할 일 id로 만든다(AI가 `coupon_id`를 필수로 본다).
- 같은 멱등 키로 다시 오면 같은 할 일을 돌려준다(할 일함 규칙).
- 화면은 `applied`가 있으면 "사이트 반영 대기"로 보여 준다(각 결과 화면에 칩 하나, 별도 흐름 없음).

### ③ 사이트에 없는 자료 (빈 정상 응답)

`metric-series`·`failed-orders`·`orders/attributed`·`sellers`·`sellers/{id}/*`·`coupons`(목록)·`banners`(목록)·`admin/faults`(GET)·`admin/payment-version`(GET)·`admin/orchestrator/*`·`admin/marketing/*`·`admin/intake/stuck`·`admin/feedback`·`admin/ideas`·`admin/returns`

- 각 주소의 응답 모양 그대로 **빈 목록·빈 값**을 준다(예: `{"data": [], "meta": {"next_cursor": null}}`). `meta.source = "site_adapter"`를 추가로 싣는다.
- AI들은 지금도 "자료 없음"을 처리한다. 숫자를 지어내지 않는다.
- 고객사 API에 해당 자료가 생기면 짝을 더해 ①로 옮긴다.

### 접수 동기화 기록 (고객지원 내부 장부)

`POST .../admin/inquiries/{seq}/synced`·`failed`·`view`·`feedback-synced`·`gone`, `.../by-support/{id}/gone`, `.../claims/{seq}/failed`, `.../ideas/{seq}/synced`, `.../returns/{id}/synced`, `.../{kind}/{seq}/retry`

- 사이트가 아니라 AXcellent 쪽 기록이다. 변환기 저장소에 상태만 남기고 200으로 답한다. `GET .../admin/inquiries?state=new`는 이미 `synced`로 기록된 문의를 빼고 준다.
- 사람 할 일로 보내지 않는다(사람이 할 일이 아니다).

### 들어오는 이벤트

고객사 사이트는 `POST /api/dashboard/events`를 보내지 않는다. 변환기는 이벤트를 만들지 않는다(이번 범위 밖). 이벤트로 움직이던 일은 각 AI의 주기 읽기(문의 접수 등)로 돈다. 필요해지면 `updated_since` 폴링으로 이벤트를 만드는 것을 따로 정한다.

## 연결 순서 (회사 연결 화면)

1. **사이트 종류**: "직접 만든 사이트" / "플랫폼으로 만든 사이트"(준비 중 표시 없앰).
2. **연결 방법**: "AXcellent 규격 API"(지금 그대로) / "다른 형식의 API". 읽기 전용 DB 칸은 지운다. 플랫폼 사이트는 "다른 형식의 API"만 보인다(안내 문구: 플랫폼 관리자 화면에서 API 토큰을 발급해 넣기).
3. **주소·인증**: API 주소, 인증 방식(Bearer / 헤더 이름 + 값). 저장 전 확인: 주소 정책, 응답 받기.
4. **둘러보기**: 변환기가 `openapi.json`(있으면)과 GET 목록 주소의 첫 쪽을 받아 본다. 표본은 저장하지 않고 짝 제안에만 쓴다. 개인정보는 가린 값만 쓴다(고객사 API가 가려 주는 범위 그대로).
5. **짝 제안**: 표본으로 자원별 짝(주소, 페이지 방식, 항목 경로, 상태 값 표)을 제안한다.
   - 이름이 같은 항목은 규칙으로 바로 짝짓고, 나머지는 LLM이 제안한다(모델 이름은 설정에서, 하드코딩 금지). 제안에는 근거(표본 값)를 같이 보여 준다.
   - 사람은 자원마다 **확인** 또는 고치기만 한다. 주문·상품 짝이 확인돼야 연결을 마칠 수 있다.
6. **연결 확인**: `run_checks`가 `kind=adapter`면 "짝 맞춘 자원을 실제로 읽어 변환이 되나"(주문·상품 필수, 나머지 경고)를 본다.
7. 저장하면 `kind=adapter`로 바뀌고 AI들이 다음 호출부터 변환기를 쓴다(재시작 없음, 지금 연결 파일 규칙 그대로).

## 오류

- 고객사 API가 응답하지 않거나 5xx: 변환기는 503 `UPSTREAM_UNAVAILABLE`(shop-api 오류 모양 `{error:{code,message}}`). AI들의 기존 `UNAVAILABLE` 처리로 간다.
- 짝이 깨짐(필수 항목 없음): 502 `MAPPING_BROKEN` + 자원·항목 이름. 회사 연결 화면 연결 지도에 "변환 오류"로 보인다.
- 인증 실패(401·403): 503 `UPSTREAM_AUTH`, 회사 연결 화면에 "키 다시 넣기".

## 시험

- 단위: 누비투어 응답 녹화(시드 데이터로 만든 것, 개인정보 가림)로 ①②③ 주소별 변환 결과·오류 시험. 변환기는 고친 도메인 테스트만 로컬에서 돌린다.
- 계약: 변환 결과를 기존 contracts 모델(`ShopOrder` 등)로 검증한다.
- 실연결: 251에서 누비투어 `/api/v1`(읽기 키)에 붙여 데이터분석·재무·고객지원 화면이 숫자를 보이는지, 쿠폰·환불 요청이 할 일함에 "사이트에 직접 반영"으로 쌓이는지 본다. 누비투어 251 배포는 catbooks-demo 세션 몫.

## 하지 않을 것 (이번)

- 고객사 사이트에 직접 쓰기(문의 답변 등 사이트 API에 있는 쓰기도 이번엔 사람 할 일)
- 읽기 전용 DB 연결, 플랫폼 앱 등록·OAuth·웹훅, 플랫폼별 기본 짝(프리셋)
- 들어오는 이벤트 만들기, 업종별 용어 바꾸기(예약 ↔ 주문 화면 문구)

## PR 나눔

1. 연결 파일 `kind`, 변환기 뼈대와 ① 읽기 변환(짝 파일을 손으로 써서 시험), ③ 빈 응답, 접수 동기화 기록
2. ② 사람 반영(할 일함 넣기, `applied` 응답, 화면 "사이트 반영 대기" 칩)
3. 둘러보기·짝 제안·확인 API와 회사 연결 화면(DB 칸 삭제, 플랫폼 열기, 짝 확인 단계)
4. 누비투어 실연결 확인과 문서
