# campaign_effect 평가 케이스 (캠페인 효과)

data 에이전트 `campaign_effect`(아직 없음)의 정답지다. 채점기는 skill PR에서 같이 만든다. 평가 정의 변경은 코드 수정과 별도 PR로 한다(`eval/README.md` 규칙).

```bash
cd backend
uv run python domains/data/eval/campaign/make_cases.py            # cases/ 다시 만들기
uv run python domains/data/eval/campaign/make_cases.py --holdout  # holdout/ 다시 만들기
uv run pytest -q domains/data/tests/test_campaign_eval_cases.py   # 약속 검사 (다시 만들기 비교는 -m eval)
```

## 데이터

결제 평가와 같은 생성기(`eval/eval_world.py`)로 만든다. 판매자·상품·구매자는 shop-api 시드, 주문·결제·쿠폰은 shop-api 코드(`Shop.place_order`·`pay`, `Promotions.issue_coupon`)로 넣는다. 5분마다 주문 수는 시간대 수준을 평균으로 한 포아송이고, 시나리오 id로 씨앗을 고정해 늘 같은 데이터다.

- **주문 증감은 합성 주입이다.** 캠페인 구간의 주문 수준에 정해진 배수를 곱한다: `injection.type` sale(늘림)·order_drop(줄임)·none(그대로). 기준선은 지난 7일 같은 시간대다(생성기가 함께 만든다).
- **캠페인 쿠폰**(`spec.campaign`, 생성기에 이번에 추가): 지난 7일보다 먼저 구매자 `buyers`명에게 캠페인 쿠폰(`campaign_id`, 한 사람 `units_each`장)을 발급한다. 구간 안에서 그 구매자의 주문은 `use_fraction` 확률로 쿠폰을 쓴다. 구간이 끝난 뒤 5분 간격으로 `used_after`장을 더 쓴다. 이 장수는 구간 안에서 쓰지 않게 남겨 둔다(조건과 기록이 늘 같게).
- `campaign`이 없는 시나리오는 예전과 똑같은 데이터다. 쿠폰 결정은 따로 씨앗을 둔 난수로 한다. 이 PR에서 기존 결제 평가 케이스 73개(cases·holdout)의 결제·주문 기록 지문이 바뀌지 않은 것을 확인했다.
- 평가용 shop-api 응답(`World.transport`)에 `GET /api/shop/coupons`(shop-api와 같은 조회 코드 `Promotions.list_coupons`)를 더했다.

## 정답 (`expected`) — 주입 조건에서 정한다

| 항목 | 정답 | 어디서 |
| --- | --- | --- |
| `orders` | `direction`(up·none·down) + `multiplier`(넣은 배수) | 주입 조건. 데이터를 보고 정하지 않는다 |
| `revenue` | `direction` (주문 방향과 같음) | 주입 조건. 쿠폰 할인만큼 매출 배수는 주문 배수보다 작을 수 있어 배수는 정하지 않는다 |
| `coupon` (있을 때) | `issued_units`(발급 장수), `used_after_units`(구간 뒤 사용 장수), `note`("조회 시점 기준 누적 사용률이며 캠페인 뒤 사용분도 섞인다") | 조건 |
| 판정 불가 | `status: unknown` + `unknown_reason` | 조건: `no_history`(구간·지난 7일 주문 기록 없음), `no_campaign_id`(요청에 campaign_id 없음), `no_coupons`(그 캠페인 쿠폰이 없음) |

`counted`는 생성기가 만든 기록에서 바로 센 참고값이다(data 에이전트 코드로 세지 않는다): 구간 주문 수·지난 7일 평균, 결제 성공 금액 합·지난 7일 평균, 쿠폰 발급·구간 안 사용·구간 뒤 사용·조회 시점 누적 사용률. 숫자 비교(증분·사용률 오차)가 필요하면 채점기가 이 값을 쓴다.

- 증분 = 구간 실제 − 지난 7일 같은 시간대 평균(기대). 예: `up_x2_2h_14h`는 주문 287 vs 기준 151.1
- 쿠폰 누적 사용률 = (구간 안 사용 + 구간 뒤 사용) ÷ 발급 장수. 조회 시점 값이라 캠페인 뒤 사용분이 섞인다

## 케이스 (`cases/` 7개)

| 케이스 | 주문 | 쿠폰 | 보는 것 |
| --- | --- | --- | --- |
| `up_x2_2h_14h` | 2배 (up) | 20명 × 3장, 30%, 구간 뒤 3장 | 증분 +, 누적 사용률에 구간 뒤 몫 |
| `flat_2h_10h` | 그대로 (none) | 15명 × 3장, 30% | 변화 없음을 증가·감소로 말하지 않는지 |
| `down_half_2h_19h` | 절반 (down) | 15명 × 3장, 30%, 구간 뒤 2장 | 감소도 그대로 말하는지 |
| `up_x3_30m_10h` | 3배, 30분 | 10명 × 3장, 60% | 짧은 캠페인 |
| `up_x2_no_campaign_id_1h_14h` | 2배 | 없음 (campaign_id 없이 요청) | 쿠폰은 확인할 수 없음(`no_campaign_id`), 주문·매출은 냄 |
| `flat_no_coupons_2h_10h` | 그대로 | 그 캠페인 쿠폰 없음 | 쿠폰 확인할 수 없음(`no_coupons`), 0%로 내지 않음 |
| `no_history_1h_14h` | 기록 없음 | 없음 | 주문·매출 확인할 수 없음(`no_history`), 0으로 채우지 않음 |

## holdout (처음 보는 케이스)

- seed **`2026110203`**, 12개. 무작위로 고르는 것: 배수(0.5·0.7·1.0·1.5·2.0·3.0), 시작 시각(9~20시), 날짜(10-14 기준 0~20일 전), 길이(1·2·3시간), 쿠폰 받은 사람(8~20명), 사용 확률(0.1~0.6), 구간 뒤 사용(0~4장)
- **holdout 케이스 내용을 열지 않는다.** 판정 코드를 고칠 때 holdout을 보고 맞추지 않고, 결과 숫자만 본다(`eval/README.md` holdout 규칙과 같다). 테스트는 다시 만든 결과와 같은지·개수·조건과 기록이 맞는지만 기계로 본다.
- 내용을 열어 분석했으면 그 seed는 더 이상 처음 보는 케이스가 아니다. 새 seed로 다시 만들고 아래 표에 이유를 적는다.

| seed | 만든 날 | 이유 |
| --- | --- | --- |
| `2026110203` | 2026-11-02 | 처음 만듦 |

## skill PR에 넘길 것 (이 PR에서 확인한 것)

- **매출 시계열이 없다.** shop-api `metric-series`는 `order_count`·`order_fail_count`·`payment_fail_rate`·`payment_count`만 준다. "매출을 metric_series로 기준선 비교"는 지금 그대로는 안 된다. 정답(`revenue.direction`)은 이 PR에 넣어 두고, skill에서 매출을 어디서 읽을지(shop-api에 매출 지표 추가 또는 확인할 수 없음)는 따로 정한다.
- **쿠폰은 읽을 수 있다.** shop-api `COUPON_READERS`에 data가 있고, `GET /api/shop/coupons?campaign_id=`가 쿠폰마다 `count`(발급 장수)와 `used_units`·`remaining_units`(조회 시점 계산)를 준다. 다만 이 응답 모양은 `shared/contracts`에 없다(shop-api 코드에만 있다).
