"""고객 아이디어(의견) 창구: 불만·문의와 따로 "이런 걸 해 주면 좋겠다"를 받아 모은다.

문의(`create_inquiry`)는 지금 겪는 문제를 풀어 달라는 요청이고, 아이디어는 앞으로 이렇게 해 주면
좋겠다는 제안이다. 둘을 섞으면 아이디어가 상담 대기열에 묻히고 문의 통계가 흐려져서 저장소·skill·
화면 경로를 따로 둔다.

규칙
- 고객이 고르는 것은 종류(`kind`)뿐이고, 영역(`area`)은 단어 규칙으로 코드가 붙인다 (LLM 없음).
- 글은 고객이 쓴 글이라 `untrusted`로만 받고, 저장할 때 개인정보를 가린다.
- 환불·취소·불량처럼 지금 풀어야 하는 문제를 적은 글은 아이디어로 받지 않고 문의로 안내한다.
- 한 고객이 하루에 낼 수 있는 수와 글자 수를 제한하고,
  같은 고객이 같은 글을 다시 내면 새로 쌓지 않는다.
- 쌓은 아이디어는 (종류, 영역) 묶음으로 센다. 기획에 넘기는 요약에는 고객 원문을 싣지 않고
  건수·고객 수·기간·많이 나온 단어만 싣는다 (원문은 이 목록에서만 본다).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from ax_contracts.schemas.common import AxModel
from support_agent import guard
from support_agent.store import now_iso

IdeaKind = Literal["event", "feature", "improvement", "product", "other"]
IdeaArea = Literal["shipping", "payment", "product", "benefit", "usability", "other"]
IdeaStatus = Literal["new", "sent"]

MAX_TEXT_CHARS = 500
MIN_TEXT_CHARS = 5
DAILY_LIMIT = 5  # 한 고객이 하루(24시간)에 낼 수 있는 아이디어 수

KIND_KO: dict[str, str] = {
    "event": "이벤트·혜택 요청",
    "feature": "새 기능 제안",
    "improvement": "개선 제안",
    "product": "상품·서비스 제안",
    "other": "기타 의견",
}
AREA_KO: dict[str, str] = {
    "shipping": "배송",
    "payment": "결제",
    "product": "상품",
    "benefit": "쿠폰·적립·이벤트",
    "usability": "사이트 사용성",
    "other": "기타",
}
# 위에서부터 먼저 맞는 영역이 된다. 맞는 단어가 없으면 `other`다.
AREA_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("shipping", ("배송", "택배", "도착", "송장", "포장", "당일", "새벽")),
    ("payment", ("결제", "카드", "간편", "페이", "할부", "무통장", "계좌")),
    ("benefit", ("쿠폰", "적립", "포인트", "이벤트", "할인", "혜택", "멤버십", "등급", "증정")),
    ("usability", ("화면", "검색", "메뉴", "앱", "사이트", "페이지", "알림", "로그인", "찾기")),
    ("product", ("상품", "입고", "재입고", "품절", "신상", "굿즈", "책", "사이즈", "색상", "종류")),
)
# 지금 풀어야 하는 문제를 적은 글은 아이디어가 아니라 문의다. 오탐을 줄이려고 분명한 말만 쓴다.
COMPLAINT_WORDS = (
    "환불",
    "반품",
    "교환",
    "취소해",
    "취소가",
    "결제 오류",
    "결제가 안",
    "불량",
    "파손",
    "안 왔",
    "안왔",
    "미배송",
    "누락",
    "잘못 왔",
    "잘못왔",
    "사기",
)
SPAM_WORDS = ("광고", "홍보", "구매링크", "할인코드", "쿠폰코드", "오픈채팅", "텔레그램", "부업")
STOPWORDS = frozenset(
    "그리고 그래서 하지만 그런데 있으면 있었으면 좋겠어요 좋겠습니다 좋겠다 해주세요 해주시면 "
    "주세요 해줬으면 했으면 싶어요 싶습니다 생각합니다 같아요 너무 정말 진짜 조금 많이 "
    "주는 이번 항상 가끔 우리 저는 제가 그냥 모든 하는 있는 없는 되는 하면 하고 "
    "에서 으로 에게".split()
)
WORD = re.compile(r"[가-힣]{2,8}|[A-Za-z]{3,12}")
# 단어 끝의 흔한 조사 (센 말이 "쿠폰"과 "쿠폰을"로 갈라지지 않게). 두 글자 단어는 건드리지 않는다.
PARTICLES = (
    "에서",
    "으로",
    "에게",
    "까지",
    "부터",
    "처럼",
    "을",
    "를",
    "이",
    "가",
    "은",
    "는",
    "에",
    "의",
    "도",
    "로",
    "와",
    "과",
)


def _stem(word: str) -> str:
    if len(word) >= 3:
        for particle in PARTICLES:
            if word.endswith(particle) and len(word) - len(particle) >= 2:
                return word[: -len(particle)]
    return word


class Idea(AxModel):
    idea_id: str  # IDEA-n (회사별 번호)
    tenant_id: str
    context_id: str  # 낸 요청의 사건 번호 (ctx_shop_…)
    user_id: int  # 낸 고객 (shop-api가 로그인한 고객으로 채운다). 화면에는 번호를 내지 않는다
    kind: IdeaKind
    area: IdeaArea
    text: str  # 개인정보를 가린 글
    text_digest: str  # 같은 고객이 같은 글을 다시 내는지 보는 지문
    status: IdeaStatus = "new"
    created_at: str
    sent_at: str | None = None
    request_id: str | None = None  # 기획이 발급한 REQ-n (넘긴 뒤)
    sent_by: str | None = None  # 넘긴 사람 (사람 토큰의 이름)


class IdeaGroup(AxModel):
    """기획에 넘길 묶음 하나: 같은 종류·영역의 새 아이디어."""

    kind: IdeaKind
    area: IdeaArea
    idea_count: int
    customer_count: int
    first_at: str
    last_at: str
    top_words: list[tuple[str, int]] = Field(default_factory=list)
    idea_ids: list[str] = Field(default_factory=list)


def looks_like_complaint(text: str) -> bool:
    lowered = text.casefold()
    return any(word in lowered for word in COMPLAINT_WORDS)


def looks_like_spam(text: str) -> bool:
    lowered = text.casefold()
    return bool(guard.LINK.search(text)) or any(word in lowered for word in SPAM_WORDS)


def classify_area(text: str) -> IdeaArea:
    lowered = text.casefold()
    for area, words in AREA_WORDS:
        if any(word in lowered for word in words):
            return area  # type: ignore[return-value]
    return "other"


def clean_text(text: str) -> str:
    """공백을 정리하고 개인정보를 가려 길이를 자른다."""
    return guard.mask_pii(" ".join(text.split()))[0][:MAX_TEXT_CHARS]


def digest(user_id: int, text: str) -> str:
    basis = f"{user_id}\n{' '.join(text.casefold().split())}"
    return hashlib.sha1(basis.encode()).hexdigest()[:12]


def top_words(texts: list[str], *, limit: int = 5) -> list[tuple[str, int]]:
    """아이디어 글에서 둘 이상의 아이디어에 나온 단어를 많이 나온 순으로 센다."""
    seen: Counter[str] = Counter()
    for text in texts:
        seen.update({w for w in map(_stem, WORD.findall(text)) if w not in STOPWORDS})
    ranked = sorted(((w, n) for w, n in seen.items() if n >= 2), key=lambda p: (-p[1], p[0]))
    return ranked[:limit]


class IdeaStore:
    def __init__(self) -> None:
        self._items: dict[str, Idea] = {}

    def _put(self, idea: Idea) -> Idea:
        """저장 지점. 영속 저장소가 여기서 DB에도 쓴다."""
        self._items[_key(idea.tenant_id, idea.idea_id)] = idea
        return idea

    def _next_id(self, tenant_id: str) -> str:
        numbers = [int(i.idea_id.split("-")[1]) for i in self.all(tenant_id)]
        return f"IDEA-{max(numbers, default=0) + 1}"

    def add(
        self, *, tenant_id: str, context_id: str, user_id: int, kind: IdeaKind, text: str
    ) -> Idea:
        """아이디어를 쌓는다. 같은 고객이 같은 글을 다시 내면 처음 것을 돌려준다."""
        text_digest = digest(user_id, text)
        for found in self.all(tenant_id):
            if found.user_id == user_id and found.text_digest == text_digest:
                return found
        return self._put(
            Idea(
                idea_id=self._next_id(tenant_id),
                tenant_id=tenant_id,
                context_id=context_id,
                user_id=user_id,
                kind=kind,
                area=classify_area(text),
                text=text,
                text_digest=text_digest,
                created_at=now_iso(),
            )
        )

    def recent_count(self, tenant_id: str, user_id: int, now: datetime | None = None) -> int:
        since = (now or datetime.now(UTC)) - timedelta(hours=24)
        return sum(
            1 for i in self.all(tenant_id) if i.user_id == user_id and _parse(i.created_at) >= since
        )

    def mark_sent(self, idea_ids: list[str], tenant_id: str, request_id: str, by: str) -> None:
        for idea_id in idea_ids:
            found = self._items.get(_key(tenant_id, idea_id))
            if found is None or found.status == "sent":
                continue
            self._put(
                found.model_copy(
                    update={
                        "status": "sent",
                        "sent_at": now_iso(),
                        "request_id": request_id,
                        "sent_by": by,
                    }
                )
            )

    def get(self, tenant_id: str, idea_id: str) -> Idea | None:
        return self._items.get(_key(tenant_id, idea_id))

    def all(self, tenant_id: str | None = None) -> list[Idea]:
        """받은 순서(오래된 것이 앞)."""
        items = [i for i in self._items.values() if tenant_id is None or i.tenant_id == tenant_id]
        return sorted(items, key=lambda i: (i.created_at, int(i.idea_id.split("-")[1])))

    def groups(
        self, tenant_id: str, *, kind: str | None = None, area: str | None = None
    ) -> list[IdeaGroup]:
        """아직 넘기지 않은 아이디어를 (종류, 영역)으로 묶어 많은 순으로 돌려준다."""
        buckets: dict[tuple[str, str], list[Idea]] = {}
        for idea in self.all(tenant_id):
            if idea.status != "new":
                continue
            if (kind and idea.kind != kind) or (area and idea.area != area):
                continue
            buckets.setdefault((idea.kind, idea.area), []).append(idea)
        groups = [
            IdeaGroup(
                kind=k,  # type: ignore[arg-type]
                area=a,  # type: ignore[arg-type]
                idea_count=len(items),
                customer_count=len({i.user_id for i in items}),
                first_at=items[0].created_at,
                last_at=items[-1].created_at,
                top_words=top_words([i.text for i in items]),
                idea_ids=[i.idea_id for i in items],
            )
            for (k, a), items in buckets.items()
        ]
        return sorted(groups, key=lambda g: (-g.idea_count, g.kind, g.area))


def _key(tenant_id: str, idea_id: str) -> str:
    return f"{tenant_id}:{idea_id}"


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


# ---------------------------------------------------------------- 일일 보고: 같은 뜻끼리 묶기
REPORT_TZ_ENV = "SUPPORT_REPORT_TZ"
REPORT_TZ_DEFAULT = "Asia/Seoul"  # 접수일은 회사 시간대로 센다
CLUSTER_SHARED_MIN = 2  # 말 조각이 이만큼 겹치거나
CLUSTER_JACCARD_MIN = 0.5  # 이만큼 비슷하면 같은 요청으로 본다
LABEL_WORDS = 3
MAX_CLUSTERS = 20


class Cluster(AxModel):
    """같은 뜻으로 겹쳐 들어온 요청. 고객 원문은 담지 않는다."""

    key: str
    label: str
    count: int
    customer_count: int


def report_tz() -> ZoneInfo:
    return ZoneInfo(os.getenv(REPORT_TZ_ENV, "").strip() or REPORT_TZ_DEFAULT)


def local_date(stamp: str) -> date:
    return _parse(stamp).astimezone(report_tz()).date()


# 이벤트 요청이면 거의 모든 글에 나오는 말이라 같은 뜻을 가르는 데 쓰지 않는다
# (다른 말이 없으면 쓴다)
GENERIC_WORDS = frozenset({"이벤트", "혜택"})


def _stems(text: str) -> frozenset[str]:
    words = {w for w in map(_stem, WORD.findall(text)) if w not in STOPWORDS}
    return frozenset(words - GENERIC_WORDS or words)


def _similar(a: frozenset[str], b: frozenset[str]) -> bool:
    shared = len(a & b)
    if shared == 0:
        return False
    return shared >= CLUSTER_SHARED_MIN or shared / len(a | b) >= CLUSTER_JACCARD_MIN


def cluster_ideas(items: list[Idea]) -> list[Cluster]:
    """같은 요청끼리 묶어 많이 겹친 순으로 돌려준다. 말 조각이 하나도 없는 글은 묶지 않는다."""
    groups: list[tuple[set[str], list[Idea]]] = []
    for idea in sorted(items, key=lambda i: (i.created_at, i.idea_id)):
        stems = _stems(idea.text)
        if not stems:
            continue
        for core, members in groups:
            if _similar(frozenset(core), stems):
                core |= stems
                members.append(idea)
                break
        else:
            groups.append((set(stems), [idea]))
    clusters: list[Cluster] = []
    for _, members in groups:
        seen: Counter[str] = Counter()
        for m in members:
            seen.update(_stems(m.text))
        by_use = sorted(seen.items(), key=lambda p: (-p[1], -len(p[0]), p[0]))
        ranked = by_use[:LABEL_WORDS]
        words = [w for w, _ in ranked]
        clusters.append(
            Cluster(
                key=" ".join(sorted(words)),
                label=" ".join(words),
                count=len(members),
                customer_count=len({m.user_id for m in members}),
            )
        )
    clusters.sort(key=lambda c: (-c.count, -c.customer_count, c.key))
    return clusters[:MAX_CLUSTERS]


def daily_report(items: list[Idea], day: date, kind: str = "event") -> tuple[int, list[Cluster]]:
    """그 날(회사 시간대) 받은 해당 종류 아이디어 수와 묶음 순위."""
    todays = [i for i in items if i.kind == kind and local_date(i.created_at) == day]
    return len(todays), cluster_ideas(todays)
