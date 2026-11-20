"""스토리보드: 칸 수 부족 재시도·실패 로그 (skill `storyboard_prompt`). 가짜 LLM으로만 돈다."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ax_agent_template.llm import LLMFormatError, LLMResponseError
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from marketing_agent import handlers
from marketing_agent import storyboard as sb

STORY = "가을 들판에서 자전거를 타던 소녀가 고양이를 만나 노을을 보며 주먹밥을 나눠 먹는다"


def _read(n: int) -> sb.StoryRead:
    return sb.StoryRead.model_validate(
        {
            "title_ko": "가을 들판",
            "logline_ko": "소녀와 고양이의 하루",
            "shots": [{"scene_ko": f"장면 {i}", "subject_en": "the girl"} for i in range(1, n + 1)],
        }
    )


class Seq:
    """호출마다 미리 정한 답(또는 예외)을 차례로 돌려주는 가짜 LLM. 마지막 답은 반복한다."""

    def __init__(self, *steps: object, timeout: float | None = None) -> None:
        self.steps = list(steps)
        self.calls = 0
        if timeout is not None:
            self.timeout = timeout

    async def structured(self, *a: object, **k: object) -> object:
        step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        if isinstance(step, BaseException):
            raise step
        return step


def _run(untrusted: dict, llm: Seq) -> object:
    return asyncio.run(sb.make_storyboard(untrusted, lambda: llm))


def _request(untrusted: dict) -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id="ctx_test_storyboard_retry",
            token="t",
            idempotency_key="k1",
            deadline=datetime.now(UTC) + timedelta(seconds=120),
        ),
        skill="storyboard_prompt",
        input={},
        untrusted=untrusted,
    )


@pytest.mark.parametrize("empty_calls", [1, 2])
def test_short_answers_are_retried_until_it_succeeds(empty_calls: int) -> None:
    llm = Seq(*([_read(0)] * empty_calls), _read(6))
    out = _run({"story": STORY, "shots": 6}, llm)
    assert len(out.shots) == 6 and llm.calls == empty_calls + 1


def test_three_short_answers_are_refused_as_llm_failed() -> None:
    llm = Seq(_read(0), _read(3), _read(0), _read(6))
    with pytest.raises(sb.StoryboardRefused) as err:
        _run({"story": STORY, "shots": 6}, llm)
    assert err.value.code == "llm_failed" and "6칸" in err.value.message
    assert llm.calls == 1 + sb.MAX_SHORT_RETRIES == 3


@pytest.mark.parametrize(
    "error",
    [LLMResponseError("timeout"), LLMResponseError("x", status_code=500), LLMFormatError("bad")],
)
def test_api_errors_are_not_retried(error: Exception) -> None:
    llm = Seq(error, _read(6))
    with pytest.raises(sb.StoryboardRefused) as err:
        _run({"story": STORY, "shots": 6}, llm)
    assert err.value.code == "llm_failed" and llm.calls == 1


@pytest.mark.parametrize(
    ("untrusted", "code"),
    [({}, "no_story"), ({"story": STORY, "shots": 7}, "invalid_input")],
)
def test_bad_inputs_do_not_call_the_llm(untrusted: dict, code: str) -> None:
    llm = Seq(_read(6))
    with pytest.raises(sb.StoryboardRefused) as err:
        _run(untrusted, llm)
    assert err.value.code == code and llm.calls == 0


def test_no_retry_when_the_next_call_could_pass_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = [0.0]  # 시작 시각만 0, 그 뒤는 70초: 첫 응답이 70초 걸림 → 70 + 60 > 110
    fake = SimpleNamespace(monotonic=lambda: ticks.pop(0) if ticks else 70.0)
    monkeypatch.setattr(sb, "time", fake)  # asyncio가 쓰는 time 모듈은 건드리지 않는다
    llm = Seq(_read(0), _read(6), timeout=60.0)
    with pytest.raises(sb.StoryboardRefused):
        _run({"story": STORY, "shots": 6}, llm)
    assert llm.calls == 1


def _count_records(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    recorded: list[int] = []
    monkeypatch.setattr(
        handlers, "_record_storyboard", lambda out, untrusted: recorded.append(1) or "CR-1"
    )
    return recorded


def test_record_is_written_once_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = Seq(_read(0), _read(0), _read(6))
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", lambda: llm)
    recorded = _count_records(monkeypatch)
    result = asyncio.run(
        handlers.HANDLERS["storyboard_prompt"](_request({"story": STORY, "shots": 6}))
    )
    assert result.status == TaskStatus.COMPLETED and llm.calls == 3 and recorded == [1]


def test_record_is_not_written_when_all_attempts_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = Seq(_read(0))
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", lambda: llm)
    recorded = _count_records(monkeypatch)
    result = asyncio.run(
        handlers.HANDLERS["storyboard_prompt"](_request({"story": STORY, "shots": 6}))
    )
    assert result.status == TaskStatus.FAILED and result.outcome == "llm_failed"
    assert recorded == [] and llm.calls == 3


def test_logs_have_counts_but_never_the_story_character_or_style(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = {
        "story": "비밀줄거리-ZXQ 소녀가 달린다",
        "character_en": "secret-character-ZXQ brown hair",
        "style_en": "secret-style-ZXQ watercolor",
    }
    with caplog.at_level(logging.INFO, logger="marketing.storyboard"):
        with pytest.raises(sb.StoryboardRefused):
            _run({**secret, "shots": 9}, Seq(_read(0)))
        _run({**secret, "shots": 6}, Seq(_read(6)))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "ZXQ" not in text and "소녀" not in text and "장면 1" not in text
    assert "outcome=llm_failed" in text and "요청칸=9" in text and "실제칸=0" in text
    assert "시도=3" in text and "초" in text
    assert text.count("스토리보드 재시도") == 2 and "스토리보드 완료" in text


def test_app_logging_setup_is_idempotent_and_scoped_to_marketing() -> None:
    from marketing_agent import main as m

    root_before = list(logging.getLogger().handlers)
    m._setup_app_logging()
    m._setup_app_logging()
    mk = logging.getLogger("marketing")
    assert sum(getattr(h, "_ax_marketing", False) for h in mk.handlers) == 1
    assert mk.level == logging.INFO
    assert logging.getLogger().handlers == root_before
