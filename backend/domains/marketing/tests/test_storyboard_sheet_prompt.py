"""스토리보드 시트 프롬프트 모순 (11/26 D). 운영 증거 11/25 17:38 CR-4dc50bc2cc75:

- "Panel 3: close-up shot of the face — Vertical 9:16 short video, wide shot …" 샷 크기 모순
- "a single purpl." 단어 중간 잘림
- 9:16 영상인데 "Every panel is the same 16:9 size", 4칸인데 3열
LLM·Higgsfield를 부르지 않는다.
"""

from __future__ import annotations

from marketing_agent import storyboard as sb


def _shot(i: int, **over: object) -> dict:
    base = {"scene_ko": f"장면 {i}", "shot_size": "close_up" if i == 3 else "wide",
            "angle": "eye_level", "composition_ko": "가운데", "has_character": True,
            "subject_en": f"the girl walks {i}", "setting_en": "an autumn field",
            "light_en": "", "mood_en": ""}  # fmt: skip
    base.update(over)
    return base


def _read(shots: list[dict]) -> sb.StoryRead:
    return sb.StoryRead.model_validate({"title_ko": "t", "logline_ko": "l", "shots": shots})


def _out(n: int, aspect: str | None = "9:16", **over: object) -> sb.StoryboardPromptOutput:
    return sb.build_output(_read([_shot(i, **over) for i in range(1, n + 1)]), None, "anime", n,
                           aspect=aspect)  # fmt: skip


def test_panel_shape_follows_video_aspect() -> None:
    out = _out(4)
    assert "same 9:16 vertical size" in out.sheet_prompt_en
    assert "16:9" not in out.sheet_prompt_en
    wide = _out(4, aspect="16:9")
    assert "same 16:9 horizontal size" in wide.sheet_prompt_en


def test_default_aspect_comes_from_render_setting(monkeypatch) -> None:  # noqa: ANN001
    from marketing_agent import creative_render

    monkeypatch.setattr(creative_render, "video_aspect", lambda cid=None: "9:16")
    assert "same 9:16 vertical size" in _out(4, aspect=None).sheet_prompt_en


def test_columns_follow_panel_count() -> None:
    four, six, twelve = _out(4), _out(6), _out(12)
    assert four.columns == 2 and "grid of 2 columns and 2 rows" in four.sheet_prompt_en
    assert six.columns == 3 and "grid of 3 columns and 2 rows" in six.sheet_prompt_en
    assert twelve.columns == 3 and "grid of 3 columns and 4 rows" in twelve.sheet_prompt_en
    assert [sb.grid_columns(n) for n in (1, 2, 3, 4, 5, 6, 8, 9)] == [1, 2, 3, 2, 3, 3, 4, 3]


def test_shot_size_and_aspect_words_removed_from_scene() -> None:
    bad = "close-up shot of the face — Vertical 9:16 short video, wide shot of the smiling girl"
    out = _out(4, subject_en=bad)
    panel3 = next(line for line in out.sheet_prompt_en.split("Panel ")[1:] if line.startswith("3:"))
    body = panel3.split("—", 1)[1]
    low = body.lower()
    for word in ("close-up", "wide shot", "9:16", "vertical", "short video"):
        assert word not in low, (word, body)
    assert "smiling girl" in low
    # 칸 샷 크기는 코드 표 하나만 (3번 칸 = close_up)
    assert panel3.lower().count("close-up") + panel3.lower().count("close up") == 1
    assert "9:16" not in out.shots[2].prompt_en.split(".", 1)[1]


def test_scene_is_cut_on_word_boundary() -> None:
    long = "a single purple balloon " * 20
    out = _out(4, subject_en=long, setting_en="")
    for line in out.sheet_prompt_en.split("Panel ")[1:5]:
        body = line.split("—", 1)[1].split(". ")[0].strip(" .")
        assert body.split()[-1] in ("a", "single", "purple", "balloon"), body
    assert sb._cut_words("a single purple", 12) == "a single"
    assert sb._cut_words("one. two three", 10) == "one"
    assert sb._cut_words("short", 100) == "short"


def test_llm_rule_forbids_shot_size_in_subject() -> None:
    assert "샷 크기" in sb.PROMPT and "subject_en·setting_en·light_en·mood_en에는" in sb.PROMPT
