# campaign_effect 유입경로 기준 평가 케이스 (#648)

data 에이전트 `campaign_effect`의 유입경로 기준 결과(`attribution_effect`, 유입 + 관련 상품 `attribution_and_products`)를 채점하는 정답지와 채점기(`score.py`)다. **이 평가 정의 PR에서는 지표를 관찰만 하고 관문이 없다.** 관문과 기준값은 skill PR에서 켠다. 평가 정의 변경은 코드 수정과 별도 PR로 한다(`eval/README.md` 규칙).

```bash
cd backend
uv run python domains/data/eval/campaign_attribution/make_cases.py            # cases/ 다시 만들기
uv run python domains/data/eval/campaign_attribution/make_cases.py --holdout  # holdout/ 다시 만들기
uv run pytest -q domains/data/tests/test_campaign_attribution_eval.py   # 약속 검사
uv run python domains/data/eval/campaign_attribution/score.py --judge <모듈>:<함수>   # 채점
```

## 데이터

결제·캠페인 평가와 같은 생성기(`eval/eval_world.py`)로 만든다. 주문·결제는 shop-api 코드로 넣는다.

- **유입 표시는 합성 주입이다**(`attribution.tags`, 생성기에 이번에 추가): 구간 안 주문마다 따로 둔 난수로 태그 하나(출처 `src_source`·매체 `src_medium`·캠페인 `src_campaign`, 비율 `fraction`)를 붙이거나 붙이지 않는다. shop-api `place_order`의 `src_*` 칸 그대로다(#655). 다른 캠페인 태그도 같은 방식이다. `attribution`이 없는 시나리오는 예전과 똑같은 데이터다(다른 평가의 다시 만들기 비교로 확인).
- 평가용 shop-api 응답(`World.transport`)에 `GET /api/shop/orders/attributed`(shop-api와 같은 조회 코드 `Lookups.attributed_orders`)를 더했다.

## 정답 — 조건과 생성기 기록에서 정한다

| 항목 | 정답 |
| --- | --- |
| 유입 표시 없음 | 구간에 유입 표시가 있는 주문이 하나도 없으면 unknown(`no_tags`, 이유에 "유입 기록 없음") |
| 이 캠페인 유입 | 요청 `campaign_id`로 들어온 **결제 완료** 주문의 수·판매액(단가 × 수량, 할인 전), 출처별 |
| 다른 캠페인 | 다른 `src_campaign` 주문은 세지 않는다 (다른 캠페인만 있으면 0건, known) |
| 유입 표시 비율 | 유입 표시(어느 캠페인이든)가 있는 결제 완료 주문 ÷ 구간 결제 완료 주문 |
| 유입 + 관련 상품 | 이 캠페인으로 들어온 결제 완료 주문 중 관련 상품 품목만 센 주문 수·수량·판매액 |

직접 귀속(주문에 남은 표시로 바로 셈)이라 기준선과 비교하지 않는다. 방향 정답은 없다.

## 케이스

`cases/` 6개

| id | 심은 조건 | 정답 |
| --- | --- | --- |
| `tagged_two_sources_2h_14h` | 이 캠페인: 인스타 20%·구글 10% | 출처별 주문·판매액 |
| `no_tags_2h_10h` | 태그 없음 | unknown(유입 기록 없음) |
| `other_campaign_only_2h_14h` | 다른 캠페인 30%만 | 0건 (다른 캠페인 안 셈) |
| `mixed_campaigns_2h_19h` | 이 캠페인 15% + 다른 캠페인 15% | 이 캠페인 것만 |
| `overlap_products_2h_14h` | 이 캠페인 30% + 무지 노트 2배, 상품 요청 | 유입 + 관련 상품 겹침 |
| `overlap_no_tags_1h_10h` | 태그 없음, 상품 요청 | 유입·겹침 모두 unknown |

`holdout/` 10개: 씨앗 `2026110601`로 태그 유무(85%)·출처 1~3개·다른 캠페인 섞임(30%)·상품 요청(40%)·시각을 뽑는다.

## 채점 (`score.py`, 관찰만)

| 지표 | 뜻 |
| --- | --- |
| `campaign_attribution.count_match` | known 케이스에서 주문 수·판매액·출처별 값이 모두 센 값과 같은 비율 |
| `campaign_attribution.share_error` | 유입 표시 비율 오차 (평균 절대값) |
| `campaign_attribution.no_tag_unknown_rate` | 유입 표시 없는 케이스를 unknown + "유입 기록 없음"으로 |
| `campaign_attribution.other_campaign_excluded_rate` | 다른 캠페인 주문이 있는 케이스에서 그 주문을 세지 않은 비율 |
| `campaign_attribution.overlap_match` | 유입 + 관련 상품 칸이 센 값과 같은 비율 (unknown 정답 포함) |

## 한계

- 유입 표시는 쇼핑몰 화면이 캠페인 링크의 꼬리표를 주문까지 넘길 때만 남는다. 표시가 없는 주문(직접 방문·꼬리표 유실)은 세지 않는다.
- 광고 데이터(노출·클릭·비용) 결합은 하지 않는다(#647 광고 부분 대기).
