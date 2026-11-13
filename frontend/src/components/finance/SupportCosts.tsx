import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/apiError";
import { Link } from "@/lib/nav";
import { shortDateTime } from "@/lib/format";
import { finance, krw, type SupportCostRecord } from "@/lib/finance";
import { Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// 고객지원 비용 기록: 고객지원이 문의를 닫으면 총괄이 재무 AI에 넘기고, 재무 AI가 끝난 환불 금액·쿠폰을 받아 주문 배송비와 함께 장부에 적은 기록.
// 읽기 전용이다. 경비를 쓰지 않은 문의(답변만 한 문의)도 처리 결과는 기록으로 남는다.

const RESOLUTION_KO: Record<string, string> = {
  answered: "답변으로 해결", claim_decided: "클레임 처리", compensated: "보상 지급", escalated: "다른 부서로 이관",
};
const DECISION_KO: Record<string, string> = { approve: "승인", reject: "거절" };

// 고객지원 문의 번호는 고객지원 문의 DB를 새로 시작하면 다시 1부터 매겨진다(11/13 확인: 예전 기록의 #5가
// 지금은 다른 고객의 문의). 그래서 링크를 걸기 전에 지금 그 번호 문의의 주문이 기록의 주문과 같은지 본다.
type InquiryMatch = "checking" | "same" | "other" | "gone" | "unknown";

function useInquiryMatch(inquiryId: number, orderId: string | null): InquiryMatch {
  const [match, setMatch] = useState<InquiryMatch>(orderId ? "checking" : "unknown");
  useEffect(() => {
    if (!orderId) return;
    let live = true;
    api.admin.get(inquiryId)
      .then((c) => live && setMatch(String(c.order_id ?? "") === orderId ? "same" : "other"))
      .catch((e) => live && setMatch(e instanceof ApiError && e.status === 404 ? "gone" : "unknown"));
    return () => { live = false; };
  }, [inquiryId, orderId]);
  return match;
}

function InquiryLink({ r }: { r: SupportCostRecord }) {
  const match = useInquiryMatch(r.inquiry_id, r.order_id);
  const cls = "ml-3 whitespace-nowrap text-sm";
  if (match === "other" || match === "gone") {
    const why = match === "other"
      ? `지금 고객지원의 문의 #${r.inquiry_id}는 다른 주문의 문의입니다. 고객지원 문의 기록이 새로 시작되기 전 기록이라 같은 번호로 찾을 수 없어요.`
      : `고객지원에 문의 #${r.inquiry_id}가 없습니다.`;
    return <span className={`${cls} text-ink-500`} title={why}>고객지원 문의 없음</span>;
  }
  if (match === "checking") return <span className={`${cls} text-ink-500`}>문의 확인 중…</span>;
  // same·unknown(주문 없음·확인 실패): 지금처럼 문의 진단으로 보낸다
  return <Link href={`/support?inquiry=${r.inquiry_id}#inquiry`} className={`${cls} font-medium text-brand-700 underline`}>고객지원 문의 →</Link>;
}

function Row({ r }: { r: SupportCostRecord }) {
  const spent = r.expense_used
    ? [r.refund_amount > 0 ? `환불 ${krw(r.refund_amount)}` : null, r.coupon_issued ? "쿠폰 지급" : null].filter(Boolean).join(" · ")
    : "경비 사용 없음";
  return (
    <tr className="border-t border-ink-100 align-top">
      <td className="py-2 pr-3 font-medium text-ink-900">#{r.inquiry_id}</td>
      <td className="py-2 pr-3 text-ink-700">
        {RESOLUTION_KO[r.resolution] ?? r.resolution}
        {r.claim_decision && <span className="text-ink-500"> ({DECISION_KO[r.claim_decision] ?? r.claim_decision})</span>}
      </td>
      <td className="py-2 pr-3 text-ink-700">{r.order_id ? <Link href={`/orders?q=${encodeURIComponent(r.order_id)}`} className="font-medium text-brand-700 underline">{r.order_id}</Link> : "–"}</td>
      <td className="py-2 pr-3 tabular-nums text-ink-700">{r.shipping_fee == null ? <span className="text-ink-500">확인할 수 없음</span> : krw(r.shipping_fee)}</td>
      <td className="py-2 pr-3"><Chip label={spent} tone={r.expense_used ? TONE.warn : TONE.muted} /></td>
      <td className="py-2 pr-3 whitespace-nowrap text-ink-500">{shortDateTime(r.recorded_at)}</td>
      <td className="py-2">
        <Link href={`/orchestrator?ctx=${encodeURIComponent(r.context_id)}`} className="whitespace-nowrap text-sm font-medium text-brand-700 underline">총괄 이슈 →</Link>
        <InquiryLink r={r} />
      </td>
    </tr>
  );
}

export function SupportCostsSection({ tick }: { tick: number }) {
  const res = useResource("support-costs", () => finance.supportCosts(), tick);
  return (
    <Panel id="support-costs" title="고객지원 비용 기록" meta={res.env?.meta}
      hint="고객지원이 문의를 해결 종료하면 총괄 AI가 재무 AI에 넘기고, 재무 AI가 고객지원이 끝낸 환불 금액과 쿠폰을 받아 주문 배송비와 함께 장부에 적은 기록입니다. 환불 여부는 고객지원이 정하고 재무는 확인하지 않습니다. 이 화면은 읽기 전용이며 돈을 움직이지 않습니다.">
      <ResourceBody res={res}>
        {(rows) => rows.length === 0 ? (
          <Empty>아직 기록이 없습니다. 고객지원에서 문의를 해결 종료하면 여기에 쌓입니다.</Empty>
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="text-sm text-ink-500">
                <tr><th className="pb-1 pr-3 font-medium">문의</th><th className="pb-1 pr-3 font-medium">처리 결과</th><th className="pb-1 pr-3 font-medium">주문</th><th className="pb-1 pr-3 font-medium">배송비</th><th className="pb-1 pr-3 font-medium">경비</th><th className="pb-1 pr-3 font-medium">기록 시각</th><th /></tr>
              </thead>
              <tbody>{rows.map((r) => <Row key={r.record_id} r={r} />)}</tbody>
            </table>
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}
