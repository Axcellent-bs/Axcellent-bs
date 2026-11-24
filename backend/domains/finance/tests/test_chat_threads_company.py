"""재무 AI 대화는 회사별로 나뉜다 (11/24 회사 격리, 같은 이름이 다른 회사에 있어도)."""

from ax_agent_template import company_scope
from finance_agent.chat.threads import ChatStore


def test_threads_are_kept_per_request_company() -> None:
    store = ChatStore()
    with company_scope.using("catbooks"):
        cat = store.create("캣북스 질문", "예진")
    with company_scope.using("nubi"):
        nubi = store.create("누비 질문", "예진")  # 이름이 같아도
        assert [t["id"] for t in store.list_all()] == [nubi["id"]]
        assert store.get(cat["id"]) is None
        assert store.hide(cat["id"], "예진") is False  # 남의 회사 대화는 못 숨긴다
    with company_scope.using("catbooks"):
        assert [t["id"] for t in store.list_all()] == [cat["id"]]
        assert store.get(nubi["id"]) is None
    # 요청 밖(정리 루틴)은 회사를 보지 않는다
    assert {t["id"] for t in store.list_all()} == {cat["id"], nubi["id"]}
