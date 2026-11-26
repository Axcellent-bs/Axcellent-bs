"""홍보 사이트에 올리기(`promo_site`). 안전 관문·AI 표시·ffmpeg는 가짜."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import ExecuteDecision, HoldReason, RequestMeta
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as render_mod
from marketing_agent import creative_thumbnails as thumbs
from marketing_agent import handlers, sns_media
from marketing_agent import promo_site as promo

SHEET = {"summary_ko": "지도 캐릭터", "prompt_en": "x", "negative_prompt_en": "y"}


class Gate:
    def __init__(self) -> None:
        self.status = {promo.PUBLISH_ACTION: "held", promo.REMOVE_ACTION: "executed"}
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    async def __call__(
        self, action: str, params: dict, meta: RequestMeta, evidence: Any
    ) -> ExecuteDecision:
        self.calls.append((action, params, str(meta.idempotency_key)))
        status = self.status[action]
        extra: dict[str, Any] = {}
        if status == "held":
            extra = {"hold_reason": list(HoldReason)[0], "review_id": "rev_1"}
        return ExecuteDecision(
            execution_id=f"ex_{len(self.calls)}",
            grade="C" if action == promo.PUBLISH_ACTION else "A",
            status=status,  # type: ignore[arg-type]
            **extra,
        )


def _meta() -> RequestMeta:
    return RequestMeta(
        context_id="ctx_promo_test",
        token="t",
        idempotency_key="k0",
        deadline=datetime.now(UTC) + timedelta(seconds=60),
    )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Any]]:
    media = tmp_path / "media"
    feed = tmp_path / "feed"
    media.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(media))
    monkeypatch.setenv(promo.FEED_DIR_ENV, str(feed))
    monkeypatch.delenv(promo.COMPANIES_ENV, raising=False)
    store = cr.CreativeRecords(":memory:")
    cr.set_store(store)
    gate = Gate()
    monkeypatch.setattr(promo, "execute", gate)

    def labeled(item_id: str, data: bytes | None = None) -> tuple[str, str]:
        """AI 표시를 입힌 것처럼 미디어 폴더에 파일을 둔다."""
        path = media / "labeled" / item_id / "labeled.mp4"
        if data is not None or not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data or f"video-{item_id}".encode())
        return path.relative_to(media).as_posix(), _sha(path.read_bytes())

    async def record_video(record_id: str, part: str) -> tuple[str, str]:
        return labeled(record_id)

    def frame(video: Path, out: Path) -> bool:
        out.write_bytes(b"jpg")
        return True

    monkeypatch.setattr(promo, "RECORD_VIDEO", record_video)
    monkeypatch.setattr(promo, "REQUEST_VIDEO", lambda _sns, item: labeled(item))
    monkeypatch.setattr(thumbs, "FIRST_FRAME", frame)
    monkeypatch.setattr(
        render_mod,
        "asset_path",
        lambda rid, part, cid=None: media / "creative" / rid / "video.mp4",
    )
    yield {"store": store, "gate": gate, "feed": feed, "media": media, "labeled": labeled}
    cr.set_store(None)


def _record(env: dict[str, Any], images: bool = True) -> str:
    store: cr.CreativeRecords = env["store"]
    rid = store.add_sheet(SHEET, None, None)
    folder = env["media"] / "creative" / rid
    folder.mkdir(parents=True)
    render: dict[str, Any] = {
        "video": {"status": "done", "asset": "video.mp4", "sha256": "raw", "seconds": 15}
    }
    if images:
        for part, name, data in (
            ("sheet_image", "sheet.jpg", b"sheet"),
            ("board_image", "board.png", b"board"),
        ):
            (folder / name).write_bytes(data)
            render[part] = {"status": "done", "asset": name, "sha256": _sha(data)}
    store.set_render(rid, render)
    return rid


def _publish(item_id: str, **more: Any) -> dict[str, Any]:
    args = {"title": "누비의 여행", "customer": "캣북스", "who": "준하", "rights": True} | more
    return asyncio.run(
        promo.publish(
            _meta(), None, item_id, args["title"], args["customer"], args["who"], args["rights"]
        )
    )


def _check() -> None:
    asyncio.run(promo.check_all(_meta()))


def _feed(env: dict[str, Any]) -> list[dict[str, Any]]:
    path = env["feed"] / promo.FEED_NAME
    return json.loads(path.read_text(encoding="utf-8"))["items"] if path.is_file() else []


def _files(env: dict[str, Any]) -> set[str]:
    return {p.name for p in env["feed"].iterdir()} if env["feed"].is_dir() else set()


def test_nothing_is_written_until_a_person_approves(env: dict[str, Any]) -> None:
    rid = _record(env)
    state = _publish(rid)
    assert state["status"] == "awaiting_approval" and state["review_id"] == "rev_1"
    assert _files(env) == set()  # 승인 전에는 폴더에 아무것도 없다
    env["gate"].status[promo.PUBLISH_ACTION] = "executed"
    _check()
    (item,) = _feed(env)
    assert item["title"] == "누비의 여행" and item["customer"] == "캣북스"
    assert item["seconds"] == 15
    assert (env["feed"] / item["video"]).read_bytes() == f"video-{rid}".encode()
    assert (env["feed"] / item["poster"]).read_bytes() == b"jpg"
    assert (env["feed"] / item["sheet"]).read_bytes() == b"sheet"
    assert item["board"].endswith("-board.png")
    assert rid not in json.dumps(item)  # 기록 번호는 밖에 드러나지 않는다
    assert len({c[2] for c in env["gate"].calls}) == 1  # 승인에 올린 키 그대로 다시 물었다
    assert promo.snapshot()["items"][0]["status"] == "published"


def test_a_request_video_can_be_published_too(env: dict[str, Any]) -> None:
    env["gate"].status[promo.PUBLISH_ACTION] = "executed"
    assert _publish("spd_a5d5fc196363d0ef")["status"] == "published"
    (item,) = _feed(env)
    assert item["sheet"] is None and item["board"] is None
    assert env["gate"].calls[0][1]["item_id"] == "spd_a5d5fc196363d0ef"


def test_only_three_at_a_time_counting_waiting_ones(env: dict[str, Any]) -> None:
    ids = [f"spd_{n}" for n in range(4)]
    for item in ids[:3]:
        _publish(item)
    with pytest.raises(promo.PromoError, match="limit_reached"):
        _publish(ids[3])
    assert promo.snapshot()["used"] == 3
    asyncio.run(promo.remove(_meta(), ids[0], "준하"))
    assert _publish(ids[3])["status"] == "awaiting_approval"


def test_newest_published_comes_first(env: dict[str, Any]) -> None:
    env["gate"].status[promo.PUBLISH_ACTION] = "executed"
    _publish("spd_old", title="먼저")
    _publish("spd_new", title="나중")
    assert [i["title"] for i in _feed(env)] == ["나중", "먼저"]


@pytest.mark.parametrize(
    ("more", "code"),
    [
        ({"rights": False}, "rights_not_confirmed"),
        ({"who": None}, "rights_not_confirmed"),
        ({"title": "  "}, "title_required"),
        ({"customer": ""}, "title_required"),
    ],
)
def test_it_needs_rights_title_and_customer(
    env: dict[str, Any], more: dict[str, Any], code: str
) -> None:
    with pytest.raises(promo.PromoError, match=code):
        _publish("spd_1", **more)
    assert env["gate"].calls == []


def test_it_needs_a_finished_video_a_folder_and_an_allowed_company(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = env["store"].add_sheet(SHEET, None, None)
    with pytest.raises(promo.PromoError, match="video_not_ready"):
        _publish(empty)
    with pytest.raises(promo.PromoError, match="not_found"):
        _publish("CR-000000000000")
    monkeypatch.delenv(promo.FEED_DIR_ENV)
    with pytest.raises(promo.PromoError, match="feed_not_configured"):
        _publish("spd_1")
    assert promo.snapshot()["feed_ready"] is False
    monkeypatch.setenv(promo.COMPANIES_ENV, "nubi")
    with pytest.raises(promo.PromoError, match="company_not_allowed"):
        _publish("spd_1")
    assert promo.snapshot()["available"] is False
    assert env["gate"].calls == []


def test_remove_deletes_the_files_and_publishing_again_asks_with_a_new_key(
    env: dict[str, Any],
) -> None:
    env["gate"].status[promo.PUBLISH_ACTION] = "executed"
    rid = _record(env)
    _publish(rid)
    (env["feed"] / "other.txt").write_text("남의 파일", encoding="utf-8")
    assert asyncio.run(promo.remove(_meta(), rid, "준하"))["status"] == "removed"
    assert _feed(env) == []
    assert _files(env) == {promo.FEED_NAME, "other.txt"}  # 우리 파일만 지운다
    env["gate"].status[promo.PUBLISH_ACTION] = "held"
    assert _publish(rid)["status"] == "awaiting_approval"
    keys = [c[2] for c in env["gate"].calls if c[0] == promo.PUBLISH_ACTION]
    assert len(set(keys)) == 2  # 옛 승인을 다시 쓰지 않는다


def test_removing_while_waiting_cancels_and_a_late_approval_does_nothing(
    env: dict[str, Any],
) -> None:
    _publish("spd_1")
    asyncio.run(promo.remove(_meta(), "spd_1", "준하"))
    env["gate"].status[promo.PUBLISH_ACTION] = "executed"
    _check()
    assert _feed(env) == [] and promo.snapshot()["items"] == []


def test_a_rejected_video_is_not_asked_again_and_frees_its_place(env: dict[str, Any]) -> None:
    env["gate"].status[promo.PUBLISH_ACTION] = "rejected"
    assert _publish("spd_1")["status"] == "rejected"
    calls = len(env["gate"].calls)
    assert _publish("spd_1")["status"] == "rejected"
    assert len(env["gate"].calls) == calls
    assert promo.snapshot()["used"] == 0
    env["gate"].status[promo.PUBLISH_ACTION] = "held"
    assert _publish("spd_1", title="새 제목")["status"] == "awaiting_approval"


def test_a_file_changed_after_the_request_is_not_published(env: dict[str, Any]) -> None:
    _publish("spd_1")
    env["labeled"]("spd_1", b"different")
    env["gate"].status[promo.PUBLISH_ACTION] = "executed"
    _check()
    (item,) = promo.snapshot()["items"]
    assert item["status"] == "failed" and item["error"] == "source_changed"
    assert _feed(env) == []


def test_a_folder_write_failure_is_retried_without_a_new_approval(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = promo._copy

    def broken(source: Path, target: Path) -> None:
        raise PermissionError("no")

    monkeypatch.setattr(promo, "_copy", broken)
    env["gate"].status[promo.PUBLISH_ACTION] = "executed"
    state = _publish("spd_1")
    assert state["status"] == "approved" and state["error"] == "feed_write_failed"
    monkeypatch.setattr(promo, "_copy", real)
    _check()
    assert len(_feed(env)) == 1 and len(env["gate"].calls) == 1


def test_a_gate_error_does_not_keep_a_place(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_args: Any) -> ExecuteDecision:
        raise RuntimeError("down")

    monkeypatch.setattr(promo, "execute", boom)
    with pytest.raises(RuntimeError):
        _publish("spd_1")
    assert promo.snapshot()["used"] == 0


def test_the_count_is_shared_across_companies_but_items_are_not(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = str(tmp_path / "creative.db")
    base = cr.CreativeRecords(db)
    other = cr.CreativeRecords(db, company_id="nubi")
    other.promo_put("spd_other", {"status": "published", "files": {}})
    cr.set_store(base)
    snap = promo.snapshot()
    assert snap["used"] == 1 and snap["items"] == []  # 자리는 세지만 남의 항목은 보이지 않는다
    with pytest.raises(promo.PromoError, match="not_found"):
        asyncio.run(promo.remove(_meta(), "spd_other", "준하"))
    base.promo_put("spd_other", {"status": "removed"})  # 남의 항목은 바뀌지 않는다
    assert base.promo_get("spd_other") == ("nubi", {"status": "published", "files": {}})


def test_skills_are_registered() -> None:
    for skill in ("promo_site_status", "promo_site_publish", "promo_site_remove"):
        assert skill in handlers.HANDLERS
