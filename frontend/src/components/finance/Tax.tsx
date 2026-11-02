import { useState } from "react";
import { finance, krw } from "@/lib/finance";
import { Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// ⑥ 세무 지원: 부가세 신고 보조 집계와 판매자별 판매 명세(부가가치세법 제75조 초안).
// 재무 AI는 신고·제출을 하지 않는다. 세무 담당이 홈택스에서 신고할 때 쓸 숫자와 파일만 만든다.
// 세무사 확인 전인 설정(수수료 부가세 별도/포함, 쿠폰 공제 구조, 배송비 공급자)은 화면에 그대로 드러낸다.

export function TaxSection({ tick }: { tick: number }) {
  const vat = useResource("tax-vat", () => finance.vat(), tick);
  const report = useResource("tax-seller-report", () => finance.sellerReport(), tick);
  const [problem, setProblem] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  async function download() {
    setDownloading(true);
    setProblem(null);
    try { await finance.downloadSellerReport(); } catch (e) { setProblem((e as Error).message); } finally { setDownloading(false); }
  }

  return (
    <div className="space-y-4">
      <Panel id="tax-vat" title="부가세 신고 보조 집계" meta={vat.env?.meta}
        hint="캣북스의 과세 매출은 판매 수수료(중개 용역)입니다. 판매자가 파는 도서(면세)·문구(과세)는 판매자 매출이라 캣북스 신고에 들어가지 않습니다. 신고서가 아니라 신고할 때 쓸 숫자입니다.">
        <ResourceBody res={vat}>
          {(p) => (
            <div className="space-y-4">
              <p className="text-sm text-ink-700">
                <b className="text-ink-900">{p.quarter}</b> ({p.from} ~ {p.to}) · {p.filing.kind} 기한 <b>{p.filing.due}</b>
              </p>
              <div className="grid gap-4 lg:grid-cols-2">
                <VatTable title="매출 (과세)" rows={p.sales} />
                <VatTable title="매입 (공제할 세액)" rows={p.purchases} />
              </div>
              <dl className="grid grid-cols-3 gap-3">
                {([["매출세액", p.output_vat], ["매입세액", p.input_vat], ["낼 세액 (참고)", p.payable]] as const).map(([label, v]) => (
                  <div key={label} className="rounded-2xl bg-surface-2 px-3 py-2">
                    <dt className="text-[11px] text-ink-500">{label}</dt>
                    <dd className="text-base font-semibold tabular-nums">{krw(v)}</dd>
                  </div>
                ))}
              </dl>
              <div className="rounded-2xl border border-ink-100 px-4 py-3 text-sm">
                <h3 className="font-semibold text-ink-900">회사 부담 쿠폰과 수수료 과세표준</h3>
                <p className="mt-1 text-ink-700">
                  쿠폰 {krw(p.coupon.company_coupon)} · 수수료 {krw(p.coupon.commission_before_coupon)} → 쿠폰 공제 후 {krw(p.coupon.commission_after_coupon)}
                  {p.coupon.coupon_over_commission ? ` (수수료를 넘는 쿠폰 ${krw(p.coupon.coupon_over_commission)}은 공제할 수 없음)` : ""}
                </p>
                <p className="mt-1 text-xs text-ink-500">
                  지금 설정: {p.coupon.reduces_base ? "쿠폰을 수수료에서 공제(에누리)" : "공제하지 않음"}. 약관상 쿠폰 할인액을 판매자 수수료에서 직접 빼는 구조면 에누리로 과세표준에서 뺍니다(대법원 2014두298).
                </p>
              </div>
              {p.seller_sales && (
                <p className="text-xs text-ink-500">참고 · 판매자 상품 매출(캣북스 매출 아님): 면세(도서) {krw(p.seller_sales.exempt)}, 과세 {krw(p.seller_sales.taxable)}</p>
              )}
              <div className="rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-900">
                <b>세무사에게 확인할 것</b>
                <ul className="mt-1 list-disc space-y-0.5 pl-5">{p.checks.map((c) => <li key={c}>{c}</li>)}</ul>
              </div>
              <p className="text-xs text-ink-500">{p.basis}</p>
            </div>
          )}
        </ResourceBody>
      </Panel>

      <Panel id="tax-seller-report" title="판매자별 판매 명세 (부가가치세법 제75조)" meta={report.env?.meta}
        tools={<button type="button" className="btn-primary" disabled={downloading} onClick={download}>{downloading ? "내려받는 중…" : "CSV 내려받기"}</button>}
        hint="통신판매중개자는 판매자별 판매·중개 명세를 분기 말일의 다음 달 15일까지 국세청에 냅니다. 재무 AI는 초안 파일만 만들고 제출은 사람이 합니다.">
        {problem && <p role="alert" className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{problem}</p>}
        <ResourceBody res={report}>
          {(r) => (
            <div className="space-y-3">
              <p className="text-sm text-ink-700"><b className="text-ink-900">{r.quarter}</b> · 제출 기한 <b>{r.due}</b></p>
              {r.missing.includes("business_no") && (
                <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">판매 사이트에 판매자 사업자등록번호가 없어 그 칸은 "자료 없음"입니다. 제출 전에 판매자 정보에서 채워야 합니다.</p>
              )}
              {r.rows.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[640px] text-sm">
                    <thead className="text-left text-xs text-ink-500">
                      <tr><th className="pb-1 font-medium">판매자</th><th className="pb-1 font-medium">사업자번호</th><th className="pb-1 font-medium">월</th><th className="pb-1 text-right font-medium">판매금액</th><th className="pb-1 font-medium">결제수단</th><th className="pb-1 font-medium">면세·과세</th></tr>
                    </thead>
                    <tbody>
                      {r.rows.map((x) => (
                        <tr key={`${x.seller_id}-${x.month}`} className="border-t border-ink-100">
                          <td className="py-1.5">{x.name}</td>
                          <td className="py-1.5">{x.business_no ?? <Chip label="자료 없음" tone={TONE.warn} />}</td>
                          <td className="py-1.5 tabular-nums">{x.month}</td>
                          <td className="py-1.5 text-right tabular-nums">{krw(x.total)}</td>
                          <td className="py-1.5 text-xs text-ink-700">{Object.entries(x.by_method).map(([k, v]) => `${k} ${krw(v)}`).join(", ")}</td>
                          <td className="py-1.5 text-xs text-ink-700">{Object.entries(x.by_tax).map(([k, v]) => `${k} ${krw(v)}`).join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <Empty>이번 분기 판매가 없습니다.</Empty>}
              <p className="text-xs text-ink-500">{r.basis}</p>
            </div>
          )}
        </ResourceBody>
      </Panel>
    </div>
  );
}

function VatTable({ title, rows }: { title: string; rows: { code: string; label: string; supply: number; vat: number }[] }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-ink-900">{title}</h3>
      {rows.length ? (
        <table className="mt-2 w-full text-sm">
          <thead className="text-left text-xs text-ink-500"><tr><th className="pb-1 font-medium">항목</th><th className="pb-1 text-right font-medium">공급가액</th><th className="pb-1 text-right font-medium">세액</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.code} className="border-t border-ink-100">
                <td className="py-1.5">{r.label}</td>
                <td className="py-1.5 text-right tabular-nums">{krw(r.supply)}</td>
                <td className="py-1.5 text-right tabular-nums">{krw(r.vat)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="mt-2 text-sm text-ink-500">확인할 수 없음 (주문 자료를 받지 못함)</p>}
    </div>
  );
}
