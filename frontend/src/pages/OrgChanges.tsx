import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Loading } from "@/components/admin/bits";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import { shortDateTime } from "@/lib/format";
import { showEmp, storedEmp, storedEmpInText } from "@/lib/hr";
import {
  hr,
  type OrgBasis, type OrgChange, type OrgChangeInput, type OrgChangeKind, type OrgDescription, type OrgParse, type OrgChanges, type OrgChangeStatus, type OrgHeadcount,
} from "@/lib/hr";

// 조직 변동(발령) 화면: ① 지금 사람이 할 일(확정 대기 초안) → ② 명부 등록 현황 → ③ 처음 명부 등록(일괄)
// → ④ 발령 입력 → ⑤ 팀 인원 미리 보기 → ⑥ 발령 기록.
// 초안은 승인 없이 저장되고 인사 담당자가 확정해야 인원에 센다. "전 직원 등록 완료"를 표시해야 재무에는
// 확정 기록 기준 인원이 나간다(그 전에는 예시 인원). 인원·반영 월은 hr이 계산한 값을 그대로 보여 준다.

const KIND_KO: Record<OrgChangeKind, string> = { transfer: "팀 이동", join: "입사", leave: "퇴사" };
const STATUS: Record<OrgChangeStatus, { label: string; tone: string }> = {
  draft: { label: "확정 대기", tone: "bg-amber-50 text-amber-700" },
  confirmed: { label: "확정됨", tone: "bg-emerald-50 text-emerald-700" },
  rejected: { label: "반려됨", tone: "bg-ink-100 text-ink-500" },
};
const INPUT = "w-full rounded-xl border border-ink-100 bg-app px-3 py-2 text-sm";

const errText = (e: unknown) => showEmp(e instanceof Error ? e.message : "요청에 실패했습니다");
const Chip = ({ label, tone }: { label: string; tone: string }) => <span className={`chip ${tone}`}>{label}</span>;
const two = (n: number) => String(n).padStart(2, "0");
const thisMonth = () => { const d = new Date(); return `${d.getFullYear()}-${two(d.getMonth() + 1)}`; };
const monthMark = (m: string) => `${Number(m.slice(5))}월`;

function Section({ title, desc, children }: { title: string; desc?: ReactNode; children: ReactNode }) {
  return (
    <section className="card space-y-4 p-6">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        {desc && <p className="mt-1 text-xs text-ink-500">{desc}</p>}
      </div>
      {children}
    </section>
  );
}

function describe(c: OrgChange): string {
  if (c.kind === "transfer") return `${c.from_team} → ${c.to_team}`;
  return c.kind === "join" ? `${c.to_team}에 입사` : `${c.from_team}에서 퇴사`;
}

export default function OrgChangesPage() {
  const [data, setData] = useState<OrgChanges | null>(null);
  const [preview, setPreview] = useState<OrgHeadcount | null>(null);
  const [month, setMonth] = useState(thisMonth());
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [changes, headcount] = await Promise.all([hr.orgChanges(), hr.orgHeadcount(month)]);
      setData(changes); setPreview(headcount); setError(null);
    } catch (e) { setError(errText(e)); }
  }, [month]);
  useEffect(() => {
    load();
    const poll = setInterval(load, 15000);
    return () => clearInterval(poll);
  }, [load]);

  async function run(job: () => Promise<unknown>) {
    setBusy(true); setMsg(null);
    try { await job(); await load(); }
    catch (e) { setMsg(errText(e)); await load(); }
    finally { setBusy(false); }
  }

  if (!data) return <Loading error={error} />;
  const drafts = data.changes.filter((c) => c.status === "draft");
  const teams = Array.from(new Set([
    ...(preview?.rows?.map((r) => r.team) ?? []),
    ...data.changes.flatMap((c) => [c.from_team, c.to_team].filter((t): t is string => !!t)),
  ]));
  const draftJoins = drafts.filter((c) => c.kind === "join").length;
  const remaining = Math.max(0, data.expected_members - data.registered_members - draftJoins);
  const nextNumber = Math.max(0, ...data.changes.map((c) => Number(/^emp_(\d+)$/.exec(c.member_id)?.[1] ?? 0))) + 1;
  const canComplete = HUMAN_TOKEN_SET && !busy && !data.roster_complete && data.registered_members === data.expected_members;

  return (
    <div className="space-y-8 px-8 pb-12 pt-2">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">조직 변동</h1>
          <p className="mt-1 text-sm text-ink-500">팀 이동·입사·퇴사를 기록합니다. 확정한 기록으로 팀별 인원을 세고, 그 인원이 재무 AI가 팀 예산을 계산할 때 쓰입니다.</p>
        </div>
        <div className="ml-auto text-xs text-ink-500">{shortDateTime(data.collected_at)} 기준</div>
      </header>

      <details className="rounded-2xl border border-ink-100 bg-surface px-5 py-3 text-sm">
        <summary className="cursor-pointer font-semibold text-ink-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">처음이라면: 관리 순서</summary>
        <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-ink-700">
          <li><b>처음 직원 등록</b>에서 팀별 인원을 넣고 초안을 만든 뒤 <b>모두 확정</b>합니다.</li>
          <li>전체 직원 {data.expected_members}명이 모두 확정되면 <b>전 직원 등록 완료</b>를 표시합니다. 그때부터 재무 AI에 확정한 기록 기준 인원이 전달됩니다.</li>
          <li>이후 팀 이동·입사·퇴사는 <b>발령 입력</b>으로 초안을 저장하고, 확인한 뒤 확정합니다.</li>
          <li>발령일이 1일이면 그 달부터, 아니면 다음 달 1일부터 인원에 셉니다. 파견은 세지 않습니다.</li>
        </ol>
      </details>

      {error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">{error}</p>}
      {msg && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">{msg}</p>}
      {!HUMAN_TOKEN_SET && <p className="rounded-xl bg-amber-50 px-4 py-2 text-xs text-amber-800">담당자 권한이 설정되지 않아 저장·확정 버튼을 쓸 수 없습니다. (개발자 설정: VITE_SAFETY_HUMAN_TOKEN)</p>}

      {/* ① 지금 사람이 할 일 */}
      <section aria-labelledby="org-todo" className={`rounded-[28px] p-6 ring-1 ${drafts.length ? "bg-amber-50 ring-amber-100" : "bg-emerald-50 ring-emerald-100"}`}>
        <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
          <div>
            <h2 id="org-todo" className={`text-sm font-semibold ${drafts.length ? "text-amber-800" : "text-emerald-800"}`}>지금 사람이 할 일</h2>
            <p className={`mt-1 text-4xl font-bold tabular-nums ${drafts.length ? "text-amber-800" : "text-emerald-800"}`}>{drafts.length ? `${drafts.length}건` : "없음"}</p>
          </div>
          <p className={`ml-auto max-w-sm pb-1 text-xs ${drafts.length ? "text-amber-800" : "text-emerald-800"}`}>저장한 초안은 확정해야 인원에 셉니다. 내용을 확인하고 확정하거나 반려하세요.</p>
        </div>
        {drafts.length > 0 && (
          <div className="mt-5 space-y-3">
            {drafts.length > 1 && (
              <button className="btn-primary" disabled={!HUMAN_TOKEN_SET || busy}
                onClick={() => run(async () => { for (const c of drafts) await hr.decideOrgChange(c.change_id, "approve"); })}>
                초안 {drafts.length}건 모두 확정
              </button>
            )}
            {drafts.map((c) => (
              <article key={c.change_id} className="grid gap-3 rounded-2xl bg-surface p-4 shadow-sm sm:grid-cols-[1fr_auto] sm:items-center">
                <div className="min-w-0 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <b className="text-[15px]">{KIND_KO[c.kind]}</b>
                    <span className="text-sm">{showEmp(c.member_id)}</span>
                    <span className="text-sm text-ink-700">{describe(c)}</span>
                  </div>
                  <p className="text-xs text-ink-500">발령일 {c.effective_date} · 인원에는 {monthMark(c.counts_from_month)}부터 셉니다{c.note ? ` · ${showEmp(c.note)}` : ""}</p>
                  <p className="text-xs text-ink-500">{c.basis_title ? `참고한 결정 카드: ${c.basis_title}` : "참고한 결정 카드 없음"}</p>
                  <Describe changeId={c.change_id} />
                </div>
                <div className="flex gap-2">
                  <button className="btn-primary" disabled={!HUMAN_TOKEN_SET || busy} onClick={() => run(() => hr.decideOrgChange(c.change_id, "approve"))}>확정</button>
                  <button className="btn-ghost" disabled={!HUMAN_TOKEN_SET || busy} onClick={() => run(() => hr.decideOrgChange(c.change_id, "reject"))}>반려</button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {/* ② 명부 등록 현황 */}
      <Section title="직원 등록 현황" desc={`전체 직원은 ${data.expected_members}명으로 가정합니다. 확정된 직원 수가 전체와 같아야 등록 완료로 표시할 수 있습니다.`}>
        <div className="flex flex-wrap items-center gap-4">
          <div className="rounded-2xl bg-brand-50 px-5 py-3 text-brand-700">
            <p className="text-xs">확정된 직원</p>
            <p className="text-3xl font-bold tabular-nums">{data.registered_members}<span className="text-lg font-semibold"> / {data.expected_members}명</span></p>
          </div>
          <div className="space-y-1">
            {data.roster_complete
              ? <Chip label="전 직원 등록 완료" tone="bg-emerald-50 text-emerald-700" />
              : <Chip label="등록 중" tone="bg-amber-50 text-amber-700" />}
            <p className="text-xs text-ink-500">
              {data.roster_complete ? "재무 AI에는 확정한 발령 기록으로 센 인원이 전달됩니다." : "등록이 끝나기 전까지 재무 AI에는 예시 인원이 전달되고, 재무에서는 예산을 확정할 수 없습니다."}
            </p>
          </div>
          <div className="ml-auto">
            {data.roster_complete
              ? <button className="btn-ghost" disabled={!HUMAN_TOKEN_SET || busy} onClick={() => run(() => hr.setRosterComplete(false))}>등록 완료 해제</button>
              : <button className="btn-primary" disabled={!canComplete} onClick={() => run(() => hr.setRosterComplete(true))}>전 직원 등록 완료로 표시</button>}
          </div>
        </div>
      </Section>

      {/* ③ 처음 직원 등록 */}
      {!data.roster_complete && (
        <BulkRegister teams={teams} remaining={remaining} startNumber={nextNumber} busy={busy} onSave={(items) => run(async () => { for (const i of items) await hr.recordOrgChange(i); })} />
      )}

      {/* ④ 발령 입력 */}
      <ChangeForm teams={teams} busy={busy} onSave={(input) => run(() => hr.recordOrgChange(input))} />

      {/* ⑤ 팀 인원 미리 보기 */}
      <Section title="팀 인원 미리 보기" desc="재무 AI가 팀 예산을 계산할 때 전달받는 인원과 같은 숫자입니다.">
        <label className="flex items-center gap-2 text-sm">월
          <input id="org-month" type="month" className={`${INPUT} max-w-[10rem]`} value={month} onChange={(e) => e.target.value && setMonth(e.target.value)} />
        </label>
        {preview?.available
          ? (
            <>
              <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
                {preview.example ? <Chip label="예시 인원" tone="bg-amber-50 text-amber-700" /> : <Chip label="확정한 기록 기준" tone="bg-emerald-50 text-emerald-700" />}
                <span>{preview.basis}</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="text-left text-xs text-ink-500"><th className="py-1 pr-4 font-medium">팀</th><th className="py-1 text-right font-medium">인원</th></tr></thead>
                  <tbody>
                    {preview.rows?.map((r) => <tr key={r.team} className="border-t border-ink-100"><td className="py-1.5 pr-4">{r.team}</td><td className="py-1.5 text-right tabular-nums">{r.headcount}명</td></tr>)}
                    <tr className="border-t border-ink-100 font-semibold"><td className="py-1.5 pr-4">합계</td><td className="py-1.5 text-right tabular-nums">{preview.rows?.reduce((n, r) => n + r.headcount, 0)}명</td></tr>
                  </tbody>
                </table>
              </div>
            </>
          )
          : <p className="rounded-xl bg-ink-50 px-4 py-3 text-sm text-ink-700">{preview?.message ?? "이 달의 인원을 알 수 없습니다."}</p>}
      </Section>

      {/* ⑥ 발령 기록 */}
      <Section title="발령 기록" desc="최근 순입니다. 확정된 기록만 팀 인원에 세고, 초안과 반려된 기록은 세지 않습니다.">
        {data.changes.length === 0
          ? <p className="py-6 text-center text-sm text-ink-500">아직 발령 기록이 없습니다.</p>
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="text-left text-xs text-ink-500">
                  <th className="py-1 pr-3 font-medium">상태</th><th className="py-1 pr-3 font-medium">구분</th><th className="py-1 pr-3 font-medium">직원</th>
                  <th className="py-1 pr-3 font-medium">내용</th><th className="py-1 pr-3 font-medium">발령일</th><th className="py-1 font-medium">인원에 세는 시작 월</th>
                </tr></thead>
                <tbody>
                  {data.changes.map((c) => (
                    <tr key={c.change_id} className="border-t border-ink-100">
                      <td className="py-1.5 pr-3"><Chip label={STATUS[c.status].label} tone={STATUS[c.status].tone} /></td>
                      <td className="py-1.5 pr-3">{KIND_KO[c.kind]}</td>
                      <td className="py-1.5 pr-3">{showEmp(c.member_id)}</td>
                      <td className="py-1.5 pr-3">{describe(c)}{c.basis_title && <span className="block text-xs text-ink-500">참고한 결정: {c.basis_title}</span>}</td>
                      <td className="py-1.5 pr-3 tabular-nums">{c.effective_date}</td>
                      <td className="py-1.5 tabular-nums">{c.status === "confirmed" ? `${monthMark(c.counts_from_month)}부터` : "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------- 발령 요약 설명문
function Describe({ changeId }: { changeId: string }) {
  const [d, setD] = useState<OrgDescription | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true); setErr(null);
    try { setD(await hr.orgDescription(changeId)); }
    catch (e) { setErr(errText(e)); }
    finally { setBusy(false); }
  }
  if (!d && !err) return <button type="button" className="btn-ghost" disabled={busy} onClick={load}>{busy ? "설명 쓰는 중…" : "요약 설명 보기"}</button>;
  return (
    <div className="rounded-xl bg-ink-50 px-3 py-2 text-sm">
      {d && (
        <>
          <p>{showEmp(d.text)}</p>
          <p className="mt-1 text-[11px] text-ink-500">{d.llm_used ? "AI가 다듬은 문장" : d.fallback ? "AI가 쓴 문장이 실제 숫자와 달라서 기본 문장으로 바꿨습니다" : "기본 문장"} · 인원 숫자는 프로그램이 직접 센 값입니다</p>
        </>
      )}
      {err && <p className="text-red-800">{err}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- 처음 명부 등록
function BulkRegister({ teams, remaining, startNumber, busy, onSave }: {
  teams: string[]; remaining: number; startNumber: number; busy: boolean;
  onSave: (items: { member_id: string; kind: "join"; to_team: string; effective_date: string }[]) => void;
}) {
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [date, setDate] = useState(`${thisMonth()}-01`);
  const total = teams.reduce((n, t) => n + (counts[t] ?? 0), 0);
  const over = total > remaining;
  function submit(e: FormEvent) {
    e.preventDefault();
    let n = startNumber;
    const items = teams.flatMap((t) => Array.from({ length: counts[t] ?? 0 }, () => ({
      member_id: `emp_${two(n++)}`, kind: "join" as const, to_team: t, effective_date: date,
    })));
    if (items.length) { onSave(items); setCounts({}); }
  }
  return (
    <Section title="처음 직원 등록" desc={`팀별 인원을 넣으면 입사 초안이 한꺼번에 만들어집니다. 아직 등록할 인원: ${remaining}명. 직원 이름은 받지 않고 번호(e1, e2처럼)만 붙입니다.`}>
      {remaining === 0
        ? <p className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800">전체 직원 수만큼 초안이 만들어졌습니다. 위에서 모두 확정한 뒤 등록 완료로 표시하세요.</p>
        : (
          <form onSubmit={submit} className="space-y-4">
            <label className="flex flex-wrap items-center gap-2 text-sm">기준일 (입사일로 기록)
              <input id="org-bulk-date" type="date" className={`${INPUT} max-w-[11rem]`} value={date} onChange={(e) => setDate(e.target.value)} required />
            </label>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {teams.map((t) => (
                <label key={t} className="space-y-1 text-sm">
                  <span className="block text-xs text-ink-500">{t}</span>
                  <input id={`org-bulk-${t}`} type="number" min={0} max={remaining} className={INPUT} value={counts[t] ?? 0}
                    onChange={(e) => setCounts({ ...counts, [t]: Math.max(0, Number(e.target.value) || 0) })} />
                </label>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <button className="btn-primary" disabled={!HUMAN_TOKEN_SET || busy || total === 0 || over || !date}>초안 {total}건 만들기</button>
              <span className={`text-xs ${over ? "text-red-700" : "text-ink-500"}`}>{over ? `등록할 인원(${remaining}명)보다 많습니다.` : `합계 ${total}명`}</span>
            </div>
          </form>
        )}
    </Section>
  );
}

// ---------------------------------------------------------------- 발령 입력
function TeamSelect({ id, teams, value, onChange }: { id: string; teams: string[]; value: string; onChange: (v: string) => void }) {
  return (
    <select id={id} className={INPUT} value={value} onChange={(e) => onChange(e.target.value)} required>
      <option value="">팀 선택</option>
      {teams.map((t) => <option key={t} value={t}>{t}</option>)}
    </select>
  );
}

function ChangeForm({ teams, busy, onSave }: {
  teams: string[]; busy: boolean;
  onSave: (input: OrgChangeInput) => void;
}) {
  const [kind, setKind] = useState<OrgChangeKind>("transfer");
  const [member, setMember] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [date, setDate] = useState("");
  const [note, setNote] = useState("");
  // 문장으로 채우기(AI 제안)
  const [sentence, setSentence] = useState("");
  const [parse, setParse] = useState<OrgParse | null>(null);
  const [parsing, setParsing] = useState(false);
  // 근거 카드 찾기(RAG)
  const [reason, setReason] = useState("");
  const [basis, setBasis] = useState<OrgBasis | null>(null);
  const [basisId, setBasisId] = useState("");
  const [finding, setFinding] = useState(false);
  const [aiErr, setAiErr] = useState<string | null>(null);

  const needFrom = kind !== "join";
  const needTo = kind !== "leave";
  const ready = member.trim() && date && (!needFrom || from) && (!needTo || to) && (kind !== "transfer" || from !== to);

  async function fillFromSentence() {
    setParsing(true); setAiErr(null); setParse(null);
    try {
      const res = await hr.orgParse(storedEmpInText(sentence.trim()));
      setParse(res);
      const s = res.suggestion;
      if (res.available && s) {
        if (s.kind) setKind(s.kind);
        setMember(showEmp(s.member_id ?? "")); setFrom(s.from_team ?? ""); setTo(s.to_team ?? ""); setDate(s.effective_date ?? "");
      }
    } catch (e) { setAiErr(errText(e)); }
    finally { setParsing(false); }
  }
  async function findBasis() {
    setFinding(true); setAiErr(null); setBasis(null); setBasisId("");
    try { setBasis(await hr.orgBasis(reason.trim())); }
    catch (e) { setAiErr(errText(e)); }
    finally { setFinding(false); }
  }
  function submit(e: FormEvent) {
    e.preventDefault(); if (!ready) return;
    onSave({
      member_id: storedEmp(member.trim()), kind, effective_date: date,
      ...(needFrom ? { from_team: from } : {}), ...(needTo ? { to_team: to } : {}),
      ...(note.trim() ? { note: note.trim() } : {}), ...(basisId ? { basis_card_id: basisId } : {}),
    });
    setMember(""); setNote(""); setSentence(""); setParse(null); setReason(""); setBasis(null); setBasisId("");
  }
  return (
    <Section title="발령 입력" desc="초안으로 저장됩니다. 확정하기 전에는 인원에 세지 않습니다. 문장으로 채우기와 근거 찾기는 제안일 뿐이고, 아래 저장 버튼을 눌러야 저장됩니다.">
      <div className="space-y-2 rounded-2xl bg-ink-50 p-4">
        <label className="block text-sm font-medium" htmlFor="org-sentence">발령 문장으로 채우기 (직원 번호로 적어 주세요)</label>
        <div className="flex flex-wrap gap-2">
          <input id="org-sentence" className={`${INPUT} min-w-[16rem] flex-1`} value={sentence} onChange={(e) => setSentence(e.target.value)}
            placeholder="다음 달 1일자로 e3 기획팀에서 개발팀으로 발령" maxLength={500} />
          <button type="button" className="btn-ghost" disabled={!HUMAN_TOKEN_SET || parsing || sentence.trim().length < 2} onClick={fillFromSentence}>{parsing ? "정리 중…" : "칸 채우기"}</button>
        </div>
        {parse && !parse.available && <p className="rounded-xl bg-white px-3 py-2 text-sm text-ink-700">{parse.message ?? "문장 정리를 쓸 수 없습니다. 직접 입력해 주세요."}</p>}
        {parse?.available && (
          <div className="rounded-xl bg-white px-3 py-2 text-sm">
            <p className="text-xs text-ink-500">아래 칸을 채웠습니다. 내용을 확인하고 고친 뒤 저장하세요.{parse.suggestion?.from_team_from_roster ? " 원래 팀은 확정된 직원 명단의 현재 소속입니다." : ""}</p>
            {parse.warnings.length > 0 && <ul className="mt-1 list-disc space-y-0.5 pl-5 text-amber-800">{parse.warnings.map((w) => <li key={w}>{showEmp(w)}</li>)}</ul>}
          </div>
        )}
      </div>

      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label className="space-y-1 text-sm"><span className="block text-xs text-ink-500">구분</span>
          <select id="org-kind" className={INPUT} value={kind} onChange={(e) => setKind(e.target.value as OrgChangeKind)}>
            {(Object.keys(KIND_KO) as OrgChangeKind[]).map((k) => <option key={k} value={k}>{KIND_KO[k]}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-sm"><span className="block text-xs text-ink-500">직원 번호</span>
          <input id="org-member" className={INPUT} value={member} onChange={(e) => setMember(e.target.value)} placeholder="e1" required />
        </label>
        <label className="space-y-1 text-sm"><span className="block text-xs text-ink-500">발령일</span>
          <input id="org-date" type="date" className={INPUT} value={date} onChange={(e) => setDate(e.target.value)} required />
        </label>
        {needFrom && <label className="space-y-1 text-sm"><span className="block text-xs text-ink-500">{kind === "leave" ? "나가는 팀" : "이전 팀"}</span><TeamSelect id="org-from" teams={teams} value={from} onChange={setFrom} /></label>}
        {needTo && <label className="space-y-1 text-sm"><span className="block text-xs text-ink-500">{kind === "join" ? "들어가는 팀" : "옮길 팀"}</span><TeamSelect id="org-to" teams={teams} value={to} onChange={setTo} /></label>}
        <label className="space-y-1 text-sm sm:col-span-2 lg:col-span-3"><span className="block text-xs text-ink-500">메모 (선택)</span>
          <input id="org-note" className={INPUT} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
        </label>

        <div className="space-y-2 sm:col-span-2 lg:col-span-3">
          <label className="block text-xs text-ink-500" htmlFor="org-reason">발령 사유 (선택): 비슷한 과거 결정 카드를 근거 후보로 찾아 줍니다</label>
          <div className="flex flex-wrap gap-2">
            <input id="org-reason" className={`${INPUT} min-w-[16rem] flex-1`} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="개발팀 인력 부족으로 기획팀 인원을 이동" maxLength={500} />
            <button type="button" className="btn-ghost" disabled={finding || reason.trim().length < 2} onClick={findBasis}>{finding ? "찾는 중…" : "근거 후보 찾기"}</button>
          </div>
          {basis && basis.candidates.length === 0 && <p className="rounded-xl bg-ink-50 px-3 py-2 text-sm text-ink-700">참고할 만한 결정 카드가 없습니다. 근거 없이도 저장할 수 있습니다.</p>}
          {basis && basis.candidates.length > 0 && (
            <fieldset className="space-y-2">
              <legend className="text-xs text-ink-500">근거로 연결할 카드를 고르세요. 고르지 않으면 연결하지 않습니다.</legend>
              {basis.candidates.map((c) => (
                <label key={c.card_id} className="flex cursor-pointer gap-2 rounded-xl bg-ink-50 px-3 py-2 text-sm">
                  <input type="radio" name="org-basis" className="mt-1" checked={basisId === c.card_id} onChange={() => setBasisId(c.card_id)} />
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-center gap-2"><b>{c.title}</b><Chip label={`비슷한 정도 ${Math.round(c.similarity * 100)}%`} tone="bg-ink-100 text-ink-500" />
                      {c.rejected && <Chip label="예전에 채택되지 않은 안" tone="bg-red-50 text-red-700" />}</span>
                    <span className="block text-xs text-ink-500">{c.summary}</span>
                  </span>
                </label>
              ))}
              {basisId && <button type="button" className="btn-ghost" onClick={() => setBasisId("")}>선택 해제</button>}
            </fieldset>
          )}
        </div>
        {aiErr && <p role="alert" className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-800 sm:col-span-2 lg:col-span-3">{aiErr}</p>}
        <div className="sm:col-span-2 lg:col-span-3"><button className="btn-primary" disabled={!HUMAN_TOKEN_SET || busy || !ready}>초안으로 저장</button></div>
      </form>
    </Section>
  );
}
