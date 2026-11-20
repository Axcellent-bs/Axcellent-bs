"""분류 시험 문장 샘플(`request_samples.json`)이 일 목록과 맞는지 본다 (LLM은 부르지 않는다)."""

import json
from pathlib import Path

from ax_orchestrator import request_tasks
from ax_orchestrator.request_classifier import build_prompt

SAMPLES = json.loads((Path(__file__).parent / "request_samples.json").read_text(encoding="utf-8"))
KINDS = {"task", "question", "clarify", "rejected", "unknown"}


def test_samples_are_thirty_sentences_with_all_kinds_of_answer() -> None:
    assert len(SAMPLES) == 30
    assert {s["expect"] for s in SAMPLES} == KINDS
    assert len({s["text"] for s in SAMPLES}) == 30


def test_task_answers_are_names_in_the_task_list_and_other_answers_have_none() -> None:
    for sample in SAMPLES:
        if sample["expect"] == "task":
            assert sample["task"] in request_tasks.BY_NAME, sample["text"]
        else:
            assert "task" not in sample, sample["text"]


def test_samples_are_not_copies_of_the_examples_shown_to_the_classifier() -> None:
    shown = build_prompt()
    for sample in SAMPLES:
        assert sample["text"] not in shown, sample["text"]  # 예시를 그대로 쓰면 정확도가 부풀려진다
