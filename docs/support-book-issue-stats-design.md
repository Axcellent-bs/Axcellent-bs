# 책별 문의·클레임·반품 집계 skill (설계 요약)

상태: 초안(코드 없음). 제안: 배병환(고객지원), 2026-11-14. 연령별 베스트셀러 파이프라인(`age-bestseller-pipeline-design.md`)에서
"고객지원이 먼저 만들어 넘기는 우리 쪽 사실" 중 첫째 항목이다.

## 목적
이벤트·캠페인 예산을 논의할 때 "인기는 있는데 불만이 많은 책"을 걸러 낼 근거를 고객지원이 준다.
데이터는 책별 문의·클레임·반품을 만들 수 없고(고객지원만 읽는다), 마케팅·재무·기획은 이 숫자로
"이 책에 이벤트 비용을 올려도 되는가"를 판단한다. 멘토 피드백(2026-11-13)의 A2A 협업 장면에 고객지원이 들어가는 길이다.

## 원칙
- 숫자는 모두 코드가 기록에서 센다. LLM을 쓰지 않는다. 모르면 `None` + 이유 (0으로 채우지 않는다).
- 읽기 전용(grade read). 상태를 바꾸지 않아 `execute()`를 거치지 않는다.
- 고객 원문·이름·구매자 ID는 싣지 않는다. 건수·금액·분류 코드만.
- 새 계약이 필요 없는 범위로 시작한다: 고객지원 안의 skill 하나 + 내부 스키마(`support_agent`). 다른 부서가
  쓰게 되는 시점에 `shared/contracts`에 **추가만** 한다.

## 이미 있는 재료
| 재료 | 위치 | 책(상품) 단위 정보 |
|---|---|---|
| 클레임 | `claims.py`, `ClaimLine` | `order_item_id`, `title`, 환불액·반품 수량. **`product_id` 없음** |
| 주문 품목 | `orders.py` `OrderItem` | `id`, `title`, `qty`. **`product_id` 없음** (판매 서버 `order_items.product_id`에는 있음) |
| 문의 | 문의 저장소 | 주문(`order_id`)에만 연결. 품목 단위 아님 |
| 기존 집계 | `claim_stats`, `ClaimStats` | 전체 건수·상태별·규칙별뿐. 책별 없음 |

핵심 빈칸: 고객지원 쪽 품목에 **상품 ID가 없다.** 제목(`title`)으로 묶으면 제목이 바뀌거나 같은 제목이 둘일 때 틀린다.

## 집계 정의 (기간별, 상품 하나당 한 줄)
- `claim_count`: 그 상품 품목이 들어간 클레임 수 (종류별 `cancel`·`return`·`exchange` 따로)
- `defect_claim_count`: 사유가 불량·파손인 클레임 수 (사유 코드는 `ClaimReason` 그대로)
- `return_qty`: 반품 입고된 수량, `refund_cash_total`: 지급한 환불 합계 (정수 원)
- `order_qty_in_claim_orders` 대비 비율(`claim_rate`): 분모는 판매 서버에서 읽은 같은 기간 그 상품 주문 수량.
  분모를 못 읽으면 `None` + 이유 (건수만 준다)
- `inquiry_count`: 그 상품이 든 주문에 연결된 문의 수. **주문 단위라 여러 품목 주문이면 그 주문의 모든 상품에 센다.**
  결과에 `inquiry_attribution: "order_level"`을 항상 같이 싣고, 품목이 하나뿐인 주문만 센 값(`inquiry_count_single_item`)도 준다.
- `top_categories`: 문의 분류(`categories.py` 코드) 상위 3개와 건수

## skill 모양 (초안)
`book_issue_stats` (읽기)

입력: `period`(`day|week|month`, 기본 `month`), `limit`(기본 20), `min_claims`(기본 1), 선택 `product_ids`
출력: `items: list[BookIssueRow]`, `missing: list[str]` (못 읽은 값과 이유), `period_start/end`, `generated_at`

```python
class BookIssueRow(AxModel):
    product_id: str                      # 필수. 없으면 이 줄은 만들지 않고 missing에 적는다
    title: str                           # 화면용. 묶는 열쇠가 아니다
    claim_count: int
    claims_by_type: dict[str, int]
    defect_claim_count: int
    return_qty: int
    refund_cash_total: int
    sold_qty: int | None                 # 판매 서버에서 읽은 같은 기간 주문 수량. 못 읽으면 None
    claim_rate: float | None             # claim 품목 수량 / sold_qty. sold_qty가 없으면 None
    inquiry_count: int
    inquiry_count_single_item: int
    inquiry_attribution: Literal["order_level"] = "order_level"
    top_categories: list[tuple[str, int]]
    reason: str                          # 코드가 만든 한 줄 근거 (예: "불량 3건·반품 2권, 판매 40권 중 7.5%")
```

## 필요한 작은 변경
1. `support_agent.orders.OrderItem`과 `claim_schemas.ClaimLine`에 `product_id: str | None = None`을 **추가**한다.
   판매 서버 주문 응답에서 이미 읽는 값(`order_items.product_id`)을 `to_order`에서 같이 옮긴다.
   옛 클레임(상품 ID 없음)은 `None`으로 두고 집계에서 "상품 ID 없는 클레임 N건"으로 `missing`에 적는다 (제목으로 추측하지 않는다).
2. 클레임·문의 기록은 보존 기간 뒤 지워지는 글이 있다(`claim_retention.py`). 집계는 코드·수량·금액만 쓰므로 영향이 없는지
   구현 때 확인한다.
3. 판매 서버에서 기간별 상품 판매 수량을 읽는 길이 있는지 확인한다 (없으면 `sold_qty`는 `None`으로 시작하고
   별도 PR로 읽기 경로를 더한다).

## 구현 순서 (작게, 테스트는 끝에 몰아서)
1. `product_id`를 `OrderItem`·`ClaimLine`에 추가하고 `to_order`·클레임 생성에서 채운다.
2. 순수 코드 `book_issue_stats.py`: 클레임·문의 기록 → `BookIssueRow` 계산 (프레임워크에 의존하지 않는다. `insights.py`와 같은 방식).
3. skill 등록(`handlers`, 서비스 카드의 skill 목록, README).
4. (선택) 상담 화면 "고객지원 현황"에 상위 5개 표를 보인다. 출처가 없으면 "준비 중" 표시.
5. 작업이 끝난 뒤 단위·전체 테스트, 실제 모드 확인을 한 번에 한다.

테스트 관점: 같은 상품 여러 클레임 합산, 제목이 다른 같은 상품 ID, 상품 ID 없는 옛 클레임, 분모 없음 → `None`,
주문 단위 문의 귀속(품목 1개/여러 개), 기간 경계, 보존 기간 뒤 지워진 기록.

## 다른 부서에 쓰이는 길 (나중)
- 마케팅·기획이 이벤트 후보 책을 정할 때 A2A로 부른다 (`call_agent`). 그때 계약을 `shared/contracts`에 추가만 한다.
- 연령별 베스트셀러 파이프라인의 `BestsellerTrendRow`에 "불만 많음" 표시로 붙인다 (데이터가 합침, 책 단위 합치기는 `isbn13` 또는 `product_id` 매핑 필요 — 열린 질문).

## 열린 질문
- 판매 서버에 기간별 상품 판매 수량을 읽는 경로가 있는가 (`claim_rate` 분모).
- 문의를 품목에 귀속하는 방식: 주문 단위(위 안)로 충분한가, 문의 접수에 품목 선택을 받아야 하는가 (쇼핑몰 화면 변경 필요).
- 책(`product_id`)과 ISBN 매핑: 상품에 ISBN이 없어 보인다. 연령별 자료(ISBN 기준)와 합칠 때 필요하다.
- 마케팅·재무가 쓰는 시점과 호출 모양 (태윤 님·마케팅 담당과 협의).
