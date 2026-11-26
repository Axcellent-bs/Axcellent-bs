import { useState } from "react";
import { Link } from "react-router";
import { CUSTOMER_MAX, TITLE_MAX, defaultPromoTitle, isHolding, promoErrorKo, promoItem, publishBlock, publishPromoSite, removePromoSite, type PromoStatus } from "@/lib/promoSite";

// 영상 한 건 → AXcellent 홍보 사이트 (제작 기록 상세 4단계 '올리기', 2026-11-26).
// 제목·고객사 이름을 확인하고 권리 확인에 체크한 뒤 [올리기] → 사람 승인(안전 처리 로그) → 승인되면 사이트에 보인다.
// 로그인 없이 누구나 보는 곳이라 사람이 고른 영상만, 회사 전체에서 최대 3개다.

export function PromoSitePanel({ itemId, title, label, status, onChanged }: {
  itemId: string;
  /** 제목 칸의 처음 값 */
  title: string;
  /** 어느 영상인지(영상이 여럿일 때) */
  label?: string;
  status: PromoStatus;
  onChanged: () => void;
}) {
  const [name, setName] = useState(defaultPromoTitle(title));
  const [customer, setCustomer] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!status.available) return null;
  const item = promoItem(status, itemId);
  const holding = isHolding(item);
  const block = publishBlock(status);

  const run = async (act: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await act();
      setConfirmed(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "요청을 보내지 못했어요");
    } finally {
      setBusy(false);
      onChanged();
    }
  };
  const remove = (text: string) => (
    <button type="button" className="btn-ghost text-xs" disabled={busy} onClick={() => void run(() => removePromoSite(itemId))}>{text}</button>
  );

  return (
    <section className="space-y-2 border-t border-ink-100 pt-3" aria-label="AXcellent 홍보 사이트">
      <p className="text-xs font-medium text-ink-700">
        AXcellent 홍보 사이트{label ? ` · ${label}` : ""}
        <span className="font-normal text-ink-500"> · 로그인 없이 누구나 봐요 · {status.used}/{status.limit}개 사용 중</span>
      </p>
      {holding && (
        <p className="text-sm text-ink-900">{item?.title} <span className="text-xs text-ink-500">({item?.customer})</span></p>
      )}
      {item?.status === "published" && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="chip bg-emerald-50 text-emerald-800">올라가 있어요</span>
          {remove("내리기")}
        </div>
      )}
      {item?.status === "awaiting_approval" && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-xs text-ink-700">사람 승인이 필요해요. <Link to="/safety" className="text-brand-700 underline">안전 처리 로그</Link>에서 승인한 뒤 이 화면을 열면 사이트에 올라가요.</p>
          {remove("요청 취소")}
        </div>
      )}
      {item?.status === "approved" && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-xs text-ink-700">승인됐어요. 사이트에 올리는 중이에요{item.error ? ` (${promoErrorKo(item.error)}, 잠시 뒤 다시 해요)` : ""}.</p>
          {remove("내리기")}
        </div>
      )}
      {(item?.status === "rejected" || item?.status === "failed") && (
        <p className="text-xs text-accent">{promoErrorKo(item.error) ?? "올리지 못했어요"}</p>
      )}
      {!holding && (block ? <p className="text-xs text-ink-500">{block}</p> : (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <label className="flex min-w-[16rem] flex-1 items-center gap-2 whitespace-nowrap text-xs text-ink-700">사이트에 보일 제목
              <input className="field py-1 text-xs" maxLength={TITLE_MAX} value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="flex items-center gap-2 whitespace-nowrap text-xs text-ink-700">고객사 이름
              <input className="field w-32 py-1 text-xs" maxLength={CUSTOMER_MAX} placeholder="예: 캣북스" value={customer} onChange={(e) => setCustomer(e.target.value)} />
            </label>
          </div>
          <label className="flex items-start gap-2 text-xs text-ink-700">
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
            <span>공개해도 되는 영상이에요(인물·음원·상표를 쓸 권리를 확인했어요). AI 표시를 입힌 영상이 올라가고, 제작 기록의 이미지 시트·스토리보드 그림이 있으면 같이 보여요.</span>
          </label>
          <button type="button" className="btn-primary text-xs" disabled={!confirmed || !name.trim() || !customer.trim() || busy}
            onClick={() => void run(() => publishPromoSite(itemId, name.trim(), customer.trim()))}>
            {busy ? "AI 표시 입히고 요청 중…" : "홍보 사이트에 올리기 (사람 승인)"}
          </button>
        </div>
      ))}
      {error && <p className="text-xs text-accent" role="alert">{error}</p>}
    </section>
  );
}
