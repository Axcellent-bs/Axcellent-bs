import { useState } from "react";
import { Link } from "@/lib/nav";
import { shortDateTime } from "@/lib/format";
import { actorText } from "@/lib/actor";
import { finance, humanTokenSet, krw, plainText, signedKrw, type BankImport, type BankUnexplained, type BankOutflows } from "@/lib/finance";
import { Chip, Empty, Panel, ResourceBody, Stat, TONE, useResource } from "./bits";

// 돈이 실제로 맞는지 보는 화면. 숫자는 재무 AI 서버가 계산한 값 그대로다.
// - 월 마감 체크리스트(①): 그 달을 닫기 전에 끝낼 일을 장부로 판정
// - 은행 입금 대조(②): 사람이 올린 은행 거래내역의 입금과 PG 정산금을 날짜별로 맞춤. 거래내역 올리기·읽은 기록도 여기
// - 은행 출금 대조(②): 항목별 출금(급여·임차료·광고비)과 설명 없는 출금. 같은 거래내역 파일을 쓰고 마감 체크도 ②로 가서 입금 대조 옆에 둔다
// - 판매자 정산 이체(③): 승인한 판매자 정산이 실제로 통장에서 나갔는지 (은행 출금 대조 중 판매자 몫)
// - 13주 현금 흐름(⑦): 들어올 돈·나갈 돈. 확정과 추정을 나눠 표시. 통장 잔액을 올렸으면 예상 잔액

const CHECK_TONE = { done: TONE.ok, pending: TONE.warn, unknown: TONE.muted } as const;
const CHECK_KO = { done: "끝남", pending: "남음", unknown: "확인 불가" } as const;
/** 체크 항목 → 바로 내려갈 섹션 앵커. 서버 link(탭 id)보다 한 칸 더 정확하다. 없으면 서버 link 탭을 연다 */
const CHECK_ANCHOR: Record<string, string> = { bank: "bank", bank_out: "bank-outflows", payables: "payables" };

const WEEKDAY_KO = ["일", "월", "화", "수", "목", "금", "토"];
/** "2026-12-03" → "12-03(목)". 날짜만 있는 값이라 시간대 없이 그 날짜 그대로 읽는다 */
function dueText(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `${iso.slice(5)}(${WEEKDAY_KO[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]})`;
}

/** `onOpen`: 탭 id 또는 섹션 앵커(#없이) */
export function ChecklistSection({ tick, onOpen }: { tick: number; onOpen: (target: string) => void }) {
  const res = useResource("close-checklist", () => finance.closeChecklist(), tick);
  return (
    <Panel id="close-checklist" title="월 마감 체크리스트" meta={res.env?.meta}
      hint="이번 달을 닫기 전에 끝낼 일입니다. 재무 AI가 장부와 기록으로 끝났는지 판정합니다(직접 체크하지 않습니다). 마지막 '월 결산 확정'만 사람이 누릅니다.">
      <ResourceBody res={res}>
        {(c) => (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
              <b className="text-ink-900">{c.month}</b>
              <span className="tabular-nums text-ink-700"><b className="text-ink-900">{c.done}</b>/{c.total} 끝남</span>
              <span className="h-1.5 w-32 overflow-hidden rounded-full bg-ink-100" aria-hidden="true">
                <span className="block h-full rounded-full bg-emerald-700" style={{ width: `${c.total ? (c.done / c.total) * 100 : 0}%` }} />
              </span>
              {c.closed ? <Chip label="마감됨" tone={TONE.ok} /> : c.ready ? <Chip label="마감 준비됨 (월 결산 확정만 남음)" tone={TONE.info} /> : null}
            </div>
            <ol className="space-y-1.5">
              {c.items.map((i, n) => {
                const done = i.status === "done";
                return (
                  // 남은 일은 테두리로 띄우고, 끝난 일은 한 단계 가라앉힌다
                  <li key={i.key} className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl px-3 py-2 text-sm ${done ? "bg-surface-2/60 text-ink-500" : "border border-ink-100"}`}>
                    <span className="w-5 text-sm tabular-nums text-ink-500">{n + 1}</span>
                    <Chip label={CHECK_KO[i.status]} tone={CHECK_TONE[i.status]} />
                    <span className={done ? "text-ink-700" : "font-medium text-ink-900"}>{i.label}</span>
                    <span className="min-w-0 flex-1 text-sm text-ink-500">{i.detail}</span>
                    {i.due && (done
                      ? <span className="text-sm tabular-nums text-ink-500">기한 {dueText(i.due)}</span>
                      : i.overdue
                        ? <Chip label={`기한 지남 · ${dueText(i.due)}`} tone={TONE.bad} />
                        : <span className="text-sm tabular-nums text-ink-700">기한 {dueText(i.due)}</span>)}
                    {!done && <button type="button" className="text-sm font-medium text-brand-700 hover:underline" onClick={() => onOpen(CHECK_ANCHOR[i.key] ?? i.link)}>하러 가기 →</button>}
                  </li>
                );
              })}
            </ol>
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}

const BANK_TONE: Record<string, string> = { matched: TONE.ok, short: TONE.bad, over: TONE.warn, missing: TONE.bad, unexpected: TONE.warn, not_covered: TONE.muted };

export function BankSection({ tick }: { tick: number }) {
  const [bump, setBump] = useState(0);
  const res = useResource("bank", () => finance.bank(), tick + bump);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function upload(file: File) {
    setBusy(true);
    setMessage(null);
    try {
      const next = await finance.uploadBankFile(file);
      res.set(() => next);
      setBump((n) => n + 1);
      const { import: got, added, withdrawals, has_withdrawals: hasOut } = next.meta;
      setMessage(`거래 ${(got.rows ?? 0).toLocaleString("ko-KR")}줄(출금 ${withdrawals}줄)을 읽어 새로 ${added}줄을 더했습니다.${hasOut ? "" : " 출금 칸이 없는 파일이라 출금 대조는 하지 않습니다."}`);
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
          {busy ? "올리는 중…" : "은행 거래내역 파일 올리기"}
          <input type="file" accept=".csv,.txt,.xlsx,.xls,text/csv" className="sr-only" disabled={!humanTokenSet || busy}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
        </label>
        <span className="text-sm text-ink-500">은행 화면의 거래내역 조회에서 받은 파일(CSV·엑셀 xlsx)을 고치지 말고 그대로 올리세요. 재무 AI가 날짜·입금·출금·적요 칸을 찾아 읽고, 같은 내역은 한 번만 셉니다.</span>
      </div>
      {!humanTokenSet && <p className="mb-2 text-sm text-amber-800">사람 권한(VITE_SAFETY_HUMAN_TOKEN)이 없어 올리기 버튼이 꺼져 있습니다.</p>}
      {message && <p role="status" className="mb-3 rounded-lg bg-surface-2 px-3 py-2 text-sm text-ink-700">{message}</p>}
      <ResourceBody res={res}>
        {(b) => !b.uploaded ? <Empty>아직 올린 은행 거래내역이 없습니다. 올리면 그 기간의 PG 정산금 입금을 맞춰 봅니다.</Empty> : (
          <div className="space-y-3">
            {(() => {
              const off = (b.counts.short ?? 0) + (b.counts.over ?? 0) + (b.counts.missing ?? 0) + (b.counts.unexpected ?? 0);
              return (
                <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
                  <p className="text-sm text-ink-500">맞음 <b className="text-lg font-semibold tabular-nums text-ink-900">{b.counts.matched ?? 0}</b>일</p>
                  <p className="text-sm text-ink-500">확인 필요 <b className={`text-lg font-semibold tabular-nums ${off ? "text-red-700" : "text-ink-900"}`}>{off}</b>일</p>
                  <p className="text-sm text-ink-500">올린 기간 {b.covered?.from} ~ {b.covered?.to} · {b.covered?.rows}줄{b.pg_memo ? ` · 적요에 "${b.pg_memo}"가 있는 입금만` : ""}</p>
                </div>
              );
            })()}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-sm">
                <thead className="text-left text-sm text-ink-500">
                  <tr><th className="pb-1 font-medium">날짜</th><th className="pb-1 text-right font-medium">PG 정산 예정</th><th className="pb-1 text-right font-medium">실제 입금</th><th className="pb-1 text-right font-medium">차이</th><th className="pb-1 font-medium">상태</th></tr>
                </thead>
                <tbody>
                  {b.days.map((d) => (
                    <tr key={d.date} className={`border-t border-ink-100 ${d.status === "matched" || d.status === "not_covered" ? "text-ink-500" : "text-ink-900"}`}>
                      <td className="py-1.5 tabular-nums">{d.date}</td>
                      <td className="py-1.5 text-right tabular-nums">{d.expected == null ? "–" : krw(d.expected)}</td>
                      <td className="py-1.5 text-right tabular-nums">{d.deposited == null ? "–" : krw(d.deposited)}</td>
                      <td className={`py-1.5 text-right tabular-nums ${d.diff ? "font-semibold text-red-700" : ""}`}>{d.diff == null || d.diff === 0 ? "–" : signedKrw(d.diff)}</td>
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
    <div className="mt-4"><BankImportsPanel tick={tick + bump} onChanged={() => setBump((n) => n + 1)} /></div>
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
      hint="급여·임차료·광고비 출금이 입력한 값과 맞는지, 장부에 근거가 없는 출금이 무엇인지 봅니다. 출금 열이 있는 거래내역을 올린 기간만 봅니다. 장부는 바꾸지 않습니다.">
      <ResourceBody res={res}>
        {(o) => !o.uploaded ? <Empty>출금 열이 있는 은행 거래내역을 아직 올리지 않았습니다. 위에서 입금·출금이 함께 있는 CSV를 올리면 출금을 맞춰 봅니다.</Empty> : (
          <div className="space-y-5">
            <p className="text-sm text-ink-500">출금 대조 기간 {o.covered.map((p) => `${p.from} ~ ${p.to}`).join(", ")} · 정산 묶음 승인 뒤 {o.transfer_days}일 안의 같은 금액 출금을 이체로 봅니다.</p>
            <p className="text-sm text-ink-500">
              승인한 판매자 정산이 통장에서 나갔는지는 <Link href="/finance/daily#seller-transfers" className="font-medium text-brand-700 underline">③ 판매자 정산 &gt; 판매자 정산 이체</Link>에서 봅니다.
            </p>
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
                      {u.note && <span className="w-full text-ink-500">메모: {plainText(u.note)}{u.noted_by ? ` · ${actorText(u.noted_by_label, u.noted_by)}` : ""}</span>}
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

/** ③ 판매자 정산 이체: 은행 출금 대조 중 승인한 판매자 정산이 실제로 이체됐는지. 거래내역은 ②에서 올린다 */
export function SellerTransfersSection({ tick }: { tick: number }) {
  const res = useResource("seller-transfers", () => finance.bankOutflows(), tick);
  return (
    <Panel id="seller-transfers" title="판매자 정산 이체 (은행 출금 대조)" meta={res.env?.meta}
      hint="고객지원이 승인받은 판매자 정산 묶음이 실제로 통장에서 나갔는지 봅니다. 은행 거래내역은 ② 매출·PG 대사 탭에서 올리고, 출금 열이 있는 기간만 맞춥니다. 장부는 바꾸지 않습니다.">
      <ResourceBody res={res}>
        {(o) => !o.uploaded ? (
          <Empty>출금 열이 있는 은행 거래내역을 아직 올리지 않았습니다. <Link href="/finance/daily#bank" className="font-medium text-brand-700 underline">② 매출·PG 대사 &gt; 은행 입금 대조</Link>에서 입금·출금이 함께 있는 파일을 올리면 판매자 이체를 맞춰 봅니다.</Empty>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-ink-500">출금 대조 기간 {o.covered.map((p) => `${p.from} ~ ${p.to}`).join(", ")} · 정산 묶음 승인 뒤 {o.transfer_days}일 안의 같은 금액 출금을 이체로 봅니다.</p>
            <section>
              {o.transfers.length === 0 ? <Empty>승인된 정산 묶음이 없습니다.</Empty> : (
                <div className="overflow-x-auto">
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
          </div>
        )}
      </ResourceBody>
    </Panel>
  );
}

/** 은행 내역 기록 한 줄: 누가·어디서 · 파일 · 결과 */
function bankImportLine(b: BankImport): string {
  if (b.status === "removed") return `${actorText(b.by_label, b.by)}님이 뺌 · ${b.file ?? ""} · ${b.rows ?? 0}줄 뺌 · 이유: ${b.reason ?? ""}`;
  const who = b.source === "upload" ? `${actorText(b.by_label, b.by)}님이 올림` : "폴더";
  if (b.status === "failed") return `${who} · ${b.file} · 읽지 못함: ${b.reason}`;
  const removed = b.removed ? ` · ${actorText(b.removed.by_label, b.removed.by)}님이 ${shortDateTime(b.removed.at)}에 뺌` : "";
  return `${who} · ${b.file} · 새로 ${b.added}줄 더함${removed}`;
}

/** 은행 거래내역 읽은 기록(최근 5개)과 잘못 올린 기록 빼기. 기록은 내보내기 목록(`/api/finance/exports`)에 함께 온다 */
function BankImportsPanel({ tick, onChanged }: { tick: number; onChanged: () => void }) {
  const [own, setOwn] = useState(0);
  const view = useResource("bank-imports", () => finance.exports(), tick + own);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inbox = (view.env?.meta as { inbox_enabled?: boolean } | undefined)?.inbox_enabled;

  async function remove(b: BankImport) {
    if (!b.import_id || !window.confirm(`${b.file ?? "이 기록"}으로 더한 은행 거래 줄을 뺄까요? 기록은 남습니다.`)) return;
    setBusy(true);
    setProblem(null);
    try { await finance.removeBankImport(b.import_id, "화면에서 잘못 올린 은행 내역 빼기"); setOwn((n) => n + 1); onChanged(); }
    catch (e) { setProblem((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Panel id="bank-imports" title="은행 거래내역 읽은 기록" meta={view.env?.meta}
      hint={`위 '은행 거래내역 파일 올리기'로 올린 파일의 최근 기록입니다. 잘못(시험으로) 올린 기록은 '이 내역 빼기'로 뺍니다.${inbox ? " 서버의 은행 내역 폴더에 넣은 파일도 자동으로 읽습니다." : ""}`}>
      {problem && <p role="alert" className="mb-3 rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{problem}</p>}
      <ResourceBody res={view}>
        {(v) => v.bank_imports.length === 0 ? <Empty>아직 읽은 은행 거래내역이 없습니다.</Empty> : (
          <ul className="space-y-1 text-sm text-ink-700">
            {v.bank_imports.slice(0, 5).map((b) => (
              <li key={`${b.import_id ?? b.file}-${b.at}-${b.status}`} className="flex flex-wrap items-center gap-2 break-all">
                <span>{shortDateTime(b.at)} · {bankImportLine(b)}</span>
                {b.status === "imported" && b.import_id && !b.removed && humanTokenSet && (
                  <button type="button" className="btn-quiet py-0.5 text-sm" disabled={busy} onClick={() => void remove(b)}>이 내역 빼기</button>
                )}
              </li>
            ))}
          </ul>
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
            {(() => {
              // 맨 위 큰 숫자 셋: 지금 잔액 → 13주 뒤 예상 잔액 → 모자랄 수 있는 주
              const last = [...c.weeks].reverse().find((w) => w.balance != null);
              const short = c.short_weeks?.length ?? 0;
              return (
                <div className="grid gap-3 sm:grid-cols-3">
                  <Stat label="기초 잔액 (통장)" value={c.opening ? krw(c.opening.balance) : "통장 내역 없음"}
                    sub={c.opening ? `${c.opening.as_of} 기준${c.opening.stale ? " · 오래됨" : ""}${c.opening.ordered ? "" : " · 같은 날 순서 모름"}` : "② 매출·PG 대사에서 통장 내역을 올리면 잔액에서 시작합니다"} />
                  <Stat lead label={last ? `${last.week_start} 주 예상 잔액` : "예상 잔액"} value={last?.balance != null ? signedKrw(last.balance) : "확인할 수 없음"}
                    sub={c.trailing_weeks ? `추정은 최근 ${c.trailing_weeks}주 평균` : "평균 낼 주가 모자라 뒤쪽 주는 비움"} />
                  <Stat label="현금 부족 예상 주" value={`${short}주`} flag={short ? "주의" : undefined}
                    sub={`예상 잔액이 ${krw(c.min_cash_krw ?? 0)}보다 적은 주`} />
                </div>
              );
            })()}
            {c.negative_weeks.length > 0 && <p role="alert" className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">나가는 돈이 들어오는 돈보다 많은 주 {c.negative_weeks.length}개: {c.negative_weeks.join(", ")} 시작 주</p>}
            <p className="text-sm text-ink-500">{c.note}</p>
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
