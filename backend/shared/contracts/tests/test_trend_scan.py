"""검색어 급상승(trend_scan) 계약. 입력 한도는 네이버 검색어 트렌드가 받는 한도와 같다."""

from datetime import date

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import TrendScanInput, TrendScanOutput, TrendScanRequest, TrendVerdict


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


def test_request_can_leave_groups_and_date_to_defaults() -> None:
    assert TrendScanRequest().keyword_groups is None  # 회사 기본 그룹, 어제
    group = {"group_name": "고양이", "keywords": ["고양이"]}
    with pytest.raises(ValidationError):  # 빈 목록은 "기본값"이 아니라 잘못된 입력
        TrendScanRequest.model_validate({"keyword_groups": []})
    with pytest.raises(ValidationError):
        TrendScanRequest.model_validate({"keyword_groups": [group, group]})


# ---------------------------------------------------------------- trend_candidates (YouTube 후보)


def test_candidates_output_needs_a_reason_and_no_candidates_when_unknown() -> None:
    from ax_contracts.schemas import TrendCandidatesOutput, TrendCandidatesRequest

    with pytest.raises(ValidationError):
        TrendCandidatesOutput(status="unknown", summary="x")
    verdict = TrendVerdict(group_name="t1", status="normal", ratio=1.0)
    candidate = {
        "keyword": "t1",
        "youtube": {"video_count": 3, "snapshot_at": "2026-11-02T05:55:08Z", "video_ids": ["a"]},
        "naver": verdict.model_dump(),
    }
    with pytest.raises(ValidationError):
        TrendCandidatesOutput.model_validate(
            {"status": "unknown", "unknown_reason": "스냅샷 없음", "summary": "x",
             "candidates": [candidate]}
        )  # fmt: skip
    out = TrendCandidatesOutput.model_validate(
        {"status": "known", "summary": "x", "candidates": [candidate], "naver_calls": 1}
    )
    assert out.candidates[0].youtube.snapshot_at.tzinfo is not None
    with pytest.raises(ValidationError):  # 후보 수 상한 (후보마다 네이버 1번)
        TrendCandidatesRequest(max_candidates=21)
    assert TrendCandidatesRequest().max_candidates is None  # 비우면 회사 설정
