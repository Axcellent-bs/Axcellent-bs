import { useEffect, useRef, useState, type DragEvent } from "react";
import { api } from "@/lib/api";
import { dateStr, won } from "@/lib/format";
import { RECEIPT_FIELD_KO, RECEIPT_IMAGES_KO, RECEIPT_MISMATCH_KO, RECEIPT_VERDICT_KO, RECEIPT_VERDICT_TONE } from "@/lib/labels";
import type { ReceiptAnalysis, ReceiptImageInput } from "@/lib/types";

const MAX_FILES = 4; // support가 읽는 최대 장수
const MAX_BYTES = 4_500_000; // 한 장 크기(약 4.5MB)
const MAX_TOTAL_BYTES = 8_000_000; // 한 번에 보내는 전체 크기 (support의 base64 합계 제한 안쪽)
const TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
type MediaType = (typeof TYPES)[number];

// 분석 결과는 문의를 오갈 때 다시 보이도록 이 화면을 연 동안만 기억한다. 사진은 기억하지 않는다.
const RESULTS = new Map<number, ReceiptAnalysis>();

// 대조하는 값 → 어긋났을 때 support가 돌려주는 코드
const MISMATCH_OF: Record<string, string> = {
  total: "RECEIPT_AMOUNT_MISMATCH", date: "RECEIPT_DATE_MISMATCH", order_no: "RECEIPT_ORDER_NO_MISMATCH", shipping_fee: "RECEIPT_SHIPPING_MISMATCH",
};

interface Picked { key: string; file: File; url: string }

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(new Error("사진을 읽지 못했습니다"));
    reader.readAsDataURL(file);
  });
}

const kb = (n: number) => (n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1000))}KB`);

function Thumbs({ items, onRemove, dim }: { items: Picked[]; onRemove?: (key: string) => void; dim?: boolean }) {
  if (!items.length) return null;
  return (
    <ul className="grid grid-cols-4 gap-1.5" aria-label="올린 영수증 사진">
      {items.map((p) => (
        <li key={p.key} className="group relative aspect-square overflow-hidden rounded-lg border border-ink-100 bg-ink-100">
          <img src={p.url} alt={p.file.name} className={`h-full w-full object-cover ${dim ? "opacity-50" : ""}`} />
          <span className="absolute inset-x-0 bottom-0 truncate bg-black/55 px-1 py-0.5 text-[9px] text-white">{kb(p.file.size)}</span>
          {onRemove && (
            <button type="button" aria-label={`${p.file.name} 빼기`} onClick={() => onRemove(p.key)}
              className="absolute right-0.5 top-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-black/60 text-[11px] text-white hover:bg-black/80">✕</button>
          )}
        </li>
      ))}
    </ul>
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

export function ReceiptAnalysisCard({ conversationId }: { conversationId: number }) {
  const [picked, setPicked] = useState<Picked[]>([]);
  const [phase, setPhase] = useState<"idle" | "analyzing" | "done">(RESULTS.has(conversationId) ? "done" : "idle");
  const [result, setResult] = useState<ReceiptAnalysis | null>(RESULTS.get(conversationId) ?? null);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const pickedRef = useRef(picked);
  pickedRef.current = picked;

  useEffect(() => () => pickedRef.current.forEach((p) => URL.revokeObjectURL(p.url)), []);
  useEffect(() => {
    if (phase !== "analyzing") return;
    const start = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [phase]);

  const add = (list: FileList | File[]) => {
    const next = [...picked];
    let total = next.reduce((n, p) => n + p.file.size, 0);
    let problem: string | null = null;
    for (const file of Array.from(list)) {
      if (!TYPES.includes(file.type as MediaType)) { problem = `${file.name}: jpeg·png·webp 사진만 올릴 수 있습니다`; continue; }
      if (file.size > MAX_BYTES) { problem = `${file.name}: 사진이 너무 큽니다 (한 장 4.5MB 이하)`; continue; }
      if (next.length >= MAX_FILES) { problem = `사진은 최대 ${MAX_FILES}장까지 올릴 수 있습니다`; break; }
      if (total + file.size > MAX_TOTAL_BYTES) { problem = "사진 크기의 합이 너무 큽니다 (8MB 이하)"; continue; }
      total += file.size;
      next.push({ key: `${file.name}-${file.size}-${file.lastModified}-${next.length}`, file, url: URL.createObjectURL(file) });
    }
    setPicked(next);
    setError(problem);
    if (phase === "done") { setPhase("idle"); setResult(null); RESULTS.delete(conversationId); }
  };

  const remove = (key: string) => {
    const gone = picked.find((p) => p.key === key);
    if (gone) URL.revokeObjectURL(gone.url);
    setPicked(picked.filter((p) => p.key !== key));
    setError(null);
  };

  const reset = () => {
    picked.forEach((p) => URL.revokeObjectURL(p.url));
    setPicked([]); setResult(null); setError(null); setPhase("idle");
    RESULTS.delete(conversationId);
  };

  const analyze = async () => {
    if (!picked.length) return;
    setError(null);
    setPhase("analyzing");
    try {
      const images: ReceiptImageInput[] = await Promise.all(
        picked.map(async (p) => ({ ref: p.file.name.slice(0, 200), media_type: p.file.type as MediaType, data_base64: await readAsBase64(p.file) })),
      );
      const out = await api.admin.receiptAnalysis(conversationId, { images });
      RESULTS.set(conversationId, out);
      setResult(out);
      setPhase("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : "영수증을 분석하지 못했습니다");
      setPhase("idle");
    }
  };

  const onDrop = (e: DragEvent<HTMLElement>) => {
    e.preventDefault();
    setOver(false);
    if (phase !== "analyzing" && e.dataTransfer.files.length) add(e.dataTransfer.files);
  };

  return (
    <div className="rounded-xl border border-ink-100 bg-surface p-3 text-xs">
      <div className="mb-2 flex items-center gap-2">
        <b>영수증 대조</b>
        <span className="text-ink-500">고객이 보낸 영수증 사진을 주문과 맞춰 봅니다</span>
      </div>

      {phase === "idle" && (
        <div className="space-y-2">
          <label
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}
            className={`flex cursor-pointer flex-col items-center gap-1 rounded-xl border-2 border-dashed px-3 py-4 text-center transition-colors focus-within:ring-2 focus-within:ring-brand-500 ${over ? "border-brand-500 bg-brand-50" : "border-ink-300 hover:bg-ink-100"}`}>
            <span className="text-lg" aria-hidden>🧾</span>
            <span className="font-medium">영수증 사진을 끌어다 놓거나 눌러서 고르세요</span>
            <span className="text-[10px] text-ink-500">jpeg·png·webp · 한 장 4.5MB 이하 · 최대 {MAX_FILES}장</span>
            <input type="file" multiple accept={TYPES.join(",")} aria-label="영수증 사진 선택" className="sr-only"
              onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
          </label>
          <Thumbs items={picked} onRemove={remove} />
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-ink-500">{picked.length ? `${picked.length}장 선택` : "선택한 사진이 없습니다"}</span>
            <button className="btn-primary ml-auto px-3 py-1 text-xs" disabled={!picked.length} onClick={analyze}>분석하기</button>
          </div>
        </div>
      )}

      {phase === "analyzing" && (
        <div className="space-y-2.5" role="status" aria-live="polite" aria-busy="true">
          <Thumbs items={picked} dim />
          <div className="rounded-xl bg-brand-50 p-3 text-brand-700">
            <div className="flex items-center gap-2">
              <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden>
                <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
                <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
              </svg>
              <b className="text-[13px]">영수증을 분석하는 중…</b>
              <span className="ml-auto tabular-nums text-[11px]">{elapsed}초</span>
            </div>
            <p className="mt-1 text-[11px] opacity-90">사진에서 금액·결제일·주문번호를 읽고 주문과 맞춰 보고 있습니다. 보통 몇 초에서 십여 초 걸립니다.</p>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-brand-100"><div className="h-full w-1/3 animate-pulse rounded-full bg-brand-500" /></div>
          </div>
        </div>
      )}

      {phase === "done" && result && (
        <div className="space-y-3">
          <Thumbs items={picked} />
          <Result r={result} />
          <div className="flex gap-1.5 border-t border-ink-100 pt-2.5">
            {picked.length > 0 && <button className="btn-ghost px-2.5 py-1 text-xs" onClick={analyze}>다시 분석</button>}
            <button className="btn-quiet ml-auto px-2.5 py-1 text-xs" onClick={reset}>다른 영수증 올리기</button>
          </div>
        </div>
      )}

      {error && <p role="alert" className="mt-2 rounded-md bg-red-50 px-2 py-1.5 text-[11px] leading-relaxed text-red-700">{error}</p>}
    </div>
  );
}
