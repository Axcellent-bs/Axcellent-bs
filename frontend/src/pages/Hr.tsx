import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Loading } from "@/components/admin/bits";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import { ago, shortDateTime } from "@/lib/format";
import {
  hr,
  type HandoverQuestion, type HandoverStatus, type HandoverView, type HrCard, type PlanCheck, type PlanDraft, type Roster,
} from "@/lib/hr";

// 인사 AI 화면 (재무 AI 화면과 같은 짜임): ① 지금 사람이 할 일 → ② 인수인계 → ③ 담당 명부 → ④ 기록 도구.
// hr이 계산한 완성도·판정을 그대로 보여 주고, 사람이 하는 일(질문에 답하기, 후보 카드 승인·폐기)만
// 버튼으로 둔다. 인수인계 시작·확정은 총괄 AI가 부르므로 화면에 없다.

const HANDOVER_STATUS: Record<HandoverStatus, { label: string; tone: string }> = {
  draft: { label: "초안", tone: "bg-ink-100 text-ink-500" },
  input_required: { label: "답변 기다림", tone: "bg-amber-50 text-amber-700" },
  ready: { label: "확정 가능", tone: "bg-brand-50 text-brand-700" },
  confirmed: { label: "확정됨", tone: "bg-emerald-50 text-emerald-700" },
  held: { label: "사람 확인 대기", tone: "bg-amber-50 text-amber-700" },
};
const REASON_KO: Record<string, string> = { shift: "교대", transfer: "담당 변경", offboarding: "담당 종료", other: "기타" };
const ITEM_KO: Record<string, string> = { pr: "PR", ticket: "티켓", deployment: "배포", decision: "결정", doc: "문서" };
const FIELD_KO: Record<string, string> = { decisions: "결정한 것", rollback_criteria: "되돌리는 기준", known_risks: "알려진 위험", open_items: "진행 중인 일", contacts: "연락처" };
const CONF_KO = { low: "낮음", mid: "보통", high: "높음" } as const;
const VERDICT: Record<string, { label: string; tone: string }> = {
  ok: { label: "겹치는 기록 없음", tone: "bg-emerald-50 text-emerald-700" },
  warn: { label: "확인 필요", tone: "bg-amber-50 text-amber-700" },
  block: { label: "진행 전 확인 필요", tone: "bg-red-50 text-red-700" },
};
const DEMO_DRAFT: PlanDraft = { title: "결제 서버 캐시 도입", summary: "결제 응답 속도를 위해 서버 캐시를 도입한다", area: "payment", kind: "spec" };
const INPUT = "w-full rounded-xl border border-ink-100 bg-app px-3 py-2 text-sm";

const errText = (e: unknown) => (e instanceof Error ? e.message : "요청에 실패했습니다");
const Chip = ({ label, tone }: { label: string; tone: string }) => <span className={`chip ${tone}`}>{label}</span>;

function Section({ title, desc, children }: { title: string; desc?: ReactNode; children: ReactNode }) {
  return (
    <section className="card space-y-4 p-6">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        {desc && <p className="mt-1 max-w-3xl text-xs text-ink-500">{desc}</p>}
      </div>
      {children}
    </section>
  );
}

function Tile({ label, count, note, tone }: { label: string; count: number | string; note: string; tone: string }) {
  return (
    <div className={`rounded-2xl p-4 ${tone}`}>
      <p className="text-xs">{label}</p>
      <p className="mt-1 text-3xl font-bold tabular-nums">{count}</p>
      <p className="mt-1 text-xs opacity-80">{note}</p>
    </div>
  );
}

interface View { handovers: HandoverView[]; cards: HrCard[]; roster: Roster }

// 데모에서 인사(인수인계·담당 명부)와 운영(결정 기록·계획 비교)을 두 부서로 나눴다. 데이터는 같은 hr 서비스에서 받는다.
export type HrPart = "hr" | "ops";
const PART = {
  hr: {
    title: "인수인계",
    desc: "담당이 바뀔 때 일이 끊기지 않게 인수인계와 담당 명부를 관리합니다.",
    guide: [
      <>맨 위 <b>지금 사람이 할 일</b>에서 인계자의 답을 기다리는 질문을 봅니다.</>,
      <>인계자가 <b>답하기</b>로 채웁니다. 모든 항목이 차면 완성도가 올라가 확정할 수 있게 됩니다.</>,
      <>인수인계의 시작·확정은 총괄 AI가 요청합니다. 이 화면에는 그 버튼이 없습니다.</>,
    ],
  },
  ops: {
    title: "결정 기록",
    desc: "회사의 결정을 기록으로 남기고, 새 계획이 과거에 기각된 안과 겹치는지 확인합니다.",
    guide: [
      <>맨 위 <b>지금 사람이 할 일</b>에서 확신이 낮아 후보로 남은 기록을 봅니다.</>,
      <>내용을 읽고 <b>기록으로 확정</b> 또는 <b>폐기</b>를 누릅니다. 애매하면 폐기하세요. 필요하면 요청한 AI가 근거를 보강해 다시 올립니다.</>,
    ],
  },
} satisfies Record<HrPart, { title: string; desc: string; guide: ReactNode[] }>;

export default function HrPage({ part = "hr" }: { part?: HrPart }) {
  const P = PART[part];
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [up, setUp] = useState(true);

  const load = useCallback(async () => {
    try {
      const [handovers, cards, roster] = await Promise.all([hr.handovers(), hr.candidateCards(), hr.roster()]);
      setView({ handovers, cards, roster }); setUp(true);
    } catch (e) { setUp(false); setError(errText(e)); }
  }, []);
  useEffect(() => {
    load();
    const poll = setInterval(load, 15000); // 15초마다 새로 받는다
    return () => clearInterval(poll);
  }, [load]);

  if (!view) return <Loading error={error} />;
  const { handovers, roster } = view;
  const cards = part === "ops" ? view.cards : [];
  const questions = part === "hr" ? handovers.flatMap((h) => h.open_questions) : [];
  const todo = cards.length + questions.length;
  const tone = todo ? "text-amber-800" : "text-emerald-800";
  const count = (s: HandoverStatus) => handovers.filter((h) => h.status === s).length;

  return (
    <div className="max-w-[1320px] space-y-8 px-8 pb-12 pt-2">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{P.title}</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-500">{P.desc} 완성도와 판정은 AI가 아니라 정해진 계산과 기준으로 합니다.</p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
          <Chip label={`${P.title} ${up ? "켜짐" : "꺼짐"}`} tone={up ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"} />
        </div>
      </header>

      <details className="rounded-2xl border border-ink-100 bg-surface px-5 py-3 text-sm">
        <summary className="cursor-pointer font-semibold text-ink-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">처음이라면: 관리자가 할 일</summary>
        <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-ink-700">
          {P.guide.map((g, i) => <li key={i}>{g}</li>)}
        </ol>
      </details>

      {!up && error && <p role="alert" className="rounded-xl bg-red-50 px-4 py-3 text-sm font-medium text-red-800">{error}</p>}

      {/* ① 지금 사람이 할 일: 화면에서 가장 중요한 곳 */}
      <section aria-labelledby="todo" className={`rounded-[28px] p-6 ring-1 ${todo ? "bg-amber-50 ring-amber-100" : "bg-emerald-50 ring-emerald-100"}`}>
        <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
          <div>
            <h2 id="todo" className={`text-sm font-semibold ${tone}`}>지금 사람이 할 일</h2>
            <p className={`mt-1 text-4xl font-bold tabular-nums ${tone}`}>{todo ? `${todo}건` : "없음"}</p>
          </div>
          <p className={`ml-auto max-w-sm pb-1 text-xs ${tone}`}>
            {part === "ops"
              ? "대부분의 기록은 AI가 근거를 보고 바로 남깁니다. 근거가 약해 확신이 낮은 기록만 여기로 옵니다."
              : "인계자만 알 수 있는 내용을 묻는 질문만 여기로 옵니다."}
          </p>
        </div>
        {!HUMAN_TOKEN_SET && todo > 0 && <p className="mt-3 text-xs text-amber-800">사람 권한(VITE_SAFETY_HUMAN_TOKEN)이 설정되지 않아 버튼이 꺼져 있습니다.</p>}
        {todo > 0 && (
          <div className="mt-5 space-y-3">
            <Candidates cards={cards} onChanged={load} />
            {questions.map((q) => <QuestionForm key={q.question_id} q={q} onDone={load} />)}
          </div>
        )}
      </section>

      {part === "hr" && <>
      {/* ② 인수인계 */}
      <Section title="인수인계 현황" desc="담당이 바뀔 때 넘길 일을 모아 둔 목록입니다. 완성도는 코드가 계산한 값이고, 시작과 확정은 총괄 AI가 요청합니다.">
        <div className="grid gap-3 sm:grid-cols-4">
          <Tile label="확정됨" count={count("confirmed")} note="인계가 끝남" tone="bg-emerald-50 text-emerald-800" />
          <Tile label="확정 가능" count={count("ready")} note="항목이 모두 찼음" tone="bg-brand-50 text-brand-700" />
          <Tile label="답변 기다림" count={count("input_required")} note="인계자 답이 필요함" tone="bg-amber-50 text-amber-800" />
          <Tile label="사람 확인 대기" count={count("held")} note="완성도가 낮은 채 확정 요청됨" tone="bg-red-50 text-red-800" />
        </div>
        {handovers.length === 0
          ? <p className="py-6 text-center text-sm text-ink-500">진행 중인 인수인계가 없습니다.</p>
          : <div className="space-y-3">{handovers.map((h) => <HandoverCard key={h.handover_id} h={h} />)}</div>}
      </Section>

      {/* ③ 담당 명부 */}
      <Section title="담당 명부" desc={`${shortDateTime(roster.collected_at)} 기준. 영역별 담당과 예비 담당이고 개인 정보는 두지 않습니다.`}>
        {roster.gap_areas.length > 0 && <p className="rounded-xl bg-amber-50 px-4 py-2 text-sm text-amber-800">담당이 비는 영역: {roster.gap_areas.join(", ")}</p>}
        <RosterGrid roster={roster} />
      </Section>
      </>}

      {part === "ops" && <>
      {/* ④ 기록 도구 */}
      <Section title="계획 비교" desc="새 계획이 과거에 기각된 안과 겹치는지 확인합니다. 저장하지 않고 보기만 합니다."><PlanChecker /></Section>
      <Section title="AI가 한 일" desc="사건 번호로 AI의 처리 내역을 시간순으로 봅니다."><Activity /></Section>
      </>}
    </div>
  );
}

// ---------------------------------------------------------------- 후보 기록
function Candidates({ cards, onChanged }: { cards: HrCard[]; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  async function decide(c: HrCard, decision: "approve" | "reject") {
    setBusy(c.card_id); setMsg(null);
    try { await hr.decide(c.card_id, decision); onChanged(); }
    catch (e) { setMsg(errText(e)); }
    finally { setBusy(null); }
  }
  return (
    <>
      {msg && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{msg}</p>}
      {cards.map((c) => (
        <article key={c.card_id} className="grid gap-4 rounded-2xl bg-surface p-4 shadow-sm sm:grid-cols-[1fr_auto] sm:items-center">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <b className="text-[15px]">{c.title}</b>
              <Chip label="후보 기록" tone="bg-amber-100 text-amber-800" />
              {c.confidence && <Chip label={`확신 ${CONF_KO[c.confidence]}`} tone="bg-ink-100 text-ink-500" />}
            </div>
            <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-ink-50 px-3 py-2 text-xs leading-relaxed text-ink-700">{c.body}</pre>
            <p className="mt-1.5 text-xs text-ink-500">{c.author}가 {ago(c.created_at)} 올림</p>
          </div>
          <div className="flex gap-2 sm:flex-col">
            <button className="btn-primary" disabled={!HUMAN_TOKEN_SET || busy !== null} onClick={() => decide(c, "approve")}>기록으로 확정</button>
            <button className="btn-ghost" disabled={!HUMAN_TOKEN_SET || busy !== null} onClick={() => decide(c, "reject")}>폐기</button>
          </div>
        </article>
      ))}
    </>
  );
}

function QuestionForm({ q, onDone }: { q: HandoverQuestion; onDone: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault(); if (!text.trim()) return;
    setBusy(true); setErr(null);
    try { await hr.answer(q.question_id, text.trim()); setText(""); onDone(); }
    catch (x) { setErr(errText(x)); }
    finally { setBusy(false); }
  }
  return (
    <form onSubmit={submit} className="space-y-2 rounded-2xl bg-surface p-4 shadow-sm">
      <p className="text-sm">
        {FIELD_KO[q.gap_field] && <b className="mr-1.5">{FIELD_KO[q.gap_field]}</b>}{q.question}
      </p>
      <div className="flex gap-2">
        <input className={INPUT} value={text} onChange={(e) => setText(e.target.value)} placeholder={`${q.assignee_id} 님의 답변`} />
        <button className="btn-primary shrink-0" disabled={!HUMAN_TOKEN_SET || busy || !text.trim()}>답하기</button>
      </div>
      {err && <p role="alert" className="text-xs text-red-700">{err}</p>}
    </form>
  );
}

// ---------------------------------------------------------------- 인수인계
function HandoverCard({ h }: { h: HandoverView }) {
  const st = HANDOVER_STATUS[h.status] ?? { label: h.status, tone: "bg-ink-100 text-ink-500" };
  return (
    <article className="space-y-3 rounded-2xl border border-ink-100 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Chip label={st.label} tone={st.tone} />
        <Chip label={h.area} tone="bg-ink-100 text-ink-500" />
        <Chip label={REASON_KO[h.reason] ?? h.reason} tone="bg-ink-100 text-ink-500" />
        <span className="ml-auto text-xs text-ink-500">{h.owner_id} → {h.successor_id ?? "후임 미정"} · {ago(h.updated_at)}</span>
      </div>
      <div>
        <div className="flex items-baseline justify-between text-xs"><span className="text-ink-500">완성도 (코드가 계산)</span><span className="font-semibold tabular-nums">{h.completeness}%</span></div>
        <div className="mt-1 h-2 overflow-hidden rounded-full bg-ink-100"><div className="h-full bg-brand-500" style={{ width: `${h.completeness}%` }} /></div>
      </div>
      {h.items.length > 0 && (
        <ul className="grid gap-1.5 sm:grid-cols-2">
          {h.items.map((i) => (
            <li key={i.item_id} className="flex items-center gap-2 rounded-lg bg-ink-50 px-2.5 py-1.5 text-xs">
              <Chip label={ITEM_KO[i.kind] ?? i.kind} tone="bg-app text-ink-500" /><span className="truncate">{i.title}</span>
            </li>
          ))}
        </ul>
      )}
      {h.open_questions.length > 0 && <p className="text-xs text-amber-800">비어 있는 항목 {h.open_questions.length}개 — 위 "지금 사람이 할 일"에서 답할 수 있습니다.</p>}
    </article>
  );
}

// ---------------------------------------------------------------- 담당 명부
function RosterGrid({ roster }: { roster: Roster }) {
  const areas = roster.entries.reduce<Record<string, Roster["entries"]>>((acc, e) => { (acc[e.area] ||= []).push(e); return acc; }, {});
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {Object.entries(areas).map(([area, rows]) => (
        <article key={area} className="space-y-2 rounded-2xl border border-ink-100 p-4">
          <h3 className="text-sm font-semibold">{area}</h3>
          {rows.map((r) => (
            <div key={`${r.member_id}${r.shift_start}`} className="flex items-center gap-2 text-xs">
              <Chip label={r.role === "owner" ? "담당" : "예비"} tone={r.role === "owner" ? "bg-brand-50 text-brand-700" : "bg-ink-100 text-ink-500"} />
              <span className="font-medium">{r.member_id}</span>
              <span className="ml-auto text-ink-500">{r.shift_start ? `${shortDateTime(r.shift_start)} ~ ${shortDateTime(r.shift_end)}` : "상시"}</span>
            </div>
          ))}
        </article>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- 계획 비교·감사 기록
function PlanChecker() {
  const [draft, setDraft] = useState<PlanDraft>(DEMO_DRAFT);
  const [result, setResult] = useState<PlanCheck | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setErr(null);
    try { setResult(await hr.checkPlan(draft)); } catch (x) { setErr(errText(x)); setResult(null); }
    finally { setBusy(false); }
  }
  const v = result ? VERDICT[result.verdict] ?? { label: result.verdict, tone: "bg-ink-100 text-ink-500" } : null;

  return (
    <div className="space-y-3">
      <form onSubmit={submit} className="grid gap-2 sm:grid-cols-2">
        <input className={INPUT} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="계획 제목" required />
        <input className={INPUT} value={draft.area} onChange={(e) => setDraft({ ...draft, area: e.target.value })} placeholder="영역 (예: payment)" required />
        <input className={`${INPUT} sm:col-span-2`} value={draft.summary} onChange={(e) => setDraft({ ...draft, summary: e.target.value })} placeholder="한 줄 요약" required />
        <div className="flex items-center gap-2 sm:col-span-2">
          <select className={INPUT} value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as PlanDraft["kind"] })}>
            <option value="spec">기획·명세</option><option value="ticket">티켓</option><option value="deploy_plan">배포 계획</option>
          </select>
          <button className="btn-primary shrink-0" disabled={busy}>비교하기</button>
        </div>
      </form>
      {err && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{err}</p>}
      {result && v && (
        <div className="space-y-2 rounded-2xl border border-ink-100 p-4">
          <Chip label={v.label} tone={v.tone} />
          {result.findings.map((f, i) => <p key={i} className="rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">{f.message}</p>)}
          {result.no_related_cards && <p className="text-xs text-ink-500">관련된 과거 기록을 찾지 못했습니다. 기록에 없는 것이지 문제가 없다는 뜻은 아닙니다.</p>}
        </div>
      )}
    </div>
  );
}

function Activity() {
  const [ctx, setCtx] = useState("ctx_hnd_0001");
  const [rows, setRows] = useState<Awaited<ReturnType<typeof hr.activity>> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function load(e: FormEvent) {
    e.preventDefault(); setErr(null);
    try { setRows(await hr.activity(ctx.trim())); } catch (x) { setErr(errText(x)); setRows(null); }
  }
  return (
    <div className="space-y-3">
      <form onSubmit={load} className="flex gap-2">
        <input className={INPUT} value={ctx} onChange={(e) => setCtx(e.target.value)} placeholder="사건 번호 (ctx_...)" />
        <button className="btn-ghost shrink-0">보기</button>
      </form>
      {err && <p role="alert" className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{err}</p>}
      {rows && (rows.length === 0 ? <p className="text-xs text-ink-500">이 사건에서 기록된 일이 없습니다.</p> : (
        <ol className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
          {rows.map((r) => (
            <li key={r.seq} className="flex flex-wrap items-baseline gap-x-3 px-4 py-2 text-xs">
              <span className="tabular-nums text-ink-500">{shortDateTime(r.at)}</span>
              <span className="font-medium">{r.kind}</span>
              <span className="text-ink-500">{Object.entries(r.payload).map(([k, val]) => `${k}: ${String(val)}`).join(" · ")}</span>
            </li>
          ))}
        </ol>
      ))}
    </div>
  );
}
