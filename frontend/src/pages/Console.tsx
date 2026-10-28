import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "@/lib/nav";
import { Composer, type ComposerLoad } from "@/components/admin/Composer";
import { ConversationList } from "@/components/admin/ConversationList";
import { CopilotPanel } from "@/components/admin/CopilotPanel";
import { KpiStrip } from "@/components/admin/KpiBar";
import { NewInquiryModal } from "@/components/admin/NewInquiryModal";
import { SummaryCard } from "@/components/admin/SummaryCard";
import { Thread } from "@/components/admin/Thread";
import { OFFLINE } from "@/components/admin/bits";
import { api } from "@/lib/api";
import { STATUS_KO } from "@/lib/labels";
import type { Conversation, ConversationListItem, Kpi } from "@/lib/types";

export default function ConsolePage() {
  return <Suspense><Console /></Suspense>;
}

function Console() {
  const params = useSearchParams();
  const [list, setList] = useState<ConversationListItem[]>([]);
  const [conv, setConv] = useState<Conversation | null>(null);
  const [kpi, setKpi] = useState<Kpi | null>(null);
  const [load, setLoad] = useState<ComposerLoad | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [showSummary, setShowSummary] = useState(true);
  const [followUp, setFollowUp] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [l, k] = await Promise.all([api.admin.list(), api.admin.kpi()]);
    setList(l); setKpi(k);
    return l;
  }, []);
  const open = useCallback(async (id: number) => { setConv(await api.admin.get(id)); setShowSummary(true); }, []);

  useEffect(() => {
    (async () => {
      try {
        const l = await refresh();
        const wanted = Number(params.get("id"));
        if (wanted && l.some((c) => c.id === wanted)) await open(wanted);
        else if (l.length) await open(l[0].id);
      } catch { setError(OFFLINE); }
    })();
  }, [refresh, open, params]);

  const afterChange = async (c: Conversation) => { setConv(c); await refresh(); };
  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label); setError(null);
    try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }
  const reload = async (id: number) => afterChange(await api.admin.get(id));

  return (
    <div className="flex h-full flex-col gap-3 p-4 lg:px-6">
      <KpiStrip kpi={kpi} />
      {error && <div role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-xs font-medium text-red-800">{error}</div>}
      <div className="flex min-h-0 flex-1 gap-3">
        <ConversationList items={list} selectedId={conv?.id ?? null} onSelect={open} onNew={() => setShowNew(true)} />
        {conv ? (
          <>
            <main className="card flex min-w-0 flex-1 flex-col overflow-hidden">
              <div className="flex shrink-0 items-center gap-3 border-b border-ink-100 bg-surface px-6 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2"><h1 className="truncate text-base font-semibold">{conv.customer_name}</h1><span className="chip bg-ink-100 text-ink-500">{STATUS_KO[conv.status]}</span></div>
                  <p className="truncate text-xs text-ink-500">{conv.subject}{conv.order_no && ` · 주문 ${conv.order_no}`} · 문의 #{conv.id}</p>
                </div>
                <div className="ml-auto flex gap-2">
                  <button className="btn-ghost whitespace-nowrap text-xs" disabled={busy !== null} onClick={() => run("summary", async () => { await api.admin.summary(conv.id); await reload(conv.id); setShowSummary(true); })}>{busy === "summary" ? "정리 중…" : "상담 기록 정리"}</button>
                  {conv.status === "open"
                    ? <button className="btn-ghost whitespace-nowrap text-xs" onClick={() => run("status", async () => { await api.admin.setStatus(conv.id, "resolved"); await reload(conv.id); })}>해결로 종료</button>
                    : <button className="btn-ghost whitespace-nowrap text-xs" onClick={() => run("status", async () => { await api.admin.setStatus(conv.id, "open"); await reload(conv.id); })}>다시 열기</button>}
                </div>
              </div>
              {conv.summary && showSummary && <SummaryCard s={conv.summary} onClose={() => setShowSummary(false)} />}
              <Thread conv={conv} />
              <div className="flex shrink-0 items-center gap-2 border-t border-dashed border-ink-100 bg-surface-2 px-6 py-2">
                <span className="text-[11px] font-medium text-ink-500">고객 후속 메시지 시뮬레이션</span>
                <input className="field flex-1 py-1 text-xs" placeholder="예: 반품 보냈는데 환불은 언제 되나요?" value={followUp} aria-label="고객 후속 메시지"
                  onChange={(e) => setFollowUp(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && followUp.trim() && !busy) run("follow", async () => { await afterChange(await api.admin.customerMessage(conv.id, followUp)); setFollowUp(""); }); }} />
                <button className="btn-ghost py-1 text-xs" disabled={!followUp.trim() || busy !== null} onClick={() => run("follow", async () => { await afterChange(await api.admin.customerMessage(conv.id, followUp)); setFollowUp(""); })}>{busy === "follow" ? "분석 중…" : "고객으로 보내기"}</button>
              </div>
              <Composer conv={conv} load={load} onSent={afterChange} />
            </main>
            <CopilotPanel conv={conv} onUseDraft={(draft) => setLoad({ nonce: Date.now(), draft })}
              onRejectDraft={(d) => run("reject", async () => { await api.admin.rejectDraft(conv.id, d.style); setKpi(await api.admin.kpi()); })} />
          </>
        ) : <div className="card flex flex-1 items-center justify-center text-sm text-ink-500">왼쪽에서 문의를 선택하세요.</div>}
      </div>
      {showNew && <NewInquiryModal onClose={() => setShowNew(false)} onCreate={async (b) => { const c = await api.admin.create(b); setShowNew(false); await afterChange(c); }} />}
    </div>
  );
}
