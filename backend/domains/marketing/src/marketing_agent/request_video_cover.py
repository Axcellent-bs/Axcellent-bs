"""의뢰 영상 썸네일 후보 3장 → 사람이 고르기 (2026-11-23, 마케팅, 크레딧 0).

- 만들기(`make_covers`): 의뢰 영상 초안이 `draft_ready`가 되면
  (`request_video.attach_label_after_generate`) AI 라벨을 입힌 `labeled.mp4`에서
  ffmpeg로 3장을 뽑는다. 외부 생성(Higgsfield 등)은 쓰지 않는다.
  - 시점(`pick_times`): 기획 장면 시작 시각이 있으면 장면 1·가운데·마지막 장면 시작에서 0.5초 뒤
    (기획 길이와 실제 길이가 다르면 비율로 맞춘다). 없으면 길이의 20%·50%·80%
  - 검은 화면·거의 한 색인 프레임(`is_blank`, ffmpeg signalstats)은 0.5초 뒤로 넘긴다(최대 3번)
  - 라벨 없는 영상(`request_video._labeled_prepared`를 통과하지 못한 파일)이면 후보를 만들지 않는다
  - 뽑은 그림은 라벨 오버레이가 이미 그려진 프레임이고, 영상과 같은 기계판독 표시
    (XMP `ai_generated=true`·모델·시각·라벨 문구, `sns_label.inject_xmp`)를 붙인다.
    `covers/meta.json`에 장마다 라벨 기록을 남긴다
  - 자리는 그 영상 폴더(`<미디어 폴더>/<회사>/<의뢰 context>/med_*/covers/`, 회사 폴더 안)이고
    초안에 `cover_candidates[{media_id, at_s, asset_ref, sha256, ...}]`를 적는다
- 고르기(`select_cover`): 후보 안의 값만 받는다. 고르면 `cover_media_id`와 `cover_rev`(+1)를 적는다.
  승인 대기·승인 뒤에 바꾸면 그 승인을 무효로 한다(Instagram 대기 건은 `cover_changed`로 닫고,
  유튜브는 초안 상태로 되돌린다). 이미 올라간(게시 중·게시됨·올리는 중·올림) 영상은 바꾸지 않는다
- 옛 영상(`backfill`): 기능이 생기기 전에 만든 의뢰 영상은 목록을 읽을 때 후보가 없으면 한 번
  만든다(읽을 때마다 최대 2건). ffmpeg·ffprobe가 없거나 실패하면 사유(`covers_error`)를 목록에
  붙여 화면에 보이고, 10분 뒤 다시 시도한다
- 기본값: 고르지 않으면 첫 후보. 승인 요청 전에 `ensure_cover`가 초안에 적는다
- 승인 해시(`cover_token`): `media_id:파일 sha256:cover_rev`. Instagram(`publish_sns`)·유튜브
  (`publish_youtube`) 승인 해시에 들어가고, 게시 직전에 지금 값으로 다시 만들어 맞춘다
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marketing_agent import request_video as _rv
from marketing_agent import sns_label, sns_media
from marketing_agent.media.config import ffmpeg_bin
from marketing_agent.sns import SnsPostDraft, SnsStore

COVER_DIR = "covers"
COVER_ID = re.compile(r"^cov_[0-9a-f]{12}$")
COUNT = 3
AFTER_SCENE_S = 0.5  # 장면 시작에서 이만큼 뒤(전환 직후 흐린 프레임을 피한다)
STEP_S = 0.5  # 빈 프레임이면 이만큼 뒤로
MAX_SKIPS = 3
FRACTIONS = (0.2, 0.5, 0.8)
END_MARGIN_S = 0.1  # 마지막 프레임 바로 앞까지만
FFMPEG_TIMEOUT_S = 60
JPEG_QSCALE = "3"
# signalstats(8비트) 기준: 밝은 쪽 10%도 이보다 어두우면 검은 화면, 밝기·색 폭이 이보다 좁으면 한 색
BLACK_YHIGH = 40.0
FLAT_RANGE = 12.0
NO_COVER = "none"  # 후보 없는 초안의 cover 토큰 자리(해시에 넣지 않는다)

Grab = Callable[[Path, float, Path], None]  # (영상, 시각, 저장할 jpg)
Stats = Callable[[Path, float], dict[str, float] | None]  # (영상, 시각) → signalstats 값
Duration = Callable[[Path], float | None]


class CoverError(Exception):
    """고르기 거절. 코드는 닫힌 집합: not_found·cover_not_candidate·cover_locked·storage_error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- 시점·빈 프레임
def pick_times(
    duration_s: float | None,
    starts: Sequence[float] = (),
    plan_duration_s: float | None = None,
) -> list[float]:
    """후보 시점(초, 오름차순, 최대 3개).

    장면이 있으면 장면 1·가운데·마지막 + 0.5초, 없으면 20·50·80%.

    장면이 적어 겹치면 20·50·80% 중 겹치지 않는 시점으로 채운다. 길이를 모르면 빈 목록."""
    if duration_s is None or duration_s <= 0:
        return []
    end = max(0.0, duration_s - END_MARGIN_S)

    def clamp(t: float) -> float:
        return round(min(max(t, 0.0), end), 2)

    picks: list[float] = []
    ordered = sorted(s for s in starts if s >= 0)
    if ordered:
        scale = duration_s / plan_duration_s if plan_duration_s and plan_duration_s > 0 else 1.0
        for i in (0, len(ordered) // 2, len(ordered) - 1):
            picks.append(clamp(ordered[i] * scale + AFTER_SCENE_S))
    else:
        picks = [clamp(duration_s * f) for f in FRACTIONS]
    out: list[float] = []
    for t in picks:
        if all(abs(t - u) >= STEP_S for u in out):
            out.append(t)
    for f in FRACTIONS:  # 장면이 1~2개라 겹쳤으면 채운다
        if len(out) >= COUNT:
            break
        t = clamp(duration_s * f)
        if all(abs(t - u) >= STEP_S for u in out):
            out.append(t)
    return sorted(out)[:COUNT]


def is_blank(stats: dict[str, float] | None) -> bool:
    """검은 화면이거나 거의 한 색인 프레임. 값을 읽지 못하면 빈 프레임으로 보지 않는다."""
    if not stats:
        return False
    try:
        if stats["YHIGH"] <= BLACK_YHIGH:
            return True
        return all(stats[f"{c}HIGH"] - stats[f"{c}LOW"] < FLAT_RANGE for c in ("Y", "U", "V"))
    except KeyError:
        return False


def frame_times(at_s: float, duration_s: float) -> list[float]:
    """한 후보에서 시도할 시각: 처음 + 0.5초씩 최대 3번(영상 끝을 넘지 않는다)."""
    end = max(0.0, duration_s - END_MARGIN_S)
    out = [at_s]
    for k in range(1, MAX_SKIPS + 1):
        t = round(at_s + STEP_S * k, 2)
        if t > end:
            break
        out.append(t)
    return out


# ---------------------------------------------------------------- ffmpeg
def ffmpeg_grab(video: Path, at_s: float, out: Path) -> None:
    args = [
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.2f}",
        "-i", str(video), "-frames:v", "1", "-q:v", JPEG_QSCALE, str(out),
    ]  # fmt: skip
    done = subprocess.run(  # noqa: S603 - 인자 목록으로만 부른다
        args, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False
    )
    if done.returncode != 0 or not out.is_file():
        raise OSError("frame_grab_failed")  # 원문에 경로가 섞일 수 있어 남기지 않는다


_STAT = re.compile(r"lavfi\.signalstats\.([A-Z]+)=([0-9.]+)")


def ffmpeg_stats(video: Path, at_s: float) -> dict[str, float] | None:
    args = [
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{at_s:.2f}",
        "-i", str(video), "-frames:v", "1",
        "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-",
    ]  # fmt: skip
    try:
        done = subprocess.run(  # noqa: S603 - 인자 목록으로만 부른다
            args, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = {k: float(v) for k, v in _STAT.findall(done.stdout or "")}
    return found or None


def probe_duration(video: Path) -> float | None:
    fmt = (sns_media.run_ffprobe(video) or {}).get("format") or {}
    try:
        value = float(fmt.get("duration"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


# ---------------------------------------------------------------- 만들기
def _label_record(labeled: Path) -> dict[str, Any] | None:
    try:
        meta = json.loads((labeled.parent / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    label = meta.get("ai_label") if isinstance(meta, dict) else None
    return label if isinstance(label, dict) else None


def _mark_frame(path: Path, label: dict[str, Any]) -> tuple[dict[str, str], str]:
    """영상과 같은 기계판독 표시(XMP)를 붙이고 읽어 확인한다. 실패는 LabelError."""
    mark = {
        "ai_generated": "true",
        "ai_generator_model": str(label.get("ai_generator_model") or ""),
        "ai_generated_at": str(label.get("ai_generated_at") or ""),
    }
    text = str(label.get("text") or "")
    sns_label.inject_xmp(path, mark, text)
    if 'catbooks:ai_generated="true"' not in (sns_media.jpeg_xmp(path) or ""):
        raise sns_label.LabelError("metadata_not_written")
    return mark, text


def make_covers(
    store: SnsStore,
    post_draft_id: str,
    *,
    grab: Grab = ffmpeg_grab,
    stats: Stats = ffmpeg_stats,
    duration: Duration = probe_duration,
) -> dict[str, Any]:
    """라벨 입힌 영상에서 후보 3장을 뽑아 초안에 적는다.

    결과는 `{"covers": n}` 또는 `{"covers_skipped": 코드}`.

    이미 후보가 있으면 다시 만들지 않는다(같은 영상이면 같은 후보)."""
    draft = store.get(post_draft_id)
    root = sns_media.media_root()
    if draft is None or root is None:
        return {"covers_skipped": "draft_missing"}
    prepared = _rv._labeled_prepared(root, draft)  # noqa: SLF001 - 목록·게시와 같은 라벨 확인
    if prepared is None:
        return {"covers_skipped": "label_missing"}  # 라벨 없는 영상이면 후보를 만들지 않는다
    labeled = (root / prepared["asset_ref"]).resolve()
    if draft.cover_candidates and all(_candidate_ok(root, c) for c in draft.cover_candidates):
        return {"covers": len(draft.cover_candidates), "covers_reused": True}
    label = _label_record(labeled)
    if label is None:
        return {"covers_skipped": "label_missing"}
    length = duration(labeled)
    times = pick_times(length, draft.scene_starts_s, draft.plan_duration_s)
    if not times or length is None:
        return {"covers_skipped": "duration_unknown"}
    folder = labeled.parent / COVER_DIR
    folder.mkdir(exist_ok=True)
    made: list[dict[str, Any]] = []
    records: dict[str, Any] = {}
    for n, first in enumerate(times, start=1):
        tries = frame_times(first, length)
        at = tries[-1]
        for t in tries:  # 빈 프레임이면 0.5초 뒤로(최대 3번), 끝까지 비면 마지막 시도를 쓴다
            if not is_blank(stats(labeled, t)):
                at = t
                break
        tmp = folder / f".cover_{n}.jpg"
        try:
            grab(labeled, at, tmp)
            mark, text = _mark_frame(tmp, label)
        except (OSError, sns_label.LabelError):
            tmp.unlink(missing_ok=True)
            continue
        sha = sns_media.sha256_file(tmp)
        media_id = f"cov_{sha[:12]}"
        final = folder / f"{media_id}.jpg"
        os.replace(tmp, final)
        if any(c["media_id"] == media_id for c in made):
            continue  # 같은 그림(정지 화면)은 한 번만
        made.append(
            {
                "media_id": media_id,
                "at_s": at,
                "asset_ref": sns_media.asset_ref_for(final),
                "sha256": sha,
                "is_ai_generated": True,
                "ai_label_text": text,
            }
        )
        records[final.name] = {
            "label_overlay": True,  # 라벨을 입힌 영상의 프레임이다
            "machine_mark": True,
            "source_file": labeled.name,
            "source_sha256": prepared["asset_sha256"],
            "at_s": at,
            "labeled_sha256": sha,
            "ai_generated": True,
            "ai_generator_model": mark["ai_generator_model"],
            "ai_generated_at": mark["ai_generated_at"],
            "text": text,
        }
    if not made:
        return {"covers_skipped": "frame_grab_failed"}
    (folder / "meta.json").write_text(
        json.dumps(
            {"ai_label": records, "made_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    latest = store.get(post_draft_id) or draft
    store.update_meta(
        [latest.model_copy(update={"cover_candidates": made, "cover_media_id": None})]
    )
    return {"covers": len(made)}


# ---------------------------------------------------------------- 지금 cover
def _candidate_ok(root: Path, c: dict[str, Any]) -> bool:
    path = (root / str(c.get("asset_ref") or "")).resolve()
    return (
        COVER_ID.match(str(c.get("media_id") or "")) is not None
        and path.is_relative_to(root)
        and path.is_file()
        and path.name == f"{c['media_id']}.jpg"
        and sns_media.sha256_file(path) == c.get("sha256")
    )


def chosen_id(draft: SnsPostDraft) -> str | None:
    """고른 후보, 없으면 첫 후보. 후보가 없으면 None."""
    ids = [str(c.get("media_id")) for c in draft.cover_candidates]
    if not ids:
        return None
    return draft.cover_media_id if draft.cover_media_id in ids else ids[0]


def current_cover(draft: SnsPostDraft) -> dict[str, Any] | None:
    """지금 cover: {media_id, at_s, asset_ref, path, sha256(지금 파일), ok, token}.

    후보가 없으면 None."""
    media_id = chosen_id(draft)
    if media_id is None:
        return None
    c = next(x for x in draft.cover_candidates if x.get("media_id") == media_id)
    root = sns_media.media_root()
    path = (root / str(c.get("asset_ref") or "")).resolve() if root is not None else None
    ok = root is not None and path is not None and _candidate_ok(root, c)
    sha = sns_media.sha256_file(path) if ok and path is not None else "missing"
    return {
        "media_id": media_id,
        "at_s": c.get("at_s"),
        "asset_ref": c.get("asset_ref"),
        "path": path,
        "sha256": sha,
        "ok": ok,
        "token": f"{media_id}:{sha}:{draft.cover_rev}",
    }


def cover_token(draft: SnsPostDraft | None) -> str:
    """승인 해시에 넣는 값. 후보 없는 초안은 `none`(해시에 넣지 않는 자리)."""
    cover = current_cover(draft) if draft is not None else None
    return cover["token"] if cover is not None else NO_COVER


def ensure_cover(store: SnsStore, draft: SnsPostDraft) -> SnsPostDraft:
    """승인 요청 전: 고르지 않았으면 첫 후보를 초안에 적는다(`cover_rev`는 그대로)."""
    media_id = chosen_id(draft)
    if media_id is None or draft.cover_media_id == media_id:
        return draft
    fixed = draft.model_copy(update={"cover_media_id": media_id})
    store.update_meta([fixed])
    return fixed


def public(draft: SnsPostDraft) -> dict[str, Any]:
    """목록 항목에 붙이는 값: covers[{media_id, at_s}]·cover_media_id(고른 것, 없으면 첫 후보)."""
    return {
        "covers": [
            {"media_id": c.get("media_id"), "at_s": c.get("at_s")} for c in draft.cover_candidates
        ],
        "cover_media_id": chosen_id(draft),
        "cover_picked": draft.cover_media_id is not None,
    }


# ---------------------------------------------------------------- 고르기
def _request_draft(store: SnsStore, post_draft_id: str) -> SnsPostDraft:
    draft = store.get(post_draft_id)
    if (
        draft is None
        or draft.media_kind != "reel"
        or draft.source_kind != "brief"
        or not draft.context_id.startswith(_rv.REQUEST_CONTEXT_PREFIX)
    ):
        raise CoverError("not_found")  # 다른 회사·다른 종류 초안은 없는 것으로 본다
    return draft


def select_cover(store: SnsStore, post_draft_id: str, media_id: str) -> dict[str, Any]:
    """사람이 고른 후보를 적는다. 승인 대기·승인 뒤면 그 승인을 무효로 한다."""
    from marketing_agent import instagram_post as _ig
    from marketing_agent import request_video_youtube as _rvy
    from marketing_agent.sns_publish import pending as pend
    from marketing_agent.sns_publish import publisher

    draft = _request_draft(store, post_draft_id)
    if media_id not in {str(c.get("media_id")) for c in draft.cover_candidates}:
        raise CoverError("cover_not_candidate")
    if draft.cover_media_id == media_id:
        return {"post_draft_id": post_draft_id, "cover_media_id": media_id, "changed": False,
                "cover_rev": draft.cover_rev, "approvals_reset": []}  # fmt: skip
    try:
        rows = publisher.store().with_draft(post_draft_id)
    except pend.PendingStorageError as error:
        raise CoverError("storage_error") from error
    youtube = _rvy.get(post_draft_id)
    reel = _ig.Target("request", post_draft_id)
    if (
        any(r.status in (pend.PUBLISHING, pend.PUBLISHED) for r in rows)
        or youtube.get("status") in ("uploading", "done")
        or _ig.cover_locked(reel)
    ):
        raise CoverError("cover_locked")  # 이미 올라간 영상의 썸네일은 여기서 바꾸지 않는다
    store.update_meta(
        [draft.model_copy(update={"cover_media_id": media_id, "cover_rev": draft.cover_rev + 1})]
    )
    reset: list[str] = []
    for row in rows:  # 승인 대기·승인된(게시 전) Instagram 건은 무효(새 승인을 받아야 한다)
        if row.status in (pend.HELD, pend.APPROVED) and publisher.store().transition(
            row.idempotency_key, row.status, pend.FAILED, failure_code="cover_changed"
        ):
            reset.append("instagram")
    if _rvy.reset_for_cover(post_draft_id):
        reset.append("youtube")
    if _ig.reset_for_cover(reel):  # 인스타 릴스 올리기(표지)도 다시 승인받아야 한다
        reset.append("instagram")
    return {
        "post_draft_id": post_draft_id,
        "cover_media_id": media_id,
        "changed": True,
        "cover_rev": draft.cover_rev + 1,
        "approvals_reset": sorted(set(reset)),
    }


def approval_waiting(post_draft_id: str, youtube: dict[str, Any] | None) -> bool:
    """승인 대기·승인됨(게시 전)인 게시·유튜브 요청이 있나.

    화면은 참이면 바꾸기 전에 확인을 묻는다."""
    from marketing_agent import instagram_post as _ig
    from marketing_agent.sns_publish import pending as pend
    from marketing_agent.sns_publish import publisher

    if (youtube or {}).get("status") == "awaiting_approval":
        return True
    if _ig.awaiting(_ig.Target("request", post_draft_id)):
        return True
    try:
        rows = publisher.store().with_draft(post_draft_id)
    except pend.PendingStorageError:
        return True  # 모르면 묻는다
    return any(r.status in (pend.HELD, pend.APPROVED) for r in rows)


def cover_file(store: SnsStore, post_draft_id: str, media_id: str) -> dict[str, str] | None:
    """미리보기용 후보 파일 참조(asset_ref·sha256). 후보가 아니거나 파일이 기록과 다르면 None."""
    try:
        draft = _request_draft(store, post_draft_id)
    except CoverError:
        return None
    root = sns_media.media_root()
    for c in draft.cover_candidates:
        if c.get("media_id") == media_id and root is not None and _candidate_ok(root, c):
            return {"asset_ref": str(c["asset_ref"]), "asset_sha256": str(c["sha256"])}
    return None


# ---------------------------------------------------------------- 옛 영상 채우기
BACKFILL_PER_READ = 2  # 목록을 한 번 읽을 때 새로 만드는 영상 수(읽기가 너무 느려지지 않게)
RETRY_AFTER_S = 600.0  # 실패한 영상은 10분 뒤 다시 시도(ffmpeg를 깐 뒤 재시작 없이 풀리게)
_tried: dict[str, tuple[float, str]] = {}  # post_draft_id → (시도 시각, 실패 사유 또는 "")


def tools_missing() -> str | None:
    """후보를 만들 도구가 서버에 없으면 그 사유(`ffmpeg_missing`·`ffprobe_missing`)."""
    if shutil.which(ffmpeg_bin()) is None:
        return "ffmpeg_missing"
    if shutil.which("ffprobe") is None:
        return "ffprobe_missing"
    return None


def backfill(
    store: SnsStore,
    post_draft_ids: Sequence[str],
    *,
    maker: Callable[[SnsStore, str], dict[str, Any]] | None = None,
    preflight: Callable[[], str | None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, dict[str, str]]:
    """후보 없는 의뢰 영상(기능이 생기기 전에 만든 영상)에 후보를 한 번 만든다(ffmpeg, 크레딧 0).

    결과는 (새로 만든 영상 수, {post_draft_id: 실패 사유}). 실패는 조용히 넘기지 않고
    화면에 보이도록 사유를 돌려준다. 같은 영상은 `RETRY_AFTER_S` 안에 다시 시도하지 않는다."""
    maker = maker or make_covers
    preflight = preflight or tools_missing
    now = clock()
    errors: dict[str, str] = {}
    made = ran = 0
    for pid in post_draft_ids:
        tried = _tried.get(pid)
        if tried is not None and now - tried[0] < RETRY_AFTER_S:
            if tried[1]:
                errors[pid] = tried[1]
            continue
        if ran >= BACKFILL_PER_READ:
            continue  # 다음에 읽을 때 만든다
        missing = preflight()
        if missing is not None:
            _tried[pid] = (now, missing)
            errors[pid] = missing
            continue
        ran += 1
        try:
            out = maker(store, pid)
        except Exception as error:  # noqa: BLE001 - 사유만 남기고 목록 읽기는 막지 않는다
            out = {"covers_skipped": type(error).__name__}
        reason = str(out.get("covers_skipped") or "")
        _tried[pid] = (now, reason)
        if reason:
            errors[pid] = reason
        elif out.get("covers") and not out.get("covers_reused"):
            made += 1
    return made, errors


__all__ = [
    "BACKFILL_PER_READ",
    "CoverError",
    "backfill",
    "cover_file",
    "cover_token",
    "current_cover",
    "ensure_cover",
    "frame_times",
    "is_blank",
    "make_covers",
    "pick_times",
    "public",
    "select_cover",
]
