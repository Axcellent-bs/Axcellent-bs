import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "@/lib/nav";
import { Chip, TONE } from "@/components/finance/bits";
import { RULE_KO } from "@/components/finance/SafetyQueue";
import { actionKo, holdPage, holdWhy, speaker } from "@/lib/agentChat";
import { ACTION_KO as FINANCE_ACTION_KO, type ExecStatus, type ExecutionRecord, type Grade, type Review } from "@/lib/finance";
import { ago, shortDateTime, won } from "@/lib/format";
import { SAFETY_ACTION_KO, SAFETY_RULE_KO, safety, type ConnectionHold, type RegisteredAction, type SafetyThresholds } from "@/lib/safety";

// 안전규칙 AI 화면: 모든 AI의 상태 변경(돈·게시·배포)은 execute()로 안전규칙 AI를 지난다.
// ① 사람 결정 대기 → ② 최근 판단 | ③ 등록된 일(두 칸) → ④ 연결 보류 → ⑤ 지키는 기준.
// ① 사람 결정 대기는 모든 AI의 C 등급 보류다(모든 AI의 상태 변경이 execute()로 이 관문을 지나므로 따로 연동할 곳이 없다).
// 모두 안전규칙 AI 서버(8001)의 기록을 그대로 읽는다. 결정은 그 일을 맡은 화면에서 사람이 한다 (여기서는 링크만).

const POLL_MS = 15_000;
const RECENT = 40;

const GRADE: Record<Grade, { label: string; tone: string; desc: string }> = {
  A: { label: "A 바로 실행", tone: TONE.ok, desc: "기준 안이라 바로 실행" },
  B: { label: "B 검증 후 실행", tone: TONE.info, desc: "다른 AI가 금액을 다시 계산해 맞으면 실행" },
  C: { label: "C 사람 결정", tone: TONE.warn, desc: "사람이 승인해야 실행" },
};
const STATUS: Record<ExecStatus, { label: string; tone: string }> = {
  executed: { label: "실행됨", tone: TONE.ok },
  held: { label: "보류", tone: TONE.warn },
  rejected: { label: "거절", tone: TONE.bad },
  verifying: { label: "검증 중", tone: TONE.muted },
  failed: { label: "실행 오류", tone: TONE.bad },
};
const OP_KO: Record<string, string> = { ">": "초과", ">=": "이상", "<": "미만", "<=": "이하" };
const MONEY_METRIC = new Set(["amount", "unit", "daily_total", "context_total", "customer_total"]);

/** 일 이름. 대화방(AI 대화방) → 재무 → 안전규칙 화면 이름표 순서로 찾는다. 채팅 일(…_chat_send/close)은 묶어 부른다 */
function jobKo(a: string): string {
  if (/_chat_(send|close)$/.test(a)) return a.endsWith("_send") ? "AI 대화 보내기" : "AI 대화 닫기";
  if (/^dev_task_/.test(a)) return "개발 작업 관리";
  const ko = actionKo(a);
  return ko !== a ? ko : FINANCE_ACTION_KO[a] ?? SAFETY_ACTION_KO[a] ?? a;
}
const ruleKo = (key: string) => RULE_KO[key] ?? SAFETY_RULE_KO[key] ?? key;
const errText = (e: unknown) => (e instanceof Error ? e.message : "불러오지 못했습니다");
const duration = (sec: number) => (sec >= 3600 ? `${Math.round(sec / 3600)}시간` : sec >= 60 ? `${Math.round(sec / 60)}분` : `${sec}초`);

function Section({ title, desc, right, children }: { title: string; desc?: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="card min-w-0 space-y-4 px-8 py-6">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">{title}</h2>
          {desc && <p className="mt-1 text-sm text-ink-500">{desc}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

const Empty = ({ children }: { children: ReactNode }) => <p className="rounded-xl bg-ink-50 px-4 py-3 text-sm text-ink-500">{children}</p>;
const Problem = ({ children }: { children: ReactNode }) => <p className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{children}</p>;

/** 보류 까닭: 걸린 기준 이름, 없으면 보류 코드 */
function reviewWhy(r: Review): string {
  const rules = (r.report.rules ?? []).map((h) => ruleKo(h.key));
  if (rules.length) return [...new Set(rules)].join(", ");
  return holdWhy(r.report.hold_reason) || r.report.reason || "사람 확인이 필요한 일";
}

function ruleText(rule: SafetyRule): string {
  // 경로·종류 규칙은 숫자가 아니라 "걸리면" 보류한다
  if (rule.value == null || rule.metric.startsWith("paths_") || rule.metric === "param_in") return "걸리면 보류";
  if (rule.op === ">" && rule.value === 0) return "있으면 보류";
  const v = MONEY_METRIC.has(rule.metric) ? won(rule.value) : rule.value.toLocaleString("ko-KR");
  return `${v} ${OP_KO[rule.op] ?? rule.op}이면 보류`;
}
type SafetyRule = SafetyThresholds["rules"][number];

export default function Safety() {
  const [execs, setExecs] = useState<ExecutionRecord[] | null>(null);
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [holds, setHolds] = useState<ConnectionHold[] | null>(null);
  const [thresholds, setThresholds] = useState<SafetyThresholds | null>(null);
  const [registry, setRegistry] = useState<RegisteredAction[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [at, setAt] = useState<string | null>(null);

  const load = useCallback(async () => {
    const got = await Promise.allSettled([safety.executions(), safety.reviews(), safety.connectionHolds(), safety.thresholds(), safety.registry()]);
    const [e, r, h, t, g] = got;
    if (e.status === "fulfilled") setExecs(e.value);
    if (r.status === "fulfilled") setReviews(r.value);
    if (h.status === "fulfilled") setHolds(h.value);
    if (t.status === "fulfilled") setThresholds(t.value);
    if (g.status === "fulfilled") setRegistry(g.value);
    const failed = got.find((x) => x.status === "rejected");
    setProblem(failed && failed.status === "rejected" ? errText(failed.reason) : null);
    setAt(new Date().toISOString());
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const recent = useMemo(() => (execs ? [...execs].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, RECENT) : []), [execs]);
  const pending = useMemo(() => (reviews ?? []).filter((r) => r.status === "pending").sort((a, b) => a.created_at.localeCompare(b.created_at)), [reviews]);
  const counts = useMemo(() => {
    const c = { total: execs?.length ?? 0, executed: 0, held: 0, rejected: 0 };
    for (const x of execs ?? []) {
      if (x.decision.status === "executed") c.executed += 1;
      else if (x.decision.status === "held") c.held += 1;
      else if (x.decision.status === "rejected" || x.decision.status === "failed") c.rejected += 1;
    }
    return c;
  }, [execs]);
  const activeHolds = (holds ?? []).filter((h) => h.hold);
  const rulesByAction = useMemo(() => {
    const m = new Map<string, SafetyRule[]>();
    for (const r of thresholds?.rules ?? []) m.set(r.action, [...(m.get(r.action) ?? []), r]);
    return m;
  }, [thresholds]);

  return (
    <div className="space-y-6 px-8">
      <header className="card space-y-4 px-8 py-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">안전규칙 AI</h1>
            <p className="mt-1 max-w-3xl text-sm text-ink-500">
              다른 AI가 돈을 쓰거나(쿠폰·환불·정산·캠페인), 밖에 글을 올리거나(공지·SNS), 배포를 바꾸려면 먼저 안전규칙 AI를 거칩니다.
              등급을 매겨 바로 실행하거나, 다른 AI에게 금액을 다시 계산하게 하거나, 사람 결정을 기다리게 보류합니다. AI끼리 A2A로 주고받는 기록은{" "}
              <Link href="/agents/chat?rooms=all" className="text-brand-700 underline">AI 대화방</Link>에서도 볼 수 있어요.
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <Link href="/agents/chat?rooms=direct:safety" className="btn-primary text-sm">안전규칙 AI에게 묻기</Link>
            <span className="text-sm text-ink-500">{at ? `${shortDateTime(at)} 기준 · 15초마다 새로 읽음` : "불러오는 중…"}</span>
          </div>
        </div>
        {problem && <Problem>{problem}</Problem>}
        <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {[
            { k: "판단한 요청", v: counts.total, sub: "서버가 켜진 뒤" },
            { k: "실행됨", v: counts.executed, sub: "A·B 등급, 승인 뒤 실행 포함" },
            { k: "사람 결정 대기", v: pending.length, sub: "C 등급 보류", warn: pending.length > 0 },
            { k: "거절·오류", v: counts.rejected, sub: "검증 거절·기한 지남 포함" },
          ].map((s) => (
            <div key={s.k} className="rounded-xl border border-ink-100 px-4 py-3">
              <dt className="text-sm text-ink-500">{s.k}</dt>
              <dd className={`mt-1 text-2xl font-semibold tabular-nums ${s.warn ? "text-amber-700" : ""}`}>{execs ? s.v.toLocaleString("ko-KR") : "–"}</dd>
              <dd className="text-sm text-ink-500">{s.sub}</dd>
            </div>
          ))}
        </dl>
      </header>

      <Section title="① 사람 결정 대기" desc="C 등급이라 사람이 승인해야 실행되는 일입니다. 결정은 그 일을 맡은 화면에서 합니다. 기한 안에 아무도 정하지 않으면 자동으로 취소돼요.">
        {!reviews ? <Empty>불러오는 중…</Empty> : pending.length === 0 ? <Empty>지금 사람 결정을 기다리는 일이 없어요.</Empty> : (
          <ul className="divide-y divide-ink-100">
            {pending.map((r) => (
              <li key={r.review_id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium">{speaker(r.requester)} · {jobKo(r.action)}{r.report.computed_amount != null && <span className="tabular-nums"> · {won(r.report.computed_amount)}</span>}</p>
                  <p className="mt-0.5 text-sm text-ink-500">{reviewWhy(r)} · {ago(r.created_at)} 요청 · {shortDateTime(r.expires_at)}까지</p>
                </div>
                <Link href={holdPage(r.action)} className="btn-ghost text-sm">결정하러 가기</Link>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <div className="grid gap-6 xl:grid-cols-2">
        <Section title="② 최근 판단" desc={`안전규칙 AI가 받은 실행 요청과 판단 결과입니다 (최근 ${RECENT}건).`}>
          {!execs ? <Empty>불러오는 중…</Empty> : recent.length === 0 ? <Empty>아직 받은 실행 요청이 없어요.</Empty> : (
            <div className="max-h-[640px] overflow-auto">
              <table className="w-full min-w-[520px] text-sm">
                <thead className="sticky top-0 bg-surface text-left text-sm text-ink-500">
                  <tr><th className="py-2 pr-3 font-medium">시각</th><th className="pr-3 font-medium">요청한 AI</th><th className="pr-3 font-medium">일</th><th className="pr-3 text-right font-medium">금액</th><th className="font-medium">판단</th></tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {recent.map((x) => {
                    const d = x.decision;
                    const st = STATUS[d.status] ?? { label: d.status, tone: TONE.muted };
                    return (
                      <tr key={x.execution_id}>
                        <td className="py-2 pr-3 tabular-nums text-ink-500">{shortDateTime(x.created_at)}</td>
                        <td className="pr-3">{speaker(x.request.requester)}</td>
                        <td className="pr-3">{jobKo(x.request.action)}</td>
                        <td className="pr-3 text-right tabular-nums">{d.computed_amount != null ? won(d.computed_amount) : "–"}</td>
                        <td className="py-2">
                          <span className="flex flex-wrap items-center gap-1">
                            <Chip label={GRADE[d.grade]?.label ?? d.grade} tone={GRADE[d.grade]?.tone ?? TONE.muted} />
                            <Chip label={st.label} tone={st.tone} />
                          </span>
                          {d.hold_reason && <span className="mt-1 block text-sm text-ink-500">{holdWhy(d.hold_reason)}</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Section>

        <Section title="③ 등록된 일" desc="안전규칙 AI가 아는 일과 기본 등급입니다. 등록되지 않은 일은 실행하지 않고 보류합니다.">
          {!registry ? <Empty>불러오는 중…</Empty> : (
            <div className="max-h-[640px] overflow-auto">
              <table className="w-full min-w-[460px] text-sm">
                <thead className="sticky top-0 bg-surface text-left text-sm text-ink-500">
                  <tr><th className="py-2 pr-3 font-medium">일</th><th className="pr-3 font-medium">맡은 AI</th><th className="pr-3 font-medium">기본 등급</th><th className="font-medium">금액 다시 계산</th></tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {registry.map((a) => (
                    <tr key={a.action}>
                      <td className="py-2 pr-3">{jobKo(a.action)}</td>
                      <td className="pr-3">{speaker(a.owner)}</td>
                      <td className="pr-3"><Chip label={GRADE[a.base_grade]?.label ?? a.base_grade} tone={GRADE[a.base_grade]?.tone ?? TONE.muted} /></td>
                      <td className="text-ink-500">{a.verifier ? speaker(a.verifier) : "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      </div>

      <Section title="④ 연결 보류" desc="개발 AI가 외부 연결 서버에서 실제로 악용 중인 취약점(CISA KEV)을 찾으면, 패치될 때까지 그 연결로 쓰는 일을 보류합니다.">
        {!holds ? <Empty>불러오는 중…</Empty> : activeHolds.length === 0 ? <Empty>보류 중인 연결이 없어요.</Empty> : (
          <ul className="divide-y divide-ink-100">
            {activeHolds.map((h) => (
              <li key={h.connection_id} className="py-3 text-sm">
                <p className="font-medium">{h.product} {h.version}</p>
                <p className="mt-0.5 text-sm text-ink-500">{h.kev_cves.join(", ")} · {shortDateTime(h.judged_at)} 판단 · {h.reason}</p>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="⑤ 지키는 기준" desc="이 기준에 걸리면 바로 실행하지 않고 사람 결정으로 보류합니다."
        right={thresholds && <span className="text-sm text-ink-500">기준 {thresholds.version}판 · 검증 대기 {duration(thresholds.verify_timeout_seconds)} · 사람 결정 기한 {duration(thresholds.review_timeout_seconds)}</span>}>
        {!thresholds ? <Empty>불러오는 중…</Empty> : thresholds.rules.length === 0 ? <Empty>등록된 기준이 없어요.</Empty> : (
          <div className="grid gap-3 md:grid-cols-2">
            {[...rulesByAction.entries()].map(([action, rules]) => (
              <div key={action} className="rounded-xl border border-ink-100 px-4 py-3">
                <p className="text-sm font-medium">{jobKo(action)}</p>
                <ul className="mt-2 space-y-1 text-sm text-ink-700">
                  {rules.map((r) => (
                    <li key={r.key} className="flex justify-between gap-3">
                      <span>{ruleKo(r.key)}</span>
                      <span className="shrink-0 tabular-nums text-ink-500">{ruleText(r)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
