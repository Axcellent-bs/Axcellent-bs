"""인스타 레이더 묶음 씨앗에서 의미 없는 단위 빼기.

232 화면에 나온 "수 있습니다"·"당첨자 발표"·"of the" 묶음이 안 생겨야 한다.
"""

from __future__ import annotations

from ax_contracts.schemas.data import InstagramRadarPost
from data_agent.skills import caption_noise as cn
from data_agent.skills import instagram as igs
from data_agent.skills import trend_report as tr


def _post(account: str, n: int) -> InstagramRadarPost:
    return InstagramRadarPost(account=account, media_id=f"{account}-{n}", metric="like_count",
                              value=500, account_median=100.0, ratio=5.0)  # fmt: skip


CAPTIONS = {
    # 세 계정이 같이 쓴 말은 전부 의미 없는 것뿐 (+ 둘만 같이 쓴 진짜 태그 #책장털기)
    "a-1": "이 제품을 만나 볼 수 있습니다\n당첨자 발표 안내\nthe best of the year #광고 #책장털기",
    "b-1": "매장에서 확인할 수 있습니다. 당첨자 발표는 내일! one of the most #협찬 #책장털기",
    "c-1": "지금 바로 참여하세요 수 있습니다 당첨자 발표 most of the time #ad #sponsored #이벤트",
}


def test_meaningless_units_do_not_seed_groups() -> None:
    posts = [_post(a, 1) for a in ("a", "b", "c")]
    groups = igs.group_bursts(posts, CAPTIONS, 2)
    names = [name for name, _, _ in groups]
    assert names == ["#책장털기"]
    members = [m for _, ms, _ in groups for m in ms]
    for bad in ("수 있습니다", "당첨자 발표", "of the", "#광고", "#ad"):
        assert bad not in names and bad not in members


def test_only_noise_means_no_groups() -> None:
    captions = {"a-1": "수 있습니다 of the #광고", "b-1": "수 있습니다 of the #광고"}
    assert igs.group_bursts([_post("a", 1), _post("b", 1)], captions, 2) == []


def test_noise_rules() -> None:
    for key in ("hashtag:광고", "hashtag:giveaway", "phrase:광고 포함", "phrase:당첨자 발표",
                "phrase:참여 방법 안내", "phrase:추첨을 통해 드려요", "hashtag:이벤트기간",
                "phrase:수 있습니다", "phrase:함께 합니다", "phrase:of the", "phrase:for you",
                "phrase:in the", "phrase:수 있습니다 당첨자", "phrase:one of the",
                "phrase:확인할 수 있습니다"):  # fmt: skip
        assert cn.noise(key), key
    for key in ("hashtag:책장털기", "phrase:비건 선크림", "phrase:리필 스테이션 후기",
                "phrase:best skincare", "hashtag:ad뷰티", "hashtag:수있습니다"):  # fmt: skip
        assert not cn.noise(key), key
    # 트렌드 보고서 해시태그 섹션과 같은 상수
    assert tr.HIDDEN_TAGS is cn.HIDDEN_TAGS
    assert tr.hidden_tag("hashtag:이벤트기간") and not tr.hidden_tag("hashtag:클린뷰티")
