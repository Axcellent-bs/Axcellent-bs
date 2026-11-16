# 연령별 베스트셀러 자료 → 데이터 분석 → 이벤트 근거 (설계)

상태: 초안(계약·집계 코드는 아직 없음). 제안: 배병환(고객지원), 2026-11-13. 2026-11-14 보강, 2026-11-16 재현(데이터) 답·판매 서버 진행을 반영(아래 "진행 상황").

## 진행 상황 (2026-11-16 갱신)
- 예진 님 동의: 판매 서버 `buyers`에 `age_band`(구간만, 생년월일 없음) 선택 칸은 가능. 캣북스는 Catbooks-demo로 옮겨져 가입·프로필 화면도
  그쪽에서 받는다. 구간은 `trend_age`에 맞춘다 (11/13 #소통 스레드).
- 고객지원 쪽 "우리 쪽 사실"은 먼저 만들었다 (분담안): 책별 클레임·불량·반품·환불·문의·클레임 비율 집계 `book_issue_stats`
  (#1096~#1100 머지, 설계 `support-book-issue-stats-design.md`). 캠페인 예산 쪽 연결은 `book-issue-a2a-design.md`(#1102).
  아직 없는 것: 연령대별 건수(`by_age_band`, 구매자 `age_band`가 생겨야 센다), 계약 PR, 베스트셀러 칸.
- **재현 님(데이터) 답, 2026-11-16 — 세 가지 모두 찬성**: ① 일일 보고에 선택 칸만 추가하면 **스키마 버전은 안 올린다**(기존 보고가 그대로 읽히면 충분) ② 연령 구간은 `trend_age` 5개(`10s`·`20s`·`30s`·`40s`·`50s_plus`, 비우면 "연령 모름")로 **확정**, 데이터 쪽 고객 분석도 이 코드를 쓴다 ③ 분담안 찬성: 고객지원은 우리 쪽 사실(연령대별 요청, 책별 문의·클레임·반품), 정보나루·판매·재고를 붙이는 일은 데이터.
- **판매 서버는 새 레포로 옮겨졌다**(`Catbooks-demo/catbooks-demo`, 예진 님). 이 레포(Axcellent-bs)의 `shop-api`에는 올리지 않고 그쪽 `backend/shop-api`에 올린다. 거기에 들어간 것: `buyers.age_band`와 문의·클레임·아이디어 읽기 응답의 `age_band`(**#9 병합, 11/16**), 판매 수량을 상품·연령대·분류별로 합치는 `GET /api/shop/sales-summary`(**#12 병합**), 상품 `isbn13`·`author`·`publisher`(**#14, 리뷰 대기**). 가입·프로필 화면은 예진 님이 `shop-web`에서 한다.
- **새 요청(재현 님)**: 데이터가 "고객·시장 분석"(20대 여성이 좋아하는 책, 이 분야는 누가 사나, 다음 달 이벤트 제안)을 만드는 중이라 판매 서버 회원에 **성별·직업 속성과 상품 장르**가 필요하다. `buyers.age_band`를 두 번 만들지 않게 판매 서버 회원 속성은 같은 방식으로 이어 붙인다(아래 "멘토 요구와 이 설계의 범위"·열린 질문).

## 목적
고객의 의견("20대가 많이 읽는 책으로 기획전을 해 주세요")을 받은 고객지원이, 연령대별 베스트셀러
자료를 근거와 함께 모아 데이터 분석에 넘긴다. 데이터는 우리 쇼핑몰의 실제 판매·재고와 견주어
"이벤트로 밀 만한 책"을 근거로 낸다. 이벤트 여부와 승인은 사람이 정한다.

## 원칙 (이 레포 규칙)
- 데이터를 지어내지 않는다. 외부 자료는 **수집 자료**이고 **검증된 판매량**이 아니다. 출처·수집 시각·
  신뢰 등급을 항상 붙이고, 모르는 값은 `None` + 이유로 둔다.
- 계약은 `shared/contracts`에 **추가만** 한다. `domains`끼리 import하지 않고 A2A(`call_agent`)로만 부른다.
- 상태를 바꾸는 일은 `execute()`를 거친다. 읽기·기록만 하는 skill은 grade read.
- 고객 원문은 넘기지 않는다 (건수·고객 수·대표 말만). `idea.py`와 같다.

## 이미 있는 것 (겹치는 부분)
설계 전에 확인한 결과, 데이터 쪽에 연령별 출처가 이미 있다. **새로 크롤링하는 길보다 이것을 쓰는 게 먼저다.**

| 있는 것 | 위치 | 연령 | 내용 |
|---|---|---|---|
| 도서관 정보나루 `loanItemSrch` | `data/connectors/data4library.py` | 있음 | 연령·성별·KDC별 인기 대출 도서와 대출 횟수. 키 없으면 꺼짐 (`existing=False`) |
| 네이버 쇼핑인사이트 | `data/connectors/naver_shopping.py` | 있음 | 분야·키워드 클릭 추이와 연령대별 비중 (판매량 아님) |
| `trend_age` skill | `data/skills/trend_age.py` | 있음 | 연령대별 "평소 대비 몇 배" 상승 |
| 고객 아이디어 일일 보고 | `contracts/schemas/idea.py` | - | 고객지원 → 데이터, 이벤트·혜택 요청 묶음 (`receive_idea_report`) |
| `product_link` | `data/skills/product_link.py` | - | 소재·제목을 회사 실제 상품과 잇는다 (판매 서버 상품 목록을 읽어 이름·카테고리 일치, 임베딩 유사도). 결과 `LinkedProduct`에 `product_id`와 근거(`match`, `reason`) |
| `book_issue_stats` | `support/book_issue_stats.py` | - | 고객지원이 먼저 만든 우리 쪽 사실: 책(상품)별 클레임·불량·반품·환불·문의·클레임 비율 (#1096~#1100) |

참고: 정보나루 커넥터는 연령을 **나이(연) 범위**(`from_age`·`to_age`)와 성별(`gender`)로 받고, 네이버 쇼핑인사이트·`trend_age`는
**네이버 연령 코드** 묶음을 쓴다. 두 출처의 구간 정의가 달라서 아래 "연령 구간 맞추기"가 필요하다.

붙여 주신 예시(예스24 기사, 블로그 요약)는 이 중 어느 출처도 아니다. 기사 속 "32.7%" 같은 수치는
출처가 블로그·기사라서 숫자 근거로 쓰지 않고, 사람이 입력한 **참고 자료**(신뢰 등급 낮음)로만 받는다.

## 흐름: 고객의 소리와 한 입구로 일·주·월·년 단위

별도 입구를 만들지 않는다. 고객의 소리 일일 보고(`receive_idea_report`)에 **칸을 추가**해서 같은 날짜 한 건으로
같이 보낸다. 데이터는 이미 일일 보고를 쌓아 주·월·연으로 합쳐 주므로(`list_idea_reports`, `IdeaPeriod`),
연령별 베스트셀러도 같은 기간 합산에 태운다.

```
고객 의견 ─▶ [고객지원] 하루 한 번 일일 보고 한 건
                 ├ clusters          (기존) 이벤트·혜택 요청 묶음
                 ├ by_age_band       (추가) 요청을 낸 연령대별 건수·고객 수  ← 구매자 age_band가 있을 때만
                 └ bestseller_groups (추가) 연령대별 베스트셀러 자료 (근거 링크 필수)
                 ▼  receive_idea_report (A2A, grade read)
            [데이터] 일일 보고를 쌓아 일·주·월·년으로 합침
                 ▼  list_idea_reports(period)  (기존 읽기, 응답에 칸 추가)
            [기획·마케팅] 이벤트 근거. 정보나루·우리 판매·재고 대조는 데이터가 같은 응답에 붙임
```

기간 합산 규칙 (기존 규칙을 따른다):
- 주는 월요일 시작. 일일 보고를 받은 날 수(`report_days`)를 같이 보여 준다 (빠진 날을 0으로 채우지 않는다).
- 연령대별 건수·고객 수는 날마다 센 값의 합이다. 여러 날 낸 같은 고객은 날마다 센다 (기존 `customer_count`와 같다).
- 베스트셀러는 기간 안에서 같은 ISBN(없으면 제목 정규화 열쇠)끼리 합쳐 **몇 번의 보고에 나왔는지**와
  가장 높았던 순위를 보여 준다. 출처가 다른 대출 횟수끼리는 더하지 않는다 (출처별로 따로 둔다).

## 계약 초안 (`shared/contracts/src/ax_contracts/schemas/idea.py`에 **추가만**)

기존 모델의 이름·타입은 바꾸지 않는다. 새 칸은 모두 선택(기본값 있음)이라 옛 보고도 그대로 받는다.
`IDEA_REPORT_SCHEMA_VERSION`은 올리지 않고 칸 추가로 처리한다 (데이터와 합의).

```python
AgeBand = Literal["10s", "20s", "30s", "40s", "50s_plus"]
SourceKind = Literal["manual", "library_loan", "retail_rank"]
Trust = Literal["verified", "reference"]  # verified: 출처 API가 준 숫자, reference: 사람 입력·기사


class AgeBandCount(AxModel):
    """요청을 낸 고객의 연령대별 집계. 개인 단위 나이는 싣지 않는다."""
    age_band: AgeBand
    count: int = Field(ge=1)
    customer_count: int = Field(ge=1)


class BestsellerEntry(AxModel):
    rank: int | None = Field(default=None, ge=1)
    isbn13: str | None = None            # 모르면 None. 제목만으로 합치지 않는다
    title: str = Field(min_length=1, max_length=200)
    author: str | None = None
    metric_name: str | None = None       # 예: "대출 횟수" (모르면 None)
    metric_value: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=200)


class BestsellerGroup(AxModel):
    age_band: AgeBand
    period_start: date | None = None
    period_end: date | None = None
    source_kind: SourceKind
    source_name: str = Field(min_length=1, max_length=80)   # 예: "정보나루", "예스24 기사"
    source_url: str = Field(min_length=1, max_length=500)   # 근거 링크 필수
    collected_at: datetime
    trust: Trust
    entries: list[BestsellerEntry] = Field(max_length=50)


class IdeaReportInput(AxModel):            # 기존 모델에 두 칸을 더한다
    ...
    by_age_band: list[AgeBandCount] = Field(default_factory=list, max_length=5)
    bestseller_groups: list[BestsellerGroup] = Field(default_factory=list, max_length=20)


class IdeaTrendBucket(AxModel):            # 기존 모델에 두 칸을 더한다
    ...
    by_age_band: list[AgeBandCount] = Field(default_factory=list)   # 기간 합산
    bestsellers: list[BestsellerTrendRow] = Field(default_factory=list)  # 아래


class BestsellerTrendRow(AxModel):
    age_band: AgeBand
    isbn13: str | None
    title: str
    appearances: int                    # 기간 안에서 나온 보고 수
    best_rank: int | None
    sources: list[str]                  # 어느 출처가 뒷받침하는가 (출처별 값은 더하지 않는다)
    trust: Trust                        # 가장 낮은 등급 기준
    shop_stock: int | None = None       # 데이터가 붙임. 모르면 None
    shop_sales_30d: int | None = None   # 데이터가 붙임. 모르면 None
    reason: str                         # 코드가 만든 한 줄 근거
```

- 중복: 같은 (`tenant`, `report_date`)는 한 번만 받는다 (기존 규칙). 베스트셀러 칸은 같은 `source_url`이 같은 날 두 번 오면 한 번만 센다.
- `trust="reference"`(사람 입력·기사) 자료는 순위 계산에 쓰지 않고 "참고" 칸에만 보인다.
- 이벤트 종류 추가는 하지 않는다.
- 사람 입력(근거 링크 필수)은 고객지원의 새 skill `submit_bestseller_reference`가 받는다. 상태를 바꾸므로 `execute()`를 거친다.

## 연령 구간 맞추기
우리 `AgeBand`(`10s`·`20s`·`30s`·`40s`·`50s_plus`)는 데이터 `trend_age` 기본 구간과 같은 5개다. 네이버 연령 코드로 보면
(`trend_age.DEFAULT_BANDS`, 코드 주석 기준):

| 우리 `AgeBand` | 화면 표기 | `trend_age`(네이버 연령 코드) | 네이버가 뜻하는 나이 |
|---|---|---|---|
| `10s` | 10대 | 2 | 13~18세 |
| `20s` | 20대 | 3, 4 | 19~29세 |
| `30s` | 30대 | 5, 6 | 30~39세 |
| `40s` | 40대 | 7, 8 | 40~49세 |
| `50s_plus` | 50대 이상 | 9, 10, 11 | 50세 이상 |

- 네이버 코드 1(0~12세)은 5구간에 없다. 서점 구매자에서 드물어 별도 구간을 두지 않고, 외부 자료의 그 구간은 버린다.
- **정의가 어긋나는 곳**: 네이버의 "20대"는 19세부터라서 우리 구매자가 말하는 20대(20~29세)와 19세 하나가 다르고, "10대"는 13~18세다.
  구간 이름·저장 값은 `trend_age`를 따르고(예진 님과 합의), 정보나루처럼 나이로 조회하는 출처는 같은 나이 범위로 번역해서 부른다.
  어긋나는 1년은 작게 보고 출처마다 정의를 `note`에 남긴다.
- 우리 구매자가 직접 고르는 값은 구간(`age_band`)뿐이라 구간 정의는 화면 안내 문구에도 같이 쓴다 (예: "10대 (13~18세)").

## 책 단위로 합치기: ISBN
**2026-11-16 갱신**: 판매 서버 `products`에는 원래 ISBN 열이 없었다(이름·분류·가격·재고·활성). 외부 자료는 ISBN(`isbn13`)으로 오는 경우가 많아 우리 상품과 ISBN으로 이을 수 없었다. 새 레포에 선택 칸 `isbn13`·`author`·`publisher`를 더하는 PR(Catbooks-demo **#14**, 리뷰 대기)이 올라갔다.
- **값 채우기는 이 변경 밖이다.** 시드 상품에는 값을 안 넣었다(지어낸 ISBN이 들어가면 매칭이 틀려진다). 판매자 등록·상품 수정 화면이 값을 받거나 별도 출처가 있어야 한다(예진 님께 질문 중). 값이 있는 상품만 ISBN으로 정확히 잇고, 없는 상품은 아래 방식으로 이어 간다.
- 데이터 `product_link`는 상품에 ISBN이 있으면 그것을 먼저 쓰도록 바뀌면 좋다(재현 님 영역, 제안).
- ISBN이 없는 상품은 새로 만들지 않고 데이터 `product_link`(이름 일치·카테고리·임베딩)를 재사용한다.
  결과에 `match`(`name`·`category`·`embedding`)와 근거 한 줄을 같이 남기고, 이름 일치 말고 임베딩·카테고리로 이은 것은 "후보"로 보인다.
- 잇지 못하면 "우리 상품으로 연결하지 못함"으로 두고 우리 재고·판매를 비워 둔다 (지어내지 않는다).
- `BestsellerTrendRow`에 데이터가 붙이는 선택 칸을 더한다: `our_product_id`(`LinkedProduct.product_id`), `link_match`. 못 이으면 둘 다 `None`.
- 외부 자료끼리 합칠 때는 `isbn13`이 있으면 그것으로, 없으면 제목·저자 정규화로 합치되 제목만으로는 합치지 않는다.

## 멘토 요구와 이 설계의 범위 (2026-11-13 멘토링)
멘토는 "20대 여성이 좋아하는 책"을 물으면 답할 수 있어야 하고, 구매자의 연령·성별·직업, 남녀 인구 대비 판매 커버율, 이벤트 효과, 솔루션으로
제공하는 다른 고객사까지 분석하라고 했다. 이 설계가 다루는 범위를 나눠 둔다.

| 요구 | 이 설계 | 비고 |
|---|---|---|
| 연령별 인기 책 | 다룸 | 이 문서의 본체 |
| 성별 | 외부 자료 + (합의되면) 구매자 | 정보나루·네이버 쇼핑인사이트는 성별 조건이 있다. `BestsellerGroup`에 선택 칸 `gender`(`all`·`female`·`male`, 기본 `all`)를 더하면 된다. **우리 구매자의 성별**은 재현 님이 분석에 필요하다고 요청했다(2026-11-16). 개인정보라서 **선택 칸 + 코드값(자유 글 금지) + 입력 화면·동의 문구**가 같이 정해진 뒤에 판매 서버(Catbooks-demo)에 `age_band`와 같은 방식으로 더하고, AXcellent로는 개인 단위가 아니라 **집계**(연령·성별·직업군별 건수)만 넘긴다 |
| 직업 | 합의되면 구매자 | 수집 출처가 없었다. 재현 님 요청(2026-11-16)으로 판매 서버 회원 속성으로 검토 중이다. 성별과 같은 조건(선택 칸·코드값·동의 문구·집계만) |
| 상품 장르 | 검토 중 | 재현 님 요청. `category_code`(책·문구)보다 세분한 값이다. 코드 목록인지 자유 글인지, 어떤 분석에 쓰는지 정해야 한다. 정해지면 상품 책 정보 칸(#14)과 같은 방식으로 더한다 |
| 남녀 인구 대비 판매 커버율 | 후속 | KOSIS 커넥터(연령·성별 통계)가 있어 시장 크기 참고값으로 쓸 수 있다. 데이터 쪽 몫 |
| 이벤트 효과 | 이미 있음 | 데이터 `campaign_effect`. 이 설계는 "무엇을 밀지" 근거까지 |
| 다른 고객사 | 구조만 | 판매 서버 연결은 `tenant`·커넥터로 일반화돼 있어 고객사별 `age_band`·상품 목록을 같은 방식으로 읽을 수 있다. 지금 실제 판매 서버는 하나 |

가장 먼저 닿는 것은 연령 + (외부 자료의) 성별이다. 구매자 성별·직업은 코드 목록·입력 화면·동의 문구를 정하기 전에는 판매 서버에 더하지 않는다(재현 님·예진 님과 합의).

## 구매자 연령대 (판매 서버 → 고객지원 → 데이터)

`by_age_band`의 재료는 구매자 연령대다. 원래 판매 서버 `buyers`에는 나이·생년이 없었다 (`buyer_id`, `tenant_id`,
`context_id`, `created_at`뿐). **2026-11-16 새 레포(Catbooks-demo #9)에서 `age_band`가 들어갔다.** 시드 구매자에 임의로 채우면 지어낸 데이터라서 하지 않는다(기존 구매자는 `null`).

1. **(끝남, Catbooks-demo #9)** 판매 서버 `buyers`에 `age_band`(선택, NULL 허용)를 `_ADDED_COLUMNS`로 추가했다. 생년월일은 받지 않고
   **연령대 구간만** 저장한다 (개인정보 부담을 줄인다).
2. **(끝남, #9)** 고객지원이 읽는 admin 문의·클레임·아이디어 읽기 항목에 `age_band`를 더했다. 기존 필드는 그대로다. 주문 응답에는 싣지 않았다: 연령대별 판매는 `sales-summary`(#12, `group_by=age_band`)로 읽는다. 단 연령대는 **주문 시점이 아니라 지금의 `buyers.age_band`** 기준이다(구매자가 나중에 바꾸면 과거 주문도 새 구간으로 센다). 전체 구매자 목록(`GET /users`)에는 싣지 않는다.
3. 고객지원은 하루 보고를 만들 때 연령대 건수만 센다. 개인 단위 나이는 데이터로 보내지 않고 저장하지도 않는다.
4. 값이 없는 접수는 "연령 모름"으로 따로 세고 어느 연령대에도 넣지 않는다. 입력 경로가 생기기 전에는 화면에
   "준비 중"을 둔다 (`Preparing.tsx`).
5. 나이를 받는 가입·프로필 화면은 캣북스 쇼핑몰(`Catbooks-demo`의 `frontend/shop-web`)에서 예진 님이 한다. 값을 정하는 경로는 `PATCH /users/{buyer_id}`(토큰 없이 누구나 바꿀 수 있는 데모)다. 동의 문구·보관 기간은 사람이 정한다. 연령대는 개인정보라서 실제 서비스로 옮길 때는 본인 확인을 붙여야 한다.

## 구현 순서 (PR 단위, 앞 단계가 뒤 단계를 막지 않게)
0. **(끝남) 고객지원 쪽 우리 사실**: 책별 집계 `book_issue_stats` (#1096~#1100). 연령대별 건수(`by_age_band`)는 아래 1번이 있어야 센다.
1. **(끝남) 구매자 연령대 칸** (판매 서버, **Catbooks-demo #9**, 11/16 병합): `buyers.age_band`와 읽는 응답 추가. 값이 없는 구매자는 NULL. AXcellent 쪽 `shop-api`에는 올리지 않는다(곧 빠진다). 가입·프로필 화면은 예진 님. 이 칸을 읽어 `by_age_band`를 세는 고객지원 집계는 판매 서버가 새 레포로 뜬 뒤에 의미가 있다.
2. **계약 PR**: `idea.py`에 위 칸을 **추가만** 한다(스키마 버전은 올리지 않는다, 재현 11/16) + 계약 테스트 (옛 보고도 받는지). 가장 먼저 머지해서 양쪽이 같은 모양을 본다.
3. **데이터 쪽**: 일일 보고 저장에 새 칸을 더하고(재시작해도 남게), 주·월·연 합산(`list_idea_reports`)에
   연령대·베스트셀러를 태운다. 정보나루·우리 판매·재고를 붙인다(분담 합의됨, 재현 11/16). 판매는 `sales-summary`를 읽을 수 있다.
4. **고객지원 쪽**: 사람 입력 화면(`submit_bestseller_reference`)과 하루 한 번 데이터로 보내기.
   출처가 없는 화면은 "준비 중" 표시로 둔다 (`Preparing.tsx`).
5. **화면**: 데이터 분석 탭에 연령대별 근거 카드(출처·신뢰 등급·우리 재고·클레임 비율), 기획 근거 연결 (`book-issue-a2a-design.md`의 A안과 같은 `InsightFact` 길).
6. **공개 순위 API 어댑터** (나중): 약관 확인 뒤 같은 입구에 붙인다. 먼저 약관·한도를 문서로 정리한다.
7. **테스트**: 단위 테스트는 목 스위치(테스트 전용). 출처 없는 항목 거절, 중복 보고, 신뢰 등급이
   `reference`면 순위에 안 쓰이는지, 값을 모를 때 `None`+이유로 답하는지.

## 열린 질문 (2026-11-16 갱신)
**합의됨(재현 11/16)**: 입구(같은 일일 보고에 선택 칸 추가, 스키마 버전 유지), 구간(`trend_age` 5개), 분담(고객지원=우리 쪽 사실, 데이터=외부 자료·판매·재고 붙이기). **예진 11/16**: `age_band`는 Catbooks-demo에, 가입·프로필 화면은 예진 님.

남은 것:
- **성별·직업·상품 장르** (재현 님 요청, 판매 서버 회원·상품 속성): 코드 목록은 재현 님이 분석에 꼭 필요한 최소한으로 정한다(예: 성별 `female`·`male`·`other`·모름, 직업군). 개인정보라서 선택 칸·코드값·집계로만 AXcellent에 넘기기·입력 화면과 동의 문구를 예진 님과 먼저 정한다. 장르는 코드 목록인지 자유 글인지와 쓰이는 분석을 정한다.
- 책 합치기: ISBN이 있는 상품은 ISBN으로, 없으면 `product_link`. 임베딩·카테고리로 이은 상품을 어디까지 보일까? 상품에 ISBN을 채우는 경로(판매자 입력 화면·별도 출처)는? (Catbooks-demo #14 리뷰 중, 예진 님·재현 님)
- 외부 순위를 자동으로 모을 때 쓸 출처와 약관 (예스24·교보·알라딘). 검토 전에는 자동 수집을 켜지 않는다. 정보나루 키(`DATA4LIBRARY_API_KEY`)가 있는지, 응답 모양은 코드 주석상 미확인 가정이다.
- 고객 의견(`IdeaCluster`)과 베스트셀러 자료를 서로 잇는 표시가 필요한가: 지금은 같은 날 보고 안에 같이 실리는 것만으로 연결한다.
- 네이버 20대(19~29세)와 일반 20대의 1년 차이는 받아들이는 것으로 두었다(재현 님이 `trend_age` 그대로 쓴다고 함). 이견이 있으면 알려 달라.
- 담당: 고객지원(수집·보관·우리 쪽 사실), 데이터(합치기·근거), 기획·마케팅(이벤트 판단). 계약 PR은 데이터와 같이 확정한다.
