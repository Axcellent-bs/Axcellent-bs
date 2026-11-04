import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Loading } from "@/components/admin/bits";
import CardSections from "@/components/hr/CardSections";
import IncidentRecords from "@/components/hr/IncidentRecords";
import { HUMAN_TOKEN_SET } from "@/lib/env";
import { ago } from "@/lib/format";
import { useSearchParams } from "@/lib/nav";
import { hr, type HrCard, type PlanCheck, type PlanDraft } from "@/lib/hr";

// 결정 기록 화면 (재무 AI 화면과 같은 짜임): ① 지금 사람이 할 일 → ② 기록 도구.
// hr이 계산한 판정을 그대로 보여 주고, 사람이 하는 일(후보 카드 승인·폐기)만 버튼으로 둔다.

const CONF_KO = { low: "낮음", mid: "보통", high: "높음" } as const;
const VERDICT: Record<string, { label: string; tone: string }> = {
  ok: { label: "겹치는 기록 없음", tone: "bg-emerald-50 text-emerald-700" },
  warn: { label: "확인 필요", tone: "bg-amber-50 text-amber-700" },
  block: { label: "진행 전 확인 필요", tone: "bg-red-50 text-red-700" },
};
const EMPTY_DRAFT: PlanDraft = { title: "", summary: "", area: "", kind: "spec" };
const INPUT = "w-full rounded-xl border border-ink-100 bg-app px-3 py-2 text-sm";

const errText = (e: unknown) => (e instanceof Error ? e.message : "요청에 실패했습니다");
const Chip = ({ label, tone }: { label: string; tone: string }) => <span className={`chip ${tone}`}>{label}</span>;

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

/** `decided`가 null이면 확정 카드 목록을 받지 못한 것(서버가 아직 이 조회를 모를 때) */
interface View { cards: HrCard[]; decided: HrCard[] | null }

const TITLE = "결정 기록";
const DESC = "회사의 결정을 기록으로 남기고, 새 계획이 과거에 기각된 안과 겹치는지 확인합니다.";
const GUIDE: ReactNode[] = [
  <>맨 위 <b>지금 사람이 할 일</b>에서 확신이 낮아 후보로 남은 기록을 봅니다.</>,
  <>내용을 읽고 <b>기록으로 확정</b> 또는 <b>폐기</b>를 누릅니다. 애매하면 폐기하세요. 필요하면 요청한 AI가 근거를 보강해 다시 올립니다.</>,
];

export default function HrPage() {
  const ctxParam = useSearchParams().get("ctx"); // 총괄 이벤트 화면의 "결정 기록에서 보기"로 들어올 때
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [up, setUp] = useState(true);

  const load = useCallback(async () => {
    try {
      const [cards, decided] = await Promise.all([
        hr.candidateCards(),
        hr.decisionCards(undefined, 200).catch(() => null), // 이 조회만 실패해도 화면 나머지는 보여 준다
      ]);
      setView({ cards, decided }); setUp(true);
    } catch (e) { setUp(false); setError(errText(e)); }
  }, []);
  useEffect(() => {
    load();
    const poll = setInterval(load, 15000); // 15초마다 새로 받는다
    return () => clearInterval(poll);
  }, [load]);

  if (!view) return <Loading error={error} />;
  const { cards } = view;
  const todo = cards.length;
  const tone = todo ? "text-amber-800" : "text-emerald-800";

  return (
    <div className="space-y-8 px-8 pb-12 pt-2">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{TITLE}</h1>
          <p className="mt-1 text-sm text-ink-500">{DESC} 완성도와 판정은 AI가 아니라 정해진 계산과 기준으로 합니다.</p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
          <Chip label={`${TITLE} ${up ? "켜짐" : "꺼짐"}`} tone={up ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"} />
        </div>
      </header>

      <details className="rounded-2xl border border-ink-100 bg-surface px-5 py-3 text-sm">
        <summary className="cursor-pointer font-semibold text-ink-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">처음이라면: 관리자가 할 일</summary>
        <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-ink-700">
          {GUIDE.map((g, i) => <li key={i}>{g}</li>)}
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
            대부분의 기록은 AI가 근거를 보고 바로 남깁니다. 근거가 약해 확신이 낮은 기록만 여기로 옵니다.
          </p>
        </div>
        {!HUMAN_TOKEN_SET && todo > 0 && <p className="mt-3 text-xs text-amber-800">사람 권한(VITE_SAFETY_HUMAN_TOKEN)이 설정되지 않아 버튼이 꺼져 있습니다.</p>}
        {todo > 0 && (
          <div className="mt-5 space-y-3">
            <Candidates cards={cards} onChanged={load} />
          </div>
        )}
      </section>

      {/* ② 기록 도구 */}
      {/* 인사 AI가 실제로 남긴 이벤트 기록과 고른 이벤트의 처리 내역 */}
      <IncidentRecords initialCtx={ctxParam} />
      {/* 확정된 카드는 장애 대응(보라)·업무 결정(민트)으로 나눠 보여 준다. 최근 순 최대 200장 */}
      <CardSections cards={view.decided} />
      <Section title="계획 비교" desc="새 계획이 과거에 기각된 안과 겹치는지 확인합니다. 저장하지 않고 보기만 합니다."><PlanChecker /></Section>
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

// ---------------------------------------------------------------- 계획 비교·감사 기록
function PlanChecker() {
  const [draft, setDraft] = useState<PlanDraft>(EMPTY_DRAFT);
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
          {result.findings.map((f, i) => (
            <div key={i} className="space-y-1 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
              <p>{f.message}</p>
              {f.explanation && <p className="text-amber-900/80">AI 설명(참고용): {f.explanation}</p>}
              {f.card_ids.length > 0 && <p className="text-[11px] text-amber-700/80" title="판정 근거로 쓴 결정 카드">근거 카드 {f.card_ids.join(", ")}</p>}
            </div>
          ))}
          {result.no_related_cards && <p className="text-xs text-ink-500">관련된 과거 기록을 찾지 못했습니다. 기록에 없는 것이지 문제가 없다는 뜻은 아닙니다.</p>}
        </div>
      )}
    </div>
  );
}
