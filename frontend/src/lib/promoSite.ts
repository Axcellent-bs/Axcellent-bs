import { useCallback, useEffect, useState } from "react";
import { failureError } from "./apiError";
import { DASHBOARD_API_URL, dashboardAuth } from "./env";
import { promoErrorKo, promoPending, type PromoStatus } from "./promoSiteView";

// 마케팅 영상을 AXcellent 홍보 사이트에 올리기(2026-11-26). 사람이 고른 영상만, 최대 3개.
// - GET /marketing/promo-site → 올린 영상과 남은 자리(읽을 때 서버가 승인 대기를 다시 확인한다)
// - POST /marketing/promo-site/{item_id} {title, customer, rights_confirmed} → 사람 승인(C publish_promo_video)
// - DELETE /marketing/promo-site/{item_id} → 내린다(승인 대기 중이면 취소)
// item_id는 제작 기록 번호(CR-…) 또는 의뢰 영상 초안 번호다.

export * from "./promoSiteView";

const PATH = `${DASHBOARD_API_URL}/api/dashboard/marketing/promo-site`;
const POLL_MS = 5_000;

async function call<T>(url: string, init: RequestInit, fallback: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store", ...init });
  const parsed: unknown = await res.json().catch(() => undefined);
  if (!res.ok) {
    const error = failureError(parsed, res.status, fallback);
    throw new Error(promoErrorKo(error.message) ?? fallback);
  }
  return (parsed as { data: T }).data;
}

export const getPromoSite = () => call<PromoStatus>(PATH, { headers: { ...dashboardAuth() } }, "홍보 사이트 상태를 읽지 못했어요");

export const publishPromoSite = (itemId: string, title: string, customer: string) =>
  call<unknown>(`${PATH}/${encodeURIComponent(itemId)}`, {
    method: "POST",
    headers: { ...dashboardAuth(), "Content-Type": "application/json" },
    body: JSON.stringify({ title, customer, rights_confirmed: true }),
  }, "홍보 사이트 올리기를 요청하지 못했어요");

export const removePromoSite = (itemId: string) =>
  call<unknown>(`${PATH}/${encodeURIComponent(itemId)}`, { method: "DELETE", headers: { ...dashboardAuth() } }, "홍보 사이트에서 내리지 못했어요");

/** 홍보 사이트 상태. 승인·올리기를 기다리는 항목이 있으면 5초마다 다시 읽는다.
 *  읽지 못하면(옛 서버 포함) status는 null이고 칸·칩을 보이지 않는다 */
export function usePromoSite() {
  const [status, setStatus] = useState<PromoStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const next = await getPromoSite();
      setStatus(Array.isArray(next?.items) ? next : null);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "홍보 사이트 상태를 읽지 못했어요");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const pending = promoPending(status);
  useEffect(() => {
    if (!pending) return;
    const t = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(t);
  }, [pending, load]);
  return { status, error, load };
}
