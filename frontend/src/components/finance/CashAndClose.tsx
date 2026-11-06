import { useState } from "react";
import { finance, humanTokenSet, krw, plainText, signedKrw, type BankRowIn, type BankUnexplained, type BankOutflows } from "@/lib/finance";
import { Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// 돈이 실제로 맞는지 보는 화면 3개. 숫자는 재무 AI 서버가 계산한 값 그대로다.
// - 월 마감 체크리스트(①): 그 달을 닫기 전에 끝낼 일을 장부로 판정
// - 은행 입금 대조(②): 사람이 올린 은행 입금 내역과 PG 정산금을 날짜별로 맞춤
// - 은행 출금 대조(②): 승인한 판매자 정산이 실제로 이체됐는지, 항목별 출금과 설명 없는 출금
// - 13주 현금 흐름(③): 들어올 돈·나갈 돈. 확정과 추정을 나눠 표시. 통장 잔액을 올렸으면 예상 잔액

const CHECK_TONE = { done: TONE.ok, pending: TONE.warn, unknown: TONE.muted } as const;
const CHECK_KO = { done: "끝남", pending: "남음", unknown: "확인 불가" } as const;

export function ChecklistSection({ tick, onOpen }: { tick: number; onOpen: (tab: string) => void }) {
  const res = useResource("close-checklist", () => finance.closeChecklist(), tick);
  return (
    <Panel id="close-checklist" title="월 마감 체크리스트" meta={res.env?.meta}
      hint="이번 달을 닫기 전에 끝낼 일입니다. 재무 AI가 장부와 기록으로 끝났는지 판정합니다(직접 체크하지 않습니다). 마지막 '월 결산 확정'만 사람이 누릅니다.">
      <ResourceBody res={res}>
        {(c) => (
          <div className="space-y-3">
            <p className="text-sm text-ink-700">
              <b className="text-ink-900">{c.month}</b> · {c.done}/{c.total} 끝남
              {c.closed ? <Chip label="마감됨" tone={TONE.ok} /> : c.ready ? <Chip label="마감 준비됨 (월 결산 확정만 남음)" tone={TONE.info} /> : null}
            </p>
            <ol className="space-y-1.5">
              {c.items.map((i, n) => (
                <li key={i.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-ink-100 px-3 py-2 text-sm">
                  <span className="w-5 text-sm tabular-nums text-ink-500">{n + 1}</span>
                  <Chip label={CHECK_KO[i.status]} tone={CHECK_TONE[i.status]} />
                  <span className="font-medium text-ink-900">{i.label}</span>
                  <span className="min-w-0 flex-1 text-sm text-ink-500">{i.detail}</span>
                  {i.status !== "done" && <button type="button" className="text-sm font-medium text-brand-700 hover:underline" onClick={() => onOpen(i.link)}>하러 가기 →</button>}
                </li>
              ))}
            </ol>
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}

/** 은행에서 내려받은 CSV를 읽는다. 머리줄에서 날짜·입금·출금·적요·잔액 열을 찾는다 (쉼표·탭 구분, 따옴표 허용).
 *  출금은 음수 금액으로 넣는다. 출금 열이 있었는지(`hasWithdrawals`)도 알려 준다 */
export function parseBankCsv(text: string): { rows: BankRowIn[]; problem: string | null; hasWithdrawals: boolean } {
  const lines = text.replace(/^﻿/, "").split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 2) return { rows: [], problem: "머리줄과 거래 줄이 있어야 합니다.", hasWithdrawals: false };
  const split = (l: string) => (l.includes("\t") ? l.split("\t") : l.match(/("([^"]|"")*"|[^,]*)(,|$)/g)?.map((c) => c.replace(/,$/, "")) ?? [])
    .map((c) => c.trim().replace(/^"|"$/g, "").replace(/""/g, '"'));
  const head = split(lines[0]);
  const col = (...names: string[]) => head.findIndex((h) => names.some((n) => h.includes(n)));
  const di = col("거래일", "일자", "날짜", "date"), ai = col("입금", "맡기신", "amount"), mi = col("적요", "내용", "memo", "기재"), bi = col("잔액", "balance");
  const oi = col("출금", "찾으신", "withdrawal");
  if (di < 0 || (ai < 0 && oi < 0)) return { rows: [], problem: "머리줄에서 '거래일자'와 '입금액'·'출금액' 열을 찾지 못했습니다.", hasWithdrawals: false };
  const won = (v: string | undefined) => Number((v ?? "").replace(/[,\s원]/g, "") || "0");
  const rows: BankRowIn[] = [];
  for (const l of lines.slice(1)) {
    const c = split(l);
    const date = (c[di] ?? "").replace(/[./]/g, "-").slice(0, 10);
    const deposit = ai >= 0 ? won(c[ai]) : 0;
    const amount = deposit > 0 ? deposit : oi >= 0 ? -Math.abs(won(c[oi])) : 0;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isInteger(amount) || amount === 0) continue;
    const balance = bi >= 0 ? Number((c[bi] ?? "").replace(/[,\s원]/g, "")) : NaN;
    rows.push({ date, amount, memo: mi >= 0 ? (c[mi] ?? "") : "", ...(Number.isInteger(balance) ? { balance } : {}) });
  }
  return rows.length ? { rows, problem: null, hasWithdrawals: oi >= 0 } : { rows, problem: "거래 줄을 하나도 읽지 못했습니다.", hasWithdrawals: oi >= 0 };
}

const BANK_TONE: Record<string, string> = { matched: TONE.ok, short: TONE.bad, over: TONE.warn, missing: TONE.bad, unexpected: TONE.warn, not_covered: TONE.muted };

export function BankSection({ tick }: { tick: number }) {
  const res = useResource("bank", () => finance.bank(), tick);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [bump, setBump] = useState(0);

  async function upload(file: File) {
    setBusy(true);
    setMessage(null);
    try {
      const { rows, problem, hasWithdrawals } = parseBankCsv(await file.text());
      if (problem) { setMessage(problem); return; }
      const next = await finance.uploadBank(rows, hasWithdrawals);
      res.set(() => next);
      setBump((n) => n + 1);
      const added = Number(next.meta.added ?? 0);
      const outs = rows.filter((r) => r.amount < 0).length;
      setMessage(`거래 ${rows.length}줄(출금 ${outs}줄)을 읽어 새로 ${added}줄을 더했습니다.${hasWithdrawals ? "" : " 출금 열이 없는 파일이라 출금 대조는 하지 않습니다."}`);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
    <Panel id="bank" title="은행 입금 대조" meta={res.env?.meta}
      hint="PG가 보내기로 한 정산금이 실제 통장에 들어왔는지 날짜별로 맞춥니다. 은행 연동이 없어 은행에서 내려받은 거래내역 CSV를 올린 기간의 입금만 봅니다. 출금은 아래 '은행 출금 대조'에서 봅니다.">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className={`btn-primary ${!humanTokenSet || busy ? "pointer-events-none opacity-50" : "cursor-pointer"}`}>
          {busy ? "올리는 중…" : "은행 거래내역 CSV 올리기"}
          <input type="file" accept=".csv,.txt,text/csv" className="sr-only" disabled={!humanTokenSet || busy}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
        </label>
        <span className="text-sm text-ink-500">머리줄에 거래일자·입금액·출금액(·적요·잔액) 열이 있으면 됩니다. 같은 내역을 다시 올려도 한 번만 셉니다.</span>
      </div>
      {!humanTokenSet && <p className="mb-2 text-sm text-amber-800">사람 권한(VITE_SAFETY_HUMAN_TOKEN)이 없어 올리기 버튼이 꺼져 있습니다.</p>}
      {message && <p role="status" className="mb-3 rounded-lg bg-surface-2 px-3 py-2 text-sm text-ink-700">{message}</p>}
      <ResourceBody res={res}>
        {(b) => !b.uploaded ? <Empty>아직 올린 은행 거래내역이 없습니다. 올리면 그 기간의 PG 정산금 입금을 맞춰 봅니다.</Empty> : (
          <div className="space-y-3">
            <p className="text-sm text-ink-500">올린 기간 {b.covered?.from} ~ {b.covered?.to} · {b.covered?.rows}줄{b.pg_memo ? ` · 적요에 "${b.pg_memo}"가 있는 입금만` : ""}</p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-sm">
                <thead className="text-left text-sm text-ink-500">
                  <tr><th className="pb-1 font-medium">날짜</th><th className="pb-1 text-right font-medium">PG 정산 예정</th><th className="pb-1 text-right font-medium">실제 입금</th><th className="pb-1 text-right font-medium">차이</th><th className="pb-1 font-medium">상태</th></tr>
                </thead>
                <tbody>
                  {b.days.map((d) => (
                    <tr key={d.date} className="border-t border-ink-100">
                      <td className="py-1.5 tabular-nums">{d.date}</td>
                      <td className="py-1.5 text-right tabular-nums">{d.expected == null ? "–" : krw(d.expected)}</td>
                      <td className="py-1.5 text-right tabular-nums">{d.deposited == null ? "–" : krw(d.deposited)}</td>
                      <td className="py-1.5 text-right tabular-nums">{d.diff == null || d.diff === 0 ? "–" : signedKrw(d.diff)}</td>
                      <td className="py-1.5"><Chip label={d.status_label} tone={BANK_TONE[d.status] ?? TONE.muted} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </ResourceBody>
    </Panel>
    <div className="mt-4"><BankOutSection tick={tick + bump} /></div>
    </>
  );
}

const TRANSFER_TONE: Record<string, string> = { paid: TONE.ok, waiting: TONE.info, not_found: TONE.bad, not_covered: TONE.muted };

function OutNote({ item, onSaved }: { item: BankUnexplained; onSaved: (next: BankOutflows) => void }) {
  const [text, setText] = useState(item.note ?? "");
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const changed = text.trim() !== "" && text.trim() !== (item.note ?? "").trim();
  async function save() {
    setSaving(true);
    setProblem(null);
    try { onSaved((await finance.noteBankOutflow(item.id, text.trim())).data); }
    catch (e) { setProblem((e as Error).message); }
    finally { setSaving(false); }
  }
  return (
    <form className="flex min-w-0 flex-1 flex-col gap-1" onSubmit={(e) => { e.preventDefault(); if (changed && humanTokenSet && !saving) void save(); }}>
      <div className="flex gap-2">
        <label className="sr-only" htmlFor={`out-${item.id}`}>출금 이유</label>
        <input id={`out-${item.id}`} className="field py-1.5" maxLength={500} placeholder="이 출금의 이유 (예: 사무용품 현금 구매)" value={text}
          onChange={(e) => setText(e.target.value)} disabled={!humanTokenSet} />
        <button type="submit" className="btn-ghost shrink-0" disabled={!humanTokenSet || !changed || saving}>{saving ? "저장 중…" : "메모 저장"}</button>
      </div>
      {problem && <p role="alert" className="text-sm text-red-700">{problem}</p>}
    </form>
  );
}

export function BankOutSection({ tick }: { tick: number }) {
  const res = useResource("bank-outflows", () => finance.bankOutflows(), tick);
  return (
    <Panel id="bank-outflows" title="은행 출금 대조" meta={res.env?.meta}
      hint="승인한 판매자 정산이 실제로 통장에서 나갔는지, 급여·임차료·광고비 출금이 입력한 값과 맞는지, 장부에 근거가 없는 출금이 무엇인지 봅니다. 출금 열이 있는 거래내역을 올린 기간만 봅니다. 장부는 바꾸지 않습니다.">
      <ResourceBody res={res}>
        {(o) => !o.uploaded ? <Empty>출금 열이 있는 은행 거래내역을 아직 올리지 않았습니다. 위에서 입금·출금이 함께 있는 CSV를 올리면 출금을 맞춰 봅니다.</Empty> : (
          <div className="space-y-5">
            <p className="text-sm text-ink-500">출금 대조 기간 {o.covered.map((p) => `${p.from} ~ ${p.to}`).join(", ")} · 정산 묶음 승인 뒤 {o.transfer_days}일 안의 같은 금액 출금을 이체로 봅니다.</p>
            <section>
              <h3 className="text-sm font-semibold text-ink-900">판매자 정산 이체</h3>
              {o.transfers.length === 0 ? <p className="mt-1 text-sm text-ink-500">승인된 정산 묶음이 없습니다.</p> : (
                <div className="mt-1 overflow-x-auto">
                  <table className="w-full min-w-[620px] text-sm">
                    <thead className="text-left text-sm text-ink-500">
                      <tr>{["정산 주", "판매자", "금액", "승인일", "기한", "통장 출금일", "상태"].map((h) => <th key={h} className="pb-1 font-medium">{h}</th>)}</tr>
                    </thead>
                    <tbody>
                      {o.transfers.map((t) => (
                        <tr key={`${t.batch}-${t.seller_id ?? "all"}`} className="border-t border-ink-100">
                          <td className="py-1.5 tabular-nums">{t.batch}</td>
                          <td className="py-1.5">{t.name}{t.bulk ? <span className="text-ink-500"> (일괄 이체)</span> : null}</td>
                          <td className="py-1.5 tabular-nums">{krw(t.amount)}</td>
                          <td className="py-1.5 tabular-nums">{t.approved_on}</td>
                          <td className="py-1.5 tabular-nums">{t.due_by}</td>
                          <td className="py-1.5 tabular-nums">{t.bank_date ?? "–"}</td>
                          <td className="py-1.5"><Chip label={t.status_label} tone={TRANSFER_TONE[t.status] ?? TONE.muted} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <section>
              <h3 className="text-sm font-semibold text-ink-900">항목별 출금</h3>
              <p className="mt-0.5 text-sm text-ink-500">적요 글자로 나눕니다. 출금 기간이 그 달을 다 덮을 때만 입력값과 견줍니다.</p>
              {o.months.map((m) => (
                <div key={m.month} className="mt-2 overflow-x-auto">
                  <p className="text-sm font-medium text-ink-700">{m.month}{m.fully_covered ? "" : " · 달 일부만 올려 견주지 않음"}</p>
                  <table className="w-full min-w-[520px] text-sm">
                    <thead className="text-left text-sm text-ink-500">
                      <tr>{["항목", "통장 출금", "장부 값", "차이"].map((h) => <th key={h} className="pb-1 font-medium">{h}</th>)}</tr>
                    </thead>
                    <tbody>
                      {m.rows.map((r) => (
                        <tr key={r.category} className="border-t border-ink-100">
                          <td className="py-1.5">{r.category}</td>
                          <td className="py-1.5 tabular-nums">{krw(r.bank)}</td>
                          <td className="py-1.5 tabular-nums">{!r.comparable ? "–" : r.book == null ? "입력 없음" : `${krw(r.book)} (${r.book_source})`}</td>
                          <td className={`py-1.5 tabular-nums ${r.diff ? "text-red-700" : ""}`}>{r.diff == null ? "–" : r.diff === 0 ? "맞음" : signedKrw(r.diff)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </section>
            <section>
              <h3 className="text-sm font-semibold text-ink-900">설명 없는 출금 {o.counts.unexplained_open ? <Chip label={`메모 필요 ${o.counts.unexplained_open}건`} tone={TONE.warn} /> : null}</h3>
              <p className="mt-0.5 text-sm text-ink-500">판매자 이체와 정한 항목에 안 걸린 출금입니다. 이유를 적으면 마감 체크가 끝납니다. 틀린 돈이면 결산의 조정 분개로 바로잡습니다.</p>
              {o.unexplained.length === 0 ? <p className="mt-1 text-sm text-ink-500">없습니다.</p> : (
                <ul className="mt-2 space-y-2">
                  {o.unexplained.map((u) => (
                    <li key={u.id} className="flex flex-wrap items-start gap-3 rounded-xl border border-ink-100 px-3 py-2 text-sm">
                      <span className="tabular-nums text-ink-700">{u.date}</span>
                      <span className="tabular-nums font-medium text-ink-900">{krw(u.amount)}</span>
                      <span className="min-w-[8rem] text-ink-500">{plainText(u.memo) || "(적요 없음)"}</span>
                      {u.note && <span className="w-full text-ink-500">메모: {plainText(u.note)}{u.noted_by ? ` · ${u.noted_by}` : ""}</span>}
                      <OutNote item={u} onSaved={(next) => res.set((env) => (env ? { ...env, data: next } : env))} />
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}

export function CashflowSection({ tick }: { tick: number }) {
  const res = useResource("cashflow", () => finance.cashflow(), tick);
  return (
    <Panel id="cashflow" title="13주 현금 흐름" meta={res.env?.meta}
      hint="앞으로 13주 동안 PG에서 들어올 돈과 판매자에게 나갈 돈입니다. 날짜가 정해진 돈은 '확정', 그 뒤는 최근 몇 주 평균으로 채운 '추정'입니다. 판매자 지급이 몰려 마이너스가 되는 주를 미리 봅니다.">
      <ResourceBody res={res}>
        {(c) => (
          <div className="space-y-3">
            {(c.short_weeks?.length ?? 0) > 0 && <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">예상 잔액이 {krw(c.min_cash_krw ?? 0)}보다 적은 주 {c.short_weeks?.length}개: {c.short_weeks?.join(", ")} 시작 주. 판매자 정산과 고정비를 낼 현금이 모자랄 수 있습니다.</p>}
            {c.opening && <p className="text-sm text-ink-700">기초 잔액 <b className="text-ink-900">{krw(c.opening.balance)}</b> ({c.opening.as_of} 통장){c.opening.stale ? <Chip label="오래됨" tone={TONE.warn} /> : null}{c.opening.ordered ? "" : " · 같은 날 거래 순서를 알 수 없어 올린 순서의 마지막 잔액"}</p>}
            {c.negative_weeks.length > 0 && <p role="alert" className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">나가는 돈이 들어오는 돈보다 많은 주 {c.negative_weeks.length}개: {c.negative_weeks.join(", ")} 시작 주</p>}
            <p className="text-sm text-ink-500">{c.note}. 추정은 최근 {c.trailing_weeks}주 평균{c.trailing_weeks ? "" : " (평균을 낼 만큼 쌓인 주가 없어 비워 둠)"}입니다.</p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-sm">
                <thead className="text-left text-sm text-ink-500">
                  <tr><th className="pb-1 font-medium">주 (월요일)</th><th className="pb-1 font-medium">구분</th><th className="pb-1 text-right font-medium">들어옴 (PG)</th><th className="pb-1 text-right font-medium">나감 (판매자)</th>{c.fixed ? <th className="pb-1 text-right font-medium">고정비 (추정)</th> : null}<th className="pb-1 text-right font-medium">순현금</th><th className="pb-1 text-right font-medium">누적</th>{c.opening ? <th className="pb-1 text-right font-medium">예상 잔액</th> : null}</tr>
                </thead>
                <tbody>
                  {c.weeks.map((w) => (
                    <tr key={w.week_start} className={`border-t border-ink-100 ${w.basis === "estimated" ? "text-ink-500" : ""}`}>
                      <td className="py-1.5 tabular-nums">{w.week_start}</td>
                      <td className="py-1.5"><Chip label={w.basis === "scheduled" ? "확정" : "추정"} tone={w.basis === "scheduled" ? TONE.info : TONE.muted} /></td>
                      <td className="py-1.5 text-right tabular-nums">{krw(w.inflow)}</td>
                      <td className="py-1.5 text-right tabular-nums">{krw(w.outflow)}</td>
                      {c.fixed ? <td className="py-1.5 text-right tabular-nums">{krw(w.fixed)}</td> : null}
                      <td className={`py-1.5 text-right tabular-nums ${w.net != null && w.net < 0 ? "text-red-700" : ""}`}>{w.net == null ? krw(null) : signedKrw(w.net)}</td>
                      <td className="py-1.5 text-right tabular-nums">{w.cumulative == null ? krw(null) : signedKrw(w.cumulative)}</td>
                      {c.opening ? <td className={`py-1.5 text-right tabular-nums ${w.balance != null && w.balance < (c.min_cash_krw ?? 0) ? "text-red-700" : ""}`}>{w.balance == null ? krw(null) : signedKrw(w.balance)}</td> : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}
