"""고객지원 AI 대화는 회사별로 나뉜다 (11/24 회사 격리, 같은 이름이 다른 회사에 있어도)."""

from ax_agent_template import company_scope
from support_agent.chat import ChatStore


def test_threads_are_kept_per_request_company() -> None:
    store = ChatStore()
    with company_scope.using("catbooks"):
        cat = store.create("예진", "캣북스 질문", "ctx_ui_1")
    with company_scope.using("nubi"):
        nubi = store.create("예진", "누비 질문", "ctx_ui_2")  # 이름이 같아도
        assert [t.id for t in store.all()] == [nubi.id]
        assert store.get(cat.id) is None
    with company_scope.using("catbooks"):
        assert [t.id for t in store.all()] == [cat.id]
        assert store.get(nubi.id) is None
    assert cat.tenant_id == "shop-demo"  # 캣북스는 예전 값 그대로
