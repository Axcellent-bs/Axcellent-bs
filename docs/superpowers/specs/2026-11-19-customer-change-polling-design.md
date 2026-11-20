# 고객사 변화 읽어서 알아채기 (이벤트 받기 → 주기 읽기) 설계

2026-11-19, 예진 님 결정을 설계로 옮겨요. **설계안까지만**이에요. 코드는 아래 PR 나눔대로 따로 해요.

## 배경과 목표

- 지금은 캣북스 판매 서버(shop-api)의 outbox가 9종 이벤트를 웹훅(`AX_EVENTS_WEBHOOK_URL`) 또는 버스로 보내고, AXcellent `POST /api/dashboard/events`(`backend/shared/dashboard-api/src/ax_dashboard_api/shop_events.py`)가 회사 키를 확인한 뒤 이벤트 버스(`ax:events`)에 넣어요.
- 이벤트를 보내는 기능이 없는 고객사(누비투어, 스터디온에듀, 앞으로 올 회사)는 그 이벤트에 기대는 기능을 못 써요.
- **결정(예진 님 11/19)**: 고객사가 먼저 보내는 이벤트에 기대는 기능을 없애요. 고객사는 가만히 있고, AXcellent가 모든 고객사에서 읽어서 스스로 알아채요.
- 사이트 변환기 설계(`2026-11-18-site-adapter-design.md` "들어오는 이벤트")가 "`updated_since` 폴링으로 이벤트를 만드는 것은 따로 정한다"로 남겨 둔 부분이 이 문서예요.
- 쇼핑몰 응답 지연·다운(`shop.degraded`·`shop.recovered` 성격)은 Zabbix → DejaFix 길이 따로 있어 손대지 않는 게 방향이에요. 그래도 지금 누가 쓰는지와 대체 경로는 아래에 적어요.

## 조사 결과: 9종 이벤트를 받아서 쓰는 곳 (11/19 dev 기준)

버스 구독은 `ax_agent_template.events.subscribe`로만 해요. 실제로 구독하는 곳은 **총괄과 재무 두 곳뿐**이에요. `backend/docs/agent-common.md` 6장 카탈로그에는 data·marketing·support도 구독한다고 적혀 있지만 코드에는 구독이 없어요(카탈로그가 계획 단계 값).

| 이벤트 | 받아서 쓰는 곳 (도메인) | 무엇을 하나 | 이벤트가 없어지면 |
|---|---|---|---|
| `order.created` | 없음 | — | 영향 없음 |
| `order.cancelled` | 없음 | — | 영향 없음 |
| `payment.succeeded` | 없음 | — | 영향 없음 |
| `payment.failed` | 없음 (버스) | 총괄의 실패 주문 묶기·데이터의 `order_fail_count`는 이벤트가 아니라 shop-api 읽기(`/api/shop/failed-orders`, `/api/shop/metric-series`)로 해요 | 영향 없음 |
| `payment.refunded` | **재무** `finance_agent/handlers.py` `on_event` (`SUBSCRIBED_EVENTS`) | 주문별 환불 누적(`ledger.refunds`) → `GET /api/finance/ledger`의 `refunds`, AI 대화방에 "환불 기록" 한 줄(`chat_log.ledger_recorded`). 결산·사건 비용(`books.refunds`)은 이벤트가 아니라 `/api/shop/orders` 읽기로 따로 계산해요 | 이벤트 장부와 AI 대화방 줄이 비어요. 결산 숫자는 그대로예요 |
| `return.received` | 없음 (버스) | 고객지원은 `GET /api/shop/admin/returns` 주기 읽기로 반품을 받아요 | 영향 없음 |
| `seller.registered` | 없음 | 계약 설명(`shop-api.json` "총괄이 입점 심사를 연다")은 있지만 총괄 코드(`CASE_EVENTS`)에 없어요 | 영향 없음 (설명만 남은 상태) |
| `shop.degraded` | **총괄** `ax_orchestrator/manager.py` (`INCIDENT_EVENTS`, `SUBSCRIBED`) | 장애 이벤트(사건) 열기, 같은 `component`면 합치기, `data.since`로 장애 시작 시각. 화면 `IncidentDetail.tsx`·`lib/data.ts`가 출처 이름("쇼핑몰 모니터")으로 보여 줘요 | 쇼핑몰이 직접 재는 장애 신호가 없어져요 → 아래 "장애 신호 대체" |
| `shop.recovered` | **총괄** `manager.py`·`engine.py`·`plans/scenario_a.py`·`timeline.py` | 복구 시각(`recovered_at`, MTTR), 복구 기다리던 단계(장애 배너 내리기) 다시 돌리기, `recovery=fault_released`면 해결 기록 문장 | 같은 부위 사건이 "복구 안 됨"으로 남아요 → 아래 "장애 신호 대체" |

덧붙여 `POST /events`의 `ALLOWED_TYPES`에는 9종 말고 `order.delivered`·`shipment.delayed`·`product.created`도 있어요. 이 셋도 구독하는 곳이 없어요.

정리하면 **주문·결제·환불 쪽에서 실제로 막히는 소비처는 재무의 `payment.refunded` 하나**예요. 나머지 주문·결제 쪽은 이미 각 AI가 shop-api를 읽어서 일해요.

### 장애 신호 대체 (`shop.degraded`·`shop.recovered`)

- 지금: 캣북스 shop-api `monitor.py`가 p95·5xx를 재서 보내요(시연용 장애 주입과 바로 이어지게). 받는 곳은 총괄뿐이에요.
- 대체 경로(이미 있음): Zabbix → DejaFix → 인프라 `infra_agent/dejafix_watch.py`가 `GET /api/incidents?status=problem`을 30초마다 읽고 `component` 태그가 붙은 장애를 `infra.anomaly_detected`로 보내요. 총괄은 이것도 `INCIDENT_EVENTS`로 받아 사건을 열어요. **열기는 이미 대체돼요.**
- 빈 곳: **복구 신호**예요. `dejafix_watch`는 장애가 목록에서 빠져도 아무것도 보내지 않아요. 그래서 DejaFix 길로 열린 사건은 `recovered_at`이 비고, 복구를 기다리는 단계(배너 내리기)가 안 돌아요.
- **결정(예진 님 11/20)**, 인프라·총괄 몫(PR 6): `dejafix_watch`가 직전 회차에 보냈던 장애가 `problem` 목록에서 빠지면 복구 신호를 하나 보내요. 새 종류 `infra.recovered`를 계약에 **추가**하고(데이터는 `InfraAnomalyData` 재사용), 총괄이 `SHOP_RECOVERED`와 같은 길로 받아요. `shop.recovered`를 인프라가 그대로 쓰는 방법도 있지만 "쇼핑몰이 알린 사실"이라는 뜻과 `source=shop-api` 표시가 섞여서 권하지 않아요.
- 그 전까지 캣북스의 `shop.degraded`·`shop.recovered` 전송은 **그대로 둬요**(시연 장애 주입 흐름 `fault_released`가 여기에 걸려 있어요). 이 문서의 "outbox 제거"는 주문·결제·환불·반품·판매자 7종만이에요.

## 읽기 경로 조사

| 필요한 변화 | 읽을 곳 | 증분 읽기 | 캣북스(shop-api 직결) | 변환기 고객사(누비투어·스터디온) |
|---|---|---|---|---|
| 주문 생성·취소, 결제 성공·실패, 환불 | `GET /api/shop/sales-export?cursor&limit` (계약 `ax_contracts.data_access.shop_sales`) | **있음.** `(updated_at, order_id)` 오름차순 커서, 상태가 바뀌면 `updated_at`이 바뀌어 다시 나와요 | 있음. 데이터 수집기(`data_agent/sales_collect.py`)가 5분마다 이미 이 길로 읽어요 | 있음(`site_adapter/router.py` `sales_export`). 단 아래 "변환기 보완" 필요 |
| 주문 목록 | `GET /api/shop/orders?since&until&cursor` | `since`는 **만든 시각** 기준이라 상태 변화는 못 잡아요 | 있음 | 있음 |
| 반품 접수 | `GET /api/shop/admin/returns` | 없음(접수함 + `synced` 표시) | 고객지원이 이미 읽어요 | 빈 응답(③) |
| 판매자 입점 | `GET /api/shop/sellers` | 없음 | 있음 | 빈 응답(③) |

- 계약 스냅숏 `backend/shared/contracts/openapi/shop-api.json`에는 `sales-export`·`sales-summary`·`customers-export`가 빠져 있어요(파이썬 계약 모델은 있음). 이번 PR 1에서 스냅숏에 **추가**해요.
- **`sales-export` 하나로 주문·결제·환불 변화를 다 볼 수 있어요.** 한 줄에 `status`, `payment_status`, `refunds[{refund_id, amount, refunded_at}]`, `updated_at`이 있어요.

### 변환기 보완 (변환기 고객사가 같은 모양을 받으려면)

`site_adapter/router.py` `sales_export`를 보면 지금 이대로는 감지기에 쓰기 어려워요.

1. **커서가 목록 순번(offset)이에요.** 매번 고객사 주문 전체를 읽어 정렬한 뒤 순번으로 잘라요. 중간 주문의 `updated_at`이 바뀌면 순번이 밀려 **빠지거나 두 번 나와요.** → 커서를 계약 설명대로 "마지막으로 준 `(updated_at, order_id)`"로 바꾸고, 그 시각을 고객사 `updated_since`(짝의 `paging.since_param`)로 넘겨 바뀐 것만 읽어요.
2. **매 호출이 전체 읽기예요**(`fetch_all`, 최대 `AX_SITE_ADAPTER_MAX_PAGES`=50쪽, 캐시 30초). 감지기가 1분마다 부르면 분당 최대 50번이 그냥 나가요. 1을 고치면 평소 1~2번이에요.
3. **환불이 비어요.** `to_sales_row`가 `refunds`를 안 채워요. 짝에 `refunded_amount`가 있으면 `refunds`에 한 줄로 실어요(`refund_id`는 고객사 값이 없으면 비워 둬요. 감지기가 아래 규칙으로 만들어요).
4. **결제 실패를 모르는 사이트**가 많아요(변환기는 받은 금액이 있으면 `succeeded`만 만들어요). 상태 값 표에 실패 상태가 있으면 `payment_status=failed`로, 없으면 비워 둬요. 지어내지 않아요.

## 방식 비교

### A. 한 곳의 "변화 감지기"가 읽고, 같은 모양의 내부 이벤트를 버스에 넣기 (추천)

```
캣북스 shop-api ─┐
                 ├─ agent_url("shop-api") ──▶ GET /api/shop/sales-export?cursor=...
변환기(누비·스터디온)┘                         │
                                 dashboard-api 변화 감지기 (주기 작업)
                                   │ 직전 상태 표와 비교 → 바뀐 것만
                                   └─ events.publish(EventEnvelope)  ──▶ ax:events ──▶ 재무 on_event (그대로)
```

- 소비처 코드가 **그대로**예요. 재무 `on_event`는 `payment.refunded` 봉투를 지금처럼 받아요.
- 도메인끼리 import가 없어요. 감지기는 `shared/dashboard-api` 안에 있고 버스로만 알려요.
- 캣북스와 변환기 고객사가 `agent_url("shop-api")` 하나로 같은 길을 타요(변환기 설계가 이미 그렇게 돌려줘요).
- 고객사 호출이 감지기 한 곳에서만 나가서 분당 한도를 세기 쉬워요.

### B. 각 도메인이 직접 읽기

- 재무가 `sales-export`를 직접 읽어 환불을 장부에 넣어요.
- 소비처마다 커서·상태 표·한도 계산이 생겨요(지금은 재무 하나지만, 앞으로 고객지원·마케팅이 주문 변화를 쓰면 같은 일을 또 해요).
- 재무 `on_event`·AI 대화방 줄을 고쳐야 하고, 이벤트 버스의 "사실을 한 번 알리고 여럿이 받는" 구조가 깨져요.

### 추천: A

소비처를 덜 바꾸고(재무 0줄), 규칙(도메인끼리 import 금지, 계약은 추가만)을 그대로 지켜요. 위치는 **dashboard-api**를 추천해요(**결정 대기**). 이유: 이미 변환기·회사 연결·`POST /events`·주기 작업(`main.py` `_retention_loop`)이 여기 있고, 컨테이너를 늘리지 않아요(11/17 멘토링 "컨테이너 줄이기").

## 감지기 설계 (A)

### 무엇을 읽고 무엇을 만드나

한 회차: `GET {agent_url("shop-api")}/api/shop/sales-export?cursor=<저장된 커서>&limit=500`을 `has_more=false`가 될 때까지 읽어요. 줄마다 **직전 상태 표**의 같은 `order_id` 줄과 비교해요.

| 만들 이벤트 | 감지 규칙 (직전 → 지금) | `data` (계약 모델) |
|---|---|---|
| `order.created` | 직전 표에 없던 주문 | `OrderCreatedData` (`lines`에서 items·total) |
| `order.cancelled` | `status`가 취소 아님 → 취소(상태 값 표의 취소 묶음) | `OrderCancelledData` |
| `payment.succeeded` | `payment_status`가 `succeeded` 아님 → `succeeded` | `PaymentSucceededData` |
| `payment.failed` | `payment_status`가 `failed` 아님 → `failed` | `PaymentFailedData` (`failure_code` 필수: 모르면 만들지 않아요) |
| `payment.refunded` | `refunds`에 직전에 없던 `refund_id` / 변환기 고객사는 환불 누적 금액 증가분 | `PaymentRefundedData` |
| `return.received` | (지금 소비처 없음) 만들지 않아요 | — |
| `seller.registered` | (지금 소비처 없음) 만들지 않아요 | — |

- **계약 모양을 다 못 채우는 종류가 있어요.** `sales-export` 줄만으로는 `OrderCreatedData.items[].seller_id`(필수), `OrderCancelledData.reason_code`(필수), `PaymentFailedData.failure_code`(필수)를 알 수 없어요. 값을 지어내면 안 되니, 이 셋은 캣북스에서 `orders/{id}`를 더 읽어 채울 수 있을 때만 만들고 변환기 고객사에서는 만들지 않아요(또는 계약에 선택 필드로 된 새 데이터 모델을 **추가**해야 해요).
- **결정(예진 님 11/20): 감지할 변화는 `payment.refunded` + `order.cancelled` 둘이에요.** `order.created`·`payment.succeeded`·`payment.failed`는 감지 규칙 표만 두고 꺼 둬요.

#### `order.cancelled` 필수 칸 채우기 (계약 확인: `ax_contracts/schemas/event.py` `OrderCancelledData`)

| 칸 | 판매 기록(`SalesOrderRow`)만으로 | 처리 |
|---|---|---|
| `order_id` | 있음 | 그대로 |
| `cancelled_amount` (int, 필수) | 계산 가능 | `items_amount + shipping_fee - points_used - coupon_discount`(주문 결제 금액). 이미 환불된 몫(`refunds` 합)이 있으면 빼요. 계산식은 코드에 고정, 지어내지 않아요 |
| `reason_code` (`buyer`·`seller`·`system`, 필수) | **없음** | 아래 |

`reason_code`는 판매 기록에 없고, shop-api 계약 스냅숏에도 취소 사유를 읽는 경로가 없어요(`orders/{id}/cancel`은 쓰기뿐). 값을 지어내지 않고 이렇게 해요.

1. **계약에 칸을 추가해요(삭제·변경 없음)**: `SalesOrderRow`에 `cancel_reason_code: Literal["buyer", "seller", "system"] | None = None`을 **추가**해요. 기본값이 `None`이라 지금 쓰는 곳(데이터 수집기 등)은 그대로예요. `OrderCancelledData`는 건드리지 않아요(`reason_code`를 선택으로 바꾸거나 `unknown` 값을 더하는 것은 타입 변경이라 하지 않아요).
2. **채우는 쪽**:
   - 캣북스: `sales-export`가 주문 취소 기록(누가 취소했는지: 손님 취소 경로 → `buyer`, 판매자·관리자 → `seller`, 결제 시간 초과 등 자동 → `system`)으로 칸을 채워요. 판매 서버 코드는 catbooks-demo 세션 몫이라 인계해요.
   - 변환기 고객사: 짝 파일 `orders.fields`에 `cancel_reason_code` 식과 `values` 값 표를 **추가**할 수 있게 해요(예: 고객사 취소 주체 칸 → `buyer`·`seller`·`system`). 사이트에 그런 칸이 없으면 비워 둬요.
3. **감지기 규칙**: 취소로 바뀐 줄에 `cancel_reason_code`가 있을 때만 `order.cancelled`를 만들어요. 비어 있으면 **만들지 않고** 상태 파일에 "사유 없음으로 건너뜀" 건수만 남겨요(상태 주소에도 보여요).
4. 지금 `order.cancelled`를 받는 곳은 없어요. 받는 곳이 생길 때 위 칸이 채워지는 고객사부터 동작해요.
 `return.received`·`seller.registered`는 읽을 곳이 `sales-export` 밖이라 호출이 늘어서 쓰는 곳이 생길 때 해요.
- `PaymentRefundedData`에 `payment_id`가 필수인데 `sales-export` 환불 줄에는 없어요. 캣북스는 바뀐 주문만 `GET /api/shop/orders/{id}`로 한 번 더 읽어 채우고, 변환기 고객사처럼 결제 id가 없으면 재무 `books.py`와 같은 규칙(`pay_of_<order_id>`, `rfd_of_<order_id>_<누적금액>`)으로 만들어요. 계약을 바꾸지 않아요.
- `source`는 `"change-detector"`(새 이름)로 해요. 재무는 `source`를 보지 않아요. 화면 출처 이름표(`lib/data.ts`)에 "변화 감지"를 하나 **추가**해요.
- `tenant_id`는 회사 연결 파일의 회사예요(고객사별 설치라 설치 하나에 회사 하나).

### 중복·누락 막기

- **커서 저장**: 감지기 상태 파일 `AX_CHANGE_DETECTOR_DIR`(기본 `backend/.data/change_detector/`)에 `cursor`, 직전 상태 표(주문별 `status`·`payment_status`·환불 id 목록·환불 누적 금액·`updated_at`), 마지막 성공 시각, 마지막 오류를 둬요. 데이터 수집기(`sales_collect.py`)처럼 **쪽마다** 이벤트를 다 넣은 뒤에 커서·상태 표를 저장해요. 중간에 죽으면 그 쪽을 다시 읽고, 아래 멱등 키 때문에 같은 이벤트가 두 번 쌓이지 않아요.
- **같은 변화 두 번 안 내보내기**: `event_id`·`idempotency_key`를 변화 내용으로 정해요. 예: `chg:<tenant>:payment.refunded:<order_id>:<refund_id>`, `chg:<tenant>:order.cancelled:<order_id>`. 버스가 같은 `event_id`를 1일 동안 한 번만 쌓고(`SET NX`), 재무도 `seen_events`로 한 번 더 걸러요.
- **처음 켤 때**: 커서가 없으면 끝까지 읽어 상태 표만 채우고 **이벤트는 내보내지 않아요**(지난 주문 수천 건이 한꺼번에 "새 주문"으로 쏟아지지 않게). 기준 시각을 상태 파일에 남겨요.
- **누락 막기**: 커서는 "마지막으로 준 `(updated_at, order_id)`"라 같은 시각에 바뀐 주문도 빠지지 않아요. 고객사 시계가 조금 늦게 `updated_at`을 찍는 경우를 위해 변환기는 `updated_since`를 커서 시각보다 **2분 앞당겨** 묻고, 이미 본 줄은 상태 표 비교로 걸러요.
- **잘림**: 변환기가 `UPSTREAM_TRUNCATED`(503)를 주면 그 회차는 실패로 두고 커서를 옮기지 않아요. 숫자를 지어내지 않아요.
- **회사 연결이 바뀌면**(캣북스 → 변환기, 주소 변경): 연결 지문이 바뀐 것을 보고 상태 파일을 새로 시작(처음 켤 때와 같이 조용히 채우기)해요.

### 주기·지연 허용치·호출 한도

- 주기: `AX_CHANGE_DETECTOR_SECONDS` 기본 **60초**(최소 30초, `0`이면 끔). 환불 장부·AI 대화방 줄은 몇 분 늦어도 되는 일이라 **지연 허용치는 5분**으로 잡아요. 웹훅 때보다 최대 1분 늦어요.
- 장애 판단(총괄)은 이 감지기를 쓰지 않아요(DejaFix 30초 주기 그대로).
- 호출 수(평소): 한 회차 1번(바뀐 것이 500건 넘으면 쪽 수만큼). 캣북스 환불이 있으면 바뀐 주문마다 `orders/{id}` 1번 더.
- 스터디온(키당 분당 600회): 변환기 보완 1을 하면 감지기는 분당 **1~3번**이에요. 다른 AI(데이터 수집기 5분마다, 화면·고객지원 읽기)와 합쳐도 한도의 10% 아래예요. 감지기는 한 회차에 **고객사 호출 최대 `AX_CHANGE_DETECTOR_MAX_CALLS`(기본 30)** 를 넘기면 거기서 멈추고 커서를 저장한 뒤 다음 회차에 이어 읽어요.
- 변환기 보완 1 전에는 감지기를 변환기 고객사에서 **켜지 않아요**(매 회차 최대 50쪽 전체 읽기 + offset 커서 문제).

### 실패와 표시

- 실패해도 서버는 안 죽고 다음 회차에 다시 해요. 같은 오류가 연달아 나면 로그는 한 줄만(데이터 수집기와 같은 규칙).
- `GET /api/dashboard/change-detector/status`(사람 토큰, 추가)로 켜짐·마지막 성공 시각·마지막 오류 종류·커서 시각을 줘요. 회사 연결 화면 연결 지도에 "변화 감지: 1분 전 확인" 정도로 보여요(화면은 PR 3).

## 전환 순서

1. **감지기 켜기 (캣북스 먼저)**: 캣북스 outbox 웹훅이 살아 있는 동안 감지기를 같이 켜요. 같은 환불이 두 길로 오지만 `event_id`가 달라서 재무 장부에 두 번 쌓일 수 있어요 → 이 기간에는 감지기를 **그림자 모드**(`AX_CHANGE_DETECTOR_PUBLISH=0`: 버스에 안 넣고 "내보냈을 이벤트"만 상태 파일·로그에 남기기)로 돌려 웹훅으로 온 이벤트와 하루 이상 맞춰 봐요.
2. **소비처 확인**: 재무 `GET /api/finance/ledger` 환불 목록·AI 대화방 "환불 기록" 줄이 그림자 기록과 같은지 봐요. 같으면 `AX_COMPANY_KEYS`에서 캣북스 키를 빼고(웹훅이 403으로 막혀요) 그림자 모드를 꺼요. 이 순서면 같은 환불이 두 번 쌓이는 때가 없어요.
3. **변환기 고객사 켜기**: 변환기 보완(커서·`updated_since`·환불 줄)이 들어간 뒤 누비투어에서 감지기를 켜고, 환불 하나를 만들어 재무 장부에 들어오는지 봐요(251 실연결은 catbooks-demo 세션 몫과 같이).
4. **캣북스 outbox 전송 제거 (catbooks-demo 세션 몫)**: 주문·결제·환불·반품·판매자 7종 웹훅 전송을 끄거나 지워요. `shop.degraded`·`shop.recovered`는 위 "장애 신호 대체"의 복구 신호가 들어갈 때까지 남겨요. 이 레포에서는 다른 레포를 고치지 않으니 인계 글로 넘겨요.
5. **`POST /api/dashboard/events` 정리**: 계약은 **삭제·이름 변경 금지, 추가만**이에요.
   - 주소·`EventType` 값·`EVENT_DATA_MODELS`는 **남겨요**(지우면 계약 삭제예요).
   - `dashboard-api.json` 스냅숏의 이 주소에 `deprecated: true`와 설명 "고객사 이벤트 받기는 쓰지 않아요. 변화 감지기가 읽어서 만들어요"를 **추가**해요.
   - 동작은 이미 fail-closed예요: `AX_COMPANY_KEYS`가 비면 모두 401/403이에요. 기본 설정·`.env.example`에서 이 값을 빼고, 고객사 연결 안내 문서에서 "이벤트 보내기"를 지워요.
   - `shop_events.py`·`events.py` 머리말, `backend/docs/agent-common.md` 6장 카탈로그의 "발행: shop-api" 줄을 "발행: 변화 감지기(dashboard-api)"로 고치고, 실제 구독처(총괄·재무)만 남겨요.
   - 남은 `shop.degraded`·`shop.recovered`가 정리되면 그때 `ALLOWED_TYPES`를 비우는 것은 따로 정해요.

## PR 나눔

| PR | 내용 | 영향 도메인 (담당) |
|---|---|---|
| 1 | 감지기 뼈대: dashboard-api 주기 작업, 상태 파일, 커서, 처음 켤 때 조용히 채우기, 그림자 모드, `payment.refunded`·`order.cancelled` 감지(나머지 3종은 규칙만, 꺼 둠), `SalesOrderRow.cancel_reason_code` 계약 추가, 상태 주소. 캣북스(shop-api 직결)만. `shop-api.json` 스냅숏에 `sales-export` 추가. 단위 시험은 가짜 판매 서버 응답(목 스위치, 테스트 전용) | dashboard-api(공용), 재무(받는 쪽 확인만, 코드 0줄) |
| 2 | 변환기 `sales-export` 보완: `(updated_at, order_id)` 커서, `updated_since` 넘기기, 환불 줄, 결제 실패 상태 값 | dashboard-api 변환기(사이트 변환기 작업자와 맞추기), 데이터(같은 주소를 5분마다 읽는 수집기 확인: 예진) |
| 3 | 화면: 회사 연결 연결 지도에 "변화 감지" 상태, 출처 이름표 추가 | 프론트 |
| 4 | 전환: 그림자 모드 끄기, `POST /events` `deprecated` 표시 추가, `AX_COMPANY_KEYS` 기본값·안내 정리, `agent-common.md` 카탈로그 정리 | dashboard-api, 재무 |
| 5 (catbooks-demo) | 캣북스 outbox 7종 전송 제거 | 캣북스 판매 서버(재헌) |
| 6 (따로) | 장애 복구 신호(11/20 결정): `infra.recovered` 계약 추가, `dejafix_watch`가 장애가 닫히면(`problem` 목록에서 빠지면) 복구 알림, 총괄이 받기 | 인프라, 총괄(준하) |

## 하지 않을 것 (이번)

- `shop.degraded`·`shop.recovered` 전송 제거(장애 신호는 DejaFix 길, 복구 신호가 들어간 뒤 정해요)
- `return.received`·`seller.registered` 감지(소비처 없음)
- 계약 값 삭제·이름 변경
- 고객사 사이트에 무언가 설치하게 하기(고객사는 가만히 있어요)

## 정한 것 (예진 님 11/20)

1. **감지할 변화**: `payment.refunded` + `order.cancelled`. 주문 취소 사유(`reason_code`)는 판매 기록에 없어서 `SalesOrderRow`에 `cancel_reason_code` 칸을 **추가**해 채우고, 비어 있으면 이벤트를 만들지 않아요(위 "`order.cancelled` 필수 칸 채우기").
2. **쇼핑몰 복구 신호**: DejaFix 감시(`dejafix_watch`)가 장애가 닫히면 복구 알림(`infra.recovered`, 계약 추가)을 보내요(PR 6). 그때까지 캣북스의 `shop.degraded`·`shop.recovered` 전송은 남겨요.

## 결정 대기

1. **감지기 위치**: 추천은 dashboard-api 안(컨테이너 안 늘림, 변환기·회사 연결과 같은 곳)이에요. 데이터 수집기와 같은 `sales-export`를 따로 읽게 되지만 분당 1~3번이라 한도 안이에요.
