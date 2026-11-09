# campaign_effect 상품 기준 평가 케이스 (#646)

data 에이전트 `campaign_effect`의 상품 기준 결과(`products_effect`, 요청 `products`)를 채점하는 정답지와 채점기(`score.py`)다. 기준값(baseline)은 skill PR에서 처음 만든다. 평가 정의 변경은 코드 수정과 별도 PR로 한다(`eval/README.md` 규칙).

```bash
cd backend
uv run python domains/data/eval/campaign_products/make_cases.py            # cases/ 다시 만들기
uv run python domains/data/eval/campaign_products/make_cases.py --holdout  # holdout/ 다시 만들기
uv run pytest -q domains/data/tests/test_campaign_products_eval.py   # 약속 검사 (다시 만들기 비교는 -m eval)
uv run python domains/data/eval/campaign_products/score.py --judge <모듈>:<함수>   # 채점
```

## 데이터

결제·캠페인 평가와 같은 생성기(`eval/eval_world.py`)로 만든다. 판매자·상품(8개)·구매자는 shop-api 시드, 주문·결제는 shop-api 코드로 넣는다. 5분마다 주문 수는 시간대 수준을 평균으로 한 포아송이고, 시나리오 id로 씨앗을 고정해 늘 같은 데이터다. 주문 하나에 상품 하나(수량 1~2)가 무작위로 들어간다.

- **상품 하나만 늘림은 합성 주입이다**(`product_boost`, 생성기에 이번에 추가): 구간 안 5분 칸마다 그 상품 주문을 평균 `칸 수준 ÷ 상품 수 × (factor − 1)`만큼 더 넣는다. 그 상품의 기대 주문 수가 `factor`배가 된다. 따로 씨앗을 둔 난수라 `product_boost`가 없는 시나리오는 예전과 똑같은 데이터다(다른 평가의 다시 만들기 비교로 확인).
- 가게 전체 증감(`injection` sale·order_drop)은 모든 상품에 같이 곱해진다.
- 결제 기록 공백(`payment_gap.days_ago`)은 매출 평가(`eval/campaign_revenue/`)와 같다. 그날 주문은 만들고 결제하지 않는다.
- 평가용 shop-api 응답(`World.transport`)에 `GET /api/shop/products`(shop-api와 같은 조회 코드 `Shop.list_products`)를 더했다. 이름 매칭에 쓴다.

## 정답 (`expected.products`) — 주입 조건과 카탈로그 시드에서 정한다

| 항목 | 정답 | 어디서 |
| --- | --- | --- |
| `matched`·`unmatched` | 상품 id가 정확히 같거나, 이름을 정규화(NFKC, 소문자, 공백 모두 뺌)한 값이 카탈로그 이름과 정확히 같으면 맞음. 일부만 같은 이름("무지 노트")은 못 맞춤(추측하지 않음) | 시드 `PRODUCTS` |
| 방향(주문 수·수량·판매액) | 요청 상품 배수 = 가게 배수 × (그 상품을 늘렸으면 boost, 아니면 1). > 1 up, < 1 down, = 1 none | 주입 조건 |
| unknown | 맞은 상품 없음 → `no_match`, 구간 결제 공백 → `payment_gap`, 공백이 아닌 지난 날 3일 미만 → `baseline_short` | 주입 조건 |

`counted`는 생성기 기록에서 바로 센 참고값이다(data 에이전트 코드로 세지 않는다). 결제 완료 주문 중 맞은 상품이 든 주문 수, 그 상품 수량 합, 그 상품 금액(단가 × 수량, 할인 전) 합이다. 증분 = 구간 − 공백이 아닌 지난 날 평균.

## 케이스

`cases/` 9개 (개발용, 기준값을 정할 때 본다)

| id | 주입 | 요청 | 정답 |
| --- | --- | --- | --- |
| `product_up_x3_2h_14h` | 무지 노트 3권 세트만 3배 | `prd_0004` | up |
| `product_by_name_x3_2h_10h` | 소설 베스트 3종만 3배 | `" 소설 베스트  3종"` (공백 다름) | up, 이름으로 맞음 |
| `other_product_up_2h_14h` | 젤펜 12색만 3배 | `prd_0004` | none (다른 상품 증가를 세지 않음) |
| `flat_2h_10h` | 없음 | `prd_0001` | none |
| `store_drop_half_2h_19h` | 가게 전체 절반 | `prd_0002` | down |
| `name_unmatched_2h_14h` | 무지 노트만 3배 | `"무지 노트"`, `"없는 상품"` | 둘 다 못 맞춤 → unknown(no_match) |
| `partly_unmatched_x2_2h_14h` | 무지 노트만 2배 | `prd_0004`, `"없는 상품"` | up, 못 맞춘 값은 따로 |
| `gap_window_1h_14h` | 무지 노트 3배 + 구간 결제 공백 | `prd_0004` | unknown(payment_gap) |
| `short_baseline_2h_14h` | 무지 노트 3배 + 지난 5일 결제 공백 | `prd_0004` | unknown(baseline_short) |

`holdout/` 12개: 씨앗 `2026110501`로 가게 배수(0.5·1·1.5)·늘린 상품·boost(1~3)·요청 상품(늘린 상품 60%, 다른 상품 40%)·id 또는 이름·구간 공백(15%)을 뽑는다. 결과 숫자만 보고 케이스를 보고 고치지 않는다.

## 채점 (`score.py`)

| 지표 | 뜻 |
| --- | --- |
| `campaign_products.direction_rate` | 정답 방향이 있는 케이스 × 세 칸 중 맞힌 비율. 정답 none은 "uncertain"도 맞음 |
| `campaign_products.other_product_none_rate` | 다른 상품만 늘린 케이스에서 세 칸 모두 none·uncertain으로 답한 비율 |
| `campaign_products.excess_error` | 판매액 증분 오차율 = \|답 − 센 값\| ÷ 기준선 판매액 (평균) |
| `campaign_products.match_rate` | matched·unmatched가 정답과 같은 케이스 비율 |
| `campaign_products.no_match_unknown_rate` | 맞는 상품 없음을 unknown + "맞는 상품 없음"으로 답한 비율 |
| `campaign_products.gap_unknown_rate` | 구간 결제 공백을 unknown + "결제 기록 공백"으로 답한 비율 |
| `campaign_products.short_baseline_unknown_rate` | 기준선 부족을 unknown + "기준선 부족"으로 답한 비율 |

판정 함수가 오류를 내거나 모양이 틀리면 그 케이스의 모든 항목은 틀린 것(`missing`)으로 센다.

## 한계

- 같은 상품이 캠페인과 다른 이유로 팔린 것은 구분하지 못한다. 정답도 "그 기간 그 상품 판매 변화"다.
- 합성 데이터라 주문 하나에 상품이 하나다. 여러 상품이 든 주문은 없다(상품 기준 주문 수 = 그 상품이 든 주문 수 정의는 그대로 맞다).
