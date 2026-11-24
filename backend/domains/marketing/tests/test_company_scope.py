"""마케팅 저장소의 회사 칸·회사 폴더 (여러 회사 설계 1-6, 2026-11-20).

- DB: 표마다 `tenant_id` 칸. 옛 DB는 열 때 붙이고 기존 줄은 `catbooks`(멱등), 자기 회사 줄만 읽는다
- 미디어 폴더·설정 파일(`cost_limits.json`·`video_providers.json`): 회사 폴더로(#1537),
  `.moved`를 남기고, `AX_COMPANY_FOLDERS=off`면 옛 자리로 되살린다
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ax_agent_template import companies, company_files
from marketing_agent import briefs, company_scope, sns
from marketing_agent.campaign import StartedCampaign
from marketing_agent.campaign_auto import CampaignTracker
from marketing_agent.chat_store import ChatStore
from marketing_agent.creative_records import CreativeRecords
from marketing_agent.media import quota_store, video_store
from marketing_agent.media.config import MediaSettings
from marketing_agent.media.ledger import MediaLedger
from marketing_agent.media.usage import UsageAmount
from marketing_agent.sns_publish import pending

OTHER = "other-co"
NOW = datetime(2026, 11, 20, tzinfo=UTC)


def _tenants(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute(f"SELECT tenant_id FROM {table}")]  # noqa: S608
    finally:
        conn.close()


def _strip_tenant(path: Path, *tables: str) -> None:
    """옛 DB를 흉내 낸다: 회사 칸이 없던 때의 표."""
    conn = sqlite3.connect(path)
    try:
        for table in tables:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN tenant_id")  # noqa: S608
        conn.commit()
    finally:
        conn.close()


def _brief_input(pid: str = "PROP-1") -> briefs.CampaignBriefInput:
    return briefs.CampaignBriefInput.model_validate(
        {
            "schema_version": "1",
            "proposal_id": pid,
            "insight_id": "INS-7",
            "title": "독서의 달 기획전",
            "idea": "11월 독서 키워드 상품을 묶어 배너로 알린다",
            "why": "독서 검색이 작년 11월 평균의 1.6배였다",
            "facts": [
                {"label": "독서", "value": "1.6배", "source": "trend_insight_brief", "link": None}
            ],
            "target": {"label": "증분", "value": "+10%", "measure": "campaign_effect"},
            "products": ["prd_1"],
            "format": "banner",
            "approved_by": "예진",
            "note": None,
            "body_md": "# 기획안",
        }
    )


def _draft(pid: str = "spd_1") -> sns.SnsPostDraft:
    caption = "캡션"
    return sns.SnsPostDraft(
        post_draft_id=pid,
        source_kind="notice",
        source_ref="ntc_1",
        notice_kind="incident_opened",
        platform="instagram",
        media_kind="image",
        caption=caption,
        hashtags=list(sns.brand_hashtags()),
        caption_sha256=sns._sha(caption),  # noqa: SLF001
        required_text=[caption],
        context_id="ctx_x",
    )


def _started(cid: str = "cmp_1") -> StartedCampaign:
    return StartedCampaign(
        campaign_id=cid,
        campaign_name="가을 세일",
        channel="sns",
        budget_krw=300_000,
        content_category="아재개그",
        format="shorts",
        production_cost_krw=40_000,
        ends_on="2026-11-18",
        started_on="2026-11-16",
    )


def _post(key: str, tenant: str = "catbooks") -> pending.PendingPost:
    return pending.PendingPost(
        idempotency_key=key,
        tenant_id=tenant,
        context_id="c",
        post_draft_id="spd_" + key,
        content_sha="x" * 64,
        execution_id=None,
        review_id=None,
        status=pending.HELD,
        params={},
    )


# ------------------------------------------------------------------ DB 회사 칸
def test_scope_id_aliases_and_fallback() -> None:
    assert company_scope.scope_id(None) == "catbooks"
    assert company_scope.scope_id("shop-demo") == "catbooks"
    assert company_scope.scope_id("NOT valid") == "catbooks"
    assert company_scope.scope_id(OTHER) == OTHER


def test_old_db_gets_the_column_and_existing_rows_become_catbooks(tmp_path: Path) -> None:
    db = tmp_path / "b.db"
    store = briefs.BriefStore(str(db))
    store.add(_brief_input(), NOW)
    store.close()
    _strip_tenant(db, "campaign_briefs", "brief_sns_handoffs", "brief_auto_handoff")
    again = briefs.BriefStore(str(db))  # 열 때 붙는다
    assert _tenants(db, "campaign_briefs") == ["catbooks"]
    assert again.existing("PROP-1") is not None  # 옛 줄이 그대로 보인다
    again.close()
    briefs.BriefStore(str(db)).close()  # 멱등: 한 번 더 열어도 오류 없음
    assert _tenants(db, "campaign_briefs") == ["catbooks"]


def test_brief_store_only_sees_its_own_company(tmp_path: Path) -> None:
    db = str(tmp_path / "b.db")
    cat, other = briefs.BriefStore(db), briefs.BriefStore(db, company_id=OTHER)
    cat.add(_brief_input("PROP-1"), NOW)
    other.add(_brief_input("PROP-2"), NOW)
    assert [s.proposal_id for s in cat.list(10)[0]] == ["PROP-1"]
    assert [s.proposal_id for s in other.list(10)[0]] == ["PROP-2"]
    assert cat.existing("PROP-2") is None
    assert cat.get(None, "cmp_PROP-2") is None
    assert cat.pending_handoff_ids(10) == ["PROP-1"]
    assert other.pending_handoff_ids(10) == ["PROP-2"]
    assert cat.auto_attempts("PROP-1")[0] == 0
    cat.record_auto_attempt("PROP-1", NOW, "x")
    assert cat.auto_attempts("PROP-1")[0] == 1
    assert other.auto_attempts("PROP-1")[0] == 0
    assert _tenants(Path(db), "brief_auto_handoff") == ["catbooks"]


def test_brief_alias_company_writes_catbooks(tmp_path: Path) -> None:
    db = tmp_path / "b.db"
    store = briefs.BriefStore(str(db), company_id="shop-demo")
    store.add(_brief_input(), NOW)
    assert _tenants(db, "campaign_briefs") == ["catbooks"]


def test_campaign_tracker_scopes_and_migrates(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    cat = CampaignTracker(str(db))
    cat.track(_started("cmp_1"), grace_h=1)
    cat.add_log("cmp_1", "ok", {"a": 1}, NOW)
    cat.close()
    _strip_tenant(db, "campaign_runs", "campaign_auto_log")
    cat = CampaignTracker(str(db))
    assert _tenants(db, "campaign_runs") == ["catbooks"]
    assert _tenants(db, "campaign_auto_log") == ["catbooks"]
    other = CampaignTracker(str(db), company_id=OTHER)
    assert cat.status("cmp_1") == "active" and other.status("cmp_1") is None
    assert len(cat.due(datetime(2030, 1, 1, tzinfo=UTC))) == 1
    assert other.due(datetime(2030, 1, 1, tzinfo=UTC)) == []
    assert len(cat.log()) == 1 and other.log() == []
    other.mark_canceled("cmp_1")  # 남의 회사 줄은 못 고친다
    assert cat.status("cmp_1") == "active"


def test_chat_store_scopes_and_migrates(tmp_path: Path) -> None:
    db = tmp_path / "chat.db"
    cat = ChatStore(str(db))
    th = cat.create("질문", "jaehun")
    cat.shutdown()
    _strip_tenant(db, "marketing_chat_threads")
    cat = ChatStore(str(db))
    assert _tenants(db, "marketing_chat_threads") == ["catbooks"]
    assert cat.get(th["id"]) is not None
    other = ChatStore(str(db), company_id=OTHER)
    assert other.get(th["id"]) is None and other.list() == []
    assert other.hide(th["id"], "x") is False
    assert cat.hide(th["id"], "x") is True
    mine = other.create("남의 질문", None)
    assert _tenants(db, "marketing_chat_threads") == ["catbooks", OTHER]
    assert cat.get(mine["id"]) is None


def test_creative_records_scope_and_migrate(tmp_path: Path) -> None:
    db = tmp_path / "cr.db"
    cat = CreativeRecords(str(db))
    rid = cat.add_sheet({"summary_ko": "시트"}, None, None)
    _strip_tenant(db, "marketing_creative_records")
    cat = CreativeRecords(str(db))
    assert _tenants(db, "marketing_creative_records") == ["catbooks"]
    other = CreativeRecords(str(db), company_id=OTHER)
    assert cat.get(rid) is not None and other.get(rid) is None
    assert len(cat.list()) == 1 and other.list() == []
    assert other.attach_storyboard(rid, "s", 1, {}) is False
    other.set_render(rid, {"video": {"status": "done"}})
    assert cat.get_render(rid) == {}


def test_media_ledger_scopes_usage_and_migrates(tmp_path: Path) -> None:
    db = tmp_path / "led.db"
    cat = MediaLedger(str(db))

    def reserve(ledger: MediaLedger, key: str) -> str:
        return ledger.reserve(
            key=key,
            month="2026-11",
            media_kind="image",
            provider="p",
            model_id="m",
            usage=UsageAmount("images", 2),
            monthly_cap=None,
            now=NOW,
        ).outcome

    assert reserve(cat, "k1") == "reserved"
    cat.close()
    _strip_tenant(db, "media_ledger")
    cat = MediaLedger(str(db))
    assert _tenants(db, "media_ledger") == ["catbooks"]
    other = MediaLedger(str(db), company_id=OTHER)
    assert cat.month_usage("2026-11", "image") == 2
    assert other.month_usage("2026-11", "image") == 0  # 월 예산은 회사마다
    assert reserve(other, "k1") == "reserved"  # 같은 키라도 남의 회사의 예약을 이어받지 않는다
    assert _tenants(db, "media_ledger") == ["catbooks", OTHER]


def test_sns_draft_store_scopes_and_migrates(tmp_path: Path) -> None:
    db = tmp_path / "d.db"
    cat = sns.SnsStore(str(db))
    cat.save(_draft())
    cat.close()
    _strip_tenant(db, "sns_drafts")
    cat = sns.SnsStore(str(db))
    assert _tenants(db, "sns_drafts") == ["catbooks"]
    other = sns.SnsStore(str(db), company_id=OTHER)
    assert cat.get("spd_1") is not None and other.get("spd_1") is None
    assert len(cat.list_by_source("notice", None)[0]) == 1
    assert other.list_by_source("notice", None)[0] == []


def test_pending_store_scopes_and_keeps_alias_rows(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    cat = pending.PendingStore(str(db))
    assert cat.create(_post("a", "catbooks"))
    assert cat.create(_post("b", "shop-demo"))  # 옛 줄: 별칭 그대로, 같은 회사로 읽는다
    other = pending.PendingStore(str(db), company_id=OTHER)
    assert other.create(_post("c", OTHER))
    assert {p.idempotency_key for p in cat.with_status(pending.HELD)} == {"a", "b"}
    assert [p.idempotency_key for p in other.with_status(pending.HELD)] == ["c"]
    assert other.get("a") is None and cat.get("c") is None
    assert other.transition("a", pending.HELD, pending.APPROVED) is False  # 남의 줄은 못 바꾼다
    assert cat.transition("a", pending.HELD, pending.APPROVED) is True
    assert cat.count_posts_since(datetime(2000, 1, 1, tzinfo=UTC)) == 0
    row = cat.get("b")
    assert row is not None and row.tenant_id == "shop-demo"


def test_pending_store_adds_column_to_an_old_file(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    pending.PendingStore(str(db)).close()
    _strip_tenant(db, "sns_pending")
    store = pending.PendingStore(str(db))
    assert _tenants(db, "sns_pending") == []
    assert store.create(_post("a")) is True
    assert _tenants(db, "sns_pending") == ["catbooks"]


# ------------------------------------------------------------------ 미디어 폴더·설정 파일
@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".data"
    root.mkdir()
    for env in (
        "MARKETING_MEDIA_DIR",
        quota_store.FILE_ENV,
        video_store.FILE_ENV,
        company_files.FOLDERS_ENV,
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv(companies.COMPANIES_DIR_ENV, str(root / "companies"))
    company_files.reset_for_tests()
    return root


FOLDER = Path("companies") / "catbooks"


def test_media_dir_without_company_folders_is_the_old_temp_dir(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV)
    assert company_scope.media_dir() == Path(tempfile.gettempdir()) / "marketing_media"
    assert MediaSettings.from_env().media_dir == company_scope.media_dir()


def test_media_dir_env_wins(data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = tmp_path / "pinned"
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(pinned))
    (data / "marketing_media").mkdir()
    assert company_scope.media_dir() == pinned
    assert (data / "marketing_media").exists()  # 옮기지 않는다


def test_media_dir_moves_to_company_folder(data: Path) -> None:
    (data / "marketing_media").mkdir()
    (data / "marketing_media" / "a.jpg").write_bytes(b"jpg")
    folder = company_scope.media_dir()
    assert folder == data / FOLDER / "marketing_media"
    assert (folder / "a.jpg").read_bytes() == b"jpg"
    assert (data / "marketing_media.moved").exists() and not (data / "marketing_media").exists()
    assert company_scope.media_dir() == folder  # 두 번째부터는 그대로
    assert MediaSettings.from_env().media_dir == folder


def test_media_dir_new_company_folder_is_created(data: Path) -> None:
    folder = company_scope.media_dir()
    assert folder == data / FOLDER / "marketing_media"
    assert folder.parent.is_dir()  # 회사 폴더는 만들어 둔다


def test_media_dir_off_restores_old_path(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (data / "marketing_media").mkdir()
    (data / "marketing_media" / "a.jpg").write_bytes(b"jpg")
    company_scope.media_dir()  # 옮김
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    old = data / "marketing_media"
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(old))  # run_all.sh가 옛 경로를 정한다
    company_files.reset_for_tests()
    assert company_scope.media_dir() == old
    assert (old / "a.jpg").read_bytes() == b"jpg"  # 회사 폴더 값으로 되살림
    assert (data / FOLDER / "marketing_media" / "a.jpg").exists()  # 회사 폴더 파일은 그대로


@pytest.mark.parametrize(
    ("module", "name"),
    [(quota_store, "cost_limits.json"), (video_store, "video_providers.json")],
)
def test_config_files_move_from_media_dir_with_history(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module, name: str
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(media))
    (media / name).write_text("{}", encoding="utf-8")
    (media / (name + ".history.jsonl")).write_text("{}\n", encoding="utf-8")
    path = module.store_path()
    assert path == data / FOLDER / name
    assert path.read_text(encoding="utf-8") == "{}"
    assert (data / FOLDER / (name + ".history.jsonl")).exists()  # 딸린 이력도 같이
    assert (media / (name + ".moved")).exists() and not (media / name).exists()
    assert module.store_path() == path


def test_config_file_env_path_wins(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pinned = tmp_path / "limits.json"
    monkeypatch.setenv(quota_store.FILE_ENV, str(pinned))
    assert quota_store.store_path() == pinned


def test_config_file_without_folders_is_the_old_place(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(companies.COMPANIES_DIR_ENV)
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path / "m"))
    assert video_store.store_path() == tmp_path / "m" / "video_providers.json"


def test_config_file_off_restores_from_company_folder(
    data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(media))
    (media / "cost_limits.json").write_text('{"v": 1}', encoding="utf-8")
    moved = quota_store.store_path()  # 회사 폴더로 옮김
    moved.write_text('{"v": 2}', encoding="utf-8")  # 옮긴 뒤 화면에서 바꾼 값
    monkeypatch.setenv(company_files.FOLDERS_ENV, "off")
    company_files.reset_for_tests()
    old = quota_store.store_path()
    assert old == media / "cost_limits.json"
    assert old.read_text(encoding="utf-8") == '{"v": 2}'  # 바꾼 값까지 되살아난다
    assert moved.exists()  # 회사 폴더 파일은 지우지 않는다


def test_quota_store_reads_the_company_folder_file(data: Path) -> None:
    assert quota_store.store_path() == data / FOLDER / "cost_limits.json"
    quota_store.store_path().write_text("{}", encoding="utf-8")
    assert quota_store._read() == {}  # noqa: SLF001
