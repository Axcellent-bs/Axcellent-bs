"""고객 아이디어(고객의 소리) 일일 보고 계약: 고객지원 → 데이터.

고객지원이 고객의 이벤트·혜택 요청을 하루 단위로 묶어, 같은 요청이 가장 많이 겹친 순으로 데이터에
넘긴다. 데이터는 일일 보고를 쌓아 주·월·연 단위로 합쳐 보여 준다.

- `receive_idea_report`: support → data. 데이터 쪽 기록만 쌓고 회사 상태는 바꾸지 않아
  `execute()`를 거치지 않는다(grade read). 요청자는 support뿐.
- `list_idea_reports`: 읽기. 기간(`period`)별로 일일 보고를 합친 순위를 돌려준다.
- 고객 원문은 싣지 않는다. 같은 요청의 대표 말(`label`)과 건수·고객 수만 싣는다.
- 같은 (`tenant`, `report_date`)는 한 번만 받는다 (다시 오면 `duplicate`).
- 추가만 한다. 삭제·이름 변경·타입 변경은 하지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel

IDEA_REPORT_SCHEMA_VERSION = 1
IdeaPeriod = Literal["day", "week", "month", "year"]


class IdeaCluster(AxModel):
    """같은 뜻으로 겹쳐 들어온 요청 하나. 고객 원문은 없다."""

    key: str = Field(min_length=1, max_length=80)  # 합칠 때 쓰는 열쇠 (대표 말을 정렬한 것)
    label: str = Field(min_length=1, max_length=80)  # 사람이 읽는 대표 말 (예: "생일 쿠폰")
    count: int = Field(ge=1)  # 이 요청이 들어온 건수
    customer_count: int = Field(ge=1)  # 낸 고객 수 (같은 고객의 중복 제출은 한 명)


class IdeaReportInput(AxModel):
    """고객지원 → 마케팅: 하루치 이벤트·혜택 요청 보고."""

    schema_version: int = IDEA_REPORT_SCHEMA_VERSION
    report_date: date  # 회사 시간대 기준 접수일
    kind: Literal["event"] = "event"  # 지금은 이벤트·혜택 요청만 넘긴다
    total_ideas: int = Field(ge=0)  # 그날 받은 해당 종류 아이디어 수 (묶지 못한 것 포함)
    clusters: list[IdeaCluster] = Field(default_factory=list, max_length=50)  # 많이 겹친 순


class IdeaReportOutput(AxModel):
    schema_version: int = IDEA_REPORT_SCHEMA_VERSION
    outcome: Literal["accepted", "duplicate", "rejected"]
    report_date: date | None = None
    reason_code: str | None = None  # rejected일 때


class IdeaTrendRow(AxModel):
    """기간 합산 순위의 한 줄."""

    key: str
    label: str
    count: int
    customer_count: int  # 날마다 센 고객 수의 합 (여러 날 낸 같은 고객은 날마다 센다)
    days: int  # 이 요청이 나온 날 수


class IdeaTrendBucket(AxModel):
    period: IdeaPeriod
    start: date  # 기간 첫날 (주는 월요일)
    end: date  # 기간 마지막 날
    report_days: int  # 일일 보고를 받은 날 수
    total_ideas: int
    top: list[IdeaTrendRow] = Field(default_factory=list)


class IdeaTrendList(AxModel):
    period: IdeaPeriod
    buckets: list[IdeaTrendBucket] = Field(default_factory=list)  # 최근 기간이 앞
    unknown_reason: str | None = None
