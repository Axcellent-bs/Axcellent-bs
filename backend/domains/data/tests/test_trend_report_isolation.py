"""분석 보고서는 요청 회사 것만 읽는다 (격리 시험 2번, test_company_isolation의 보고서 자리).

화면은 데이터 AI를 A2A로 부르고 meta.tenant_id에 로그인 세션 회사를 싣는다(#1700). 서버가 그 회사를
요청 회사로 걸고(agent-template), data의 회사 고르기가 그 값을 따른다(#1711). 그래서 A 회사 요청은
B 회사 보고서를 번호로도·목록으로도 못 본다.
"""

from datetime import UTC, date, datetime

from ax_agent_template import company_scope as request_scope
from data_agent.trend_report_store import TrendReportStore

AT = datetime(2026, 11, 24, tzinfo=UTC)


def _make(store: TrendReportStore, report_id: str) -> None:
    store.create(report_id, f"h-{report_id}", date(2026, 11, 24), {"product": "x"}, "data",
                 None, {"status": "done", "report_id": report_id}, AT)  # fmt: skip


def test_a_cannot_read_b_reports() -> None:
    store = TrendReportStore()
    with request_scope.using("nubi"):
        _make(store, "TR-B")
    with request_scope.using("studyon"):
        _make(store, "TR-A")
        assert store.get("TR-B") is None  # 번호를 알아도 못 읽는다
        assert store.statuses("TR-B") is None
        assert [r["report_id"] for r in store.recent(10)] == ["TR-A"]
    with request_scope.using("nubi"):
        assert store.get("TR-B") is not None  # 대조: B는 자기 보고서를 읽는다
        assert [r["report_id"] for r in store.recent(10)] == ["TR-B"]
    assert store.company_of("TR-B") == "nubi"
