import { useEffect, useState } from "react";
import { shortDateTime } from "@/lib/format";
import { finance, humanTokenSet, krw, plainText, signedKrw, type ClosingPeriod, type ClosingReport } from "@/lib/finance";
import { AppliedRules, Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// ① 결산·리포트: 하루·한 달 손익 요약, 장부에 적을 내용 미리보기, 쉬운 말 설명, 사람이 결산 확정.

const toMonth = (date: string) => date.slice(0, 7);

export function ClosingSection({ tick, onChanged, focus }: {
  tick: number;
  onChanged: () => void;
  /** 할 일에서 "숫자 보기"로 온 날짜. 누를 때마다 새 객체라 같은 날짜도 다시 연다 */
  focus?: { date: string } | null;
}) {
  const [period, setPeriod] = useState<ClosingPeriod>("day");
  /** 비어 있으면 서버 기본값(오늘·이번 달) */
  const [date, setDate] = useState(focus?.date ?? "");
  useEffect(() => {
    if (focus) {
      setPeriod("day");
      setDate(focus.date);
    }
  }, [focus]);
  const res = useResource(`${period}:${date}`, () => finance.closing(period, date || null), tick);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  async function adjust(report: ClosingReport) {
    if (!report.adjustment_proposal) return;
    setConfirming(true);
    setProblem(null);
    try {
      const next = await finance.adjustClosing(report.date, reason.trim(), report.adjustment_proposal.signature);
      res.set(() => next);
      setReason("");
      onChanged();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setConfirming(false);
    }
  }

  async function confirm(report: ClosingReport) {
    setConfirming(true);
    setProblem(null);
    try {
      const next = await finance.confirmClosing(report.period, report.date);
      res.set(() => next);
      onChanged();
    } catch (e) {
      setProblem((e as Error).message);
    } finally {
      setConfirming(false);
    }
  }

  const tools = (
    <>
      <div role="group" aria-label="기간" className="inline-flex rounded-full border border-ink-100 p-0.5 text-sm">
        {(["day", "month"] as const).map((p) => (
          <button key={p} type="button" aria-pressed={period === p} onClick={() => { setPeriod(p); setDate(""); }}
            className={`rounded-full px-3 py-1 ${period === p ? "bg-brand-600 text-white" : "text-ink-700 hover:bg-ink-100"}`}>
            {p === "day" ? "하루" : "한 달"}
          </button>
        ))}
      </div>
      <label className="flex items-center gap-1 text-sm text-ink-500">
        <span className="sr-only">{period === "day" ? "날짜" : "달"}</span>
        {period === "day"
          ? <input type="date" className="field w-auto py-1" value={date || res.env?.data.date || ""} onChange={(e) => setDate(e.target.value)} />
          : <input type="month" className="field w-auto py-1" value={date ? toMonth(date) : res.env ? toMonth(res.env.data.date) : ""} onChange={(e) => setDate(e.target.value ? `${e.target.value}-01` : "")} />}
      </label>
      {date && <button type="button" className="btn-quiet" onClick={() => setDate("")}>{period === "day" ? "오늘" : "이번 달"}</button>}
    </>
  );

  return (
    <Panel id="closing" title="① 결산·리포트" titleHidden meta={res.env?.meta} tools={tools}
      hint="결산은 '이 기간에 얼마를 벌고 얼마를 썼나'를 정리해 숫자를 확정하는 일입니다. 숫자는 코드가 계산하고, 설명 문장만 AI가 씁니다. 숫자가 맞으면 아래에서 결산을 확정하세요.">
      <ResourceBody res={res}>
        {(r) => (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-ink-900">{r.period === "day" ? `${r.date} 하루` : `${toMonth(r.date)} 한 달`}</span>
              {r.status === "confirmed"
                ? <Chip label="확정됨" tone={TONE.ok} />
                : <Chip label="확정 전" tone={TONE.warn} />}
              {r.status === "confirmed" && (
                <span className="text-sm text-ink-500">{r.confirmed_by ? `${r.confirmed_by}님이 ` : ""}{shortDateTime(r.confirmed_at)}에 확정</span>
              )}
              {res.loading && <span className="text-sm text-ink-500">새로 받는 중…</span>}
            </div>

            <div className="grid gap-5 lg:grid-cols-2">
              <div>
                <h3 className="text-sm font-semibold text-ink-900">손익 요약</h3>
                <p className="text-sm text-ink-500">들어온 돈은 그대로, 나간 돈은 − 로 표시합니다.</p>
                {r.lines.length ? (
                  <table className="mt-2 w-full text-sm">
                    <tbody>
                      {r.lines.map((l) => {
                        const total = l.code === "net_result";
                        return (
                          <tr key={l.code} className={`border-t border-ink-100 ${total ? "font-semibold text-ink-900" : ""}`}>
                            <th scope="row" className="py-2 pr-3 text-left font-normal">{total ? <b>{l.label}</b> : l.label}</th>
                            <td className={`py-2 text-right tabular-nums ${l.amount == null ? "text-ink-500" : l.amount < 0 ? "text-red-700" : ""}`}>{signedKrw(l.amount)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                ) : <div className="mt-2"><Empty>이 기간에는 정리할 거래가 없습니다.</Empty></div>}
              </div>

              <div>
                <h3 className="text-sm font-semibold text-ink-900">설명</h3>
                <p className="text-sm text-ink-500">{r.commentary_source === "llm" ? "AI가 위 숫자를 보고 쓴 문장입니다. 숫자는 표가 기준입니다." : "정해진 문장 틀(템플릿)에 숫자를 넣어 만든 설명입니다."}</p>
                <div className="mt-2 rounded-2xl bg-surface-2 px-4 py-3 text-sm leading-relaxed text-ink-700">
                  <Chip label={r.commentary_source === "llm" ? "AI가 씀" : "템플릿 문장"} tone={r.commentary_source === "llm" ? TONE.info : TONE.muted} />
                  <p className="mt-1.5 whitespace-pre-line">{plainText(r.commentary) || "설명이 없습니다."}</p>
                </div>
                {(r.adjustments ?? []).map((a) => <p key={a.at} className="mt-2 text-sm text-ink-700">{a.posted_month}에 조정 반영 · {a.by} · {a.reason}</p>)}
                {(r.prior_adjustments ?? []).length > 0 && (
                  <div className="mt-2 rounded-2xl bg-surface-2 px-4 py-3 text-sm text-ink-700">
                    <p className="font-medium text-ink-900">이번 달에 반영한 전기 조정</p>
                    {r.prior_adjustments!.map((a, n) => <p key={`${a.for_month}-${n}`}>{a.for_month} · {signedKrw(a.lines.find((x) => x.code === "net_result")?.diff ?? 0)} · {a.reason}</p>)}
                    {r.net_after_adjustments != null && <p className="mt-1 font-medium tabular-nums text-ink-900">조정 후 영업 결과 {krw(r.net_after_adjustments)}</p>}
                  </div>
                )}
                <div className="mt-3"><AppliedRules rules={r.applied_rules} /></div>

                <div className="mt-4 rounded-2xl border border-ink-100 px-4 py-3">
                  {r.status === "confirmed" ? (
                    <div className="space-y-2 text-sm">
                      <p className="text-emerald-800">{r.snapshot_at ? "이 기간의 결산은 확정됐습니다. 위 숫자는 확정할 때 저장한 값입니다." : "이 기간의 결산은 확정됐습니다. 예전 확정이라 숫자가 저장되지 않아 지금 장부로 계산한 값입니다."}</p>
                      {(r.close_warnings ?? []).map((w) => <p key={w.code} className="text-ink-700">확정할 때 경고: {w.message}</p>)}
                      {(r.drift ?? []).length > 0 && (
                        <div className="rounded-xl bg-amber-50 px-3 py-2 text-amber-900">
                          <p className="font-medium">확정 뒤 장부 숫자가 달라졌습니다. 확정 숫자는 그대로 두고, 차이는 발생한 기간에 조정하세요.</p>
                          <ul className="mt-1 space-y-0.5 tabular-nums">
                            {r.drift!.map((x) => <li key={x.code}>{x.label}: 확정 {krw(x.confirmed)} → 지금 {krw(x.current)} ({signedKrw(x.diff)})</li>)}
                          </ul>
                          {r.adjustment_proposal ? (
                            <div className="mt-2 space-y-2">
                              <p>조정 분개안 (영업 결과 {signedKrw(r.adjustment_proposal.net)})</p>
                              <ul className="space-y-0.5 tabular-nums">
                                {r.adjustment_proposal.journal.map((j, n) => <li key={`${j.account}-${n}`}>{j.account}: 차변 {krw(j.debit)} / 대변 {krw(j.credit)}</li>)}
                              </ul>
                              <div className="flex flex-wrap items-center gap-2">
                                <input className="field min-w-0 flex-1" aria-label="조정 이유" placeholder="조정 이유" value={reason} onChange={(e) => setReason(e.target.value)} />
                                <button className="btn-primary" disabled={!humanTokenSet || confirming || !reason.trim()} onClick={() => adjust(r)}>이번 달에 조정 반영</button>
                              </div>
                            </div>
                          ) : <p className="mt-1">일 결산은 속보입니다. 차이는 월 결산에서 조정합니다.</p>}
                        </div>
                      )}
                    </div>
                  ) : (
                    <>
                      <p className="text-sm text-ink-700">숫자를 확인했으면 결산을 확정하세요. 확정하면 이 기간 숫자를 보고 기준으로 고정합니다(돈이 움직이지는 않습니다).</p>
                      {(r.close_warnings ?? []).map((w) => <p key={w.code} className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">{w.message}</p>)}
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <button className="btn-primary" disabled={!humanTokenSet || confirming || r.closable === false} onClick={() => confirm(r)}>{confirming ? "확정하는 중…" : "결산 확정"}</button>
                        {r.closable === false && <span className="text-sm text-ink-500">아직 끝나지 않은 기간이라 끝난 뒤 확정할 수 있습니다.</span>}
                        {!humanTokenSet && <span className="text-sm text-amber-800">사람 권한(VITE_SAFETY_HUMAN_TOKEN)이 설정되지 않아 버튼이 꺼져 있습니다.</span>}
                      </div>
                    </>
                  )}
                  {problem && <p role="alert" className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">{problem}</p>}
                </div>
              </div>
            </div>

            <details className="rounded-2xl border border-ink-100 px-4 py-3">
              <summary className="cursor-pointer text-sm font-semibold text-ink-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">
                장부에 적을 내용 미리보기 (분개 {r.journal.length}줄)
              </summary>
              <p className="mt-1 text-sm text-ink-500">회계 장부에 적는 방식(분개)입니다. 왼쪽(차변)은 늘어난 자산·비용, 오른쪽(대변)은 늘어난 매출·갚을 돈입니다. 두 합계가 같아야 맞습니다.</p>
              {r.journal.length ? (
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full min-w-[480px] text-sm">
                    <thead className="text-left text-sm text-ink-500">
                      <tr><th className="pb-1 font-medium">계정</th><th className="pb-1 text-right font-medium">차변</th><th className="pb-1 text-right font-medium">대변</th><th className="pb-1 pl-3 font-medium">메모</th></tr>
                    </thead>
                    <tbody>
                      {r.journal.map((j, i) => (
                        <tr key={i} className="border-t border-ink-100">
                          <td className="py-1.5">{j.account}</td>
                          <td className="py-1.5 text-right tabular-nums">{j.debit ? krw(j.debit) : ""}</td>
                          <td className="py-1.5 text-right tabular-nums">{j.credit ? krw(j.credit) : ""}</td>
                          <td className="py-1.5 pl-3 text-sm text-ink-500">{j.memo}</td>
                        </tr>
                      ))}
                      <tr className="border-t-2 border-ink-300 font-semibold">
                        <td className="py-1.5">합계</td>
                        <td className="py-1.5 text-right tabular-nums">{krw(r.journal.reduce((n, j) => n + j.debit, 0))}</td>
                        <td className="py-1.5 text-right tabular-nums">{krw(r.journal.reduce((n, j) => n + j.credit, 0))}</td>
                        <td />
                      </tr>
                    </tbody>
                  </table>
                </div>
              ) : <p className="mt-2 text-sm text-ink-500">적을 내용이 없습니다.</p>}
            </details>
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}
