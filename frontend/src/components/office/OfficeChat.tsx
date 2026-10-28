import { useLayoutEffect, useRef } from "react";
import type { ChatItem, Tone } from "@/lib/agentChat";
import { Link } from "@/lib/nav";

// AI 오피스 옆 단톡방. AI 대화방과 같은 ChatItem을 메신저처럼 좁게 보여 준다 (결정 버튼은 두지 않고 AI 대화방으로 보낸다).

const AVATAR: Record<string, string> = {
  infra: "bg-ink-700 text-app", data: "bg-violet-700 text-white dark:text-app", memory: "bg-violet-700 text-white dark:text-app",
  planning: "bg-brand-500 text-white", marketing: "bg-accent text-white", support: "bg-emerald-700 text-white dark:text-app",
  finance: "bg-amber-700 text-white dark:text-app", hr: "bg-red-700 text-white dark:text-app",
  safety: "bg-brand-strong text-white", orchestrator: "bg-brand-strong text-white",
};
const TONE_ICON: Record<Tone, string> = { request: "", ok: "✓ ", warn: "⏸ ", fail: "✕ ", muted: "" };
const TONE_EDGE: Record<Tone, string> = {
  request: "", ok: "border-l-2 border-emerald-700/50", warn: "border-l-2 border-amber-700/60", fail: "border-l-2 border-red-700/60", muted: "opacity-80",
};
const hhmm = (iso: string) => (iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" }) : "");

export interface Typing { key: string; name: string; what: string }

export function OfficeChat({ items, typing, freshIds, title, sub }: { items: ChatItem[]; typing: Typing[]; freshIds: ReadonlySet<string>; title: string; sub: string }) {
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  useLayoutEffect(() => {
    const el = box.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [items, typing]);
  const onScroll = () => {
    const el = box.current;
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  };

  return (
    <section aria-label="부서 단톡방" className="card flex min-h-[420px] min-w-0 flex-1 flex-col overflow-hidden xl:w-[580px] xl:flex-none">
      <header className="flex items-center gap-2 border-b border-ink-100 px-4 py-3">
        <h2 className="card-title truncate">{title}</h2>
        <span className="truncate text-xs text-ink-500">{sub}</span>
      </header>
      <div ref={box} onScroll={onScroll} className="scroll-thin min-h-0 flex-1 overflow-y-auto bg-surface-2 px-3 py-4">
        {items.length === 0 ? (
          <p className="py-16 text-center text-sm text-ink-500">아직 주고받은 말이 없습니다.</p>
        ) : (
          <ol role="log" aria-live="polite" aria-relevant="additions" className="flex flex-col gap-3">
            {items.map((it) => <Row key={it.id} it={it} fresh={freshIds.has(it.id)} />)}
          </ol>
        )}
      </div>
      {typing.length > 0 && (
        <ul aria-label="작업 중인 부서" className="flex flex-col gap-1.5 border-t border-ink-100 bg-surface-2 px-3 py-2.5">
          {typing.map((ty) => (
            <li key={ty.key} className="flex items-center gap-2 text-xs text-ink-500">
              <span aria-hidden="true" className={`flex h-6 w-6 items-center justify-center rounded-full text-[10px] font-semibold ${AVATAR[ty.key] ?? "bg-ink-500 text-white"}`}>{ty.name.slice(0, 1)}</span>
              <span className="flex gap-0.5" aria-hidden="true">
                {[0, 1, 2].map((i) => <span key={i} className="h-1.5 w-1.5 animate-bounce rounded-full bg-ink-500" style={{ animationDelay: `${i * 120}ms` }} />)}
              </span>
              <span className="truncate"><b className="font-semibold text-ink-700">{ty.name}</b> {ty.what} 중</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Row({ it, fresh }: { it: ChatItem; fresh: boolean }) {
  const pop = fresh ? "animate-[officePop_.28s_ease-out]" : "";
  if (it.kind === "system") {
    return (
      <li className={`flex justify-center ${pop}`}>
        <p className="max-w-[92%] rounded-full bg-ink-100 px-3 py-1 text-center text-[11px] text-ink-500">{it.text}</p>
      </li>
    );
  }
  if (it.kind === "separator") {
    return (
      <li role="separator" className="flex items-center gap-2 pt-1 text-[11px] font-medium text-ink-500">
        <span className="h-px flex-1 bg-ink-300/60" /><span className="max-w-[80%] truncate">{it.text}</span><span className="h-px flex-1 bg-ink-300/60" />
      </li>
    );
  }
  if (it.kind === "hold") {
    const done = it.state !== "pending";
    const outcome = { approved: "사람이 승인 → 다시 진행", rejected: "사람이 거절", timeout: "기한 지나 자동 거절", pending: "" }[it.state];
    return (
      <li className={`rounded-2xl bg-ink-900 px-3.5 py-2.5 text-app dark:bg-surface dark:text-ink-900 dark:ring-1 dark:ring-amber-700/40 ${pop}`}>
        <p className="text-[13px] font-semibold"><span aria-hidden="true">🚩 </span>{done ? "인간 판단" : "사람 판단 필요"}: {it.title}</p>
        <p className="mt-0.5 text-[11px] opacity-75">{it.detail}</p>
        {done ? <p className="mt-1 text-[11px] font-medium opacity-90">{outcome}</p>
          : it.contextId === "replay-scn-a" ? <p className="mt-1 text-[11px] opacity-90">리플레이에서는 잠시 뒤 자동으로 승인됩니다.</p>
          : <Link href="/agents/chat" className="mt-1.5 inline-block text-[11px] font-semibold underline underline-offset-2">AI 대화방에서 결정 →</Link>}
      </li>
    );
  }
  const right = it.central;
  return (
    <li className={`flex gap-2 ${right ? "flex-row-reverse" : ""} ${pop}`}>
      <span aria-hidden="true" className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-[11px] text-xs font-semibold ${AVATAR[it.fromKey] ?? "bg-ink-500 text-white"}`}>
        {it.from.slice(0, 1)}
      </span>
      <div className={`flex min-w-0 max-w-[85%] flex-col ${right ? "items-end" : "items-start"}`}>
        <span className="text-[11px] text-ink-700">{it.from} <span className="text-ink-500">→ {it.to}</span></span>
        <div className={`mt-0.5 flex items-end gap-1.5 ${right ? "flex-row-reverse" : ""}`}>
          <div title={it.raw} className={`rounded-2xl px-3 py-1.5 text-[13px] leading-relaxed ${right ? "rounded-tr-md bg-brand-600 text-white" : `rounded-tl-md bg-surface ${TONE_EDGE[it.tone]}`}`}>
            <p>{right ? "" : TONE_ICON[it.tone]}{it.text}</p>
            {it.facts.length > 0 && (
              <dl className="mt-1 flex flex-wrap gap-1">
                {it.facts.map((f) => (
                  <div key={f.label} className="rounded-md bg-ink-50 px-1.5 py-0.5 text-[10.5px] text-ink-700">
                    <dt className="inline text-ink-500">{f.label} </dt><dd className="inline font-medium tabular-nums">{f.value}</dd>
                  </div>
                ))}
              </dl>
            )}
            {it.tag && <p className={`mt-0.5 text-[10.5px] ${right ? "text-white/70" : "text-ink-500"}`}>{it.tag}</p>}
          </div>
          <time dateTime={it.at} className="shrink-0 pb-0.5 text-[10px] tabular-nums text-ink-500">{hhmm(it.at)}</time>
        </div>
      </div>
    </li>
  );
}
