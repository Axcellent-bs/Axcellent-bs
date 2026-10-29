import { useEffect, useRef, useState, type DragEvent } from "react";
import { api } from "@/lib/api";
import { dateStr, won } from "@/lib/format";
import { RECEIPT_FIELD_KO, RECEIPT_IMAGES_KO, RECEIPT_MISMATCH_KO, RECEIPT_VERDICT_KO, RECEIPT_VERDICT_TONE } from "@/lib/labels";
import type { ReceiptAnalysis, ReceiptImageInput } from "@/lib/types";

const MAX_FILES = 4; // 한 번에 다루는 최대 장수
const MAX_BYTES = 4_500_000; // 한 장 크기(약 4.5MB)
const MAX_TOTAL_BYTES = 8_000_000; // 올린 사진 전체 크기
const CONCURRENCY = 2; // 동시에 분석하는 사진 수 (모델 호출이 몰리지 않게)
const TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
type MediaType = (typeof TYPES)[number];

// 사진마다 따로 분석한다. 여러 장을 한 번에 보내면 서버가 한 영수증의 쪽으로 합쳐 읽어서,
// 서로 다른 영수증이 섞이면 어긋난 쪽이 가려질 수 있다.
interface Item {
  key: string; name: string; size: number;
  state: "idle" | "analyzing" | "done" | "error";
  result?: ReceiptAnalysis; error?: string;
  file?: File; url?: string; // 사진과 미리보기는 이 화면에서 올린 동안에만 있다 (문의를 오가면 결과만 남는다)
}

// 분석 결과는 문의를 오갈 때 다시 보이도록 이 화면을 연 동안만 기억한다. 사진은 기억하지 않는다.
const STORE = new Map<number, Item[]>();
const keep = (items: Item[]): Item[] => items.map(({ file: _f, url: _u, ...rest }) => rest);

// 대조하는 값 → 어긋났을 때 support가 돌려주는 코드
const MISMATCH_OF: Record<string, string> = {
  total: "RECEIPT_AMOUNT_MISMATCH", date: "RECEIPT_DATE_MISMATCH", order_no: "RECEIPT_ORDER_NO_MISMATCH", shipping_fee: "RECEIPT_SHIPPING_MISMATCH",
};

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(new Error("사진을 읽지 못했습니다"));
    reader.readAsDataURL(file);
  });
}

async function runPool<T>(list: T[], limit: number, fn: (t: T) => Promise<void>) {
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(limit, list.length) }, async () => {
    while (next < list.length) await fn(list[next++]);
  }));
}

const kb = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1000))}KB`);

function Spinner() {
  return (
    <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

function Row({ label, receipt, order, bad }: { label: string; receipt: string | null; order: string; bad: boolean }) {
  return (
    <tr className={`border-t border-ink-100 ${bad ? "bg-red-50" : ""}`}>
      <td className="py-1.5 pr-2 text-ink-500">{label}</td>
      <td className={`py-1.5 pr-2 tabular-nums ${receipt == null ? "text-ink-500" : bad ? "font-semibold text-red-700" : ""}`}>{receipt ?? "읽지 못함"}</td>
      <td className="py-1.5 tabular-nums">{order}</td>
    </tr>
  );
}

const BANNER: Record<string, { title: (n: number) => string; sub: string }> = {
  consistent: { title: () => "영수증이 주문과 일치합니다", sub: "대조한 값이 모두 주문과 같습니다." },
  mismatch: { title: (n) => `영수증이 주문과 다릅니다 (${n}건)`, sub: "어긋난 항목을 확인하고 고객에게 다시 물어보세요." },
  insufficient: { title: () => "영수증만으로는 확인할 수 없습니다", sub: "읽은 값이 부족해 주문과 대조하지 못했습니다. 더 선명한 사진을 받아 보세요." },
};

function Result({ r }: { r: ReceiptAnalysis }) {
  const bad = new Set(r.check.mismatches);
  const isBad = (field: string) => bad.has(MISMATCH_OF[field]);
  const unchecked = Object.keys(RECEIPT_FIELD_KO).filter((f) => !r.check.checked.includes(f));
  const f = r.facts;
  const banner = BANNER[r.check.verdict];
  return (
    <div className="space-y-2.5">
      <div className={`rounded-xl p-3 ${RECEIPT_VERDICT_TONE[r.check.verdict]}`} role="status">
        <p className="text-[13px] font-semibold">{banner.title(r.check.mismatches.length)}</p>
        <p className="mt-0.5 text-[11px] opacity-90">{banner.sub}</p>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`chip ${RECEIPT_VERDICT_TONE[r.check.verdict]}`}>{RECEIPT_VERDICT_KO[r.check.verdict]}</span>
        <span className="chip bg-ink-100 text-ink-700">{RECEIPT_IMAGES_KO[r.images_status]}</span>
        {r.check.matched_basis && <span className="chip bg-ink-100 text-ink-700">{r.check.matched_basis === "cash" ? "실결제액 기준" : "적립금 반영 전 금액 기준"}</span>}
      </div>

      <table className="w-full text-left text-[11px]">
        <thead><tr className="text-ink-500"><th className="pb-1 font-medium">항목</th><th className="pb-1 font-medium">영수증</th><th className="pb-1 font-medium">주문 {r.order.order_id}</th></tr></thead>
        <tbody>
          <Row label="총 결제 금액" receipt={f.total == null ? null : won(f.total)} order={won(r.order.cash_paid)} bad={isBad("total")} />
          <Row label="결제일" receipt={f.paid_on ? dateStr(f.paid_on) : null} order={dateStr(r.order.paid_on)} bad={isBad("date")} />
          <Row label="주문번호" receipt={f.order_no} order={r.order.order_id.toUpperCase()} bad={isBad("order_no")} />
          <Row label="배송비" receipt={f.shipping_fee == null ? null : won(f.shipping_fee)} order={won(r.order.shipping_fee)} bad={isBad("shipping_fee")} />
        </tbody>
      </table>

      {f.items.length > 0 && (
        <div className="rounded-lg border border-ink-100 p-2">
          <p className="mb-1 text-[10px] font-medium text-ink-500">영수증 품목</p>
          <ul className="space-y-0.5 text-[11px]">
            {f.items.map((i, k) => <li key={k} className="flex justify-between gap-2"><span className="truncate">{i.title} × {i.qty}</span><span className="tabular-nums text-ink-700">{i.amount == null ? "–" : won(i.amount)}</span></li>)}
          </ul>
        </div>
      )}

      {r.check.mismatches.length > 0 && (
        <ul className="space-y-1">
          {r.check.mismatches.map((m) => <li key={m} className="rounded-md bg-red-50 px-2 py-1 text-[11px] text-red-700">{RECEIPT_MISMATCH_KO[m] ?? m}</li>)}
        </ul>
      )}
      {unchecked.length > 0 && (
        <p className="rounded-md bg-amber-50 px-2 py-1 text-[11px] leading-relaxed text-amber-800">
          영수증에서 읽지 못해 대조하지 못한 항목: <b>{unchecked.map((k) => RECEIPT_FIELD_KO[k]).join(", ")}</b>
        </p>
      )}
      {r.check.unmatched_receipt_items.length > 0 && <p className="text-[11px] text-ink-700">주문에서 찾지 못한 영수증 품목: {r.check.unmatched_receipt_items.join(", ")}</p>}
      {r.check.missing_from_receipt.length > 0 && <p className="text-[11px] text-ink-700">영수증에 없는 주문 품목: {r.check.missing_from_receipt.join(", ")}</p>}
      {r.pii_masked.length > 0 && <p className="text-[11px] text-violet-700">개인정보를 가린 뒤 읽었습니다: {r.pii_masked.join(", ")}</p>}
      {r.notes.map((n, i) => <p key={i} className="text-[11px] text-ink-500">· {n}</p>)}
      <p className="text-[10px] text-ink-500">주문과 같은지만 알려 줍니다. 환불 여부는 판정하지 않습니다.</p>
    </div>
  );
}

/** 사진마다 나온 결과를 모아 한눈에 보여 준다. 어긋난 영수증이 하나라도 있으면 그걸 먼저 알린다. */
function Summary({ items, running }: { items: Item[]; running: boolean }) {
  const settled = items.filter((i) => i.state === "done" || i.state === "error");
  if (!settled.length) return null;
  const verdicts = items.map((i) => i.result?.check.verdict);
  const mismatch = verdicts.filter((v) => v === "mismatch").length;
  const consistent = verdicts.filter((v) => v === "consistent").length;
  const insufficient = verdicts.filter((v) => v === "insufficient").length;
  const failed = items.filter((i) => i.state === "error").length;
  const waiting = items.length - settled.length;
  const total = items.length;
  let tone: string; let title: string; let sub: string;
  if (mismatch > 0) {
    tone = RECEIPT_VERDICT_TONE.mismatch; title = `${total}장 중 ${mismatch}장이 주문과 다릅니다`; sub = "어긋난 영수증을 열어 확인하고 고객에게 다시 물어보세요.";
  } else if (waiting > 0) {
    tone = "bg-brand-50 text-brand-700"; title = `${settled.length}/${total}장 분석 완료`; sub = running ? "나머지 사진을 분석하는 중입니다." : "분석하지 않은 사진이 있습니다.";
  } else if (consistent === total) {
    tone = RECEIPT_VERDICT_TONE.consistent; title = total === 1 ? "영수증이 주문과 일치합니다" : `영수증 ${total}장이 모두 주문과 일치합니다`; sub = "사진마다 따로 대조한 결과입니다.";
  } else {
    tone = RECEIPT_VERDICT_TONE.insufficient; title = "일부 영수증은 확인하지 못했습니다";
    sub = [consistent ? `일치 ${consistent}장` : "", insufficient ? `확인 불가 ${insufficient}장` : "", failed ? `분석 실패 ${failed}장` : ""].filter(Boolean).join(" · ");
  }
  return (
    <div className={`rounded-xl p-3 ${tone}`} role="status" aria-live="polite">
      <p className="text-[13px] font-semibold">{title}</p>
      <p className="mt-0.5 text-[11px] opacity-90">{sub}</p>
      {total > 1 && (
        <p className="mt-1.5 flex flex-wrap gap-1.5 text-[10px]">
          {consistent > 0 && <span className="chip bg-white/70 text-emerald-700">일치 {consistent}</span>}
          {mismatch > 0 && <span className="chip bg-white/70 text-red-700">다름 {mismatch}</span>}
          {insufficient > 0 && <span className="chip bg-white/70 text-amber-700">확인 불가 {insufficient}</span>}
          {failed > 0 && <span className="chip bg-white/70 text-red-700">실패 {failed}</span>}
        </p>
      )}
    </div>
  );
}

function StateChip({ item }: { item: Item }) {
  if (item.state === "analyzing") return <span className="chip bg-brand-50 text-brand-700"><Spinner />분석 중</span>;
  if (item.state === "error") return <span className="chip bg-red-50 text-red-700">실패</span>;
  if (item.state === "done" && item.result) return <span className={`chip ${RECEIPT_VERDICT_TONE[item.result.check.verdict]}`}>{RECEIPT_VERDICT_KO[item.result.check.verdict]}</span>;
  return <span className="chip bg-ink-100 text-ink-700">대기</span>;
}

export function ReceiptAnalysisCard({ conversationId }: { conversationId: number }) {
  const [items, setItems] = useState<Item[]>(() =>
    // 문의를 떠나는 사이 끝나지 못한 분석은 결과를 받지 못했으니 실패로 보인다
    (STORE.get(conversationId) ?? []).map((i) => i.state === "analyzing"
      ? { ...i, state: "error" as const, error: "분석이 끝나기 전에 문의를 바꿔서 결과를 받지 못했습니다. 사진을 다시 올려 분석하세요." } : i));
  const [open, setOpen] = useState<Set<string>>(() => new Set(items.filter((i) => i.result && i.result.check.verdict !== "consistent").map((i) => i.key)));
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const seq = useRef(0);

  const running = items.some((i) => i.state === "analyzing");
  const pending = items.filter((i) => i.state === "idle" && i.file);
  const retryable = items.filter((i) => i.file && (i.state === "error" || i.state === "done"));

  useEffect(() => { // 이 화면을 연 동안 결과를 기억한다 (사진·미리보기 제외)
    if (items.length) STORE.set(conversationId, keep(items)); else STORE.delete(conversationId);
  }, [conversationId, items]);
  useEffect(() => () => itemsRef.current.forEach((i) => i.url && URL.revokeObjectURL(i.url)), []);
  useEffect(() => {
    if (!running) return;
    const start = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [running]);

  const patch = (key: string, p: Partial<Item>) => setItems((prev) => prev.map((i) => (i.key === key ? { ...i, ...p } : i)));

  const add = (list: FileList | File[]) => {
    const next = [...items];
    let total = next.reduce((n, i) => n + i.size, 0);
    let problem: string | null = null;
    for (const file of Array.from(list)) {
      if (!TYPES.includes(file.type as MediaType)) { problem = `${file.name}: jpeg·png·webp 사진만 올릴 수 있습니다`; continue; }
      if (file.size > MAX_BYTES) { problem = `${file.name}: 사진이 너무 큽니다 (한 장 4.5MB 이하)`; continue; }
      if (next.length >= MAX_FILES) { problem = `사진은 최대 ${MAX_FILES}장까지 올릴 수 있습니다`; break; }
      if (total + file.size > MAX_TOTAL_BYTES) { problem = "사진 크기의 합이 너무 큽니다 (8MB 이하)"; continue; }
      total += file.size;
      next.push({ key: `p${++seq.current}-${file.name}`, name: file.name, size: file.size, state: "idle", file, url: URL.createObjectURL(file) });
    }
    setItems(next);
    setError(problem);
  };

  const remove = (key: string) => {
    const gone = items.find((i) => i.key === key);
    if (gone?.url) URL.revokeObjectURL(gone.url);
    setItems(items.filter((i) => i.key !== key));
    setError(null);
  };

  const clearAll = () => {
    items.forEach((i) => i.url && URL.revokeObjectURL(i.url));
    setItems([]); setOpen(new Set()); setError(null);
  };

  const analyzeOne = async (item: Item) => {
    if (!item.file) return;
    patch(item.key, { state: "analyzing", error: undefined, result: undefined });
    try {
      const image: ReceiptImageInput = { ref: item.name.slice(0, 200), media_type: item.file.type as MediaType, data_base64: await readAsBase64(item.file) };
      // 사진마다 한 번씩 보낸다: 서버는 한 요청 안의 사진을 하나의 영수증으로 합쳐 읽는다
      const out = await api.admin.receiptAnalysis(conversationId, { images: [image] });
      patch(item.key, { state: "done", result: out });
      if (out.check.verdict !== "consistent" || itemsRef.current.length === 1) setOpen((s) => new Set(s).add(item.key));
    } catch (e) {
      patch(item.key, { state: "error", error: e instanceof Error ? e.message : "영수증을 분석하지 못했습니다" });
    }
  };

  const analyze = (list: Item[]) => { setError(null); void runPool(list, CONCURRENCY, analyzeOne); };

  const toggle = (key: string) => setOpen((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n; });

  const onDrop = (e: DragEvent<HTMLElement>) => {
    e.preventDefault();
    setOver(false);
    if (!running && e.dataTransfer.files.length) add(e.dataTransfer.files);
  };

  const doneCount = items.filter((i) => i.state === "done" || i.state === "error").length;

  return (
    <div className="rounded-xl border border-ink-100 bg-surface p-3 text-xs">
      <div className="mb-2 flex items-center gap-2">
        <b>영수증 대조</b>
        <span className="text-ink-500">사진마다 따로 주문과 맞춰 봅니다</span>
      </div>

      <div className="space-y-2.5">
        {!running && (
          <label
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}
            className={`flex cursor-pointer flex-col items-center gap-1 rounded-xl border-2 border-dashed px-3 text-center transition-colors focus-within:ring-2 focus-within:ring-brand-500 ${items.length ? "py-2.5" : "py-4"} ${over ? "border-brand-500 bg-brand-50" : "border-ink-300 hover:bg-ink-100"}`}>
            {!items.length && <span className="text-lg" aria-hidden>🧾</span>}
            <span className="font-medium">영수증 사진을 끌어다 놓거나 눌러서 고르세요</span>
            <span className="text-[10px] text-ink-500">jpeg·png·webp · 한 장 4.5MB 이하 · 최대 {MAX_FILES}장 · 사진 한 장이 영수증 한 장입니다</span>
            <input type="file" multiple accept={TYPES.join(",")} aria-label="영수증 사진 선택" className="sr-only"
              onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
          </label>
        )}

        {running && (
          <div className="flex items-center gap-2 rounded-xl bg-brand-50 px-3 py-2 text-brand-700" role="status" aria-live="polite" aria-busy="true">
            <Spinner />
            <b className="text-[13px]">영수증을 분석하는 중…</b>
            <span className="ml-auto tabular-nums text-[11px]">{doneCount}/{items.length}장 · {elapsed}초</span>
          </div>
        )}

        <Summary items={items} running={running} />

        {items.length > 0 && (
          <ul className="space-y-1.5" aria-label="올린 영수증 사진">
            {items.map((item) => {
              const expanded = open.has(item.key) && item.state === "done" && item.result;
              return (
                <li key={item.key} className="overflow-hidden rounded-lg border border-ink-100">
                  <div className="flex items-center gap-2 p-1.5">
                    <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" aria-expanded={!!expanded}
                      disabled={item.state !== "done"} onClick={() => toggle(item.key)}>
                      {item.url
                        ? <img src={item.url} alt="" className={`h-10 w-10 shrink-0 rounded-md border border-ink-100 object-cover ${item.state === "analyzing" ? "opacity-50" : ""}`} />
                        : <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-ink-100 text-base" aria-hidden>🧾</span>}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">{item.name}</span>
                        <span className="block text-[10px] text-ink-500">{kb(item.size)}{item.state === "done" ? (expanded ? " · 눌러서 접기" : " · 눌러서 자세히") : ""}</span>
                      </span>
                    </button>
                    <StateChip item={item} />
                    {item.state !== "analyzing" && item.file && (item.state === "error" || item.state === "done") &&
                      <button type="button" className="btn-quiet px-1.5 py-0.5 text-[11px]" onClick={() => void analyzeOne(item)}>다시</button>}
                    {item.state !== "analyzing" &&
                      <button type="button" aria-label={`${item.name} 빼기`} className="btn-quiet px-1.5 py-0.5 text-[11px]" onClick={() => remove(item.key)}>✕</button>}
                  </div>
                  {item.state === "error" && item.error && <p role="alert" className="border-t border-red-100 bg-red-50 px-2 py-1.5 text-[11px] leading-relaxed text-red-700">{item.error}</p>}
                  {expanded && item.result && <div className="border-t border-ink-100 p-2.5"><Result r={item.result} /></div>}
                </li>
              );
            })}
          </ul>
        )}

        <div className="flex items-center gap-1.5">
          <span className="text-[11px] text-ink-500">{items.length ? `${items.length}장` : "선택한 사진이 없습니다"}</span>
          <div className="ml-auto flex gap-1.5">
            {items.length > 0 && !running && <button className="btn-quiet px-2 py-1 text-xs" onClick={clearAll}>모두 지우기</button>}
            {!running && !pending.length && retryable.length > 1 && <button className="btn-ghost px-2.5 py-1 text-xs" onClick={() => analyze(retryable)}>전체 다시 분석</button>}
            <button className="btn-primary px-3 py-1 text-xs" disabled={!pending.length || running} onClick={() => analyze(pending)}>
              {pending.length > 1 ? `${pending.length}장 분석하기` : "분석하기"}
            </button>
          </div>
        </div>
      </div>

      {error && <p role="alert" className="mt-2 rounded-md bg-red-50 px-2 py-1.5 text-[11px] leading-relaxed text-red-700">{error}</p>}
      <p className="mt-2 text-[10px] text-ink-500">사진마다 따로 분석해서, 서로 다른 영수증도 하나씩 대조합니다. 환불 여부는 판정하지 않습니다.</p>
    </div>
  );
}
