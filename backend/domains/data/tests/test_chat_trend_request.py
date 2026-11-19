"""데이터분석 대화의 의뢰 문장 → 분석 의뢰 카드 · 대화 카드로 넣은 의뢰의 진행·완료 메시지.

실제 LLM·인스타·네이버는 부르지 않는다.
- 대화는 카드(미리보기)만 만들고 보고서를 저장하지 않는다
- 접수(trend_report_create + chat_thread_id)는 사람이 카드 버튼으로. 칸 값이 오면 LLM 안 부름
- 러너는 그 대화에 코드 문장 + 진행·완료 카드를 붙인다. 닫힌 대화·그 대화 카드가 아니면 안 붙임
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import TypeAdapter

from ax_agent_template.llm import LLMSettings
from ax_contracts.schemas.data import (
    DataChatCard,
    TrendBeat,
    TrendReportEvidence,
    TrendReportInput,
    TrendRequestCard,
)
from data_agent import data_chat as dc
from data_agent import llm_budget
from data_agent.data_chat_store import DataChatStore
from data_agent.handlers import TimedLLMClient
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.trend_report_runner import TrendReportService, report_label, report_path
from data_agent.trend_report_store import INTERRUPTED_REASON, TrendReportStore

NOW = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parent / "fixtures" / "chat_cards_example.json"
ECO = ("친환경 화장품 브랜드 인지도를 높이고 싶어요. 20~30대 여성 타깃으로 인스타 릴스 30초"
       " 홍보 영상 만들어 주세요.")  # fmt: skip


@pytest.fixture(autouse=True)
def budget(monkeypatch: pytest.MonkeyPatch) -> llm_budget.Budget:
    fresh = llm_budget.Budget(None)
    monkeypatch.setattr(llm_budget, "BUDGET", fresh)
    return fresh


# ---------------------------------------------------------------- 1. 의뢰 알아보기 (코드 규칙)

TRUE_CASES = [
    ECO,
    "20대 남성 타깃으로 캠핑 용품 숏츠 광고 영상을 만들어줘. 경쟁사 계정도 같이 봐 줘요.",
    "우리 브랜드 인지도를 높일 수 있게 요즘 트렌드랑 밈을 분석해 주세요. 인스타 릴스로 할 거예요.",
    "비건 선크림 신제품 홍보 릴스 기획해 주세요. 30대 여성 대상이고 15초 정도면 좋겠어요.",
    "경쟁사 toun28_official이랑 비교해서 우리 홍보 방향 추천해 주세요, 타깃은 20대예요.",
    "이번 달 캠페인용으로 30대 직장인 타깃 쇼츠 영상 아이디어 정리해 주세요. 책 홍보입니다.",
    "고양이 간식 브랜드 광고를 하려는데 요즘 뜨는 트렌드 알려 줘. 20대 여성 대상이야.",
    "친환경 세제 인지도 캠페인을 하려고 해요. 인스타에서 뭐가 뜨는지 분석해 주세요 부탁드려요.",
    "제주 여행 패키지 홍보 영상 만들어 주실래요? 타깃은 40대 부부고 릴스 30초예요.",
    "새로 나온 그림책을 알리고 싶어요. 엄마들 타깃으로 숏츠 광고 방향 추천해 주세요.",
]
FALSE_CASES = [
    "지난 보고서 결론 뭐였어? 친환경 화장품 트렌드 분석해 준 거 말이야.",  # 지난 결과 조회
    "어제 분석 결과 어땠어? 경쟁사 릴스 비율 다시 보고 싶어요.",  # 조회
    "책추천 캠페인 영상을 마케팅에 전송해 줘 지금 바로 부탁해요 빨리요",  # 상태 명령
    "새 숏츠 추적 수집해 줘. 트렌드 영상이 너무 오래됐어요 다시 모아요.",  # 상태 명령
    "릴스 홍보 영상 만들어 주세요",  # 30자 미만
    # 주제어만, 요청 동사 없음
    "V5 시험 영상 성과가 어떻게 됐는지 지금 기준으로 숫자로 말해 주실 수 있나요",
    "오늘 날씨가 정말 좋네요. 산책 가기 딱 좋은 날씨라서 기분이 좋아요 하하하",  # 둘 다 없음
    "요즘 인기 영상 카테고리별로 몇 개씩 있는지 궁금합니다 데이터 화면 기준으로요",  # 동사 없음
    "LLM 토큰 장부랑 유튜브 할당량 사용량을 오늘 기준으로 정리해 주세요 부탁해요",  # 주제어 없음
    "캠페인 측정 결과 나왔어? 다음 측정은 언제인지 화면 어디서 보는지도 알려 줘",  # 조회
]


@pytest.mark.parametrize("text", TRUE_CASES)
def test_trend_request_true(text: str) -> None:
    assert len(" ".join(text.split())) >= dc.TREND_MIN_CHARS
    assert dc.is_trend_request(text), text


@pytest.mark.parametrize("text", FALSE_CASES)
def test_trend_request_false(text: str) -> None:
    assert not dc.is_trend_request(text), text


# ---------------------------------------------------------------- 2. 카드 답


class Extract:
    """의뢰문 추출(_Extracted)만 답하는 가짜 LLM (호출 수를 센다)."""

    def __init__(self, answer: dict[str, Any] | None = None,
                 error: Exception | None = None) -> None:  # fmt: skip
        self.answer, self.error, self.calls = answer or {}, error, []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.calls.append((schema.__name__, dict(untrusted or {})))
        if self.error:
            raise self.error
        return schema.model_validate(self.answer)


def _service(llm: Any, names: dict[str, str] | None = None,
             store: DataChatStore | None = None) -> dc.DataChatService:  # fmt: skip
    return dc.DataChatService(store or DataChatStore(":memory:"),
                              material=lambda b: {"bundles": []}, llm=lambda: llm,
                              report_names=lambda: names or {})  # fmt: skip


def _talk(service: dc.DataChatService, text: str, who: str = "재현") -> dict:
    async def run() -> dict:
        th = service.send(who, None, text)
        assert th["status"] == "answering"
        await service.wait_idle()
        return service.get(who, th["id"])

    return asyncio.run(run())


EXTRACTED = {"product": "친환경 화장품", "gender": "f", "ages": ["20", "30"],
             "format": "인스타 릴스 30초", "competitors": [], "keywords": []}  # fmt: skip


def test_trend_request_gets_a_card_not_a_report() -> None:
    llm = Extract(EXTRACTED)
    th = _talk(_service(llm, names={"toun28_official": "톤28(보고서)"}), ECO)
    assert th["status"] == "talking"
    last = th["messages"][-1]
    assert last["text"] == (
        "트렌드 분석 의뢰로 보여요. 영상 제작은 마케팅 몫이라, 데이터는 먼저 근거가 되는"
        " 트렌드 분석 보고서를 만들어 드릴게요. 이렇게 넣을까요?")  # fmt: skip
    card = TrendRequestCard.model_validate(last["card"])
    assert card.request_text == ECO and card.preset == "eco_beauty"
    b = card.brief
    assert (b.product, b.target_gender, b.target_ages) == ("친환경 화장품", "f", ["20", "30"])
    assert b.sources.product == "request_text" and b.sources.competitors == "preset"
    # 프리셋 display_names + 이전 보고서 저장값(먼저). 인스타는 부르지 않는다
    assert b.competitor_names["toun28_official"] == "톤28(보고서)"
    assert b.competitor_names["beplain_official"] == "비플레인"
    assert set(b.competitor_names) == set(b.competitors)
    assert card.brief_reason is None and card.cost_note.endswith("AI 1번")
    assert th["messages"][0]["card"] is None  # 사람 말은 카드 없음
    # 의뢰문은 데이터 블록으로만, LLM은 1번
    assert llm.calls == [("_Extracted", {"의뢰문": ECO})]


def test_card_text_without_video_words_drops_the_marketing_clause() -> None:
    text = "우리 브랜드 인지도를 높이고 싶은데 요즘 트렌드를 분석해 주세요. 20대 여성 대상입니다."
    th = _talk(_service(Extract(EXTRACTED)), text)
    assert th["messages"][-1]["text"] == (
        "트렌드 분석 의뢰로 보여요. 데이터는 먼저 근거가 되는 트렌드 분석 보고서를 만들어 드릴게요."
        " 이렇게 넣을까요?")  # fmt: skip


def test_llm_failure_fills_preset_values_with_a_reason() -> None:
    th = _talk(_service(Extract(error=llm_budget.LLMDailyCapReached("LLM 하루 상한"))), ECO)
    last = th["messages"][-1]
    assert last["text"].endswith(dc.CARD_FALLBACK)
    card = TrendRequestCard.model_validate(last["card"])
    assert set(card.brief.sources.model_dump().values()) == {"preset"}
    assert card.brief.product == "친환경 화장품" and card.brief_reason
    assert "의뢰문을 해석하지 못함" in card.brief_reason


def test_chat_daily_cap_refuses_trend_requests_too(budget: llm_budget.Budget) -> None:
    budget.add(dc.CHAT_CALLS, 1000, skill="data_chat", task="문장")

    async def send() -> str:
        try:
            _service(Extract(EXTRACTED)).send("재현", None, ECO)
        except dc.ChatRefused as refused:
            return refused.outcome
        return "sent"

    assert asyncio.run(send()) == "chat_limit"


def test_card_extraction_is_booked_as_data_chat(budget: llm_budget.Budget) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(EXTRACTED, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                          "usage": {"prompt_tokens": 500,
                                                    "completion_tokens": 40}})  # fmt: skip

    settings = LLMSettings(model="test-model", base_url="https://llm.example.com/v1", api_key=None)
    client = TimedLLMClient("data", settings=settings, transport=httpx.MockTransport(handler))
    th = _talk(_service(client), ECO)
    assert th["messages"][-1]["card"]["kind"] == "trend_request"
    assert {s for s, _, _, _ in budget.by_task()} == {"data_chat"}


# ---------------------------------------------------------------- 3. 저장 (card)


def test_card_is_stored_and_old_messages_read_as_null(tmp_path: Path) -> None:
    path = str(tmp_path / "chat.db")
    store = DataChatStore(path)
    th = store.create("의뢰", "재현")
    store.begin_turn(th["id"], ECO)
    card = {"kind": "trend_report_progress", "report_id": "tr_1"}
    store.finish_turn(th["id"], "카드", card=card)
    # 예전 메시지(card 칸 없음)를 직접 넣는다
    conn = sqlite3.connect(path)
    old = [{"id": "m1", "role": "user", "text": "옛 질문", "at": "2026-11-01T00:00:00+00:00",
            "kind": "text", "plan": None, "task_id": None}]  # fmt: skip
    conn.execute("INSERT INTO data_chat_threads (seq, id, title, status, who, summary, messages,"
                 " created_at, updated_at) VALUES (99, 'datachat-99', 't', 'talking', '재현', '',"
                 " ?, '2026-11-01', '2026-11-01')", (json.dumps(old, ensure_ascii=False),))
    conn.commit()
    conn.close()
    again = DataChatStore(path)
    assert again.get(th["id"])["messages"][-1]["card"] == card  # type: ignore[index]
    assert again.get("datachat-99")["messages"][0]["card"] is None  # type: ignore[index]


# ---------------------------------------------------------------- 4. 접수 (chat_thread_id)


def test_fields_given_means_no_llm_extraction() -> None:
    llm = Extract(EXTRACTED)
    request = TrendReportInput(request_text=ECO, product="친환경 화장품", target_gender="f",
                               target_ages=["20", "30"], caller="human",
                               chat_thread_id="datachat-1")  # fmt: skip
    got = asyncio.run(tb.resolve(request, llm))
    assert llm.calls == [] and got.llm_calls == 0 and got.extract_error is None
    assert got.brief.sources.product == "input" and got.brief.sources.keywords == "preset"
    # 칸 값이 없으면 지금처럼 의뢰문을 읽는다
    asyncio.run(tb.resolve(TrendReportInput(request_text=ECO), llm))
    assert len(llm.calls) == 1


def _chat_with_card(store: DataChatStore, who: str = "재현") -> str:
    th = store.create("의뢰", who)
    store.begin_turn(th["id"], ECO)
    card = TrendRequestCard.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8"))[0])
    card = card.model_copy(update={"accepted_report_id": None})  # 아직 접수 전
    store.finish_turn(th["id"], "카드", card=card.model_dump(mode="json"))
    return th["id"]


def _done_build(status: str = "done") -> Any:
    async def build(report: Any, request: Any, deps: Any, store: Any, **kwargs: Any) -> Any:
        report.status, report.summary_line = status, "질문형 릴스"
        ev = [TrendReportEvidence(id=f"hashtag:{i}", text=f"근거 문장 {i}", short=f"짧은 {i}")
              for i in range(1, 3)]  # fmt: skip
        report.sections.recommendation.beats = [
            TrendBeat(slot="0-3", text="훅", evidence=[e.text for e in ev], evidence_items=ev),
            TrendBeat(slot="3-15", text="전환", evidence=["옛 근거 문장"]),
            TrendBeat(slot="15-25", text="증거", evidence=["넷째"]),
        ]
        report.finished_at = NOW
        if kwargs.get("progress"):
            got = kwargs["progress"](report)  # 실제 build처럼 코루틴이면 기다린다
            if asyncio.iscoroutine(got):
                await got
        return report

    return build


def _create(service: TrendReportService, thread: str | None, **extra: Any) -> str:
    request = TrendReportInput(request_text=ECO, product="친환경 화장품", caller="human",
                               chat_thread_id=thread, **extra)  # fmt: skip

    async def run() -> str:
        out = await service.create(request)
        await service.wait(out.report_id)
        return out.report_id

    return asyncio.run(run())


def test_chat_thread_id_is_saved_and_gets_progress_and_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = DataChatStore(":memory:")
    thread = _chat_with_card(chat)
    monkeypatch.setattr(tr, "build", _done_build())
    service = TrendReportService(TrendReportStore(), lambda: None, publish=lambda e: None,  # type: ignore[arg-type, return-value]
                                 clock=lambda: NOW, chat=lambda: chat)  # fmt: skip
    report_id = _create(service, thread)
    assert service.store.chat_thread(report_id) == thread
    msgs = chat.get(thread)["messages"]  # type: ignore[index]
    progress, done = msgs[-2], msgs[-1]
    label = report_label(report_id)
    assert label == "TR-" + report_id[3:7].upper()
    assert progress["text"] == f"의뢰 {label} 만드는 중…"
    assert progress["card"] == {"kind": "trend_report_progress", "report_id": report_id}
    assert done["text"] == f"의뢰 {label} 보고서 완료"
    # 의뢰 카드에 접수 표시 (새로고침해도 "접수됨")
    request_card = next(m["card"] for m in msgs if (m["card"] or {}).get("kind") == "trend_request")
    assert request_card["accepted_report_id"] == report_id
    card = TypeAdapter(DataChatCard).validate_python(done["card"])
    assert card.kind == "trend_report_done" and card.status == "done"
    assert card.top_evidence == ["짧은 1", "짧은 2", "옛 근거 문장"]  # short 먼저, 최대 3
    assert card.report_path == report_path(report_id) == f"/data/trend#request?id={report_id}"
    assert chat.get(thread)["status"] == "talking"  # type: ignore[index]  # 상태는 그대로


def test_failed_report_says_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = DataChatStore(":memory:")
    thread = _chat_with_card(chat)

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("x")

    monkeypatch.setattr(tr, "build", boom)
    service = TrendReportService(TrendReportStore(), lambda: None, publish=lambda e: None,  # type: ignore[arg-type, return-value]
                                 clock=lambda: NOW, chat=lambda: chat)  # fmt: skip
    report_id = _create(service, thread)
    done = chat.get(thread)["messages"][-1]  # type: ignore[index]
    want = f"의뢰 {report_label(report_id)} 실패: 보고서를 만들지 못함 (RuntimeError)"
    assert done["text"] == want
    assert done["card"]["status"] == "failed"


def test_closed_or_foreign_thread_gets_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    chat = DataChatStore(":memory:")
    closed = _chat_with_card(chat)
    chat.close(closed)
    foreign = chat.create("남의 대화", "태윤")["id"]  # 이 의뢰 카드가 없는 대화
    monkeypatch.setattr(tr, "build", _done_build())
    service = TrendReportService(TrendReportStore(), lambda: None, publish=lambda e: None,  # type: ignore[arg-type, return-value]
                                 clock=lambda: NOW, chat=lambda: chat)  # fmt: skip
    before = len(chat.get(closed)["messages"])  # type: ignore[index]
    with caplog.at_level("INFO", logger="data_agent.trend_report"):
        _create(service, closed)
        _create(service, foreign, keywords=["다른 의뢰"])
    assert len(chat.get(closed)["messages"]) == before  # type: ignore[index]
    assert chat.get(foreign)["messages"] == []  # type: ignore[index]
    assert any("closed" in r.getMessage() for r in caplog.records)
    assert any("not_from_here" in r.getMessage() for r in caplog.records)


def test_interrupted_on_restart_tells_the_chat(tmp_path: Path) -> None:
    chat = DataChatStore(":memory:")
    thread = _chat_with_card(chat)
    store = TrendReportStore(tmp_path / "r.db")
    report = tr.TrendReport(report_id="tr_abcdef0123", status="running", created_at=NOW,
                            caller="human", brief=tb.input_brief(TrendReportInput()))  # fmt: skip
    store.create(report.report_id, "h", NOW.date(), {"request_text": ECO}, "human", None,
                 report.model_dump(mode="json"), NOW, chat_thread_id=thread)  # fmt: skip
    TrendReportService(store, lambda: None, publish=lambda e: None,  # type: ignore[arg-type, return-value]
                       clock=lambda: NOW, chat=lambda: chat)  # fmt: skip
    done = chat.get(thread)["messages"][-1]  # type: ignore[index]
    assert done["text"] == f"의뢰 TR-ABCD 실패: {INTERRUPTED_REASON}"  # 표기 하나로 (11/19)
    assert done["card"]["status"] == "interrupted"


def test_old_report_db_gets_the_chat_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE reports (id TEXT PRIMARY KEY, brief_hash TEXT NOT NULL,"
                 " day TEXT NOT NULL, brief TEXT NOT NULL, caller TEXT NOT NULL, context_id TEXT,"
                 " status TEXT NOT NULL, sections TEXT NOT NULL, result TEXT NOT NULL,"
                 " created_at TEXT NOT NULL, finished_at TEXT)")  # fmt: skip
    conn.execute("INSERT INTO reports VALUES ('tr_old','h','2026-11-18','{}','human',NULL,"
                 "'done','{}','{}','2026-11-18',NULL)")  # fmt: skip
    conn.commit()
    conn.close()
    store = TrendReportStore(path)
    TrendReportStore(path).close()  # 두 번째 열기도 안전
    assert store.chat_thread("tr_old") is None


def test_known_names_from_saved_reports() -> None:
    store = TrendReportStore()
    for i, names in enumerate([{"a": "옛 이름", "b": None}, {"a": "새 이름", "c": "씨"}]):
        report = tr.TrendReport(report_id=f"tr_{i}", status="done",
                                created_at=NOW.replace(minute=i), caller="human",
                                brief=tb.input_brief(TrendReportInput()))  # fmt: skip
        report.brief.competitor_names = names
        store.create(report.report_id, f"h{i}", NOW.date(), {}, "human", None,
                     report.model_dump(mode="json"), NOW.replace(minute=i))  # fmt: skip
    assert store.known_names() == {"a": "새 이름", "c": "씨"}


# ---------------------------------------------------------------- 6. 계약 예시


def test_chat_cards_example_validates() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cards = [TypeAdapter(DataChatCard).validate_python(c) for c in raw]
    assert [c.kind for c in cards] == ["trend_request", "trend_report_progress",
                                       "trend_report_done"]  # fmt: skip
    assert [c.model_dump(mode="json") for c in cards] == raw  # 빠진 칸·바뀐 값 없음
    assert dc.is_trend_request(cards[0].request_text)  # type: ignore[union-attr]
