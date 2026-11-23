# 사이트 변환기 (다른 형식 API·플랫폼 사이트 연결) 설계

2026-11-18, 예진 님과 정함. 회사 연결(`/system/connect`)의 "준비 중"을 정리한다.

## 배경과 목표

- 지금 AI들은 고객사 사이트를 **AXcellent 규격(shop-api)** 으로만 읽고 쓴다. 규격을 그대로 쓰는 사이트(캣북스)만 연결된다.
- AXcellent를 모르는 사이트(가상 고객사 2호 누비투어, `Catbooks-demo/nubi-tour` `/api/v1`)도 붙여야 한다. 사이트 코드는 고치지 않는다.
- 서비스는 모두 필수다(#1273). 연결 방식 때문에 AI마다 "건너뛰는 흐름"이 생기지 않게 한다.

## 정한 것 (예진 님)

1. 회사 연결의 **읽기 전용 DB**는 없앤다(고객 DB 계정 보관·사내망 개방 부담).
2. **다른 형식의 API**를 만든다. 범위는 **읽기만 변환**하고, AI가 사이트에 쓰려는 일은 **안전 관문 승인 뒤 사람 할 일("사이트에 직접 반영")** 로 보낸다.
   - (11/19 바뀜 → **③-쓰기로 대체**) 고객사 API에 쓰기 경로가 있고 키에 쓰기 권한이 있으면 변환기가 짝대로 **직접 쓴다**. 없으면 위처럼 사람 할 일. 안전 관문은 그대로다.
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
| `GET /api/shop/admin/claims` | 취소·환불 신청 | 사이트에 있으면 짝(`inboxes.claims`), 없으면 404 `NOT_MAPPED`. (11/23 W5) 누비투어 `GET /api/v1/cancellations` 짝 구현 |
| `GET /health` | 고객사 API 상태 | 짝 확인용 |

상태 값은 짝에 **값 표**로 적는다(예: 누비투어 `deposit_paid`·`fully_paid` → 결제 완료, `cancelled`·`expired` → 취소). 금액·시각 형식(원 단위 정수, ISO 시각)은 변환기가 맞춘다.

### ② 사람 반영 (쓰기)

> 11/19: 직접 쓸 수 있는 쓰기는 **③-쓰기**가 먼저 판정한다. ②는 ③-쓰기에서 "할 일함"으로 판정된 쓰기의 처리 방법으로 남는다.

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

### ③-쓰기 (직접 쓰기, 11/19 추가)

11/19 예진 님 결정: 고객사는 백엔드 주소만 열어 주고 읽기·쓰기를 허락한다. 고객사는 아무것도 먼저 보내지 않고, AXcellent가 그 주소로 직접 읽고 쓴다. 데이터는 고객사 DB에 남고 AXcellent는 틀만 준다(고객사마다 독립 공간). 그래서 고객사 API에 쓰기 경로가 있으면 변환기가 짝대로 **실제로 쓴다**. 돈이 걸린 일의 사람 승인과 안전 등급 규칙(safety, `execute()` 관문)은 지금 그대로다.

#### 지금 상태 (11/19 코드 확인)

- 변환기(`site_adapter/router.py`)는 접수 동기화 기록만 받고, 나머지 쓰기(`POST /api/shop/admin/{kind}/{seq}/{action}` 중 기록이 아닌 것, 환불·쿠폰·배너)는 404 `NOT_MAPPED`다. ② 사람 반영도 아직 구현 전이다(PR 나눔 2번).
- 짝 파일(`mapping.py`, `<AX_SITE_ADAPTER_DIR>/mapping.json`)은 `version: 1`, `auth_header`·`auth_scheme`, `resources`(`orders`·`products`만; `path`·`paging`·`fields`·`values`·`items`)뿐이다. 쓰기 항목은 없다. 항목 식은 점 경로·`sum:`·`const:`·`first:`.

#### 판매 서버 쓰기를 부르는 곳과 안전 관문

AI가 shop-api(→ 변환기)에 보내는 쓰기는 아래가 전부다(`backend/domains` grep, 시험·평가 코드 제외).

| shop-api 쓰기 | 부르는 곳 | 관문 | 근거 |
|---|---|---|---|
| `POST /payments/{id}/refund` (`refund_amount`·`idempotency_key`·`context_id`) | 고객지원 `shop_client.refund` | `execute("refund_request")`가 `executed`일 때만 | `support_agent/claim_handlers.py` 536행 `execute` → 571행 `!= "executed"`면 보류 → 591행 `refund` |
| `POST /coupons` (`idempotency_key`·`purpose_code`·`buyer_id`·`count`·`unit_value`·`campaign_id`·`context_id`) | 마케팅 `shop_client.issue_coupon` | `execute("issue_coupon")` | `marketing_agent/handlers.py` 1037행 → 1056행 |
| `POST /banners` (`notice_id`·`text`·`valid_from`·`valid_to`·`context_id`) | 마케팅 `post_banner` | `execute("publish_notice")` | 같은 파일 334행 → 389행 |
| `DELETE /banners/{notice_id}` | 마케팅 `end_banner` | `execute("publish_notice")`(정상화 공지가 앞 배너를 내림), `execute("end_incident_notice")` | 334행 → 439행, 495행 → 524행 |
| `POST /admin/payment-version/rollback` (`to_version`) | 인프라 `rollback_payment_version` | `execute(<롤백>)`이 `executed`일 때만 | `infra_agent/handlers.py` 254행 → 288·310행 |
| `DELETE /admin/faults` | 인프라 `release_fault` | `execute("release_fault")` 또는 롤백 실행 뒤 | 같은 파일 598행 → 604행, 325행 |
| `POST /admin/claims/{seq}/result` (`support_claim_id`·`status`·`result`) | 고객지원 `write_claim_result` | **관문 없음** (판정 결과를 고객 화면에 옮김) | `support_agent/inquiry_pull.py` 246·252·277행 |
| `POST /admin/inquiries/{seq}/view` (`view`·`view_digest`) | 고객지원 `write_inquiry_view` | **관문 없음** (고객이 볼 문의 상태·답변을 옮김) | `support_agent/shop_pull_more.py` 145행 |
| 접수 동기화 기록(`synced`·`failed`·`gone`·`retry` 등) | 고객지원 | 관문 없음 | 사이트가 아니라 AXcellent 장부라 변환기에만 기록(지금 그대로) |

정리: **돈·사이트 상태가 바뀌는 쓰기(환불·쿠폰·배너·롤백·장애 해제)는 모두 부르는 도메인의 `execute()`가 `executed`를 낸 뒤에만 변환기에 온다.** 관문이 없는 둘(신청 결과·문의 화면 값)은 AXcellent가 이미 정한 결과를 고객 화면에 옮기는 일이고, 캣북스(shop-api)에서도 지금 관문 없이 쓴다.

#### 변환기는 관문을 대신하지 않는다 — 어떻게 막나

변환기는 승인을 다시 묻지도, 대신 내리지도 않는다. 다만 변환기 주소는 에이전트 토큰만 있으면 부를 수 있어, "관문을 거쳤다"는 것을 호출하는 쪽 말만 믿지 않도록 세 겹으로 막는다.

1. **보낸 서비스 확인**: 쓰기는 에이전트 토큰(`verify_token`)이 가리키는 서비스만 받는다(사람 토큰·판매 서버 서비스 키는 403). 동작마다 허용 서비스를 고정한다: 환불·신청 결과·문의 답변 = `support`, 쿠폰·배너 = `marketing`, 롤백·장애 해제 = `infra`.
2. **돈이 걸린 일은 관문 기록을 확인**: 환불·쿠폰은 요청 본문의 `context_id`·`idempotency_key`로 safety 실행 기록을 찾아 `action`이 맞고(`refund_request`·`issue_coupon`) `decision.status == "executed"`이며 금액(`refund_amount`, `count × unit_value`)이 기록의 `params`와 같을 때만 쓴다. 없거나 다르면 고객사에 쓰지 않고 403 `GATE_NOT_PASSED`(경고 로그). safety에는 지금 `GET /api/safety/executions?context_id=`가 있다. 실행 하나를 바로 찾는 `GET /api/safety/executions/{execution_id}`를 **추가**하면 더 좋다(계약은 추가만).
3. **본문에 근거가 없는 쓰기는 직접 쓰지 않는다**: 롤백(`to_version`만)·장애 해제(본문 없음)는 2번 확인을 할 수 없다. 이 둘은 도메인 쪽 클라이언트가 `context_id`를 함께 보내도록 바꾸기 전까지 **할 일함**으로 보낸다(고객사 사이트에 해당 경로도 아직 없다).

#### 판정 표 (직접 쓰기 / 할 일함)

변환기는 쓰기 요청마다 아래를 위에서부터 본다. 하나라도 걸리면 그 줄의 결과로 끝난다.

| 순서 | 조건 | 결과 |
|---|---|---|
| 1 | 보낸 서비스가 그 동작의 허용 서비스가 아님 | 403 `AGENT_NOT_ALLOWED` (할 일함에도 넣지 않음) |
| 2 | 돈이 걸린 동작인데 safety 기록 확인 실패 | 403 `GATE_NOT_PASSED` (할 일함에도 넣지 않음) |
| 3 | 짝 파일에 그 동작의 `writes` 항목 없음 (고객사에 경로가 없음 포함) | **할 일함** (② 그대로) |
| 4 | 회사 연결에서 그 동작의 "직접 쓰기"를 꺼 둠 (기본 꺼짐) | **할 일함** |
| 5 | 키에 쓰기 권한이 없다고 기록됨 (앞서 고객사가 403을 줌) | **할 일함** + 회사 연결 화면 "키에 쓰기 권한 없음" |
| 6 | 같은 멱등 키로 이미 보냈음 | 앞 결과를 그대로 (아래 멱등) |
| 7 | 모두 통과 | **직접 쓰기** |

직접 쓰기 결과:

| 고객사 응답 | 변환기 처리 | AI가 받는 것 |
|---|---|---|
| 2xx | 기록 `done` | shop-api 성공 모양 + `applied: "site"` |
| 400·404·409·422 (입력·상태 거절) | 기록 `rejected`, 재시도 안 함 | 409 `SITE_REJECTED` + 고객사 오류 코드(`error.site_code`). AI는 지금의 쓰기 실패 처리(보류·실패)로 간다 |
| 401·403 | 기록 `rejected`, 그 동작을 "키에 쓰기 권한 없음"으로 표시 | 할 일함에 넣고 ② 모양(`applied: "pending_human"`) |
| 짝 `partial_codes`에 든 오류 코드 (W5, 예: 누비투어 502 `REFUND_FAILED`) | 기록 `partial`, 재시도 안 함 (반영은 됐다) | 할 일함 "결제사 환불 확인"(환불) / "뒤따르는 처리 확인"(그 밖), ② 모양 |
| 429·5xx·시간 초과·연결 끊김 | 기록 `unknown`, **재시도 안 함** | 할 일함 "사이트에 반영됐는지 확인 후 처리"(멱등 키·보낸 본문 요약을 담음), ② 모양 |

- 시간 초과에 다시 보내지 않는 이유: 고객사 API는 멱등 키를 받지 않아(누비투어·스터디온에듀 모두) 이미 반영됐는지 알 수 없다. 사람이 사이트에서 보고 처리한다.
- 할 일함으로 간 일은 ②와 같이 `to_service` = 보낸 AI, `skill = site_apply_<동작>`, `idempotency_key` = 요청의 멱등 키로 넣는다.

#### 멱등 (같은 요청 두 번 → 고객사에 한 번)

- 변환기 저장소(`AX_SITE_ADAPTER_DIR`)에 **쓰기 장부** `writes.json`을 둔다. 키 = `(보낸 서비스, 동작, 멱등 키)`, 값 = 본문 지문(sha256)·상태(`sending`·`done`·`rejected`·`unknown`·`handoff`)·고객사 응답 요약·할 일 id.
- 동작별 멱등 키: 환불·쿠폰 = 본문 `idempotency_key`, 배너 게시·내림 = `notice_id`, 신청 결과 = `seq` + 결과 지문, 문의 답변 = `seq` + `view_digest`.
- 같은 키·같은 지문: `done`이면 저장한 응답을 그대로(`meta.replayed: true`), `sending`·`unknown`이면 다시 보내지 않고 같은 할 일을 돌려준다. 같은 키·다른 지문: 409 `IDEMPOTENCY_CONFLICT`.
- 장부에 `sending`을 먼저 쓰고(파일 잠금) 고객사에 보낸다. 변환기가 보내는 도중 죽으면 `sending`이 남고, 다음 요청은 `unknown`처럼 할 일함으로 간다(두 번 쓰지 않는 쪽을 고른다).
- 고객사 쪽 자연 멱등도 같이 쓴다: 문의 답변은 다시 보내면 고쳐 쓰기라 안전, 스터디온에듀 쿠폰은 멱등 키로 만든 쿠폰 코드가 겹치면 409 `DUPLICATE_CODE` → 장부에 없을 때만 "이미 있음"으로 보고 할 일함(내용이 같은지 읽을 길이 없다).

#### 기록

- 쓰기 장부 한 줄: 시각, 보낸 서비스, 동작, shop-api 경로, 고객사 메서드·경로(주소의 호스트만, 키 없음), `context_id`, 멱등 키, safety `execution_id`(확인한 경우), 본문 지문, 고객사 응답 코드·`x-request-id`(있으면)·오류 코드, 결과(`done`·`rejected`·`unknown`·`handoff`), 할 일 id.
- 본문 원문은 남기지 않는다(문의 답변 등 개인정보). 할 일함에 넣는 일에만 사람이 처리할 만큼 담는다.
- 키 값은 어디에도 넣지 않는다. 고객사 감사 기록에는 `api:<키 이름>`으로 남으므로 회사 연결 안내에 "키 이름을 `AXcellent`로 발급"을 적는다.
- 회사 연결 화면에 "최근 사이트 쓰기"(최근 50건, 결과별 칩)를 보인다. 각 AI 결과 화면은 `applied`가 `site`면 "사이트에 반영됨", `pending_human`이면 "사이트 반영 대기".

#### 짝 파일 쓰기 항목 모양

`SiteMapping`에 **추가 필드** `writes`(동작 → 쓰기 짝)를 둔다. 없으면 모든 쓰기가 할 일함(예전 파일 그대로 읽힘).

```json
"writes": {
  "<동작>": {
    "method": "POST",                       // POST | PUT | PATCH | DELETE
    "path": "/api/v1/inquiries/{site_id}/answer",  // ResourceMap.path와 같은 검사(/로 시작, @ ? # // 금지)
    "path_params": { "site_id": "link:inquiries.seq" },
    "body": { "answer": "view.answer" },    // 값은 짝 식 (요청 본문 기준)
    "ok": [200, 201],
    "result": { "coupon_id": "coupon.id" }  // 고객사 응답 → shop-api 응답 칸 (필요한 동작만)
  }
}
```

- 동작 이름: `refund`·`coupon_issue`·`banner_post`·`banner_end`·`claim_result`·`inquiry_answer`·`payment_rollback`·`fault_release`. shop-api 경로 → 동작은 변환기 코드에 고정한다(짝 파일이 정하지 않는다).
- 본문 식은 지금 식(점 경로·`const:`·`sum:`·`first:`)에 셋을 더한다: `path:<이름>`(shop-api 경로 값), `key_code:<접두>`(멱등 키로 만든 고정 코드, `[A-Z0-9_-]` 30자 이하), `now_plus:<일>`(ISO 시각). 계산은 코드로만, LLM이 본문을 만들지 않는다.
- `link:<자원>.<칸>`: shop-api 번호(문의 `seq`·배너 `notice_id`)와 고객사 id의 짝. 읽기 변환(① 문의·신청)이 번호를 줄 때 변환기 장부에 같이 적어 둔 것을 찾는다. 없으면 할 일함.
- `const:` 식이 `true`·숫자로 보내야 하는 칸은 `json:` 접두로 쓴다(예: `json:true`, `json:["web"]`).
- (W5 추가) `partial_codes`: "쓰기는 반영됐고 뒤따르는 일만 실패"를 뜻하는 고객사 오류 코드 목록. 이 코드면 `unknown`이 아니라 `partial`로 적는다(위 결과 표).

#### 누비투어 쓰기 짝 (실제 경로 근거)

근거: `Catbooks-demo/nubi-tour` `src/app/api/v1/**/route.ts`, 키 범위 `src/server/apikeys.ts`(`read`·`read_pii`·`write`, write 설명 "문의 답변·예약 메모·출발일 상태"). 쓰기 경로: `POST /api/v1/inquiries/{id}/answer`(`{answer}` 1~5000자, 다시 보내면 고침), `POST /api/v1/bookings/{code}/memo`, `POST /api/v1/departures/{id}/status`(`open`·`confirmed`·`closed`; 출발 취소는 환불이 따라와 직원 화면에서만), (11/23, nubi-tour #16 `c38542c`) `POST /api/v1/cancellations/{id}/decision`, `GET /api/v1/me`.

취소 결정 (`decision`): 본문 `{decision: "approve"|"reject", refund_amount?: 원 정수 0 이상, reason?: 500자까지}`. approve에 `refund_amount`를 빼면 사이트 규칙 수수료대로, 넣으면 수수료 = 받은 금액 − 환불 금액이고 그것이 규칙 수수료와 다르면 `reason` 필수(400 `REASON_REQUIRED`), 받은 금액보다 많으면 400 `BAD_REFUND`. reject는 `reason` 필수. 200 `{id, booking_code, status: done|rejected, fee, refund_amount, decided_at, replayed}`. 같은 결정 재전송은 200 `replayed: true`, 다른 결정·금액은 409 `ALREADY_DECIDED`, 취소 요청 상태가 아니면 409 `BAD_STATE`, 없는 id 404, 결정은 반영됐는데 결제사 환불만 실패하면 502 `REFUND_FAILED`. `Idempotency-Key` 헤더는 없다(결정 자체가 멱등).

```json
"inboxes": {
  "claims": {
    "path": "/api/v1/cancellations",
    "fields": {
      "claim_id": "id", "order_id": "booking_code", "status": "status",
      "type": "const:cancel", "reason": "const:other", "description": "reason",
      "buyer_id": "order.member_id", "customer_name": "order.contact.name",
      "created_at": "created_at",
      "site_refund_amount": "refund", "site_fee": "fee", "site_fee_rate": "fee_rate"
    },
    "values": { "status": { "requested": "requested", "done": "closed", "rejected": "closed" } }
  }
},
"writes": {
  "inquiry_answer": {
    "method": "POST",
    "path": "/api/v1/inquiries/{site_id}/answer",
    "path_params": { "site_id": "link:inquiries.seq" },
    "body": { "answer": "view.answer" },
    "ok": [200]
  },
  "refund": {
    "method": "POST",
    "path": "/api/v1/cancellations/{site_id}/decision",
    "path_params": { "site_id": "link:payments.payment_id" },
    "body": {
      "decision": "const:approve",
      "refund_amount": "refund_amount",
      "reason": "const:AXcellent 안전 관문에서 담당자가 승인한 환불 금액이에요"
    },
    "ok": [200],
    "partial_codes": ["REFUND_FAILED"]
  }
}
```

사이트 규칙 환불 (11/23 결정): 신청 짝의 선택 칸 `site_refund_amount`·`site_fee`·`site_fee_rate`(누비투어 `refund`·`fee`·`fee_rate`)를 받은함 줄에 옮긴다. 고객지원은 사이트 예상 환불액을 판정·환불 승인 금액으로 쓴다(책 쇼핑몰 규칙 = 받은 금액 전액이 아니라). 그래서 safety 실행 기록 `params.refund_amount` = 관문 대조 금액 = 결정 본문 `refund_amount` = 사이트 규칙 환불액이고, 누비투어 수수료(받은 금액 − 환불 금액)도 규칙 수수료 그대로다. 사이트 환불액이 받은 금액 기준 환불 가능액보다 크면 그 금액으로 막고 사람 검토(`SITE_REFUND_OVER_PAID`)로 넘긴다. 보류 카드 요약에 "사이트 규칙: 수수료율·수수료·환불"이 붙는다.

시험 고정 파일: `backend/shared/dashboard-api/tests/site_adapter/fixtures/nubi_inboxes.json`·`nubi_writes.json`·`nubi_cancellations_p1.json`·`_p2.json`.

| 동작 | 누비투어 | 판정 |
|---|---|---|
| `inquiry_answer` | 있음 | 직접 쓰기 (① 문의 읽기 짝이 먼저 있어야 함) |
| `refund` | 있음 (11/23 `POST /api/v1/cancellations/{id}/decision` approve) | **직접 쓰기** (관문 확인 뒤, 동작별 켜기. ① 신청 읽기가 결제 번호 → 신청 id 짝을 먼저 적어야 함, 없으면 할 일함) |
| `claim_result` | 거절(`reject`)은 같은 경로로 가능 | 할 일함 (W5 남은 일: 결과 쓰기는 상태가 바뀔 때마다 오므로 `rejected`일 때만 쓰는 조건이 짝에 필요) |
| `coupon_issue` | 없음 (쿠폰 기능 없음) | 할 일함 |
| `banner_post`·`banner_end` | 없음 (공지 관리는 직원 화면만) | 할 일함 |
| `payment_rollback`·`fault_release` | 없음 | 할 일함 |

예약 메모·출발일 상태는 지금 부르는 AI가 없어 짝을 만들지 않는다.

#### 스터디온에듀 짝 초안 (읽기·쓰기, 경로 근거)

근거: `Catbooks-demo/study-on-edu` `src/server/v1/openapi.ts`, `src/server/v1/serialize.ts`, `src/app/api/v1/**/route.ts`. 키 범위 `read`·`read_pii`·`write`·`metrics`(write 설명 "문의 답변·쿠폰·서비스 모드·장애 공지"). 목록은 모두 `{data, next_cursor}`, `updated_since`·`cursor`·`limit`(최대 200), 금액 원 정수, 시각 UTC ISO.

읽기(주문·상품은 `tests/site_adapter/test_order_items.py`의 짝과 같다):

```json
"resources": {
  "orders": {
    "path": "/api/v1/orders",
    "paging": { "items_field": "data", "next_field": "next_cursor", "cursor_param": "cursor",
                "since_param": "updated_since", "limit_param": "limit", "limit": 200 },
    "fields": { "order_id": "code", "status": "status", "created_at": "created_at",
                "updated_at": "updated_at", "buyer_id": "member.id",
                "total_amount": "payable", "refunded_amount": "refunded_amount" },
    "values": { "status": { "pending": "pending_payment", "paid": "paid",
                            "partially_refunded": "paid", "refunded": "refunded",
                            "cancelled": "cancelled" } },
    "items": { "path": "items",
               "fields": { "product_id": "first:course_code,bundle_code", "qty": "const:1",
                           "unit_price": "unit_price", "paid_amount": "paid_amount" } }
  },
  "products": {
    "path": "/api/v1/courses",
    "paging": { "items_field": "data", "next_field": "next_cursor", "cursor_param": "cursor",
                "since_param": "updated_since", "limit_param": "limit", "limit": 200 },
    "fields": { "product_id": "code", "name": "title", "price": "price",
                "category_code": "category", "active": "const:true" }
  }
}
```

- 다음에 더할 읽기 자원(지금 `Resource`는 `orders`·`products`뿐이라 추가 필드): 문의 `GET /api/v1/inquiries`(`status` `open`→`new`, `answered`; `id`·`member.id`·`title`·`body`·`answer`), 취소·환불 신청 `GET /api/v1/refunds`(`requested`→신청, `approved`·`rejected`·`done`; `order_code`·`order_item_id`·`amount`), 결제 `GET /api/v1/payments`(`kind` `charge`·`refund`, `refund_of`).
- `products.active`는 강의 `status == "published"`가 맞지만 지금 식에 비교가 없어 `const:true`로 둔다(값 표 `values.active`로 `published`→`true`를 넣을 수 있게 하는 것은 구현 때 정함).

쓰기:

```json
"writes": {
  "inquiry_answer": {
    "method": "POST",
    "path": "/api/v1/inquiries/{site_id}/answer",
    "path_params": { "site_id": "link:inquiries.seq" },
    "body": { "answer": "view.answer" },
    "ok": [200]
  },
  "coupon_issue": {
    "method": "POST",
    "path": "/api/v1/coupons",
    "body": { "code": "key_code:AX", "name": "purpose_code", "kind": "const:fixed",
              "value": "unit_value", "target": "const:all", "per_user_once": "json:true",
              "total_limit": "count", "starts_at": "now_plus:0",
              "ends_at": "first:expires_at,now_plus:30" },
    "ok": [201],
    "result": { "coupon_id": "coupon.code" }
  }
}
```

| 동작 | 스터디온에듀 | 판정 |
|---|---|---|
| `inquiry_answer` | `POST /api/v1/inquiries/{id}/answer` | 직접 쓰기 |
| `coupon_issue` | `POST /api/v1/coupons` (코드형. 칸: `code`·`kind`(`fixed`·`percent`)·`value`·`target`(`all`·`category`·`course`·`instructor`)·`per_user_once`·`total_limit`·`starts_at`·`ends_at`·`private`(11/20 추가: `true`면 받은 회원만 1인 1회, 아니면 403 `COUPON_NOT_YOURS`)) → `POST /api/v1/coupons/{code}/grants` 보냄 `{member_ids:[...], message?:{title?,body?}}` → `{coupon, granted, already, not_found}` (멱등: 같은 요청 여러 번 → 한 번 발급, 한 번 최대 500명, `member_ids`는 v1 응답의 `member_id`, 새로 받은 회원 메시지함에 안내 1건(기본 문구 있음), 꺼진 쿠폰 409·기간 지남 400·강사 추천 코드 400) | **고객별 쿠폰 = 돈이 걸린 쓰기**: safety 관문 통과 뒤, 변환기가 실행 기록과 대조하고 ① `private: true` 쿠폰 생성 ② `grants` 순서로 직접 쓴다. ② 실패 시 할 일함으로 보내고 ①의 쿠폰은 **남긴다**(아래 근거). **공용 쿠폰**(`target: all`, `private` 없음)은 회원 지정이 필요 없을 때만 쓴다. 아래 "정한 것" 1번 |
| `notice_inbox` (메시지함 안내) | `POST /api/v1/inbox` 보냄 `{member_ids, title, body, kind?="notice", link?}` → 201 `{sent, not_found}` (돈과 무관한 안내용, `link`는 `/`로 시작하는 사이트 안 경로만) | **돈과 무관한 쓰기**: 승인 없이 직접 쓴다. 회사 연결에서 켜야 동작, 기본 꺼짐 |
| `banner_post`·`banner_end` | 장애 공지 `POST /api/v1/incidents`·`PATCH /api/v1/incidents/{id}`(`status: resolved`)만 있음. 일반 공지 없음 | 장애 공지(배너의 `context_id`가 장애 건)만 직접 쓰기 후보, 그 밖은 할 일함. 1차는 할 일함 |
| `refund`·`claim_result` | 없음 (`GET /api/v1/refunds`만, 승인은 직원 화면 `admin/refunds/[id]`) | 할 일함 |
| `payment_rollback`·`fault_release` | 없음 (서비스 모드·대기열은 있지만 같은 일이 아님) | 할 일함 |

서비스 모드(`POST /api/v1/service-mode`)·대기열(`POST /api/v1/queue`)은 지금 부르는 AI가 없어 짝을 만들지 않는다(인프라 AI가 쓰게 되면 그때 동작과 관문을 따로 정한다).

#### 사이트에 더 필요한 쓰기 경로 (catbooks-demo 세션 몫)

AXcellent는 사이트 코드를 고치지 않는다. 아래는 사이트를 만드는 catbooks-demo 세션에 넘길 목록이다(모두 `write` 범위, `{error:{code,message}}` 오류 모양, 감사 기록 `api:<키 이름>`).

| 사이트 | 경로 (제안) | 쓰는 AI 동작 | 비고 |
|---|---|---|---|
| 둘 다 | `GET /api/v1/me` → `{name, scopes}` | 연결 확인 | 키에 쓰기 권한이 있는지 쓰기 전에 알기 위함. 없으면 첫 403으로 안다 |
| 둘 다 | 쓰기 요청의 `Idempotency-Key` 헤더 받기 (같은 키는 앞 결과) | 모든 쓰기 | 있으면 시간 초과 때 다시 보낼 수 있다 (판정 표의 `unknown`을 줄임) |
| 누비투어 | `POST /api/v1/cancellations/{id}/decision` (`approve`·`reject`, 환불 금액·사유) | `refund`·`claim_result` | **생김** (11/23, nubi-tour #16). 취소 수수료 계산은 사이트 규칙. 돈이 걸리므로 AXcellent 관문 뒤에만 부름. `GET /api/v1/me`도 같이 생김 |
| 누비투어 | `POST /api/v1/notices`, `DELETE /api/v1/notices/{id}` (문구·기간) | `banner_post`·`banner_end` | |
| 누비투어 | (선택) 쿠폰 기능과 `POST /api/v1/coupons` | `coupon_issue` | 여행사에 쿠폰이 필요할 때만 |
| 스터디온에듀 | `POST /api/v1/refunds/{id}/decision` (`approve`·`reject`, `amount`·`adjust_reason`) | `refund`·`claim_result` | 지금은 직원 화면에서만 승인 |
| 스터디온에듀 | ~~① 회원 메시지함 ② 쿠폰에 받는 사람 지정 칸~~ → **생김** (11/20, study-on-edu #13 dev 머지, 예진 PC 서버엔 아직 배포 전): `private` 쿠폰 + `POST /api/v1/coupons/{code}/grants`, `POST /api/v1/inbox` | `coupon_issue` (고객별)·메시지함 안내 | 모두 write 키, 사이트 감사 기록 `api:<키 이름>`, 발신 출처 값은 `site`·`admin`·`api`(AX 전용 값 없음). 발급 내역·메시지함 read 목록 API는 없다 → 쿠폰 사용 여부는 주문으로 확인 |
| 스터디온에듀 | `POST /api/v1/notices`, `DELETE /api/v1/notices/{id}` | `banner_post`·`banner_end` (장애 아닌 공지) | |

#### 단계 (PR 단위)

1. **W1 쓰기 뼈대**: `writes` 짝 필드(추가), 쓰기 장부, 서비스 허용 표, 판정 표 1·3·4·6과 할 일함 넣기(② 구현을 여기서 같이 한다). 직접 쓰기는 아직 없음 → 모든 쓰기가 할 일함.
2. **W2 관문 확인**: safety `GET /api/safety/executions/{execution_id}` 추가(또는 `context_id` 목록), 환불·쿠폰 기록 확인(판정 2). 확인 실패 시험.
3. **W3 직접 쓰기 + 문의 답변**: 고객사 호출(시간 초과·응답 처리, 판정 5·7), ① 문의 읽기 짝과 `link:` 장부, `inquiry_answer` 직접 쓰기(누비투어·스터디온에듀). 고객지원의 문의 화면 값 쓰기가 사이트 답변으로 간다.
   - (11/23 W3 구현 메모) 직접 쓰기 실행·판정 5·본문 식·`link:` 장부(`<변환기 폴더>/links.json`)까지 했다. **① 문의 읽기 짝은 다음 PR로 남겼다**: 고객지원은 문의함(`GET /admin/inquiries?state=new`)에서 `seq`·`key`·`context_id`·`buyer_id`(`buy_<숫자>` 형식 필수)·`customer_name`·`order_id`·`subject`·`message`를, 열린 문의(`GET /admin/inquiries/open`, `after`·`next_after`)에서 `seq`·`support_inquiry_id`·`view_digest`를 읽는다. 누비투어 v1 문의에는 회원 번호가 없고(`member: true/false`만) 비회원 문의도 있어 `buyer_id` 짝과 `synced` 기록에 따른 `state=new` 거르기·열린 문의 목록까지 새로 정해야 한다. 그때 문의 번호를 줄 때 `links.remember("inquiries", seq, 사이트 id)`를 같이 적는다
   - 정한 것: `view.answer`는 고객지원 화면 값에 없는 칸이라 변환기가 상담원(`agent`) 메시지 글을 순서대로 빈 줄로 이어 만든다(사이트 답변 칸은 하나, 다시 보내면 고침). 답변이 아직 없으면 계산 실패로 할 일함. 짝 `ok`에 없는 2xx·3xx(리다이렉트는 따라가지 않음)는 `unknown`. 직접 쓰기는 시간당 60번(`AX_SITE_ADAPTER_WRITES_PER_HOUR`), 응답 본문은 64KB까지만 읽는다. 오류 응답에 `error.site_code`·`site_status`·`site_message`(120자까지, 장부에는 안 남김)를 더 싣는다
4. **W4 화면**: 회사 연결 "동작별 직접 쓰기" 켜기(기본 꺼짐)·"최근 사이트 쓰기"·"키에 쓰기 권한 없음", 결과 칩 "사이트에 반영됨"/"사이트 반영 대기".
5. **W5 사이트가 경로를 더한 뒤**: 고객별 쿠폰(스터디온에듀 `private` 쿠폰 + `grants`, 11/20 생김. 공용 쿠폰은 W3 뒤 바로 가능)·메시지함 안내(`/inbox`), 환불·신청 결과(사이트 `decision` 경로), 공지. 사이트 경로마다 PR 하나.
   - (11/23 W5 누비투어 환불 구현 메모) **① 신청 받은함**: 짝 `inboxes.claims`(추가 필드, 회사 연결 화면이 저장하는 `resources`와 따로)로 `GET /admin/claims`를 준다. `state=new`는 결정 전(`requested`)이고 고객지원 결과가 아직 없는 신청(`seq` 순, `limit`, `meta.total`·`failed`), `state=open`은 결과가 적혔고 끝나지 않은(`completed`·`rejected`·`not_accepted` 아님) 신청(`after`·`meta.next_after`, `seq`·`support_claim_id`·`status`·`result_digest`). 신청 받은함은 고객 글이 있어 접수 동기화와 같은 인증(에이전트 토큰·판매 서버 서비스 키)을 받는다
   - `seq`: 누비투어 취소 id(cuid)는 정수가 아니라 처음 본 순서대로 1부터 번호를 매긴다(`links.numbers`, `links["claims"][seq]` = 취소 id, 같은 id는 늘 같은 번호). `context_id` = `ctx_site_claim_<seq>`, `key` = 취소 id
   - 구매자·이름: 취소 줄에 회원·이름이 없어 같은 예약 원본을 `order`로 붙여 짝 식이 `order.member_id`·`order.contact.name`(read 범위라 가린 이름)을 읽는다. 비회원 예약은 `guest-<예약 번호>`(판매 기록 내보내기와 같은 이름표). `type` = `cancel`, `reason` = `other`(사이트 사유는 고객이 쓴 글이라 `description`, untrusted)
   - 처리 기록: `POST /admin/claims/{seq}/result`가 쓰기 판정을 지나면 `<변환기 폴더>/claims.json`에 `support_claim_id`·`status`·결과 지문(고객지원 `claim_digest`와 같은 방식)만 남긴다. `failed`·`retry`는 예전처럼 접수 동기화 기록에만
   - 주문 하나 읽기(`GET /orders/{id}`)에 고객지원 `to_order`가 읽는 `payments[].payment_id`(= 예약 번호: 사이트 환불이 예약 단위)·`items[].order_item_id`(`<예약 번호>-<순번>`)·`title`(상품 번호)을 더하고, 비회원은 `guest-<예약 번호>`. 없으면 고객지원이 누비투어 주문을 읽지 못한다
   - **환불**: 결정 전 신청을 읽을 때 `links["payments"][<예약 번호>]` = 취소 id를 적고, 환불 쓰기 짝 `link:payments.payment_id`가 그것을 찾는다(없으면 할 일함 `link_missing`). 본문 approve + `refund_amount` = 관문 기록 금액(W2가 맞춰 봄) + 고정 사유 `const:`(금액이 사이트 규칙과 달라도 400 `REASON_REQUIRED`가 나지 않게 늘 싣는다; 같으면 사이트 처리 메모로만 남음)
   - 응답: 200(`replayed` 포함) `done`, 400 `REASON_REQUIRED`·`BAD_REFUND`·404·409 `ALREADY_DECIDED`·`BAD_STATE` → `rejected` 409 `SITE_REJECTED`, 502 `REFUND_FAILED` → `partial` 할 일함 "결제사 환불 확인"(짝 `partial_codes`), 401·403·429·5xx·시간 초과 → W3 그대로
   - 남은 일: ⓐ 시간 초과 재전송: 결정이 멱등(같은 결정은 `replayed`, 다른 결정은 409)이라 같은 본문 재전송은 안전해 보인다. 이번엔 W3 규칙(재시도 안 함 → 할 일함)을 그대로 두고, 동작별 "재전송 안전" 표시를 짝에 더하는 것은 다음에 정한다 ⓑ 거절(`reject`): 고객지원은 거절을 `write_claim_result`(상태가 바뀔 때마다 부름)로만 알려, 짝에 "상태가 `rejected`일 때만" 조건이 있어야 한다. 지금 `claim_result`는 할 일함 ⓒ 고객지원 판정 금액은 책 쇼핑몰 규칙(취소 = 받은 금액 전액)이라 여행사 취소 수수료(`cancellation.fee`)를 모른다. 사람이 관문에서 금액을 승인하지만, 그대로 보내면 사이트 수수료가 0으로 바뀐다(사유는 남음). 사이트 예상 환불액을 고객지원 판정에 넘기는 길이 필요하다 ⓓ 신청 `failed` 횟수로 받은함에서 빼기(판매 서버처럼)는 아직 없다 ⓔ 회사 연결 화면 저장이 `inboxes`·`writes`를 덮어쓰지 않게(W4)
6. 롤백·장애 해제: 도메인 클라이언트가 `context_id`를 보내게 바꾼 뒤 따로 정한다(그때까지 할 일함).

#### 시험

- 단위: 고객사는 `upstream.TRANSPORT`(httpx.MockTransport)로 바꾼다. 동작마다 판정 표 1~7줄, 응답 표 4줄(2xx·4xx·401/403·시간 초과)을 하나씩. 같은 멱등 키 두 번 → 고객사 호출 1번, 다른 지문 → 409, `sending` 남은 채 다시 → 할 일함. safety는 실행 기록 응답을 흉내 낸다(확인 실패 → 고객사 호출 0번).
- 짝: 누비투어·스터디온에듀 쓰기 짝 파일을 시험 고정 파일로 두고 본문 변환 결과를 사이트 OpenAPI 스키마(필수 칸·형식)로 검사한다. 키·개인정보가 장부·로그에 없는지 본다.
- 계약: 변환기 응답을 기존 shop-api 응답 모델로 검증(추가 칸 `applied`만 더해짐).
- 실연결: 고객사 사이트는 사내망(251)에만 있어 **클라우드 CI·클라우드 세션에서는 닿지 않는다.** 사내 PC 또는 251에서 사람이 한다. 시드 데이터로 띄운 시험용 사이트에 `write` 범위 키(이름 `AXcellent`)를 발급해 문의 답변 하나를 보내고, ① 사이트 문의에 답변이 보이는지 ② 사이트 감사 기록에 `api:AXcellent`·`inquiry.answer`가 남는지 ③ 같은 요청 재전송 때 감사 기록이 늘지 않는지 ④ 쓰기 권한 없는 키로 바꾸면 할 일함으로 가는지 본다. 돈이 걸린 동작은 사이트 경로가 생긴 뒤 같은 방법으로.

#### 보안 요구 (재헌 님 의견, 11/19 슬랙)

1. **고객사 주소 호출**: https만 받는다. 사설·루프백·링크 로컬·클라우드 메타데이터 IP는 **연결 직전 DNS 검사**로 막고(이름 풀이 뒤 실제 붙는 IP를 본다), 리다이렉트는 따라가지 않는다. 이미 있는 `safe_fetch` 방식을 그대로 다시 쓴다.
2. **고객사 키**: 암호화해 저장하고, 화면·응답·로그 어디에도 값을 내보내지 않는다(이름·끝 몇 자만). 최소 권한 키를 요구하고(읽기만 쓸 땐 `read`만, 직접 쓰기를 켤 때만 `write`), 고객사별로 따로 둔다(한 고객사 키로 다른 고객사를 부르지 못함). 키 교체 절차(새 키 등록 → 연결 확인 → 옛 키 폐기)를 회사 연결에 둔다.
3. **고객사 API 응답은 믿지 않는 데이터**: AI는 경로·필드 짝짓기(짝 파일 초안)만 하고, 응답 본문을 지시로 읽지 않는다. 변환 결과는 shop-api 스키마로 검증해 맞지 않으면 버린다.
4. **개인정보**: 필요한 칸만 받고(최소화), 로그는 마스킹하고, 보관 기간을 정하고, 고객사와의 처리 위탁 계약 문구를 준비한다.
5. **기록·한도**: 누가 언제 무엇을 켰는지, AI 초안을 사람이 어떻게 고쳤는지를 버전으로 남긴다. 고객사 호출은 시간당 횟수·타임아웃·응답 크기 상한을 둔다.

설명서(OpenAPI)가 없을 때:

- 자동 탐색·GET 추정은 괜찮다.
- 코드 레포는 고객사가 동의할 때만 본다. 먼저 설명서를 달라고 한다.
- HAR 파일은 올리기 전에 인증 정보(쿠키·토큰·키)를 반드시 지운다. Postman 컬렉션·샘플 응답이 더 안전하다.
- 읽기 전용 DB 계정은 고객사가 볼 테이블·칼럼을 정해 줄 때만 쓴다.

#### 정한 것 (예진 님, 11/20·11/23)

1. **스터디온에듀 쿠폰**: (11/20 갱신, study-on-edu #13 dev 머지, 예진 PC 서버엔 아직 배포 전) 사이트에 회원 메시지함과 받는 사람 지정이 **생겼다**. 고객별 쿠폰은 `POST /api/v1/coupons`(`private: true`) → `POST /api/v1/coupons/{code}/grants` 두 단계 직접 쓰기다(돈이 걸린 쓰기: safety 관문 통과 뒤, 실행 기록과 대조). 공용 쿠폰(`target: all`)은 **회원 지정이 필요 없을 때**만 쓴다.
   - **두 번째 단계(`grants`) 실패 시**: 할 일함으로 보내고, 첫 단계로 만든 쿠폰은 **비활성 처리하지 않고 남긴다.** 근거: ① `private` 쿠폰은 받은 회원만 쓸 수 있어 발급 전엔 아무도 못 쓰므로(다른 회원은 403 `COUPON_NOT_YOURS`) 남겨도 돈이 새지 않는다 ② `grants`는 멱등이라 사람이 할 일함에서 같은 코드로 다시 보내면 된다(쿠폰을 다시 만들면 `DUPLICATE_CODE` 409) ③ 비활성 처리는 또 하나의 쓰기라 그것도 실패할 수 있고, 위 v1 경로에 끄는 경로도 정해져 있지 않다. 장부에는 쿠폰 코드와 "발급 대기"를 남긴다.
   - 발급 내역 읽기 API가 없으니 쿠폰을 썼는지는 주문으로 확인한다.
   - 메시지함 안내(`POST /api/v1/inbox`)는 아래 2번(돈과 상관없는 쓰기)에 든다.
2. **돈과 상관없는 쓰기(문의 답변·신청 처리 결과 표시·메시지함 안내 `/inbox`)**: 승인 없이 바로 쓴다. 단 고객사 관리자가 회사 연결에서 켜야 동작하고, **기본 꺼짐**.
3. **직접 쓰기를 켜는 단위 (예진 님, 11/23)**: **동작별 켜기, 기본 꺼짐**으로 정한다(기능 묶음별이 아니다). 돈이 걸린 동작(`refund`·`coupon_issue`)은 W2 관문 확인이 들어가기 전에는 켤 수 없다(켜 둬도 꺼진 것으로 본다). W1은 변환기 폴더의 `direct_writes.json`(`{"enabled": ["inquiry_answer", ...]}`)을 읽기만 하고, 켜는 화면은 W4에서 만든다.

#### 열린 질문

- 없음. (11/23) 1번 "직접 쓰기를 켜는 단위"는 위 "정한 것" 3번으로 옮겼다. 연결할 때 AI가 고객사 API를 읽어 기능별 짝 초안을 만들고 사람이 확인하는 화면의 모양은 W4(화면)에서 정한다.

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

- ~~고객사 사이트에 직접 쓰기(문의 답변 등 사이트 API에 있는 쓰기도 이번엔 사람 할 일)~~ → **③-쓰기로 대체** (11/19)
- 읽기 전용 DB 연결, 플랫폼 앱 등록·OAuth·웹훅, 플랫폼별 기본 짝(프리셋)
- 들어오는 이벤트 만들기, 업종별 용어 바꾸기(예약 ↔ 주문 화면 문구)

## PR 나눔

1. 연결 파일 `kind`, 변환기 뼈대와 ① 읽기 변환(짝 파일을 손으로 써서 시험), ③ 빈 응답, 접수 동기화 기록
2. ② 사람 반영(할 일함 넣기, `applied` 응답, 화면 "사이트 반영 대기" 칩)
3. 둘러보기·짝 제안·확인 API와 회사 연결 화면(DB 칸 삭제, 플랫폼 열기, 짝 확인 단계)
4. 누비투어 실연결 확인과 문서
5. (11/19 추가) 직접 쓰기: ③-쓰기의 "단계" W1~W5. 2번(② 사람 반영)은 W1에 합친다.
