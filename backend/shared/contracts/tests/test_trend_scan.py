"""검색어 급상승(trend_scan) 계약. 입력 한도는 네이버 검색어 트렌드가 받는 한도와 같다."""

from datetime import date

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import TrendScanInput, TrendScanOutput, TrendVerdict


def test_input_limits_match_naver() -> None:
    six = [{"group_name": f"g{i}", "keywords": ["x"]} for i in range(6)]
    with pytest.raises(ValidationError):
        TrendScanInput.model_validate(
            {"keyword_groups": six, "start_date": "2026-10-01", "end_date": "2026-11-01"}
        )
    with pytest.raises(ValidationError):  # 거꾸로 된 기간
        TrendScanInput.model_validate({"keyword_groups": six[:1], "start_date": "2026-11-02",
                                       "end_date": "2026-11-01"})  # fmt: skip
    with pytest.raises(ValidationError):  # hour는 네이버가 받지 않는다
        TrendScanInput.model_validate({"keyword_groups": six[:1], "start_date": "2026-11-01",
                                       "end_date": "2026-11-01", "time_unit": "hour"})  # fmt: skip


def test_verdict_needs_reason_when_unknown() -> None:
    with pytest.raises(ValidationError):
        TrendVerdict(group_name="캣북스", status="unknown")
    with pytest.raises(ValidationError):
        TrendVerdict(group_name="밈", status="rising")  # 배수 없이 판정할 수 없다
    out = TrendScanOutput(
        start_date=date(2026, 8, 4), end_date=date(2026, 11, 1), time_unit="date",
        source="naver_datalab",
        results=[
            TrendVerdict(group_name="밈", status="rising", ratio=2.12),
            TrendVerdict(group_name="캣북스", status="unknown", unknown_reason="네이버 자료 없음"),
        ],
    )  # fmt: skip
    assert out.value_kind == "relative"
