import { useState } from "react";
import { CATEGORY_KO, STATUS_KO, URGENCY_DOT } from "@/lib/labels";
import type { ConversationListItem } from "@/lib/types";

type Filter = "all" | "waiting" | "escalated";

export function ConversationList({ items, selectedId, onSelect, onNew }: {
  items: ConversationListItem[]; selectedId: number | null; onSelect: (id: number) => void; onNew: () => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const test: Record<Filter, (c: ConversationListItem) => boolean> = {
    all: () => true, waiting: (c) => c.awaiting_reply && c.status === "open", escalated: (c) => c.status === "escalated",
  };
  const shown = items.filter(test[filter]);
  const label: Record<Filter, string> = { all: "전체", waiting: "답변 대기", escalated: "이관" };

  return (
    <aside className="card flex w-72 shrink-0 flex-col overflow-hidden" aria-label="문의함">
      <div className="flex items-center justify-between px-4 pb-2 pt-4">
        <h2 className="text-sm font-semibold">문의함</h2>
        <button className="btn-primary px-2.5 py-1 text-xs" onClick={onNew}>+ 새 문의</button>
      </div>
      <div className="mx-4 mb-2 flex rounded-full bg-ink-100 p-0.5 text-xs">
        {(Object.keys(label) as Filter[]).map((f) => (
          <button key={f} onClick={() => setFilter(f)} aria-pressed={filter === f}
            className={`flex-1 rounded-full py-1 font-medium transition ${filter === f ? "bg-surface shadow-sm" : "text-ink-500"}`}>
            {label[f]}<span className="ml-1 text-ink-500">{items.filter(test[f]).length}</span>
          </button>
        ))}
      </div>
      <ul className="scroll-thin flex-1 overflow-y-auto">
        {shown.map((c) => (
          <li key={c.id}>
            <button onClick={() => onSelect(c.id)} className={`w-full border-l-2 px-4 py-3 text-left transition ${selectedId === c.id ? "border-brand-600 bg-brand-50" : "border-transparent hover:bg-ink-50"}`}>
              <div className="flex items-center gap-2">
                <span className={`h-2 w-2 shrink-0 rounded-full ${URGENCY_DOT[c.urgency ?? "low"]}`} title={`긴급도 ${c.urgency}`} />
                <span className="truncate text-sm font-medium">{c.customer_name}</span>
                {c.order_id && <span className="chip ml-auto shrink-0 bg-ink-100 text-ink-700">주문 연결</span>}
              </div>
              <div className="mt-1 truncate text-xs font-medium text-ink-700">{c.subject}</div>
              <div className="mt-1 line-clamp-2 text-xs text-ink-500">{c.preview}</div>
              <div className="mt-1.5 flex flex-wrap gap-1">
                {c.category && <span className="chip bg-brand-50 text-brand-700">{CATEGORY_KO[c.category] ?? c.category}</span>}
                {c.awaiting_reply && c.status === "open" && <span className="chip bg-red-50 text-red-700">답변 대기</span>}
                {c.status !== "open" && <span className="chip bg-ink-100 text-ink-500">{STATUS_KO[c.status]}</span>}
              </div>
            </button>
          </li>
        ))}
        {!shown.length && <li className="px-4 py-8 text-center text-xs text-ink-500">문의가 없습니다.</li>}
      </ul>
    </aside>
  );
}
