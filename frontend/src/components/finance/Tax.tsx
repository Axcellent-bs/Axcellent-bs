import { useState } from "react";
import { finance, humanTokenSet, krw } from "@/lib/finance";
import { shortDateTime } from "@/lib/format";
import { AppliedRules, Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// ⑤ 세무 지원: 부가세 신고 보조 집계와 판매자별 판매 명세(부가가치세법 제75조 초안).
// 재무 AI는 신고·제출을 하지 않는다. 세무 담당이 홈택스에서 신고할 때 쓸 숫자와 파일만 만든다.
// 세무사 확인 전인 설정(수수료 부가세 별도/포함, 쿠폰 공제 구조, 배송비 공급자)은 화면에 그대로 드러낸다.

export function TaxSection({ tick }: { tick: number }) {
  const [quarterDate, setQuarterDate] = useState<string | null>(null);
  const [own, setOwn] = useState(0);
  const vat = useResource(`tax-vat-${quarterDate ?? "now"}`, () => finance.vat(quarterDate), tick + own);
  const [confirming, setConfirming] = useState(false);
  const report = useResource("tax-seller-report", () => finance.sellerReport(), tick);
  const [problem, setProblem] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<"csv" | "xlsx" | null>(null);

  async function confirmVat(date: string) {
    setConfirming(true);
    setProblem(null);
    try { await finance.confirmVat(date); setOwn((n) => n + 1); }
    catch (e) { setProblem((e as Error).message); }
    finally { setConfirming(false); }
  }

  /** 지금 보는 분기의 바로 앞 분기 마지막 날 */
  function previousQuarter(from: string): string {
    const d = new Date(`${from}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() - 1);
    return d.toISOString().slice(0, 10);
  }

  async function download(format: "csv" | "xlsx") {
    setDownloading(format);
    setProblem(null);
    try { await finance.downloadSellerReport(format); } catch (e) { setProblem((e as Error).message); } finally { setDownloading(null); }
  }

  return (
    <div className="space-y-4">
      <Panel id="tax-vat" title="부가세 신고 보조 집계" meta={vat.env?.meta}
        hint="회사의 과세 매출은 판매 수수료(중개 용역)입니다. 판매자가 파는 도서(면세)·문구(과세)는 판매자 매출이라 회사 신고에 들어가지 않습니다. 신고서가 아니라 신고할 때 쓸 숫자입니다.">
        <ResourceBody res={vat}>
          {(p) => (
            <div className="space-y-4">
              <p className="text-sm text-ink-700">
                <b className="text-ink-900">{p.quarter}</b> ({p.from} ~ {p.to}) · {p.filing.kind} 기한 <b>{p.filing.due}</b>
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <button className="btn-ghost" onClick={() => setQuarterDate(previousQuarter(p.from))}>앞 분기 보기</button>
                {quarterDate && <button className="btn-ghost" onClick={() => setQuarterDate(null)}>이번 분기로</button>}
                {p.filing_status === "confirmed"
                  ? <Chip label={`숫자 확정 · ${p.confirmed_by ?? ""}`} tone={TONE.ok} />
                  : <button className="btn-primary" disabled={!humanTokenSet || confirming || p.closable === false} onClick={() => confirmVat(p.to)}>이 분기 숫자 확정</button>}
                {p.closable === false && <span className="text-sm text-ink-500">분기가 끝난 뒤 확정할 수 있습니다.</span>}
              </div>
              {(p.drift ?? []).length > 0 && (
                <div className="rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  <p className="font-medium">확정 뒤 장부 숫자가 달라졌습니다. 수정신고나 경정청구가 필요한지 세무사에게 확인하세요.</p>
                  <ul className="mt-1 space-y-0.5 tabular-nums">
                    {p.drift!.map((x) => <li key={x.code}>{x.label}: 확정 {krw(x.confirmed)} → 지금 {krw(x.current)}</li>)}
                  </ul>
                </div>
              )}
              {problem && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{problem}</p>}
              <div className="grid gap-4 lg:grid-cols-2">
                <VatTable title="매출 (과세)" rows={p.sales} />
                <VatTable title="매입 (공제할 세액)" rows={p.purchases} />
              </div>
              <dl className="grid grid-cols-3 gap-3">
                {([["매출세액", p.output_vat], ["매입세액", p.input_vat], ["낼 세액 (참고)", p.payable]] as const).map(([label, v], i) => {
                  // 매출세액 − 매입세액 = 낼 세액. 결과(낼 세액)를 가장 크게
                  const lead = i === 2;
                  return (
                    <div key={label} className={`rounded-2xl border px-4 py-3 ${lead ? "border-brand-100 bg-brand-50/40" : "border-ink-100"}`}>
                      <dt className="text-sm text-ink-500">{i === 1 ? `− ${label}` : i === 2 ? `= ${label}` : label}</dt>
                      <dd className={`mt-1 font-semibold tabular-nums text-ink-900 ${lead ? "text-2xl" : "text-xl"}`}>{krw(v)}</dd>
                    </div>
                  );
                })}
              </dl>
              <div className="rounded-2xl border border-ink-100 px-4 py-3 text-sm">
                <h3 className="font-semibold text-ink-900">회사 부담 쿠폰과 수수료 과세표준</h3>
                <p className="mt-1 text-ink-700">
                  쿠폰 {krw(p.coupon.company_coupon)} · 수수료 {krw(p.coupon.commission_before_coupon)} → 쿠폰 공제 후 {krw(p.coupon.commission_after_coupon)}
                  {p.coupon.coupon_over_commission ? ` (수수료를 넘는 쿠폰 ${krw(p.coupon.coupon_over_commission)}은 공제할 수 없음)` : ""}
                </p>
                <p className="mt-1 text-sm text-ink-500">
                  지금 설정: {p.coupon.reduces_base ? "쿠폰을 수수료에서 공제(에누리)" : "공제하지 않음"}. 약관상 쿠폰 할인액을 판매자 수수료에서 직접 빼는 구조면 에누리로 과세표준에서 뺍니다(대법원 2014두298).
                </p>
              </div>
              {p.seller_sales && (
                <p className="text-sm text-ink-500">참고 · 판매자 상품 매출(회사 매출 아님): 면세(도서) {krw(p.seller_sales.exempt)}, 과세 {krw(p.seller_sales.taxable)}</p>
              )}
              <div className="rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-900">
                <b>세무사에게 확인할 것</b>
                <ul className="mt-1 list-disc space-y-0.5 pl-5">{p.checks.map((c) => <li key={c}>{c}</li>)}</ul>
              </div>
              <p className="text-sm text-ink-500">{p.basis}</p>
              <AppliedRules rules={p.applied_rules} />
            </div>
          )}
        </ResourceBody>
      </Panel>

      <Panel id="tax-seller-report" title="판매자별 판매 명세 (부가가치세법 제75조)" meta={report.env?.meta}
        tools={<>
          <button type="button" className="btn-primary" disabled={downloading !== null} onClick={() => download("xlsx")}>{downloading === "xlsx" ? "내려받는 중…" : "엑셀 내려받기"}</button>
          <button type="button" className="btn-ghost" disabled={downloading !== null} onClick={() => download("csv")}>{downloading === "csv" ? "내려받는 중…" : "CSV 내려받기"}</button>
        </>}
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
                    <thead className="text-left text-sm text-ink-500">
                      <tr><th className="pb-1 font-medium">판매자</th><th className="pb-1 font-medium">사업자번호</th><th className="pb-1 font-medium">월</th><th className="pb-1 text-right font-medium">판매금액</th><th className="pb-1 pl-6 font-medium">결제수단</th><th className="pb-1 pl-4 font-medium">면세·과세</th></tr>
                    </thead>
                    <tbody>
                      {r.rows.map((x) => (
                        <tr key={`${x.seller_id}-${x.month}`} className="border-t border-ink-100">
                          <td className="py-1.5">{x.name}</td>
                          <td className="py-1.5">{x.business_no ?? <Chip label="자료 없음" tone={TONE.warn} />}</td>
                          <td className="py-1.5 tabular-nums">{x.month}</td>
                          <td className="py-1.5 text-right font-medium tabular-nums text-ink-900">{krw(x.total)}</td>
                          <td className="py-1.5 pl-6 text-sm tabular-nums text-ink-500">{Object.entries(x.by_method).map(([k, v]) => `${k} ${krw(v)}`).join(", ")}</td>
                          <td className="py-1.5 pl-4 text-sm tabular-nums text-ink-500">{Object.entries(x.by_tax).map(([k, v]) => `${k} ${krw(v)}`).join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <Empty>이번 분기 판매가 없습니다.</Empty>}
              <p className="text-sm text-ink-500">{r.basis}</p>
              <AppliedRules rules={r.applied_rules} />
            </div>
          )}
        </ResourceBody>
      </Panel>
      <IncomeEstimatePanel tick={tick} />
      <ExportsPanel tick={tick} />
    </div>
  );
}

function IncomeEstimatePanel({ tick }: { tick: number }) {
  const est = useResource("tax-income-estimate", () => finance.incomeEstimate(), tick);
  return (
    <Panel id="tax-income" title="법인세 참고 추정" meta={est.env?.meta}
      hint="장부의 영업 결과로 계산한 참고치입니다. 세무조정 전이라 신고에 쓰지 않고, 세무사가 검토합니다.">
      <ResourceBody res={est}>
        {(e) => (
          <div className="space-y-3">
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {([["올해 영업 결과", e.operating_result], ["법인세", e.income_tax], ["지방소득세", e.local_income_tax], ["합계 (참고)", e.total]] as const).map(([label, v], i) => {
                const lead = i === 3;
                return (
                  <div key={label} className={`rounded-2xl border px-4 py-3 ${lead ? "border-brand-100 bg-brand-50/40" : "border-ink-100"}`}>
                    <dt className="text-sm text-ink-500">{label}</dt>
                    <dd className={`mt-1 font-semibold tabular-nums ${v === null ? "text-sm text-ink-500" : `text-ink-900 ${lead ? "text-2xl" : "text-xl"}`}`}>{v === null ? "자료 없음" : krw(v)}</dd>
                  </div>
                );
              })}
            </dl>
            <p className="text-sm text-ink-700">빠진 항목: {e.not_included.join(", ")}</p>
            <AppliedRules rules={e.applied_rules} />
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}

function ExportsPanel({ tick }: { tick: number }) {
  const [own, setOwn] = useState(0);
  const view = useResource("tax-exports", () => finance.exports(), tick + own);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function act(run: () => Promise<unknown>) {
    setBusy(true);
    setProblem(null);
    try { await run(); setOwn((n) => n + 1); }
    catch (e) { setProblem((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Panel id="tax-exports" title="자동으로 만든 파일" meta={view.env?.meta}
      hint="지난달 손익계산서를 AI가 만들어 보관합니다. 부가세·판매자 자료는 분기가 끝난 달에 함께 만듭니다. 내려받아 세무사에게 전달해 주세요. 전문가 확인 목록도 들어 있습니다. AI는 외부로 보내거나 신고하지 않습니다."
      tools={<button className="btn-ghost" disabled={busy || !humanTokenSet} onClick={() => act(() => finance.runExports())}>{busy ? "처리 중" : "지금 만들기"}</button>}>
      {problem && <p role="alert" className="mb-3 rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{problem}</p>}
      <ResourceBody res={view}>
        {(v) => (
          <div className="space-y-4">
            {v.packages.length === 0 ? <Empty>아직 만든 파일이 없습니다.</Empty> : (
              <ul className="space-y-3">
                {v.packages.map((p) => (
                  <li key={p.package_id} className="rounded-2xl border border-ink-100 p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <b className="text-ink-900">{p.month}</b>
                      <span className="text-sm text-ink-500">{p.version}번째 판 · {shortDateTime(p.generated_at)}</span>
                      <button className="btn-primary ml-auto" disabled={busy} onClick={() => act(() => finance.downloadBundle(p.package_id))}>전체 내려받기 (zip)</button>
                    </div>
                    <ul className="mt-2 flex flex-wrap gap-2">
                      {p.files.map((f) => (
                        <li key={f.name}>
                          <button className="btn-ghost break-all" onClick={() => act(() => finance.downloadExport(p.package_id, f.name))}>{f.name}</button>
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
            <div>
              <h3 className="text-sm font-semibold text-ink-900">은행 거래내역 읽은 기록</h3>
              <p className="mt-1 text-sm text-ink-500">
                은행 거래내역은 ② 매출·PG 대사 탭의 '은행 거래내역 파일 올리기'로 올립니다.
                {(view.env?.meta as { inbox_enabled?: boolean } | undefined)?.inbox_enabled ? " 서버의 은행 내역 폴더에 넣은 파일도 자동으로 읽습니다." : ""}
              </p>
              {v.bank_imports.length === 0 ? <p className="mt-1 text-sm text-ink-500">아직 읽은 은행 거래내역이 없습니다.</p> : (
                <ul className="mt-1 space-y-1 text-sm text-ink-700">
                  {v.bank_imports.slice(0, 5).map((b) => (
                    <li key={`${b.file}-${b.at}`} className="break-all">
                      {shortDateTime(b.at)} · {b.source === "upload" ? `${b.by ?? "사람"}님이 올림` : "폴더"} · {b.file} · {b.status === "imported" ? `새로 ${b.added}줄 더함` : `읽지 못함: ${b.reason}`}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}

function VatTable({ title, rows }: { title: string; rows: { code: string; label: string; supply: number; vat: number }[] }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-ink-900">{title}</h3>
      {rows.length ? (
        <table className="mt-2 w-full text-sm">
          <thead className="text-left text-sm text-ink-500"><tr><th className="pb-1 font-medium">항목</th><th className="pb-1 text-right font-medium">공급가액</th><th className="pb-1 text-right font-medium">세액</th></tr></thead>
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
