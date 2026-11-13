# 연령별 베스트셀러 자료 → 데이터 분석 → 이벤트 근거 (설계)

상태: 초안(코드 없음). 제안: 배병환(고객지원), 2026-11-13. 데이터(재현)·기획·마케팅과 맞춰야 한다.

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

## 구매자 연령대 (판매 서버 → 고객지원 → 데이터)

`by_age_band`의 재료는 구매자 연령대다. 지금 판매 서버 `buyers`에는 나이·생년이 없다 (`buyer_id`, `tenant_id`,
`context_id`, `created_at`뿐). 시드 구매자에 임의로 채우면 지어낸 데이터라서 하지 않는다.

1. 판매 서버 `buyers`에 `age_band`(선택, NULL 허용)를 `_ADDED_COLUMNS`로 추가한다. 생년월일은 받지 않고
   **연령대 구간만** 저장한다 (개인정보 부담을 줄인다).
2. 고객지원이 읽는 문의·클레임·아이디어 접수 응답(`inquiry_inbox`, `idea_inbox`)과 주문 응답에 `age_band`를 추가한다.
   기존 필드는 그대로 둔다.
3. 고객지원은 하루 보고를 만들 때 연령대 건수만 센다. 개인 단위 나이는 데이터로 보내지 않고 저장하지도 않는다.
4. 값이 없는 접수는 "연령 모름"으로 따로 세고 어느 연령대에도 넣지 않는다. 입력 경로가 생기기 전에는 화면에
   "준비 중"을 둔다 (`Preparing.tsx`).
5. 나이를 받는 가입·프로필 화면은 캣북스 쇼핑몰(ax-frontend) 쪽이다. 이 레포에서는 그 레포에 쓰지 않으므로
   예진 님과 협의한다. 동의 문구·보관 기간은 사람이 정한다.

## 구현 순서 (PR 단위, 앞 단계가 뒤 단계를 막지 않게)
1. **구매자 연령대 칸** (판매 서버): 확인 결과 지금은 없다. `buyers.age_band`와 읽는 응답 추가 PR. 값이 없는 동안은 NULL.
2. **계약 PR**: `idea.py`에 위 칸을 추가 + 계약 테스트 (옛 보고도 받는지). 가장 먼저 머지해서 양쪽이 같은 모양을 본다.
3. **데이터 쪽**: 일일 보고 저장에 새 칸을 더하고(재시작해도 남게), 주·월·연 합산(`list_idea_reports`)에
   연령대·베스트셀러를 태운다. 정보나루·우리 판매·재고를 붙인다. 재현 님과 합의 후.
4. **고객지원 쪽**: 사람 입력 화면(`submit_bestseller_reference`)과 하루 한 번 데이터로 보내기.
   출처가 없는 화면은 "준비 중" 표시로 둔다 (`Preparing.tsx`).
5. **화면**: 데이터 분석 탭에 연령대별 근거 카드(출처·신뢰 등급·우리 재고), 기획 근거 연결.
6. **공개 순위 API 어댑터** (나중): 약관 확인 뒤 같은 입구에 붙인다. 먼저 약관·한도를 문서로 정리한다.
7. **테스트**: 단위 테스트는 목 스위치(테스트 전용). 출처 없는 항목 거절, 중복 보고, 신뢰 등급이
   `reference`면 순위에 안 쓰이는지, 값을 모를 때 `None`+이유로 답하는지.

## 열린 질문
- 입구: 같은 일일 보고에 칸을 추가하는 것으로 정했다. 스키마 버전을 올릴지 칸 추가로만 할지는 데이터와 확인한다.
- 외부 순위를 자동으로 모을 때 쓸 출처와 약관 (예스24·교보·알라딘). 검토 전에는 자동 수집을 켜지 않는다.
- 연령대 구간: 정보나루·네이버의 구간과 우리 구매자 구간이 다르면 어느 쪽에 맞출지.
- 고객 의견(`IdeaCluster`)과 베스트셀러 자료를 서로 잇는 표시가 필요한가: 지금은 같은 날 보고 안에 같이 실리는 것만으로 연결한다.
- 담당: 고객지원(수집·보관), 데이터(합치기·근거), 기획·마케팅(이벤트 판단). 계약은 데이터와 같이 확정한다.
