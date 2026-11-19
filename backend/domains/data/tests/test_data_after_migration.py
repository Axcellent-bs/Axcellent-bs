"""새 서버 점검(11/19 15:05 app.axcellent.cloud)에서 나온 데이터 문제.

- 인스타 레이더 묶음 씨앗: 해시태그 + 따옴표 안 구절만 (캡션 일반 구절은 씨앗이 아님)
- 보고서 해시태그: 레이더 저장분 재사용에도 캡션·이름이 남고, 계정 수에 따라 2곳·3곳 기준
- 경쟁사 이름: 레이더 저장소 accounts.name → 없으면 프리셋 display_names
- 보고서 번호 표기 TR-XXXX, 검색광고 "< 10"은 0이 아니라 under_10
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ax_contracts.schemas.data import InstagramRadarPost
from data_agent import instagram_store as st
from data_agent.connectors import naver_searchad as sa
from data_agent.connectors.instagram_graph import OWNER_NAME
from data_agent.skills import caption_noise as cn
from data_agent.skills import instagram as igs
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.trend_report_runner import report_label

NOW = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
TODAY = date(2026, 11, 19)

# 232·새 서버 화면에 나온 의미 없는 묶음 이름
SCREEN_NOISE = ("one of", "취소될 수", "프로필 링크에서", "기간 11월", "모집 인원", "만날 수",
                "more than")  # fmt: skip


def _post(account: str, n: int = 1) -> InstagramRadarPost:
    return InstagramRadarPost(account=account, media_id=f"{account}-{n}", metric="like_count",
                              value=500, account_median=100.0, ratio=5.0)  # fmt: skip


def _caption(i: int) -> str:
    return (f"이벤트 안내 {i}! 프로필 링크에서 신청하세요. 모집 인원 30명, 기간 11월 20일까지."
            " 일정이 취소될 수 있어요. 매장에서 만날 수 있어요. one of the best, more than ever."
            f" 「꾸안꾸 루틴」 #비건뷰티 #11월 #2026 #a #광고")  # fmt: skip


def test_screen_noise_phrases_do_not_seed_radar_groups() -> None:
    posts = [_post(a) for a in ("a", "b", "c")]
    captions = {p.media_id: _caption(n) for n, p in enumerate(posts)}
    groups = igs.group_bursts(posts, captions, 2)
    names = [g[0] for g in groups]
    members = {m for g in groups for m in g[1]}
    for bad in SCREEN_NOISE:
        assert bad not in names and bad not in members, bad
    # 해시태그 묶음은 그대로, 따옴표·「」 안 구절은 씨앗이 될 수 있다
    assert "#비건뷰티" in names or "#비건뷰티" in members
    assert "꾸안꾸 루틴" in names or "꾸안꾸 루틴" in members
    # 숫자·날짜만 있는 태그, 1글자 태그, 표기용 태그는 없음
    for bad in ("#11월", "#2026", "#a", "#광고"):
        assert bad not in names and bad not in members


def test_only_plain_caption_phrases_means_no_group() -> None:
    posts = [_post("a"), _post("b")]
    captions = {p.media_id: "프로필 링크에서 확인하세요 모집 인원 많아요" for p in posts}
    assert igs.group_bursts(posts, captions, 2) == []  # 억지로 채우지 않음


def test_caption_units_rules() -> None:
    units = cn.caption_units("\"아침 루틴\" 그리고 don't stop it's fine #리필 #11월19일 #가")
    assert set(units) == {"phrase:아침 루틴", "hashtag:리필"}
    assert cn.weak_tag("hashtag:2026") and cn.weak_tag("hashtag:11월") and cn.weak_tag("hashtag:가")
    assert not cn.weak_tag("hashtag:11월룩") and not cn.weak_tag("phrase:11월")


# ---------------------------------------------------------------- 3. 보고서 해시태그


def _items(account: str, tags: str, name: str | None = None) -> list[dict[str, Any]]:
    out = []
    for i in range(3):
        m = {"id": f"{account}-{i}", "media_product_type": "FEED", "media_type": "IMAGE",
             "timestamp": (NOW - timedelta(days=1 + i)).isoformat(), "like_count": 100,
             "comments_count": 1, "caption": f"오늘의 기록 {tags}"}  # fmt: skip
        if name:
            m[OWNER_NAME] = name
        out.append(m)
    return out


def test_radar_reuse_keeps_captions_and_names(tmp_path: Path) -> None:
    path = tmp_path / "ig_radar.db"
    store = st.RadarStore(path)
    store.save(TODAY, "toun28_official", _items("toun28_official", "#클린뷰티", "톤28"))
    store.save(TODAY, "sidmool", _items("sidmool", "#클린뷰티"))  # 이름 없음
    store.complete(TODAY, NOW, 2)
    store.close()
    day, media, _ = st.radar_latest(path)
    assert day == TODAY
    assert all("#클린뷰티" in m["caption"] for ms in media.values() for m in ms)  # 캡션 그대로
    assert media["toun28_official"][0][OWNER_NAME] == "톤28"
    assert OWNER_NAME not in media["sidmool"][0]


def test_old_radar_db_gets_the_name_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE accounts (day TEXT NOT NULL, account TEXT NOT NULL,"
                 " status TEXT NOT NULL, reason TEXT, PRIMARY KEY (day, account))")  # fmt: skip
    conn.execute("INSERT INTO accounts VALUES ('2026-11-18', 'a', 'ok', NULL)")
    conn.commit()
    conn.close()
    st.RadarStore(path).close()
    st.RadarStore(path).close()  # 두 번째 열기도 안전
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(accounts)")}
    assert "name" in cols


def test_tag_threshold_follows_account_count() -> None:
    """의뢰 34곳 중 매거진 7곳 제외 = 27곳(< 30)이면 2곳이 같이 써도 남는다."""
    preset = tb.load_preset("eco_beauty")
    assert preset is not None and len(preset.accounts) == 34 and len(preset.media_accounts) == 7
    media = {a: _items(a, "#평범한기록") for a in preset.accounts}
    for a in ("toun28_official", "sidmool"):  # 브랜드 2곳만 같이 쓴 태그
        media[a] = _items(a, "#리필스테이션")
    for a in preset.media_accounts:  # 매거진만 같이 쓴 태그는 세지 않는다
        media[a] = _items(a, "#매거진협찬")
    section, _ = tr.hashtag_section(media, [], NOW, None, preset.media_accounts)
    tags = {r.tag: r for r in section.rows}
    assert "#리필스테이션" in tags and tags["#리필스테이션"].accounts == 2
    assert "#매거진협찬" not in tags
    assert tr.min_tag_accounts(27) == 2 and tr.min_tag_accounts(30) == 3
    # 30곳 이상이면 3곳 기준: 2곳만 쓴 태그는 빠진다
    big = {**media, **{f"extra_{i}": _items(f"extra_{i}", "#평범한기록") for i in range(5)}}
    section, _ = tr.hashtag_section(big, [], NOW, None, preset.media_accounts)
    assert "#리필스테이션" not in {r.tag for r in section.rows}


# ---------------------------------------------------------------- 4. 이름


def test_preset_display_names_loaded() -> None:
    preset = tb.load_preset("eco_beauty")
    assert preset is not None
    names = preset.display_names
    assert names["toun28_official"] == "톤28" and names["abib.official"] == "아비브"
    assert names["directorpi"] == "디렉터파이" and len(names) == 34
    assert set(names) == set(preset.accounts)


# ---------------------------------------------------------------- 6. 표기·검색량


def test_report_label() -> None:
    assert report_label("tr_b8358e1234567890") == "TR-B835"
    assert report_label("tr_0123456789abcdef") == "TR-0123"


def test_searchad_under_10_is_not_zero(tmp_path: Path) -> None:
    stats = sa.parse({"keywordList": [
        {"relKeyword": "적은말", "monthlyPcQcCnt": "< 10", "monthlyMobileQcCnt": "< 10"},
        {"relKeyword": "반반", "monthlyPcQcCnt": "< 10", "monthlyMobileQcCnt": 120},
    ]})  # fmt: skip
    low = stats[0]
    assert (low.pc, low.mobile, low.pc_below_10, low.mobile_below_10) == (None, None, True, True)

    class Ad:
        configured, calls_today = True, 0

        async def keywords(self, hints: list[str]) -> list[Any]:
            self.calls_today += 1
            return stats

    got, calls = asyncio.run(tr.monthly_volumes(["적은말", "반반"], Ad(), None, TODAY))
    assert got == {"적은말": tr.UNDER_10, "반반": 120} and calls == 1
