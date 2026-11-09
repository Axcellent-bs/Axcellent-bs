# campaign_effect 매출(증분 판매액) 평가

재무(캠페인 손익)가 쓰는 **증분 판매액**을 data `campaign_effect`가 맞게 내는지 잰다. 평가 정의만 있고, 판정 코드는 다른 PR(`feat/data-campaign-revenue`)이다.

```bash
cd backend
uv run python domains/data/eval/campaign_revenue/make_cases.py            # cases/ 다시 만들기
uv run python domains/data/eval/campaign_revenue/make_cases.py --holdout  # holdout/ (숫자만 본다)
uv run python domains/data/eval/campaign_revenue/score.py --judge <모듈>:<함수>
```

## 데이터
결제·캠페인 평가와 같은 생성기(`eval/eval_world.py`, shop-api 코드)로 만든 합성 쇼핑몰. 이 평가를 위해 생성기에 두 칸을 더했다(없으면 예전과 똑같이 만든다).
- `basket.qty_factor`: 구간 안 주문의 수량 배수 (주문 수는 그대로, 금액만 늘림)
- `payment_gap.days_ago`: 그 날(0 = 구간, k = k일 전 같은 시간대) 주문은 만들고 결제하지 않음 (결제 기록 공백, shop-api에는 결제 대기 주문으로 남는다)
- 응답에 `GET /api/shop/orders?since&until`(shop-api와 같은 코드)을 더함

## 정답
- **방향은 심은 조건에서**: 주문 수준 배수 × 수량 배수 > 1 이면 up, < 1 이면 down, = 1 이면 none
- 구간 결제 기록 공백 → unknown(payment_gap). 지난 날 하나에만 공백 → 그날을 기준선에서 빼고 판정(정답 known)
- 쿠폰 귀속: 캠페인 쿠폰이 있는 케이스는 쿠폰 주문 판매액 합을 맞혀야 한다
- `counted`(참고값, 생성기 기록에서 바로 셈): 결제 완료 주문의 주문 금액(상품가 × 수량, 할인 전) 합. 증분 = 구간 − 공백이 아닌 지난 7일 같은 시간대 평균

## 케이스
- `cases/` 9개: 주문 2배, 수량 2배(주문 수 그대로), 변화 없음, 절반, 구간 결제 공백, 지난 날 하나 공백, 쿠폰 2배, 쿠폰 변화 없음, 기준선 부족(지난 5일 공백)
- `holdout/` 12개 (seed `2026110401`): 배수·수량·공백·쿠폰을 무작위로. **열어 보지 않고 숫자만 본다**

## 지표
| 지표 | 뜻 |
|---|---|
| `campaign_revenue.direction_rate` | 정답 방향이 있는 케이스 중 맞힌 비율. 정답 none은 uncertain도 맞음 |
| `campaign_revenue.excess_error` | 증분 판매액 오차율 = \|답 − 센 값\| ÷ 기준선 판매액 (평균) |
| `campaign_revenue.gap_unknown_rate` | 구간 결제 공백을 unknown + "결제 기록 공백"으로 답한 비율 |
| `campaign_revenue.direct_match` | 쿠폰 주문 판매액 합이 센 값과 같은 비율 |
| `campaign_revenue.short_baseline_unknown_rate` | 공백이 아닌 지난 날이 최소(3일)보다 적은 케이스를 unknown + "기준선 부족"으로 답한 비율 (`short_baseline_2h_14h`: 5일 공백 → 2일). holdout에는 이 케이스가 없다 |
