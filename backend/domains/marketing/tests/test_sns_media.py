"""SNS 미디어 규격 검사·AI 라벨 오버레이 시험.

ffmpeg 없이 도는 시험(규격 검사, 설정, 점검 규칙, 도구 없음 처리)과,
ffmpeg·ffprobe·글꼴이 있어야 도는 시험(실제 오버레이)으로 나뉜다.
뒤쪽은 도구가 없으면 사유를 밝히고 건너뛴다(약하게 만든 것이 아니다).
"""

import dataclasses
import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from marketing_agent import handlers, sns, sns_label, sns_media
from marketing_agent.main import app

CTX = "ctx_inc_0001"
client = TestClient(app)
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
ANY_FONT = next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)
needs_ffmpeg = pytest.mark.skipif(
    not (FFMPEG and ANY_FONT),
    reason="ffmpeg·ffprobe 또는 글꼴이 없어 실제 오버레이 시험을 건너뜀(이 PR 설명 참고)",
)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(media))
    monkeypatch.delenv(sns_label.CONFIG_ENV, raising=False)
    monkeypatch.delenv(sns_label.FONT_ENV, raising=False)
    events.reset_mock_bus()
    handlers.reset_state()


def media_dir() -> Path:
    root = sns_media.media_root()
    assert root is not None
    return root


def make_jpeg(path: Path, width: int, height: int, pad: int = 0) -> Path:
    """헤더만 맞춘 합성 JPEG. 규격 검사는 헤더(SOI·SOF)만 읽는다. 화면에 그릴 수는 없다."""
    sof = b"\xff\xc0" + (11).to_bytes(2, "big") + b"\x08"
    sof += height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x01\x01\x11\x00"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8" + sof + b"\xff\xd9" + b"\x00" * pad)
    return path


def record_overlay(folder: Path, name: str, kind: str = "image", mark: bool = True) -> None:
    """`sns_label`이 남기는 것과 같은 모양의 기록을 만든다(실제 오버레이 없이 점검 규칙만 시험)."""
    target = folder / name
    if mark and kind == "image":
        sns_label.inject_xmp(target, sns_label.machine_mark("test-model", datetime.now(UTC)))
    meta = {
        "ai_label": {
            "label_overlay": True,
            "labeled_file": name,
            "labeled_sha256": sns_media.sha256_file(target),
        }
    }
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _call(skill: str, input: dict) -> dict:
    body = {
        "meta": {
            "context_id": CTX,
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": input,
    }
    return client.post("/a2a", json=body).json()


def _coupon() -> dict:
    return {
        "campaign_id": "cmp_1",
        "unit_value": 2000,
        "constraints": [
            {"code": code, "text": f"{label} 안내"} for code, label in sns.CONSTRAINT_LABELS.items()
        ],
    }


def _draft(asset: Path | None = None, **extra) -> dict:
    data = {"coupon": _coupon()} | extra
    if asset is not None:
        data["asset_path"] = str(asset.relative_to(media_dir()))
    result = _call("draft_sns_post", data)
    assert result["status"] == "completed", result
    return result["output"]


def _check(draft: dict, **overrides) -> dict:
    data = {
        "post_draft_id": draft["post_draft_id"],
        "caption": draft["caption"],
        "is_ai_generated": True,
    } | overrides
    return _call("check_sns_post", data)["output"]


def labeled_image(width: int = 1080, height: int = 1350, mark: bool = True) -> Path:
    folder = media_dir() / "t1" / "c1" / "img1"
    path = make_jpeg(folder / "labeled.jpg", width, height)
    record_overlay(folder, "labeled.jpg", mark=mark)
    return path


# --- 이미지 규격 ---


@pytest.mark.parametrize(
    ("width", "height", "pad", "problem"),
    [
        (1080, 1350, 0, None),  # 4:5
        (1080, 566, 0, None),  # 1.91:1 가까이
        (1080, 1500, 0, "image_aspect_out_of_range"),  # 4:5보다 길다
        (1440, 600, 0, "image_aspect_out_of_range"),  # 1.91:1보다 넓다
        (300, 375, 0, "image_width_out_of_range"),
        (1500, 1500, 0, "image_width_out_of_range"),
        (1080, 1350, 9 * 1024 * 1024, "image_too_large"),
    ],
)
def test_image_spec(tmp_path: Path, width: int, height: int, pad: int, problem: str | None) -> None:
    report = sns_media.check_image(make_jpeg(tmp_path / "a.jpg", width, height, pad))
    assert report.problems == ([problem] if problem else [])


def test_image_must_be_jpeg(tmp_path: Path) -> None:
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert sns_media.check_image(png).problems == ["image_not_jpeg"]
    broken = tmp_path / "b.jpg"
    broken.write_bytes(b"\xff\xd8\xff\xd9")
    assert sns_media.check_image(broken).problems == ["image_unreadable"]


def test_valid_labeled_image_is_approved_and_publishable() -> None:
    asset = labeled_image()
    out = _check(_draft(asset))
    assert out["problems"] == []
    assert (out["verdict"], out["publishable"], out["media_checked"]) == ("approved", True, True)
    assert out["machine_mark_present"] is True
    assert out["ai_label_missing_count"] == 0


@pytest.mark.parametrize(("w", "h"), [(1080, 1500), (300, 375)])
def test_check_rejects_image_spec_violation(w: int, h: int) -> None:
    out = _check(_draft(labeled_image(w, h)))
    assert out["verdict"] == "rejected"
    assert out["publishable"] is False
    assert any(p.startswith("image_") for p in out["problems"])


# --- AI 라벨 ---


def test_missing_overlay_record_blocks() -> None:
    folder = media_dir() / "t1" / "c1" / "raw_only"
    asset = make_jpeg(folder / "labeled.jpg", 1080, 1350)
    out = _check(_draft(asset))
    assert out["verdict"] == "rejected"
    assert out["ai_label_missing_count"] == 1
    assert "ai_label_overlay_missing" in out["problems"]
    assert "machine_mark_missing" in out["problems"]
    assert out["publishable"] is False


def test_overlay_record_for_other_file_blocks() -> None:
    asset = labeled_image()
    asset.write_bytes(asset.read_bytes() + b"\x00")  # 기록한 뒤 파일이 바뀜
    out = _check(_draft(asset))
    assert out["ai_label_missing_count"] >= 1
    assert out["verdict"] == "rejected"


def test_missing_machine_mark_blocks() -> None:
    out = _check(_draft(labeled_image(mark=False)))
    assert out["machine_mark_present"] is False
    assert out["verdict"] == "rejected"
    assert out["publishable"] is False


def test_each_of_three_labels_blocks() -> None:
    draft = _draft(labeled_image())
    assert _check(draft)["verdict"] == "approved"
    caption = draft["caption"].replace(sns.AI_LABEL_TEXT, "")
    assert _check(draft, caption=caption)["ai_label_missing_count"] == 1
    assert _check(draft, is_ai_generated=None)["ai_label_missing_count"] == 1
    assert _check(draft, is_ai_generated=False)["verdict"] == "rejected"


def test_caption_only_draft_is_never_publishable() -> None:
    out = _check(_draft())
    assert out["verdict"] == "approved"  # 캡션 규칙은 통과하지만
    assert (out["media_checked"], out["publishable"]) == (False, False)
    assert "media_not_attached" in out["warnings"]


# --- 캡션 규칙 ---


def test_caption_limits() -> None:
    draft = _draft(labeled_image())
    tags = " ".join(f"#t{i}" for i in range(sns.HASHTAG_MAX))
    out = _check(draft, caption=draft["caption"] + "\n" + tags)
    assert "hashtag_limit_exceeded" in out["problems"]  # 기본 #캣북스 1개 + 30개
    mentions = " ".join(f"@u{i}" for i in range(sns.MENTION_MAX + 1))
    out = _check(draft, caption=draft["caption"] + "\n" + mentions)
    assert "mention_limit_exceeded" in out["problems"]
    assert out["mention_count"] == sns.MENTION_MAX + 1


# --- 내용 주소 id·저장 ---


def test_content_id_covers_caption_asset_kind_and_options(tmp_path: Path) -> None:
    asset = labeled_image()
    base = _draft(asset)
    assert base["asset_sha256"] == sns_media.sha256_file(asset)
    assert _draft(asset)["post_draft_id"] == base["post_draft_id"]
    other = make_jpeg(media_dir() / "t1" / "c1" / "img1" / "labeled.jpg", 1080, 1351)
    assert _draft(other)["post_draft_id"] != base["post_draft_id"]
    assert _draft()["post_draft_id"] != base["post_draft_id"]
    a = sns.content_id("c", "s", "reel", "instagram", {"share_to_feed": True})
    assert a != sns.content_id("c", "s", "reel", "instagram", {"share_to_feed": False})
    assert a != sns.content_id("c", "s", "image", "instagram", {"share_to_feed": True})
    assert a != sns.content_id("c2", "s", "reel", "instagram", {"share_to_feed": True})
    assert a != sns.content_id("c", "s2", "reel", "instagram", {"share_to_feed": True})


def test_reel_draft_states_share_to_feed() -> None:
    draft = _draft(media_kind="reel")
    assert draft["platform_options"] == {"share_to_feed": True}
    assert sns.AI_LABEL_TEXT_REEL in draft["caption"]
    off = _draft(media_kind="reel", platform_options={"share_to_feed": False})
    assert off["post_draft_id"] != draft["post_draft_id"]


def test_asset_must_be_inside_media_dir(tmp_path: Path) -> None:
    outside = make_jpeg(tmp_path / "outside.jpg", 1080, 1350)
    result = _call("draft_sns_post", {"coupon": _coupon(), "asset_path": str(outside)})
    assert result["outcome"] == "asset_outside_media_dir"
    missing = _call("draft_sns_post", {"coupon": _coupon(), "asset_path": "nope.jpg"})
    assert missing["outcome"] == "asset_not_found"
    traversal = _call("draft_sns_post", {"coupon": _coupon(), "asset_path": "../outside.jpg"})
    assert traversal["outcome"] == "asset_outside_media_dir"


def test_asset_changed_after_draft() -> None:
    asset = labeled_image()
    draft = _draft(asset)
    asset.write_bytes(asset.read_bytes() + b"\x00")
    out = _check(draft)
    assert out["text_mismatch_count"] >= 1
    assert "asset_changed_after_draft" in out["problems"]


def test_draft_store_persists_in_file(tmp_path: Path) -> None:
    db = str(tmp_path / "drafts.db")
    first = sns.SnsStore(db)
    draft = sns.SnsPostDraft(
        post_draft_id="spd_x",
        source_kind="coupon",
        source_ref="cmp_1",
        platform="instagram",
        media_kind="image",
        caption="c",
        hashtags=[],
        caption_sha256="h",
        required_text=[],
        context_id=CTX,
    )
    first.save(draft)
    first.close()
    assert sns.SnsStore(db).get("spd_x") == draft
    assert sns.SnsStore().get("spd_x") is None  # 기본은 메모리


def test_draft_store_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "env.db"
    monkeypatch.setenv(sns.DRAFT_DB_ENV, str(db))
    store = sns.SnsStore()
    store.close()
    assert db.exists()


# --- 도구 없음 ---


def test_reel_without_ffprobe_is_not_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    folder = media_dir() / "t1" / "c1" / "vid1"
    folder.mkdir(parents=True)
    video = folder / "labeled.mp4"
    video.write_bytes(b"not really a video")
    (folder / "meta.json").write_text(
        json.dumps(
            {
                "ai_label": {
                    "label_overlay": True,
                    "labeled_file": "labeled.mp4",
                    "labeled_sha256": sns_media.sha256_file(video),
                }
            }
        ),
        encoding="utf-8",
    )
    empty = tmp_path / "empty_path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    out = _check(_draft(video, media_kind="reel"))
    assert out["media_check_unavailable"] is True
    assert out["verdict"] == "rejected"
    assert out["publishable"] is False
    assert out["machine_mark_present"] is None  # 읽지 못한 값은 지어내지 않는다
    assert "media_check_unavailable_ffprobe_not_found" in out["problems"]


def test_overlay_without_tools_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = make_jpeg(tmp_path / "src.jpg", 1080, 1350)
    font = tmp_path / "font.ttf"
    font.write_bytes(b"x")
    monkeypatch.setenv(sns_label.FONT_ENV, str(font))
    empty = tmp_path / "empty_path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(sns_label.LabelError) as caught:
        sns_label.apply_label(src, tmp_path / "out", generator_model="m")
    assert caught.value.code == "ffmpeg_not_found"
    assert not (tmp_path / "out" / "labeled.jpg").exists()  # 조용히 라벨 없는 파일을 만들지 않는다


def test_overlay_without_font_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = make_jpeg(tmp_path / "src.jpg", 1080, 1350)
    cfg = dataclasses.replace(
        sns_label.load_config(), font_file="", font_bundled="", font_candidates=()
    )
    with pytest.raises(sns_label.LabelError) as caught:
        sns_label.apply_label(src, tmp_path / "out", generator_model="m", config=cfg)
    assert caught.value.code == "font_not_found"
    monkeypatch.setenv(sns_label.FONT_ENV, str(tmp_path / "missing.ttf"))
    with pytest.raises(sns_label.LabelError) as caught:
        sns_label.apply_label(src, tmp_path / "out", generator_model="m")
    assert caught.value.code == "font_not_found"


# --- 설정 파일 ---


def _config_text() -> str:
    return sns_label.DEFAULT_CONFIG.read_text(encoding="utf-8")


def test_default_config_is_marked_provisional() -> None:
    cfg = sns_label.load_config()
    assert "재헌 확정(2026-11-12)" in cfg.text_status
    assert "확인 필요" in cfg.text_status  # 서면 보관은 [확인 필요]
    assert "승인" not in cfg.text_status
    assert cfg.text_image == "생성형 AI로 만든 이미지"
    assert cfg.text_video == "생성형 AI로 만든 영상"
    assert cfg.text_unknown == "생성형 AI로 만든 이미지/영상"
    assert cfg.display_time == "full"
    assert len(cfg.sha256) == 64


def test_config_file_controls_text_and_position(tmp_path: Path) -> None:
    path = tmp_path / "label.toml"
    label = "생성형 AI로 만든 영상"
    text = _config_text().replace('position = "bottom"', 'position = "top"')
    text = text.replace("font_size_ratio = 0.032", "font_size_ratio = 0.05")
    path.write_text(text.replace(label, "CUSTOM LABEL"), encoding="utf-8")
    cfg = sns_label.load_config(path)
    assert (cfg.position, cfg.text_video) == ("top", "CUSTOM LABEL")
    base = sns_label.load_config()
    textfile = tmp_path / "t.txt"
    top = sns_label.build_drawtext(cfg, "/f.ttf", 1000, 2000, str(textfile), cfg.text_video)
    bottom = sns_label.build_drawtext(base, "/f.ttf", 1000, 2000, str(textfile), cfg.text_video)
    # 여백은 상자 가장자리 기준이라 글자 위치에 상자 안쪽 여백(border)을 더한다.
    # 1000x2000은 세로로 긴 화면이라 위·아래 여백이 2000 x 0.15 = 300이다
    assert "y=320" in top  # 300 + border 20(글자 100px x 0.2)
    assert "y=h-text_h-313" in bottom  # 300 + border 13(글자 64px x 0.2)
    assert "x=w-text_w-63" in bottom  # 기본은 오른쪽: 좌우 여백 1000 x 0.05 = 50 + border 13
    assert "fontsize=100" in top and "fontsize=64" in bottom
    assert "enable" not in top  # 표시 시간 제한 없음 = 전 구간
    assert textfile.read_text(encoding="utf-8") == "CUSTOM LABEL"


def test_config_env_selects_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "label.toml"
    path.write_text(
        _config_text().replace('position = "bottom"', 'position = "center"'), encoding="utf-8"
    )
    monkeypatch.setenv(sns_label.CONFIG_ENV, str(path))
    assert sns_label.load_config().position == "center"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('position = "bottom"', 'position = "left"'),
        ('time = "full"', 'time = "first_3s"'),
        ("jpeg_quality = 92", "jpeg_quality = 0"),
        ('font_color = "white"', 'font_color = "white:x=1"'),
        ("font_size_ratio = 0.032", "font_size_ratio = 0"),
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, old: str, new: str) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(_config_text().replace(old, new), encoding="utf-8")
    with pytest.raises(sns_label.LabelError) as caught:
        sns_label.load_config(path)
    assert caught.value.code == "config_invalid"
    with pytest.raises(sns_label.LabelError):
        sns_label.load_config(tmp_path / "nope.toml")


def test_wrap_text_keeps_lines_inside_width() -> None:
    cfg = sns_label.load_config()
    long_text = ("생성형 AI로 만든 영상 " * 3).strip()
    wrapped = sns_label.wrap_text(long_text, 270, 20, cfg)
    assert "\n" in wrapped
    assert wrapped.replace("\n", " ") == long_text
    per_line = int(270 * (1 - 2 * cfg.side_margin_ratio) / 20)
    assert all(len(line) <= per_line for line in wrapped.split("\n"))


def test_text_for_picks_text_by_kind() -> None:
    cfg = sns_label.load_config()
    assert cfg.text_for("image") == "생성형 AI로 만든 이미지"
    assert cfg.text_for("video") == "생성형 AI로 만든 영상"
    assert cfg.text_for("unknown") == "생성형 AI로 만든 이미지/영상"
    assert cfg.text_for("something-else") == cfg.text_unknown


def test_config_text_per_kind_and_empty_rejected(tmp_path: Path) -> None:
    path = tmp_path / "label.toml"
    path.write_text(
        _config_text().replace("생성형 AI로 만든 이미지/영상", "KIND UNKNOWN"), encoding="utf-8"
    )
    assert sns_label.load_config(path).text_unknown == "KIND UNKNOWN"
    path.write_text(
        _config_text().replace('unknown = "생성형 AI로 만든 이미지/영상"', 'unknown = ""'),
        encoding="utf-8",
    )
    with pytest.raises(sns_label.LabelError) as caught:
        sns_label.load_config(path)
    assert caught.value.code == "config_invalid"


def test_drawtext_text_goes_via_file_so_slash_and_colon_are_safe(tmp_path: Path) -> None:
    cfg = sns_label.load_config()
    textfile = tmp_path / "t.txt"
    label = "생성형 AI로 만든 이미지/영상: 100% \\ %{n}"
    font = "C:\\f\\a'b.ttf"
    draw = sns_label.build_drawtext(cfg, font, 1000, 2000, str(textfile), label)
    written = textfile.read_text(encoding="utf-8").replace(chr(10), " ")
    assert written == label  # 문구는 필터 문자열에 들어가지 않는다
    assert "생성형" not in draw and "/영상" not in draw
    assert "expansion=none" in draw  # `%{n}`을 해석하지 않는다
    assert "fontfile='C\\:/f/a'\\''b.ttf'" in draw  # 콜론·역슬래시·작은따옴표 이스케이프


def test_machine_mark_xmp_roundtrip(tmp_path: Path) -> None:
    jpeg = make_jpeg(tmp_path / "a.jpg", 1080, 1350)
    at = datetime(2026, 11, 5, 1, 2, 3, tzinfo=UTC)
    sns_label.inject_xmp(jpeg, sns_label.machine_mark("model-x", at))
    assert sns_media.read_machine_mark(jpeg, "image") == {
        "ai_generated": "true",
        "ai_generator_model": "model-x",
        "ai_generated_at": "2026-11-05T01:02:03Z",
    }
    assert "trainedAlgorithmicMedia" in (sns_media.jpeg_xmp(jpeg) or "")
    assert sns_media.jpeg_size(jpeg) == (1080, 1350)  # 규격 검사는 그대로 읽는다


# --- 실제 ffmpeg (도구가 있을 때만) ---


def _ascii_config(**kw) -> sns_label.LabelConfig:
    return dataclasses.replace(
        sns_label.load_config(),
        text_image="AI generated image - demo service",
        text_video="AI generated video - demo service",
        font_file=ANY_FONT or "",
        **kw,
    )


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)  # noqa: S603, S607


@needs_ffmpeg
def test_overlay_image_end_to_end(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=720x900:rate=1", "-frames:v", "1", str(src))
    out = media_dir() / "t1" / "c1" / "img1"
    at = datetime(2026, 11, 5, tzinfo=UTC)
    result = sns_label.apply_label(
        src, out, generator_model="gpt-image-x", config=_ascii_config(), now=at
    )
    labeled = out / "labeled.jpg"
    assert result.labeled_path == labeled
    assert result.labeled_sha256 == sns_media.sha256_file(labeled)
    assert (out / "raw.png").read_bytes() == src.read_bytes()  # 원본 보관
    assert result.source_sha256 == sns_media.sha256_file(src)
    assert sns_media.is_jpeg(labeled)
    assert sns_media.check_image(labeled).problems == []
    assert labeled.read_bytes() != src.read_bytes()
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))["ai_label"]
    assert meta["label_overlay"] is True
    assert meta["labeled_sha256"] == result.labeled_sha256
    assert meta["ai_generated"] is True
    assert (meta["ai_generator_model"], meta["ai_generated_at"]) == (
        "gpt-image-x",
        "2026-11-05T00:00:00Z",
    )
    assert "확인 필요" in meta["text_status"]
    assert meta["text"] == "AI generated image - demo service"
    xmp = sns_media.jpeg_xmp(labeled) or ""
    assert 'ai_label_text="AI generated image - demo service"' in xmp
    assert sns_media.read_machine_mark(labeled, "image")["ai_generated"] == "true"

    checked = _check(_draft(labeled))
    assert checked["verdict"] == "approved", checked["problems"]
    assert checked["publishable"] is True


@needs_ffmpeg
def test_overlay_config_changes_output(tmp_path: Path) -> None:
    src = tmp_path / "src.jpg"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=720x900:rate=1", "-frames:v", "1", str(src))
    bottom = sns_label.apply_label(src, tmp_path / "a", generator_model="m", config=_ascii_config())
    top = sns_label.apply_label(
        src, tmp_path / "b", generator_model="m", config=_ascii_config(position="top")
    )
    assert bottom.labeled_sha256 != top.labeled_sha256


@needs_ffmpeg
def test_overlay_video_end_to_end(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30:duration=3.5",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src),
    )  # fmt: skip
    out = media_dir() / "t1" / "c1" / "vid1"
    result = sns_label.apply_label(src, out, generator_model="gemini-x", config=_ascii_config())
    labeled = out / "labeled.mp4"
    assert result.labeled_sha256 == sns_media.sha256_file(labeled)
    assert (out / "raw.mp4").read_bytes() == src.read_bytes()
    report = sns_media.check_reel(labeled)
    assert report.problems == [], report.problems
    assert report.details["tags"]["ai_generated"] == "true"
    assert report.details["tags"]["ai_generator_model"] == "gemini-x"
    assert report.details["tags"]["description"] == "AI generated video - demo service"
    assert report.details["duration_s"] == pytest.approx(3.5, abs=0.3)  # 전 구간 유지

    draft = _draft(labeled, media_kind="reel")
    checked = _check(draft)
    assert checked["verdict"] == "approved", checked["problems"]
    assert checked["publishable"] is True
    assert checked["machine_mark_present"] is True


@needs_ffmpeg
def test_reel_spec_violations(tmp_path: Path) -> None:
    short = tmp_path / "short.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(short),
    )  # fmt: skip
    assert "video_duration_out_of_range" in sns_media.check_reel(short).problems
    mpeg4 = tmp_path / "old.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30:duration=4",
        "-c:v", "mpeg4", str(mpeg4),
    )  # fmt: skip
    assert "video_codec_unsupported" in sns_media.check_reel(mpeg4).problems
    wide = tmp_path / "wide.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(wide),
    )  # fmt: skip
    wide_report = sns_media.check_reel(wide)
    assert wide_report.problems == []
    assert "video_aspect_not_9_16" in wide_report.warnings  # 권장 위반은 경고
    slow = tmp_path / "slow.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=270x480:rate=10:duration=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(slow),
    )  # fmt: skip
    assert "video_fps_out_of_range" in sns_media.check_reel(slow).problems
    webm = tmp_path / "a.webm"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30:duration=4",
        "-c:v", "libvpx", str(webm),
    )  # fmt: skip
    probe_problems = sns_media.check_reel(webm).problems
    assert "video_container_unsupported" in probe_problems


@needs_ffmpeg
def test_overlay_default_korean_text_with_system_font(tmp_path: Path) -> None:
    """기본 문구(슬래시 포함)를 한글 글꼴로 실제로 그려 파일이 만들어지고 메타가 같다."""
    cfg = dataclasses.replace(sns_label.load_config(), font_file=ANY_FONT or "")
    if sns_label.missing_glyphs(ANY_FONT or "", cfg.text_unknown):
        pytest.skip("로컬 글꼴에 한글이 없다")
    src = tmp_path / "src.png"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=720x900:rate=1", "-frames:v", "1", str(src))
    result = sns_label.apply_label(src, tmp_path / "o", generator_model="m", config=cfg)
    assert result.text == "생성형 AI로 만든 이미지"
    assert "생성형 AI로 만든 이미지" in (sns_media.jpeg_xmp(result.labeled_path) or "")
    unknown_cfg = dataclasses.replace(cfg, text_image=cfg.text_unknown)
    again = sns_label.apply_label(src, tmp_path / "o2", generator_model="m", config=unknown_cfg)
    assert again.text == "생성형 AI로 만든 이미지/영상"
    assert again.labeled_path.stat().st_size > 0


def test_missing_glyphs_detects_font_without_hangul(tmp_path: Path) -> None:
    assert (
        sns_label.missing_glyphs(str(tmp_path / "nope.ttf"), "생성형") == ""
    )  # 읽지 못하면 건너뜀
    bad = tmp_path / "bad.ttf"
    bad.write_bytes(b"not a font")
    assert sns_label.missing_glyphs(str(bad), "생성형") == ""


@needs_ffmpeg
def test_apply_label_refuses_text_the_font_cannot_draw(tmp_path: Path) -> None:
    """글꼴에 없는 글자가 문구에 있으면 네모로 그린 라벨을 만들지 않고 거절한다."""
    bundled = sns_label.bundled_font(sns_label.load_config())
    assert bundled is not None
    odd = "\U0001f600"  # 이모지: Pretendard에 없다
    if not sns_label.missing_glyphs(str(bundled), odd):
        pytest.skip("번들 글꼴에 시험용 글자가 있다")
    cfg = dataclasses.replace(sns_label.load_config(), text_image=odd)
    src = tmp_path / "src.png"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=720x900:rate=1", "-frames:v", "1", str(src))
    with pytest.raises(sns_label.LabelError) as caught:
        sns_label.apply_label(src, tmp_path / "o", generator_model="m", config=cfg)
    assert caught.value.code == "font_glyph_missing"
    assert not (tmp_path / "o" / "labeled.jpg").exists()


def test_bundled_font_covers_default_label_texts() -> None:
    cfg = sns_label.load_config()
    bundled = sns_label.bundled_font(cfg)
    assert bundled is not None
    for kind in ("image", "video", "unknown"):
        assert sns_label.missing_glyphs(str(bundled), cfg.text_for(kind)) == ""


@needs_ffmpeg
def test_default_labels_render_with_bundled_font(tmp_path: Path) -> None:
    """번들 글꼴만으로(환경변수·toml file 없이) 이미지·영상 한글 라벨이 거절 없이 만들어진다."""
    cfg = sns_label.load_config()
    assert not cfg.font_file
    src = tmp_path / "src.png"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=720x900:rate=1", "-frames:v", "1", str(src))
    image = sns_label.apply_label(src, tmp_path / "i", generator_model="m", config=cfg)
    assert image.text == "생성형 AI로 만든 이미지"
    assert image.labeled_path.stat().st_size > 0
    vid = tmp_path / "src.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30:duration=3.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid),
    )  # fmt: skip
    video = sns_label.apply_label(vid, tmp_path / "v", generator_model="m", config=cfg)
    assert video.text == "생성형 AI로 만든 영상"
    assert video.labeled_path.stat().st_size > 0
