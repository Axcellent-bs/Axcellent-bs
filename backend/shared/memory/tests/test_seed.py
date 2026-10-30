import re

from ax_memory.seed import POLICY_CONDITION_CODES, POLICY_SECTIONS, seed_policies
from ax_memory.seed.loader import UNCONFIRMED
from ax_memory.store import InMemoryCardStore


def test_first_run_creates_one_card_per_section() -> None:
    store = InMemoryCardStore()
    report = seed_policies(store)
    assert report.created == len(POLICY_SECTIONS)
    assert report.existing == 0
    assert len(store.list()) == len(POLICY_SECTIONS)


def test_second_run_is_idempotent() -> None:
    store = InMemoryCardStore()
    seed_policies(store)
    report = seed_policies(store)
    assert report.created == 0
    assert report.existing == len(POLICY_SECTIONS)
    assert len(store.list()) == len(POLICY_SECTIONS)


def test_cards_are_policy_cards_of_default_tenant() -> None:
    store = InMemoryCardStore()
    seed_policies(store)
    for card in store.list():
        assert card.kind == "policy"
        assert card.tenant_id == "shop-demo"
        assert card.author == "memory-seed"
        assert card.context_id.startswith("ctx_ntc_")
        assert card.extra["version"] in {"2026-10-22", "2026-10-30"}
        assert card.extra["doc_id"] in card.tags
        # 확정 카드는 high, 미확정 카드는 low + 제목 표시 + extra.confirmed=False (#192)
        if card.extra.get("confirmed", True):
            assert card.confidence == "high"
            assert UNCONFIRMED not in card.title
        else:
            assert card.confidence == "low"
            assert card.title.endswith(UNCONFIRMED)


# 고객지원 분류 코드 (support_agent.categories.CATEGORIES에서 general 제외).
# domains끼리 import하지 않으므로 여기 적는다
SUPPORT_CATEGORIES = (
    "refund",
    "shipping",
    "payment",
    "cancel",
    "product",
    "account",
    "review",
    "privacy_deletion",
)


def test_every_support_category_has_policy_cards() -> None:
    """#192: 고객지원 분류마다 정책 카드가 있다. doc_id가 분류 코드와 같아야 support가 읽는다."""
    store = InMemoryCardStore()
    seed_policies(store)
    doc_ids = {c.extra["doc_id"] for c in store.list()}
    assert set(SUPPORT_CATEGORIES) <= doc_ids


def test_privacy_and_retention_cards_are_unconfirmed() -> None:
    """방침 제4조가 확인 필요라 개인정보 삭제·법정 보존 카드는 미확정이다 (#192)."""
    privacy = [s for s in POLICY_SECTIONS if s.doc_id == "privacy_deletion"]
    assert {s.section_id for s in privacy} == {"privacy_deletion-1", "privacy_deletion-2"}
    assert all(not s.confirmed and s.title.endswith(UNCONFIRMED) for s in privacy)
    assert any("5년" in s.body for s in privacy)


def test_section_ids_are_unique_and_titles_name_the_policy() -> None:
    ids = [s.section_id for s in POLICY_SECTIONS]
    assert len(ids) == len(set(ids))
    titles = [s.title for s in POLICY_SECTIONS]
    assert len(titles) == len(set(titles))  # 멱등 키가 제목이라 같으면 한 장으로 합쳐진다
    assert all(s.section_id.startswith(f"{s.doc_id}-") for s in POLICY_SECTIONS)
    assert all("정책: " in s.title for s in POLICY_SECTIONS)


def test_condition_codes() -> None:
    assert {"LATE_3D", "DEFECT_RETURN_OK", "PAYMENT_OUTAGE"} <= POLICY_CONDITION_CODES
    store = InMemoryCardStore()
    seed_policies(store)
    codes = {c.extra["condition_code"] for c in store.list() if "condition_code" in c.extra}
    assert codes == POLICY_CONDITION_CODES


def test_support_kb_facts_present() -> None:
    store = InMemoryCardStore()
    seed_policies(store)
    text = " ".join(c.body for c in store.list())
    assert "수령 후 7일 이내 단순 변심 환불은 왕복 배송비를 고객이 부담한다" in text
    assert "판매자 부담으로 환불한다" in text
    assert "영업일 기준 2~3일" in text


def test_no_personal_info() -> None:
    store = InMemoryCardStore()
    seed_policies(store)
    pattern = re.compile(r"[\w.]+@[\w.]+|\d{2,3}-\d{3,4}-\d{4}")
    for card in store.list():
        assert not pattern.search(card.title + card.body)
