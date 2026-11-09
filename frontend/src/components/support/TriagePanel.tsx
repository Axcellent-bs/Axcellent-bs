import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "@/lib/nav";
import { Loading, OFFLINE } from "@/components/admin/bits";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { CATEGORY_KO, TRIAGE_REASON_KO, tenantName } from "@/lib/labels";
import type { ConversationListItem } from "@/lib/types";

const LABEL_MAX = 40;

/**
 * 분류 필요: 로그·오류 코드로 원인을 알 수 없는 문의를 따로 모아 상담원이 분류 이름을 정한다.
 * 예) "장바구니 창이 안 열려서 결제가 안 돼요"는 연결된 실패 결제 기록이 없다.
 * 회사(고객사)별로 나눠 볼 수 있고, 다른 회사의 문의도 같은 화면에서 처리한다.
 */
export function TriagePanel({ onCount }: { onCount?: (needed: number) => void }) {
  const [rows, setRows] = useState<ConversationListItem[] | null>(null);
  const [company, setCompany] = useState<string>("all");
  const [view, setView] = useState<"needed" | "done">("needed");
  const [labels, setLabels] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const all = await api.admin.list();
      setRows(all.filter((c) => c.triage));
      onCount?.(all.filter((c) => c.triage?.state === "needed").length);
      setError(null);
    } catch {
      setError(OFFLINE);
    }
  }, [onCount]);
  useEffect(() => { load(); }, [load]);

  const companies = useMemo(() => {
    const count = new Map<string, number>();
    for (const c of rows ?? []) {
      if (c.triage?.state !== view) continue;
      const id = c.tenant_id ?? "shop-demo";
      count.set(id, (count.get(id) ?? 0) + 1);
    }
    return [...count.entries()];
  }, [rows, view]);

  const labelCounts = useMemo(() => {
    const count = new Map<string, number>();
    for (const c of rows ?? []) {
      if (c.triage?.state === "done" && c.triage.label && (company === "all" || (c.tenant_id ?? "shop-demo") === company)) {
        count.set(c.triage.label, (count.get(c.triage.label) ?? 0) + 1);
      }
    }
    return [...count.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows, company]);

  async function act(id: number, action: "decide" | "clear", label?: string) {
    setBusy(id); setError(null);
    try {
      await api.admin.setTriage(id, { action, ...(label ? { label } : {}) });
      setLabels((l) => ({ ...l, [id]: "" }));
      await load();
    } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  if (!rows) return <Loading error={error} />;
  const shown = rows.filter((c) => c.triage?.state === view && (company === "all" || (c.tenant_id ?? "shop-demo") === company));
  const neededCount = rows.filter((c) => c.triage?.state === "needed").length;
  const doneCount = rows.filter((c) => c.triage?.state === "done").length;

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-3" aria-label="분류 필요 문의">
      <p className="text-xs text-ink-500">
        로그나 오류 코드로 원인을 알 수 없는 문의입니다. 예: &ldquo;장바구니 창이 안 열려서 결제가 안 돼요&rdquo;. 상담원이 원인 이름(분류)을 정하고 마칩니다.
      </p>
      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-xs font-medium text-red-800">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button className={`chip ${view === "needed" ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`} onClick={() => setView("needed")}>분류 필요 {neededCount}</button>
        <button className={`chip ${view === "done" ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`} onClick={() => setView("done")}>분류 완료 {doneCount}</button>
        <span className="mx-1 h-4 w-px bg-ink-200" aria-hidden />
        <button className={`chip ${company === "all" ? "bg-ink-800 text-white" : "bg-ink-100 text-ink-700"}`} onClick={() => setCompany("all")}>모든 회사</button>
        {companies.map(([id, n]) => (
          <button key={id} className={`chip ${company === id ? "bg-ink-800 text-white" : "bg-ink-100 text-ink-700"}`} onClick={() => setCompany(id)}>{tenantName(id)} {n}</button>
        ))}
      </div>
      {view === "done" && labelCounts.length > 0 && (
        <div className="card p-3">
          <h2 className="panel-title mb-2">분류 이름별 건수</h2>
          <div className="flex flex-wrap gap-2">
            {labelCounts.map(([name, n]) => <span key={name} className="chip bg-emerald-50 text-emerald-800">{name} · {n}건</span>)}
          </div>
        </div>
      )}
      <div className="card min-h-0 flex-1 overflow-y-auto">
        {shown.length === 0
          ? <p className="p-6 text-center text-sm text-ink-500">{view === "needed" ? "분류가 필요한 문의가 없습니다." : "분류를 마친 문의가 없습니다."}</p>
          : <ul className="divide-y divide-ink-100">
              {shown.map((c) => {
                const t = c.triage!;
                const text = labels[c.id] ?? "";
                return (
                  <li key={c.id} className="flex flex-col gap-2 p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="chip bg-indigo-50 text-indigo-800">{tenantName(c.tenant_id)}</span>
                      <span className="chip bg-amber-50 text-amber-800">{TRIAGE_REASON_KO[t.reason] ?? t.reason}</span>
                      {c.category && <span className="chip bg-ink-100 text-ink-700">{CATEGORY_KO[c.category] ?? c.category}</span>}
                      {t.label && <span className="chip bg-emerald-50 text-emerald-800">{t.label}</span>}
                      <Link href={`/console?id=${c.id}`} className="ml-auto text-xs font-medium text-brand-700 underline">문의 #{c.id} 열기</Link>
                    </div>
                    <p className="truncate text-sm font-medium">{c.subject_display ?? c.subject}</p>
                    <p className="truncate text-xs text-ink-500">{c.preview} · {ago(c.updated_at)}</p>
                    {t.state === "needed"
                      ? <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); const v = text.trim(); if (v) act(c.id, "decide", v); }}>
                          <input className="field flex-1 py-1 text-xs" maxLength={LABEL_MAX} placeholder="분류 이름 (예: 장바구니 화면 오류)" aria-label={`문의 ${c.id} 분류 이름`} value={text} onChange={(e) => setLabels((l) => ({ ...l, [c.id]: e.target.value }))} />
                          <button className="btn-primary whitespace-nowrap text-xs" type="submit" disabled={busy === c.id || !text.trim()}>분류하고 마치기</button>
                          <button className="btn-ghost whitespace-nowrap text-xs" type="button" disabled={busy === c.id} onClick={() => act(c.id, "clear")}>표시 지우기</button>
                        </form>
                      : <div className="flex gap-2">
                          <button className="btn-ghost whitespace-nowrap text-xs" disabled={busy === c.id} onClick={() => act(c.id, "clear")}>표시 지우기</button>
                        </div>}
                  </li>
                );
              })}
            </ul>}
      </div>
    </section>
  );
}
