import { useState } from "react";
import { api } from "@/lib/api";
import { dateStr, won } from "@/lib/format";
import { RECEIPT_FIELD_KO, RECEIPT_IMAGES_KO, RECEIPT_MISMATCH_KO, RECEIPT_VERDICT_KO, RECEIPT_VERDICT_TONE } from "@/lib/labels";
import type { ReceiptAnalysis, ReceiptImageInput } from "@/lib/types";

const MAX_BYTES = 4_500_000; // support가 받는 한 장 크기(약 4.5MB)
const TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
type MediaType = (typeof TYPES)[number];
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

function Row({ label, receipt, order, bad }: { label: string; receipt: string | null; order: string; bad: boolean }) {
  return (
    <tr className={`border-t border-ink-100 ${bad ? "bg-red-50" : ""}`}>
      <td className="py-1.5 pr-2 text-ink-500">{label}</td>
      <td className={`py-1.5 pr-2 tabular-nums ${receipt == null ? "text-ink-500" : ""}`}>{receipt ?? "읽지 못함"}</td>
      <td className="py-1.5 tabular-nums">{order}</td>
    </tr>
  );
}

function Result({ r }: { r: ReceiptAnalysis }) {
  const bad = new Set(r.check.mismatches);
  const isBad = (field: string) => bad.has(MISMATCH_OF[field]);
  const unchecked = Object.keys(RECEIPT_FIELD_KO).filter((f) => !r.check.checked.includes(f));
  const f = r.facts;
  return (
    <div className="space-y-2.5">
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
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ReceiptAnalysis | null>(null);

  const pick = (f: File | null) => {
    setResult(null); setError(null);
    if (f && !TYPES.includes(f.type as MediaType)) { setFile(null); setError("jpeg·png·webp 사진만 올릴 수 있습니다"); return; }
    if (f && f.size > MAX_BYTES) { setFile(null); setError("사진이 너무 큽니다 (4.5MB 이하)"); return; }
    setFile(f);
  };

  const analyze = async () => {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const image: ReceiptImageInput = { ref: file.name.slice(0, 200), media_type: file.type as MediaType, data_base64: await readAsBase64(file) };
      setResult(await api.admin.receiptAnalysis(conversationId, { images: [image] }));
    } catch (e) {
      setResult(null); setError(e instanceof Error ? e.message : "영수증을 분석하지 못했습니다");
    } finally { setBusy(false); }
  };

  return (
    <div className="rounded-xl border border-ink-100 bg-surface p-3 text-xs">
      <div className="mb-2 flex items-center gap-2">
        <b>영수증 대조</b>
        <span className="text-ink-500">고객이 보낸 영수증 사진을 주문과 맞춰 봅니다</span>
      </div>
      <div className="flex items-center gap-2">
        <input type="file" accept={TYPES.join(",")} aria-label="영수증 사진 선택" className="min-w-0 flex-1 text-[11px] file:mr-2 file:rounded-md file:border-0 file:bg-ink-100 file:px-2 file:py-1 file:text-[11px]"
          onChange={(e) => pick(e.target.files?.[0] ?? null)} />
        <button className="btn-primary px-2.5 py-1 text-xs" disabled={!file || busy} onClick={analyze}>{busy ? "분석 중…" : "분석"}</button>
      </div>
      {error && <p role="alert" className="mt-2 rounded-md bg-red-50 px-2 py-1 text-[11px] text-red-700">{error}</p>}
      {result && <div className="mt-3 border-t border-ink-100 pt-3"><Result r={result} /></div>}
    </div>
  );
}
