# 일별 집계 — 설계 (3단계 3-1)

2026-11-24 · 작성: 재현(세션2) · 리뷰: 재헌(3-3 공동)·예진(회사 서버 조회 계약)
이 문서는 설계안이다. 코드는 바꾸지 않았다. 코드 위치는 `backend/domains/data/src/data_agent/` 기준이다(따로 적은 곳 빼고).
표의 숫자 예시는 실제 데이터가 아니라 **모양 예시**다.

## 0. 배경

- 여러 회사 설계(`docs/superpowers/specs/2026-11-20-multi-company-design.md`) 3단계: 고객 원본(주문·주문 줄·환불·고객 가명 `buyer_id`·속성)을 AXcellent에 쌓지 않는다.
- **3-1(이 문서)**: 일별 집계 저장소·계약. "회사·날짜·차원 → 숫자"만 저장한다. 가명 id·주문 id·문의 원문은 없다.
- **3-2**: 매출·고객 분석이 `sales.db` 대신 집계를 읽는다. 집계에 없는 질문은 회사 서버를 그때그때 조회한다.
- **3-3**: `sales_collect`를 끈다(재현·재헌). `sales.db`는 지우지 않고 보관한다.
- 여러 회사 설계의 미결 6번(k값·보관 기간)은 이 문서 1절에서 정한다.

### 0.1 이미 정한 것 (다시 묻지 않는다)

**작은 칸 숨김 (k)**
- 지금 카탈로그 `[sales] min_group_size`를 그대로 쓴다(캣북스 5, 기본 5, `schemas/data.py:583`). 새 k를 만들지 않는다.
- 숨김은 집계를 **읽을 때뿐 아니라 저장할 때도** 적용한다. k 미만 칸은 숫자 없이 "k 미만" 표시만 저장한다.
- 보완 숨김(`segment_analysis.protect`)도 같은 규칙으로 쓴다.

**보관**
- 일별 행은 13개월(전년 같은 달 비교용) 둔다.
- 그보다 오래된 것은 월 단위로 합쳐 5년 둔 뒤 지운다.

**저장하지 않는 것**
- `buyer_id`(가명 포함), `order_id`
- 주문 시각(분 단위)
- 개인 속성 원값 조합이 k 미만인 칸

**저장해도 되는 것**
- 회사 상품 id·상품 이름·분류·장르 코드(회사 카탈로그이고 개인정보가 아니다)
- 유입 source·medium·campaign
- 쿠폰 코드

### 0.2 이 문서가 새로 정한 큰 원칙 하나

k 숨김을 **저장할 때** 적용하면, 작은 칸을 저장해 두었다가 더해서 큰 칸을 만드는 방식은 쓸 수 없다. 숨긴 작은 칸이 합에서 빠지기 때문이다. 그래서 숫자를 두 종류로 나눈다.

| 종류 | 예 | 저장 단위 | k |
| --- | --- | --- | --- |
| **더할 수 있는 숫자** (사람 차원 없음) | 주문 수·매출·수량·쿠폰 할인·포인트·환불액 | 날짜 × 상품·유입·쿠폰 (일별 행) | 적용하지 않는다. 사람 수도 속성도 없는 칸이라 0.1의 "속성 원값 조합" 규칙에 걸리지 않는다. 지금 매출 분석도 k를 쓰지 않고 `min_product_orders`(증감 비교 표본)만 쓴다 |
| **사람을 세는 숫자** | 서로 다른 구매자 수, 새·재구매자 수, 코호트, 떠날 위험, 함께 산 사람, 속성 묶음 | 화면에 나가는 묶음 그대로, 정해진 기간마다 미리 계산 (기간 스냅샷) | 저장할 때 적용(k 미만 표시 + 보완 숨김) |

## 1. 지금 읽는 곳과 필요한 것

### 1.1 공통 사실 (모든 읽기 함수)

**결제 완료**
- 조건: `payment_status = 'succeeded'`이고 상태가 `cancelled·canceled·payment_failed·pending_payment·refunded_full`이 아니다(`sales_clean.py:22-25,62-64`).
- 부분 환불은 결제 완료에 남는다.

**기간**
- 모든 기간은 끝이 **지금(now)** 이다.
- 매출 분석은 `today·7d·30d`이고, 한국 시간 오늘 0시에서 N−1일 전 0시부터 지금까지다(`sales_quality.py:25,38-51`). 비교 기간은 같은 길이를 앞으로 민 것이라 "같은 시각까지" 비교다.
- 고객 분석은 `1d·7d·30d·90d·365d`이고 직전 기간이 없다(`skills/segment_analysis.py:140,170-174`).
- **계약에 임의 기간(from/to)은 없다.** `SegmentPeriod = "30d"|"90d"|"365d"|"1d"|"7d"`(`schemas/data.py:3519`)이고, 화면이 고르는 것은 `SCREEN_PERIODS = 1d·7d·30d·90d`(`segment_analysis.py:149`)뿐이다.
- 저장소 함수 `SalesStore.segment_session(start, end)`는 임의 시각을 받지만 계약으로는 열려 있지 않다.

**관문**
- `gate`(`sales_quality.py:162-185`)가 막는 경우는 둘이다.
  - 마지막 수집이 `max_staleness_minutes`(30)보다 오래됐을 때
  - 결제 완료 **주문 수**가 `min_period_orders`(20)보다 적을 때
- 고객 분석 관문 `snapshot`(`segment_analysis.py:221-251`)이 더 읽는 것:
  - 고객 수
  - 품질 정보: 주문이 있는 날, 카탈로그에 없는 분류·장르 코드, 정제에서 버린 것, 속성 값

**구매자 요약 표**
- `sales_buyer_summary`·`sales_buyer_items`(`sales_summary.py:47-75`)에 구매자마다 첫·두 번째·마지막 결제 시각, 결제 주문 수, 첫 주문 유입이 이미 있다. 항목(장르·상품·분류)마다는 첫·마지막 결제 시각이 있다.
- 요약 표가 준비되면 고객 분석은 "기간 안 결제가 있다"를 `last_paid_at >= 시작`으로 센다(`sales_store.py:952-968`).
- **이 요약 표가 4절의 "회사 서버 구매자 요약"과 같은 모양이다.** 원본 대신 회사 서버에서 같은 요약을 받으면 된다.

**속성**
- 속성은 기간과 상관없는 지금 값이다. `sales_customer_attributes`에 시점 칸이 없다.
- 캣북스 속성: `age_band`(10s·20s·30s·40s·50s_plus), `gender`(female·male·no_answer), `occupation`(student·employee·self_employed·other·no_answer) (`catalogs/catbooks.toml:165-191`).

### 1.2 읽기 함수별 표 (`sales_reads.py`)

판정은 네 가지다.
- **(a)** 일별 집계로 그대로 됨
- **(b)** 기간 스냅샷이 필요
- **(c)** 회사 서버 조회로 대신
- **(d)** 원본 없이 못 함

| 함수 (skill) | 지금 SQL이 세는 것 | 기간·차원 | k | 판정과 대신하는 방법 |
| --- | --- | --- | --- | --- |
| `sales_analysis` (`skills/sales_analysis.py:245`, `sales_sql.tally`) | **금액·주문 수**: 결제 완료 주문 수·쿠폰 주문 수, 줄 순매출(줄 금액 − 쿠폰·포인트 몫)을 분류·상품·유입(source·medium)별로. 상품이 든 주문 수(DISTINCT order_id). 기간 안 환불은 환불 시각으로 빼고 줄 순매출 비율로 나눈다(`allocate`).<br>**사람 수**: 구매자 수, 재구매 고객 수(기간 시작 전에도 결제 완료 주문이 있는 사람), 새·재구매 매출(같은 기준)(`sales_sql.py:131-206`) | `today·7d·30d`, 끝 = 지금, 비교 = 같은 시각까지 | 없음 (`min_product_orders`만) | 금액·주문 수는 **(a)**. 다 지난 날은 일별 행으로 더하고, 오늘 0시~지금과 비교 기간 끝날(같은 시각까지)은 **(c)** 그날만 회사 서버에서 읽어 계산한다(저장 안 함).<br>구매자 수·새/재구매는 **(b)**. 날짜별로 더할 수 없다. 정의를 "어제까지"로 바꿀지는 결정 필요 1 |
| `segment_options` (`segment_analysis.py:470-504`) | 세는 것 없음. 카탈로그 값과 출처가 가짜(fake)인지만(`sync_state.source`) | - | 값만 전달 | **(a)** 집계 저장소의 "계산 기록" 줄에서 출처를 읽는다 |
| `segment_profile` (`segment_analysis.py:520-608`) | 전부 **서로 다른 구매자 수**.<br>기간 안 결제 완료 구매자를 속성 값별로, 대상(분류·장르·상품 하나)이 든 주문의 구매자를 속성 값별로 센다. 분모는 그 속성을 아는 사람이다. lift = 대상 비율 ÷ 전체 비율 | 기간 5개, 대상 종류 3개 × 코드, 속성은 하나씩 출력 | 대상 구매자 < k면 전체를 숨긴다. 값 칸 < k(0 포함)는 숨기고, "모름"은 0 < n < k일 때만 숨긴다. `protect` 반복 | **(b)** 기간 × 대상 × 속성 하나 × 값의 구매자 수 스냅샷. 상품 대상은 그 기간 구매자 ≥ k인 상품만 줄을 만들고, 나머지는 "k 미만" 한 줄 |
| `segment_favorites` (`segment_analysis.py:647-698`) | 서로 다른 구매자 수: 조건(속성 AND)에 맞는 구매자, 항목(장르·분류·상품)마다 전체 구매자와 묶음 구매자. share·lift | 기간 5개, 조건 = 속성 1개 이상의 AND, 항목 3종 | 묶음 < k면 전체를 숨긴다. 항목 n < k는 숫자 없이 "k 미만". `protect`는 쓰지 않음 | **(b)** 기간 × 조건 조합(카탈로그 속성의 모든 부분집합 × 값) × 항목 스냅샷. 캣북스는 속성 3개(값 5·3·5)라 조합이 (5+1)(3+1)(5+1)−1 = 143개다. 항목은 n ≥ k만 숫자로 두고, 상품은 상위 20개만 둔다(입력 `top` ≤ 20).<br>상품 이름 규칙(기간 안 처음 나온 줄 이름)은 집계 계산 때 정해 행에 넣는다. 트렌드 보고서 `_trend_report_company`도 이 함수(90d)를 쓴다(`handlers.py:1275,1308`) |
| `coverage_buyers` (market_coverage) (`segment_analysis.py:986-1048`) | 최근 365일 서로 다른 구매자를 인구 속성 조합(캣북스 `age_band × gender`) 칸별로. 모름이 하나라도 있으면 빼고 셈 | `365d` 고정, 속성 2개 교차 | 칸 < k(0 포함) 숨김, 행·열·전체 합 묶음으로 `protect` | **(b)** 365d × 인구 조합 칸 스냅샷. 저장 전에 같은 `protect` |
| `event_base` (event_proposal) (`skills/event_proposal.py:308-374`) | 서로 다른 구매자: 묶음(속성 1~2개 값 조합) 크기, 대상 고객(최근 `active_days`=365일), 묶음 × 장르 구매자, 장르별 상위 상품 3개(구매자 수 순), 장르 쌍 함께 산 사람.<br>**금액**: 장르 객단가 = 장르 줄 순매출 ÷ 그 장르가 든 결제 완료 주문 수. 환불은 빼지 않음 | 90d 고정(입력 없음), `active_days` | 묶음·대상·장르 n < k면 후보에서 버림(숨김이 아님). 쌍은 together ≥ k만 | 객단가는 **(a)** 일별 줄 행(장르 순매출·장르가 든 주문 수)을 90일 더한다.<br>나머지는 **(b)** 90d 묶음 × 장르 구매자, active_days 구매자, 장르 쌍 스냅샷 |
| `retention` (`skills/retention.py`) | 서로 다른 구매자.<br>**코호트**: 첫 결제 달(한국, 전체 기록), 첫 구매 뒤 30·60·90일 안에 결제 완료 주문이 2건 이상인 사람(`REPEAT_DAYS`, `sales_store.py:174`). 다 안 지난 칸은 없음.<br>**단골**: 결제 완료 주문 ≥ `repeat_min_orders`(2). **떠날 위험**: 단골 중 마지막 결제 < 지금 − `churn_days`(60). 단골이 가장 많이 산 장르 | 입력 `cohorts` 1~6, `filters`. 기간 칸 없음(기록 전체, 관문만 365d) | 코호트 달 고객 < k면 숨김. 재구매 0 < r < k면 그 칸 숨김. 떠날 위험은 k 미만(0 포함) 숨김 + `protect` | **(b)** 기준일 × 코호트 달 × 속성 칸, 기준일 × 속성 칸 스냅샷.<br>**구매자마다 첫·두 번째·마지막 결제 시각과 결제 주문 수가 필요**하다 → 4절. 회사 서버가 못 주면 "확인 못 함" |
| `basket_pairs` (`skills/basket_pairs.py`) | 같은 **사람**이 기간 안에 둘 다 산 쌍(주문 단위가 아님, `schemas/data.py:3861`). together·a·b 구매자, confidence, lift = together × 전체 ÷ (a × b). 장르 또는 상품 | 기간 5개, level 2개, filters(속성 AND), `max_pairs` ≤ 20 | 전체 < k면 없음. 쌍 < k는 SQL에서 빼고 남는 자리를 "k 미만"으로 채움 | **(b)** 기간 × level × 조건 조합 × 쌍 상위 20 + "k 미만" 자리 수 스냅샷.<br>"함께 산 주문 수"는 지금 코드가 세지 않는다. 새 칸으로 둘지는 결정 필요 5 |
| `channel_value` (`skills/channel_value.py`) | 새 고객 = 첫 결제 완료 주문(전체 기록, 접수 시각·주문 id 순)이 기간 안인 사람. 첫 주문의 `src_source` 하나로 귀속(first-touch, medium·campaign 안 씀). 첫 구매부터 `value_horizon_days`(90) 안: 순매출(상품 금액 − 쿠폰 − 포인트 − 환불, 환불은 환불 시각 기준)·주문 수·주문 2건 이상인 사람. horizon이 덜 지난 사람은 빼고 수를 남김 | 기간 5개(기본 365d), 출처 | 경로 새 고객 < k 숨김 + `protect` | **(b)** 기준일 × 기간 × 출처 스냅샷.<br>구매자마다 첫 결제 시각·첫 유입과 horizon 안 주문 금액이 필요하다 → 4절 |

### 1.3 `sales.db`를 직접 여는 다른 곳

| 곳 | 무엇 | 3-2·3-3 뒤 |
| --- | --- | --- |
| `sales_collect.py` (L148-232, 384-534) | 유일한 쓰기. `upsert`·`upsert_customers`·`save_cursor`·`finish_sync`, `--reset`(파일을 `.bak-<시각>`으로 옮기고 처음부터), `--rebuild-summary`. `run_all.sh:633-637`이 300초마다 띄운다 | 3-3에서 끈다. `--reset` 요약 읽기(`order_span`·`orders_by_source`·`tally`)는 같이 없어진다 |
| `sales_summary.py` | 구매자 요약 표 `refresh`(upsert 거래 안)·`rebuild` | 병행 기간에는 집계 입력(구매자 요약)으로 쓴다. 3-3 뒤에는 회사 서버 요약으로 |
| `handlers.py:417-425` (`get_sales_store`) + 위 9개 핸들러 | 읽기 진입점 | 3-2에서 집계 읽기로 바꾼다 |
| `main.py:110-118` | 시작 때 `ensure_summary`, 미리 계산 `run_forever` | 미리 계산은 "집계 계산이 끝나면 한 번"으로 바꾼다 |
| `sales_cache.py:41,188-207` | 캐시 키 = 저장소 버전·`cache_token`·`store_id` | 키를 "집계 계산 번호(run id)"로 |
| `sales_quality.py` | 품질: 버린 것, 주문이 있는 날, 모르는 코드, 출처 상태, 속성 값 | 품질 일별 행(3.8)에서 |
| `sales_sql.py` | 매출 분석 집계 `tally` | 집계 계산 함수로 옮긴다(5절: 같은 함수, 입력만 다름) |

**`sales.db`를 쓰지 않는 곳**
- `campaign_effect`·`campaign_result`는 판매 서버 커넥터에서 바로 읽는다.
- `kpi_summary`·`impact_analysis`·`data_chat`·`product_link`도 `sales.db`를 쓰지 않는다.
- 다른 도메인에는 참조가 없다.

**테스트**: 판매 관련 테스트 파일 19개(`domains/data/tests/test_sales_*`·`test_segment_*`·`test_retention`·`test_basket_pairs`·`test_channel_value`·`test_event_proposal`·`test_other_company_analysis`·`test_no_company_labels`).

## 2. 더할 수 있는 숫자와 없는 숫자 (가장 중요)

### 2.1 더할 수 있는 숫자 → 일별 행

| 숫자 | 날짜 기준 | 근거 |
| --- | --- | --- |
| 결제 완료 주문 수, 쿠폰 주문 수, 유입별 주문 수 | 접수 날짜(한국) | 주문 한 건은 한 날에만 있다 |
| 줄 순매출, 수량, 쿠폰 할인, 포인트 | 접수 날짜 | 위와 같음. 줄 몫이 주문 할인과 다른 주문은 `line_nets`·`allocate`로 똑같이 나눈다 |
| 환불액 | **환불 날짜** | 지금 정의 그대로(기간 안 환불은 환불 시각으로 뺀다). 줄 순매출 비율로 상품·분류·유입에 나눈 값을 그 날짜 행에 둔다 |
| 상품이 든 주문 수, 장르가 든 주문 수 | 접수 날짜 | 한 주문은 한 날에만 있으므로 날짜끼리 더해도 된다. **상품끼리는 더하면 안 된다**(한 주문에 상품이 여럿). 그래서 주문 단위 행과 줄 단위 행을 따로 둔다(3.1) |

### 2.2 더할 수 없는 숫자 → 기간 스냅샷

- "서로 다른 구매자 수"는 날짜별로 더하면 안 된다. 같은 사람이 이틀 사면 두 번 센다.
- 속성 칸끼리는 더해도 된다. 한 사람은 속성 칸 하나에만 있다.
- 다만 0.2 원칙(저장할 때 k 숨김) 때문에 작은 칸을 저장해 더하는 방식은 쓰지 않는다. 화면 묶음 그대로 저장한다.
- 그래서 정해진 기간의 구매자 숫자를 **매일 새로 계산**해 기간 스냅샷 행으로 둔다.
  - 정해진 기간: 최근 1·7·30·90·365일, 이번 달, 지난달
  - 1일은 지금 계약의 `1d` 때문에 더했다
- 지난달은 매달 1일에 한 번 계산하고 고정한다. 기간이 기준일보다 앞에서 끝나면 `last_paid_at >= 시작`으로는 셀 수 없기 때문이다(4.2).

### 2.3 화면이 임의 기간을 고르는 곳

- 지금은 없다(1.1). 계약 기간은 모두 위의 정해진 기간 안에 있다.
- 앞으로 임의 기간이 생기면 이렇게 한다.
  - 금액·주문 수: 일별 행을 더해 정확하게 낸다.
  - 구매자 숫자: 가장 가까운 정해진 기간으로 맞추고 "최근 30일 기준" 같은 말을 붙인다. 그 기간이 꼭 필요하면 회사 서버 조회(c)로 그때 계산하고 저장하지 않는다.

### 2.4 "지금까지" 기간의 끝

- 지금 기간은 모두 끝이 지금이다. 집계는 하루 한 번(한국 0시 뒤) 만들기 때문에 스냅샷의 끝은 **어제 24시**다.
- 금액·주문 수는 다 지난 날을 일별 행으로 더하고, 오늘 0시~지금과 비교 기간의 끝날 몫만 회사 서버에서 그날 주문을 읽어 더하면 지금과 같은 숫자가 된다(c, 저장 안 함).
- 구매자 숫자는 이렇게 이을 수 없다. 결정 필요 1.

## 3. 집계 표 설계

**저장소**
- 새 SQLite `aggregates.db`(`DATA_AGGREGATES_DB`, 기본 `backend/.data/aggregates.db`)
- 회사 폴더를 켜면 회사마다 파일을 둘지는 결정 필요 7

**모든 행의 공통 칸**
- `tenant_id`
- `computed_at`: 계산 시각, UTC
- `source_cursor`: 원본 기준 시각. 병행 기간에는 `sales.db`의 판매 출처 `sync_state.cursor`와 마지막 수집 시각, 3-3 뒤에는 회사 서버 응답의 기준 시각
- `run_id`: 계산 번호

**숨긴 칸**: 숫자 칸을 NULL로 두고 `hidden = 1`("k 미만")을 둔다. 0은 숫자로 둔다. 다만 지금 규칙이 0도 숨기는 칸(속성 값 칸·인구 칸)은 지금처럼 숨긴다.

### 3.1 일별 매출 `agg_sales_daily`

주문 단위 행과 줄 단위 행 두 단(`grain`)으로 둔다. 주문 수를 상품끼리 더하지 않게 하려는 것이다.

| 칸 | 키 | 설명 |
| --- | --- | --- |
| `day` | 키 | 한국 날짜. 주문 숫자는 접수 날짜, 환불은 환불 날짜 |
| `grain` | 키 | `order`(상품 칸 비움) · `line` |
| `product_id`, `category_code`, `genre_code` | 키 | `line`만. 없으면 빈 글자 |
| `src_source`, `src_medium`, `src_campaign` | 키 | 없으면 빈 글자 |
| `coupon_used` | 키 | 0/1 (주문에 쿠폰 할인 > 0) |
| `orders` | 숫자 | `order`: 결제 완료 주문 수. `line`: 그 상품(장르)이 든 결제 완료 주문 수 |
| `revenue_net` | 숫자 | 줄 순매출 합 (환불 전) |
| `qty` | 숫자 | 수량 |
| `coupon_discount`, `points_used` | 숫자 | `order`만 |
| `refund_amount` | 숫자 | 그 날 환불액 (`line`은 줄 순매출 비율로 나눈 몫) |
| `created_orders` | 숫자 | `order`만. 상태 무관 접수 주문 수 (품질 보고 `rows_read`·`created`용) |

- k 없음(0.2).
- 매출 분석의 매출 = Σ `revenue_net` − Σ `refund_amount`.

예시 (모양만):

| day | grain | product_id | category_code | genre_code | src_source | src_medium | src_campaign | coupon_used | orders | revenue_net | qty | refund_amount |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-23 | order | | | | instagram | social | winter_sale | 1 | 12 | 312000 | 15 | 0 |
| 2026-11-23 | line | P-1042 | novel | mystery | instagram | social | winter_sale | 1 | 4 | 58000 | 4 | 14500 |

### 3.2 상품 이름표 `agg_products`

- 칸: `product_id`, `title`, `category_code`, `genre_code`, `first_day`, `last_day`
- 지금 이름 규칙은 "기간 안 마지막 분류와 마지막으로 비지 않은 이름"(`sales_sql.product_names`)이다. 이를 날짜별 마지막 값으로 둔다(`day`를 키에 넣은 `agg_product_days`). 기간별 이름은 그 기간 마지막 날 값을 쓴다.
- 고객 분석 상품 이름은 "기간 안 처음 나온 줄 이름"이라 규칙이 다르다. 스냅샷 행에 이름을 같이 넣는다.

### 3.3 기간 스냅샷 구매자 `agg_buyers_period`

| 칸 | 키 | 설명 |
| --- | --- | --- |
| `as_of` | 키 | 기준일(한국). 기간 끝 = 그날 24시 |
| `period` | 키 | `1d·7d·30d·90d·365d·this_month·last_month` |
| `dim_kind` | 키 | `all`·`category`·`genre`·`product`·`attr`(속성 하나)·`attr_combo`(조건 조합) |
| `dim_code` | 키 | 분류·장르·상품 코드 (없으면 빈 글자) |
| `attr_cell` | 키 | `age_band=20s;gender=female` 꼴(키 순서 = 카탈로그 순서). 없으면 빈 글자 |
| `buyers` | 숫자 | 서로 다른 구매자 |
| `new_buyers`, `repeat_buyers` | 숫자 | 첫 결제가 기간 시작 이후 / 이전 |
| `new_revenue`, `repeat_revenue` | 숫자 | `dim_kind = all`·`attr_cell` 비움 줄만 (매출 분석 고객 구성용, 금액이라 숨기지 않음) |
| `title` | | 상품일 때 기간 안 처음 나온 이름 |
| `hidden` | | 1이면 숫자 칸 NULL |

- **숨김**: `buyers` < k면 숨긴다. 화면이 함께 내보내는 묶음(한 대상의 속성 값 칸 전부 + 모름, 인구 조합의 행·열·전체)을 저장 전에 `protect`로 같이 처리한다. 규칙은 지금 `profile_hidden`·`coverage_groups` 그대로다.

예시 (모양만):

| as_of | period | dim_kind | dim_code | attr_cell | buyers | new_buyers | repeat_buyers | hidden |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-23 | 90d | genre | mystery | age_band=30s | 41 | 18 | 23 | 0 |
| 2026-11-23 | 90d | genre | mystery | age_band=10s | | | | 1 |

### 3.4 코호트 `agg_cohorts`

| 칸 | 키 | 설명 |
| --- | --- | --- |
| `as_of` | 키 | 기준일 |
| `cohort_month` | 키 | 첫 결제 달 `2026-08` (한국) |
| `attr_cell` | 키 | filters 조합 (`retention`의 filters와 같은 꼴, 비우면 전체) |
| `first_buyers` | 숫자 | 그 달 첫 구매 고객 |
| `repeat_30`, `repeat_60`, `repeat_90` | 숫자 | 첫 구매 뒤 N일 안 결제 완료 주문 2건 이상인 사람. 그 달 끝 + N일이 기준일보다 뒤면 NULL(`not_due`) |
| `hidden_*` | | 칸별 숨김 |

- 규칙은 지금 `retention` 그대로다: 첫 구매 고객 < k면 줄 숨김, 0 < r < k면 그 칸 숨김.
- 지금 달은 넣지 않는다. 과거 6개 달(입력 `cohorts` 최대 6)을 둔다.

예시 (모양만):

| as_of | cohort_month | attr_cell | first_buyers | repeat_30 | repeat_60 | repeat_90 |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-11-23 | 2026-08 | | 230 | 31 | 52 | 66 |
| 2026-11-23 | 2026-10 | gender=female | 96 | 12 | (not_due) | (not_due) |

### 3.5 떠날 위험 단골 `agg_at_risk`

| 칸 | 키 | 설명 |
| --- | --- | --- |
| `as_of` | 키 | 기준일 |
| `attr_cell` | 키 | 카탈로그 속성 값 전부의 조합(지금 출력 그대로) |
| `regulars` | 숫자 | 결제 완료 주문 ≥ `repeat_min_orders` |
| `at_risk` | 숫자 | 단골 중 마지막 결제 < 기준일 24시 − `churn_days` |
| `top_genre` | | 그 칸 단골이 가장 많이 산 장르 |
| `hidden` | | k 미만(0 포함) + `protect` |

- `churn_days`·`repeat_min_orders`는 계산할 때 카탈로그 값을 쓰고, 줄에 그 값을 남긴다(설정이 바뀌면 다음 계산부터).

예시 (모양만):

| as_of | attr_cell | regulars | at_risk | top_genre |
| --- | --- | --- | --- | --- |
| 2026-11-23 | age_band=30s;gender=female;occupation=employee | 58 | 9 | essay |
| 2026-11-23 | age_band=10s;gender=male;occupation=student | | | (hidden) |

### 3.6 함께 산 상품 `agg_pairs`

| 칸 | 키 | 설명 |
| --- | --- | --- |
| `as_of`, `period` | 키 | 기간 스냅샷 |
| `level` | 키 | `genre`·`product` (분류는 지금처럼 뺀다) |
| `attr_cell` | 키 | filters 조합 (비우면 전체) |
| `code_a`, `code_b` | 키 | a = 산 사람이 더 많은 쪽 |
| `together_buyers`, `a_buyers`, `b_buyers`, `overall_buyers` | 숫자 | 사람 단위 (지금 정의) |
| `together_orders` | 숫자 | 같은 주문에 함께 든 수 (새 칸, 결정 필요 5) |
| `hidden_slots` | | 그 조건에서 together < k라 숫자 없이 채운 자리 수 |

- 상위 20쌍(lift → together → code 순)만 둔다.

예시 (모양만):

| as_of | period | level | attr_cell | code_a | code_b | together_buyers | a_buyers | b_buyers | overall_buyers |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-23 | 90d | genre | | mystery | thriller | 37 | 140 | 88 | 1210 |
| 2026-11-23 | 90d | genre | age_band=20s | essay | poetry | 11 | 46 | 25 | 310 |

### 3.7 속성 묶음 프로필 `agg_segment_profile`

| 칸 | 키 | 설명 |
| --- | --- | --- |
| `as_of`, `period` | 키 | |
| `attr_cell` | 키 | 조건 조합(즐겨 사는 것) 또는 속성 하나(프로필) |
| `item_level`, `item_code` | 키 | `category`·`genre`·`product` |
| `buyers` | 숫자 | 그 묶음에서 그 항목을 산 사람 (지금 favorites의 n) |
| `item_buyers` | 숫자 | 그 항목을 산 전체 사람 (favorites의 total, lift 분모) |
| `orders`, `revenue` | 숫자 | 그 묶음 사람의 그 항목 결제 완료 주문 수·줄 순매출 (요청에 있는 칸. 지금 화면은 쓰지 않는다) |
| `title`, `hidden` | | |

- **숨김**: `buyers` < k면 숫자 없이 둔다(지금 favorites 규칙). 묶음 크기 < k면 그 묶음 줄을 하나만 두고 숨긴다.
- 상품은 묶음마다 상위 20개.
- event_proposal의 "묶음 × 장르 구매자"도 이 표를 쓴다(`attr_cell`이 속성 1~2개, `period = 90d`).

예시 (모양만):

| as_of | period | attr_cell | item_level | item_code | buyers | item_buyers | orders | revenue |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-23 | 90d | age_band=20s;gender=female | genre | essay | 27 | 160 | 35 | 498000 |
| 2026-11-23 | 90d | age_band=20s;gender=female | product | P-2210 | | 44 | | |

### 3.8 채널 가치 `agg_channel_value`와 품질 `agg_quality_daily`

**채널 가치** (`agg_channel_value`)
- 키: `as_of`, `period`, `source`(카탈로그 이름으로 묶기 전 `src_source`, 빈 글자 = 직접)
- 숫자: `new_buyers`, `horizon_net`, `horizon_orders`, `repeaters`, `excluded_incomplete`
- 숨김: `new_buyers` < k + `protect`(지금 규칙)
- 출처를 화면 이름("기타 경로")으로 묶는 것은 저장 전에 한다. 묶은 뒤에 k를 보기 때문이다. 줄 키는 화면 이름이다.

**품질** (`agg_quality_daily`)
- 키: `day`
- 숫자:
  - `created_orders`, `paid_orders`
  - `dropped_by_reason`(이유 → 수, JSON)
  - `unknown_codes`(분류·장르 코드 → 수)
  - `unmapped_attributes`(속성 key·값 → 수)
  - `customers`(고객 수, 기준일 값)
- 주문 번호는 남기지 않는다.

## 4. 첫 구매·마지막 구매를 원본 없이 아는 방법

### 4.1 정한 방법

- 재구매·코호트·떠날 위험·채널 가치는 구매자마다 첫·두 번째·마지막 결제 시각, 결제 주문 수, 첫 주문 유입이 필요하다.
- 집계를 계산할 때(하루 한 번) 회사 서버에서 그날 바뀐 구매자의 요약을 받는다. 요약은 첫 결제일·마지막 결제일·결제 주문 수·속성이다.
- 받은 요약은 메모리에서만 쓰고 숫자만 저장한다. 구매자 id를 담은 표는 만들지 않는다.
- 지금 `sales_buyer_summary`·`sales_buyer_items`(1.1)가 이 요약과 같은 모양이다. 병행 기간에는 그 표에서 읽는다.

### 4.2 정한 방법에서 확인한 빈틈 (결정 필요 2)

"그날 바뀐 구매자만" 받으면 맞출 수 없는 숫자가 있다.

- **떠날 위험**: 아무것도 안 산 사람도 날짜가 지나면 떠날 위험으로 넘어간다. 바뀐 구매자 목록에 없으니 다시 셀 수 없다.
  - 마지막 결제일별 사람 수(히스토그램)를 숫자로 유지하는 방법도 있다. 하지만 구매자가 다시 사면 그 사람의 **예전** 마지막 결제일 칸에서 빼야 하는데, id 표가 없어 예전 값을 모른다.
- **취소 되돌림**: 결제 완료가 취소(`refunded_full`)로 바뀌면 그 사람의 첫·두 번째 결제가 뒤로 밀릴 수 있다. 코호트 숫자를 빼고 다시 더하려면 예전 값이 필요하다.
- **기간 구매자**(2.2): 기준일 기간의 구매자 수는 "그 기간에 결제가 있는 모든 사람"이 필요하다. 바뀐 사람만으로는 셀 수 없다.

제안
- 회사 서버 조회를 둘로 둔다. **전체 구매자 요약**(페이지로 전부)과 **바뀐 구매자 요약**(커서)이다.
- 하루 계산은 전체를 한 번 메모리로 훑어 스냅샷을 다시 만든다.
- 바뀐 구매자는 "오늘 다시 계산할 날짜"를 찾는 데만 쓴다(5.3).
- 메모리 크기: 캣북스 구매자 수는 확인 필요. 구매자 한 명에 수십 바이트 + 항목 수라서, 수십만 명이면 수백 MB 안이다(확인 필요).

### 4.3 회사 서버 조회 계약 초안 (`data_access/shop_buyer_summaries.py`, 추가만)

`GET /api/shop/buyer-summaries?as_of&cursor&limit&changed_since` → `ShopBuyerSummariesResult`. 읽기 전용이다.

```python
class ShopBuyerSummariesQuery(AxModel):
    as_of: UtcDatetime              # 이 시각 전 결제만 센다 (기준일 24시). 오늘 주문이 섞이지 않게
    cursor: str | None = None       # (buyer_id) 오름차순. 커서 글자 모양은 회사 서버만 안다
    limit: int = 500                # 1..1000
    changed_since: UtcDatetime | None = None  # 있으면 그 뒤 결제·취소·속성이 바뀐 구매자만
    tenant_id: str = TENANT_ID_DEFAULT

class BuyerItemSummary(AxModel):
    level: Literal["genre", "category", "product"]
    code: str
    first_paid_at: UtcDatetime
    last_paid_at: UtcDatetime
    paid_orders: int

class BuyerSummaryRow(AxModel):
    buyer_id: str                   # 가명. 받는 쪽은 메모리에서만 쓰고 저장하지 않는다
    first_paid_at: UtcDatetime | None   # 결제 완료 주문이 없으면 None
    second_paid_at: UtcDatetime | None
    last_paid_at: UtcDatetime | None
    paid_orders: int = 0
    first_src_source: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)  # 고객 내보내기와 같은 꼴
    items: list[BuyerItemSummary] = Field(default_factory=list)
    tenant_id: str = TENANT_ID_DEFAULT

class ShopBuyerSummariesResult(AxModel):
    buyers: list[BuyerSummaryRow] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    as_of: UtcDatetime              # 회사 서버가 센 기준 (집계 행 source_cursor로 남긴다)
    tenant_id: str = TENANT_ID_DEFAULT
```

- "결제 완료" 정의는 1.1과 같다. 회사 서버가 정의를 바꾸면 숫자가 달라지므로 응답에 `paid_definition_version`을 둘지 결정 필요 6.
- 채널 가치의 horizon 안 금액(첫 구매부터 90일 안 순매출·주문 수·환불)은 이 요약으로는 모른다. 아래 판매 기록 기간 조회로 그 구간 주문을 읽어 메모리에서 계산한다.

**판매 기록 내보내기(`data_access/shop_sales.py`)에 선택 칸 추가 (추가만)**
- `ShopSalesExportQuery.created_from`, `created_to`: 접수 시각 범위. 날짜 하나 다시 계산, 오늘 0시~지금 계산에 쓴다.
- `ShopSalesExportQuery.refunded_from`, `refunded_to`: 환불 시각 범위. 그날 환불이 있는 주문(접수는 옛날일 수 있음)에 쓴다.
- 지금 칸(`cursor`·`limit`)과 같이 쓰면 범위 안에서 `(updated_at, order_id)` 순으로 이어 받는다.

### 4.4 변환기와 맞출 점 (예진 님)

- 이 문서는 "A형 = 회사 서버가 shop-api 규격을 직접 구현(캣북스 shop-api)", "B형 = 사이트 변환기(`dashboard-api/site_adapter`)가 회사 API를 짝 파일로 규격에 맞춤"으로 읽었다. **확인 필요.**
- B형에서 `sales-export` 범위 조회:
  - 변환기 판매 기록은 지금 `(updated_at, order_id)` 커서로 이어 읽는다(#1525).
  - 회사 API가 접수 시각·환불 시각 범위 거르기를 줄 수 있는지 짝 파일에 칸이 필요하다.
  - 못 주면 변환기가 커서로 훑으며 거른다(느림).
- B형에서 `buyer-summaries`: 회사 API에 구매자 요약이 없으면 변환기가 주문 전체를 커서로 훑어 메모리에서 만들어야 한다. 하루 한 번이고 회사 API 호출 수가 많아진다. 짝 파일에 "요약 직접 제공 / 변환기가 계산 / 못 함" 칸을 둘지 맞춘다.
- 속성 `attributes`는 고객 내보내기와 같은 key·값 code다(카탈로그 `[sales] attributes`).
- 회사 서버가 이 조회를 못 하면 그 회사는 재구매·코호트·떠날 위험·채널 가치·구매자 수 스냅샷을 **"확인 못 함"** 으로 보여 준다(지어내지 않는다). 금액·주문 수(일별 행)는 판매 기록만으로 된다.

## 5. 계산 흐름과 첫 채우기

### 5.1 하루 계산

- 한국 0시 뒤(기본 00:30, 회사마다 한 번) 수집기 회차에서 돈다.
- 기준일 = 어제.
- 순서:
  1. 다시 계산할 날짜를 찾는다(5.3).
  2. 그 날짜들의 일별 행(매출·품질)을 다시 만들어 바꿔 넣는다.
  3. 구매자 요약 전체를 메모리로 읽는다.
  4. 스냅샷(3.3~3.8)을 기준일로 만든다.
  5. 계산 기록 줄을 남긴다(`agg_runs`: run_id, 기준일, 입력 길(`sales_db`·`company_server`), 원본 기준 시각, 행 수, 결과).
- 원본(주문·구매자)은 메모리에서만 쓰고 저장하지 않는다.
- 실패하면 그날 스냅샷은 만들지 않는다. 화면은 가장 최근 성공한 기준일을 "11월 22일 기준"으로 보여 준다.

### 5.2 두 길, 같은 함수

- 계산 함수는 `aggregate.build_day(day, orders, refunds) -> rows`와 `aggregate.build_snapshots(as_of, buyers, orders_in_windows) -> rows` 하나씩이다.
- 입력만 다르다.
  - **병행 기간(3-3 전)**: `sales.db`(SalesStore 읽기 연결)에서 날짜 범위 주문과 구매자 요약 표를 읽어 넘긴다.
  - **3-3 뒤**: 회사 서버 `sales-export`(범위)와 `buyer-summaries`를 읽어 넘긴다.
- 매출 숫자 계산은 지금 `sales_sql`·`sales_summary`의 정의(결제 완료, `line_nets`, `allocate`, 환불 시각)를 그대로 옮긴다. 원본 경로와 숫자가 같아야 6절 비교가 0차이로 끝난다.

### 5.3 다시 계산할 날짜

- 주문은 나중에 바뀐다(취소·환불·결제 성공). 지금 수집도 `updated_at`이 바뀐 주문을 다시 받아 덮어쓴다.
- 하루 계산은 지난 실행 뒤 바뀐 주문(`updated_at` 커서)을 받아 날짜를 모은다. 그 주문의 접수 날짜와 새 환불의 환불 날짜다.
- 모은 날짜마다 그날 전체(범위 조회)로 일별 행을 다시 만든다. 13개월보다 오래된 날짜는 월 행을 다시 만든다.
- 구매자 스냅샷은 매일 전체를 새로 만들어 영향이 없다.

### 5.4 첫 채우기 (backfill)

- `python -m data_agent.aggregate --backfill`: `sales.db`에 있는 기간 전체로 일별 행을 만든다.
  - 13개월보다 오래된 것은 월 행으로 만든다.
  - 스냅샷은 오늘 기준일 하나와, 지난 13개월 월말 기준일(월 행 보관용)을 만든다.
- 다시 돌려도 같은 결과(같은 키를 바꿔 넣음).
- 데이터 AI가 시작할 때는 자동으로 돌리지 않는다(사람이 명령으로).

### 5.5 보관

- 일별 행(`agg_sales_daily`·`agg_quality_daily`)은 13개월 둔다. 그보다 오래된 것은 `agg_sales_monthly`로 합쳐 5년 둔 뒤 지운다.
- 스냅샷 표는 기준일마다 쌓지 않는다. 날마다 쌓으면 기준일 × 묶음으로 커진다. 그래서 이렇게 둔다.
  - 최근 35일 기준일은 날마다
  - 그 앞 13개월은 월말 기준일 하나
  - 그 앞 5년은 월말 하나
  - 그 뒤는 지운다
- 이 "월 단위로 합치기"를 스냅샷에서는 "월말 기준일만 남기기"로 읽었다(결정 필요 4).

## 6. 병행 비교 계획 (3-3 시작 조건)

### 6.1 방법

- 캣북스에서 하루 이상(제안: 연속 3일) 같은 질문을 원본 경로(지금 함수, `sales.db`)와 집계 경로(3-2 함수, `aggregates.db`)로 둘 다 계산해 비교한다.
- **같은 정의로 비교한다.** 원본 경로도 기준일 24시까지로 다시 센다. 결정 필요 1에서 화면 정의를 바꾸면, 비교는 바뀐 정의로 원본 경로를 다시 돌린 값과 한다.

### 6.2 비교 대상과 허용 차이

| 대상 | 허용 차이 |
| --- | --- |
| 매출 분석 주요 숫자(7d·30d): 매출, 주문 수, 쿠폰 주문 수, 분류별 매출, 상품 증감 상위·하위, 유입별 매출·주문 | 금액·주문 수 0. 순위(상품 상위)는 상위 10 순서가 같음 |
| 매출 분석 고객 구성: 구매자 수, 재구매 고객, 새·재구매 매출 | 수·금액 0 |
| 코호트 재구매율(cohorts=6, filters 없음과 속성 하나씩) | 소수 첫째 자리까지 같음. 숨김 칸 위치가 같음 |
| 함께 산 상품 상위 10(90d, genre·product, filters 없음) | 상위 10 순서가 같음, 수 0 |
| 채널 가치(365d) | 1인 매출·주문은 소수 첫째 자리, 재구매율은 소수 첫째 자리, 숨김 같음 |

- 다르면 원인을 이 문서(부록 "병행 비교 기록")에 적고 3-3을 미룬다.

### 6.3 결과를 남기는 방법

- `aggregates.db`의 `agg_compare` 표에 한 비교마다 한 줄을 둔다(기준일, 대상, 같음/다름, 다른 칸 이름과 두 값). 구매자 id·주문 id는 남기지 않는다.
- 수집기 로그에 한 줄: `[aggregate] 병행 비교 11월 23일: 같음 18 · 다름 0`
- 데이터 출처 화면에 한 줄: "일별 집계 병행 비교: 3일 연속 같음(마지막 11월 23일)". 지금 데이터 출처(`skills/data_sources.py`)에 판매 출처 줄이 있는지는 확인 필요다. 없으면 수집기 상태 칸 옆에 둔다.
- 다르면 "다름 2 — 자세히" 링크로 `agg_compare` 줄을 보여 준다.

## 7. 계약 초안 (shared/contracts, 추가만)

구현 PR에서 그대로 쓸 이름이다. 새 파일 `data_access/sales_aggregates.py`(집계 행)와 4.3의 `data_access/shop_buyer_summaries.py`(회사 서버 조회), `data_access/shop_sales.py`의 선택 칸 4개다.

```python
# data_access/sales_aggregates.py
AggPeriod = Literal["1d", "7d", "30d", "90d", "365d", "this_month", "last_month"]
AggGrain = Literal["order", "line"]
AggInput = Literal["sales_db", "company_server"]

class AggRowBase(AxModel):
    tenant_id: str = TENANT_ID_DEFAULT
    run_id: str
    computed_at: UtcDatetime
    source_cursor: str | None = None      # 원본 기준 (병행: sync 커서, 뒤: 회사 서버 as_of)

class SalesDailyRow(AggRowBase):
    day: dt.date
    grain: AggGrain
    product_id: str = ""
    category_code: str = ""
    genre_code: str = ""
    src_source: str = ""
    src_medium: str = ""
    src_campaign: str = ""
    coupon_used: bool = False
    orders: int = 0
    revenue_net: int = 0
    qty: int = 0
    coupon_discount: int = 0
    points_used: int = 0
    refund_amount: int = 0
    created_orders: int = 0

class HiddenCount(AxModel):
    """사람 수 칸: hidden이면 value None ("k 미만")."""
    value: int | None = None
    hidden: bool = False

class BuyerPeriodRow(AggRowBase):
    as_of: dt.date
    period: AggPeriod
    dim_kind: Literal["all", "category", "genre", "product", "attr", "attr_combo"]
    dim_code: str = ""
    attr_cell: dict[str, str] = Field(default_factory=dict)
    buyers: HiddenCount
    new_buyers: HiddenCount
    repeat_buyers: HiddenCount
    new_revenue: int | None = None
    repeat_revenue: int | None = None
    title: str | None = None

class CohortRow(AggRowBase):
    as_of: dt.date
    cohort_month: str                      # "2026-08"
    attr_cell: dict[str, str] = Field(default_factory=dict)
    first_buyers: HiddenCount
    repeat: dict[Literal["30", "60", "90"], HiddenCount | None]  # None = 아직 다 안 지남

class AtRiskRow(AggRowBase):
    as_of: dt.date
    attr_cell: dict[str, str]
    regulars: HiddenCount
    at_risk: HiddenCount
    top_genre: str | None = None
    churn_days: int
    repeat_min_orders: int

class PairRow(AggRowBase):
    as_of: dt.date
    period: AggPeriod
    level: Literal["genre", "product"]
    attr_cell: dict[str, str] = Field(default_factory=dict)
    code_a: str
    code_b: str
    together_buyers: int
    a_buyers: int
    b_buyers: int
    overall_buyers: int
    together_orders: int | None = None     # 결정 필요 5
    hidden_slots: int = 0

class SegmentProfileRow(AggRowBase):
    as_of: dt.date
    period: AggPeriod
    attr_cell: dict[str, str]
    item_level: Literal["category", "genre", "product"]
    item_code: str
    buyers: HiddenCount
    item_buyers: int
    orders: int | None = None
    revenue: int | None = None
    title: str | None = None

class ChannelValueRow(AggRowBase):
    as_of: dt.date
    period: AggPeriod
    source_label: str                      # 카탈로그 이름으로 묶은 뒤 ("기타 경로" 포함)
    new_buyers: HiddenCount
    horizon_net: int | None = None
    horizon_orders: int | None = None
    repeaters: int | None = None
    excluded_incomplete: int = 0
    horizon_days: int

class QualityDailyRow(AggRowBase):
    day: dt.date
    created_orders: int = 0
    paid_orders: int = 0
    dropped_by_reason: dict[str, int] = Field(default_factory=dict)
    unknown_codes: dict[str, int] = Field(default_factory=dict)
    unmapped_attributes: dict[str, int] = Field(default_factory=dict)
    customers: int | None = None

class AggRun(AxModel):
    run_id: str
    tenant_id: str = TENANT_ID_DEFAULT
    as_of: dt.date
    input: AggInput
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    status: Literal["running", "done", "failed"]
    reason: str | None = None
    source_cursor: str | None = None
    rows: dict[str, int] = Field(default_factory=dict)   # 표 이름 → 행 수

class AggCompareRow(AxModel):
    tenant_id: str = TENANT_ID_DEFAULT
    as_of: dt.date
    target: str                            # "sales_analysis:30d" 등
    same: bool
    diffs: list[str] = Field(default_factory=list)       # "revenue: 1234000 != 1233000"
    compared_at: UtcDatetime
```

- 이 행들은 **저장소 내부 모양**이다. 지금 화면·skill 출력 계약(`SalesAnalysisOutput` 등)은 바꾸지 않는다.
- 근거(`basis`)에 기준일·계산 번호를 넣을 칸이 필요하면 3-2에서 선택 칸으로 더한다(추가만).

## 8. 구현 PR 나누기

| PR | 내용 | 바뀌는 파일 | 테스트 |
| --- | --- | --- | --- |
| **3-1a** 저장소·계약·첫 채우기 | 7절 계약, `aggregates.db` 저장소, `build_day`·`build_snapshots`(입력 = `sales.db`), `--backfill`, 보관 지우기 | `shared/contracts/.../data_access/sales_aggregates.py`, `shop_buyer_summaries.py`, `shop_sales.py`(선택 칸), `data_agent/aggregate/{store,build_day,build_snapshots,backfill}.py` | 계약 기본값·옛 응답 읽기, 일별 행이 지금 `tally`와 같은 숫자(가짜 판매 데이터), 숨김(k 미만·0·`protect`), id 칸이 어떤 표에도 없음(스키마 검사), 보관(13개월 → 월 행), 다시 돌려도 같음, 회사별 격리 |
| **3-1b** 하루 계산 | 5.1 하루 계산, 5.3 다시 계산할 날짜, `agg_runs`, 수집기 회차에 걸기, 병행 비교 `agg_compare`와 로그·데이터 출처 한 줄 | `data_agent/aggregate/daily.py`, `compare.py`, `youtube_collect.py` 또는 판매 수집기 회차, `skills/data_sources.py` | 취소·환불이 과거 날짜를 다시 계산, 실패하면 마지막 성공 기준일 유지, 비교 표가 같음·다름을 남김, 하루 한 번만 |
| **3-2** 읽기 함수 바꾸기 | 순서: ① `sales_analysis`(일별 + 오늘 회사 서버) ② `segment_options` ③ `channel_value` ④ `retention` ⑤ `basket_pairs` ⑥ `segment_profile`·`segment_favorites`·`coverage_buyers` ⑦ `event_base` ⑧ 미리 계산·캐시 키 ⑨ 관문(마지막 수집 → 마지막 집계 기준일) | `sales_reads.py`, `skills/*.py`(읽기 부분만), `sales_cache.py`, `sales_quality.py`, `handlers.py`, `main.py` | 함수마다 원본 경로와 집계 경로가 같은 출력(6.2 허용 차이), 집계가 없을 때 "확인 못 함", 회사 서버 조회를 못 하는 회사 |
| **3-3** 수집 끄기 | `run_all.sh`·스케줄에서 `sales_collect`를 뺀다. 입력 길을 `company_server`로 바꾼다. `sales.db`는 보관 | `backend/scripts/run_all.sh`, `data_agent/aggregate/daily.py`(입력 고르기) | 병행 비교 3일 같음 확인 기록, `sales.db` 없이 모든 판매 skill이 돈다(시험), 되돌리기(수집 다시 켜기) 절차 |

## 9. 못 하게 되는 것과 그 대신

| 기능 | 3-3 뒤 | 사용자에게 보이는 것 |
| --- | --- | --- |
| 오늘 0시~지금 숫자 (매출 분석 today, 7d·30d의 오늘 몫) | (c) 회사 서버 조회(저장 안 함) | 숫자는 그대로. 근거에 "오늘 몫은 회사 서버에서 바로 조회했어요" |
| 지금까지 기간의 구매자 숫자 | (b) "어제까지" 스냅샷 (결정 필요 1) | "어제(11월 23일) 기준" 표시 |
| 회사 서버가 구매자 요약을 못 주는 회사의 재구매·코호트·떠날 위험·채널 가치·구매자 수 | 못 함 | "이 회사 서버는 구매자 요약을 주지 않아 확인할 수 없어요"(숫자 없음, 지어내지 않음) |
| 판매 원본 다시 받기·되감기(`sales_collect --reset` 명령, 리셋 요약) | (d) 없어진다 | 화면에는 없는 명령이라 운영 문서에서만 뺀다. 집계 다시 채우기는 `--backfill`(병행 기간에만) |
| 정제에서 버린 주문의 주문 번호(`sales_dropped`) | (d) 이유별 수만 | "버린 주문 12건(금액 음수 3·수량 0 9)" — 번호 없음 |
| 앞으로 생길 임의 기간의 구매자 질문 | (c) 회사 서버 조회 또는 가까운 정해진 기간 | "이 질문은 원본이 필요해 회사 서버에서 바로 조회해요" |
| 집계 계산이 실패한 날 | 마지막 성공 기준일 | "11월 22일 기준(어제 계산 실패: 이유)" |

- 지금 코드에서 구매자 한 사람을 찾는 질문(그 사람이 산 것 등)은 판매 skill에 없다. 데이터 대화(`data_chat.py`)도 판매 저장소를 읽지 않는다(grep 0건). 그래서 이 종류로 없어지는 기능은 없다.

## 10. 결정이 필요한 것

"이미 정한 것"(0.1)은 묻지 않는다.

1. **"지금까지" 기간의 구매자 숫자 정의**: 화면 기간은 끝이 지금이고, 구매자 수는 날짜별로 더할 수 없다(2.4).
   - 추천: 구매자 숫자(고객 분석 전부, 매출 분석의 구매자·재구매·새/재구매 매출)는 "어제 24시까지"로 바꾸고 "어제 기준"을 표시한다.
   - 다른 안: 요청마다 회사 서버에서 그 기간 구매자를 세서 지금 정의를 유지한다(저장 안 함, 30d·365d는 무겁다).
2. **회사 서버 구매자 요약을 "바뀐 구매자만"이 아니라 "전체 + 바뀐 구매자" 둘로**: 4.2의 빈틈(떠날 위험·취소 되돌림·기간 구매자) 때문이다. 정한 방법을 바꾸는 것이라 재현·예진 확인이 필요하다.
3. **일별 매출 행에 k를 적용하지 않는다**는 읽기(0.2): 사람 수·속성이 없는 칸이라 "속성 원값 조합이 k 미만인 칸"에 해당하지 않는다고 봤다. 상품 × 날짜 × 캠페인에 주문 1건인 줄도 숫자로 남는다. 이렇게 읽으면 되는지 확인이 필요하다.
4. **스냅샷 보관**: "13개월 뒤 월 단위로 합치기"를 스냅샷에서는 "월말 기준일만 남기기"로 읽었다. 최근 35일은 날마다 둔다(5.5). 35일이 맞는지.
5. **함께 산 상품의 "함께 산 주문 수"**: 요청 표에 있지만 지금 코드는 사람 단위만 센다. 새 칸(`together_orders`)으로 더할지, 화면에서 쓸지.
6. **결제 완료 정의의 판**: 회사 서버가 결제 완료를 세는 규칙이 바뀌면 집계 숫자가 달라진다. 응답에 `paid_definition_version`을 둘지.
7. **저장소 파일**: `aggregates.db` 하나에 `tenant_id` 칸만 둘지, 회사 폴더(`.data/companies/<회사>/aggregates.db`)로 나눌지. 여러 회사 설계 3.2의 data 줄은 "칸"이다. 추천은 칸 하나에 회사별 격리 시험이다.
8. **하루 계산 시각**: 00:30(한국)으로 둘지. YouTube 할당량은 17:00에 바뀌는데 이것과는 상관없다. 회사 서버 부하 시간대는 회사마다 확인이 필요하다.
9. **오늘 몫 회사 서버 조회의 캐시**: 지금 판매 캐시처럼 화면 요청에 예전 결과를 먼저 주고 뒤에서 갱신할지, 몇 분마다 다시 읽을지(제안: 5분).

## 부록. 병행 비교 기록

(3-1b가 돌기 시작하면 날짜별로 여기에 적는다. 다르면 원인과 고친 PR.)
