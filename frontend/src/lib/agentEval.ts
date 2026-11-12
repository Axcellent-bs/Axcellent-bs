// 에이전트 평가 화면 → 인사 AI가 모은 판정 (dashboard-api /agent-evaluation). 모양은 contracts/hr.py 그대로다.
// 화면은 점수·등급·순위를 만들지 않는다. 사람이 "제때 끝냈다 / 늦었다"를 바로 읽도록 문장과 점으로만 보인다.

export type Verdict = "normal" | "check" | "no_record" | "blocked" | "approval_expired" | "silent" | "in_progress" | "absent" | "handed_off";

export interface AgentEventDot {
  context_id: string;
  title: string;
  at: string | null;
  verdict: Verdict;
  reason: string | null;
  steps: number;
}
export interface RepeatEventType { event_type: string; title: string; count: number }
export interface AgentSummary {
  dept: string;
  events: number;
  normal: number;
  check: number;
  no_record: number;
  blocked: number;
  handed_off: number;
  approval_expired: number;
  silent: number;
  in_progress: number;
  retried: number;
  slow: number;
  avg_work_seconds: number | null;
  max_work_seconds: number | null;
  repeats: RepeatEventType[];
  recent: AgentEventDot[];
}
export interface EvaluatedStep {
  step_id: string;
  label: string;
  dept: string;
  group: string;
  verdict: Verdict;
  work_seconds: number | null;
  approval_wait_seconds: number | null;
  human_wait_seconds: number | null;
  attempts: number;
  limit_seconds: number;
  max_silence_seconds: number | null;
  reason: string | null;
}
export interface EvaluatedEvent {
  context_id: string;
  title: string;
  event_type: string | null;
  at: string | null;
  criteria_version: number;
  steps: EvaluatedStep[];
}
export interface DailyTrend { day: string; events: number; with_check: number }
export interface AgentEvaluation {
  criteria_version: number;
  criteria_updated: string;
  normal_max_seconds: number;
  normal_max_attempts: number;
  events: EvaluatedEvent[];
  agents: AgentSummary[];
  trend: DailyTrend[];
  collected_at: string;
}

/** 화면에 보이는 에이전트 이름 (총괄은 평가 대상이 아니다) */
export const AGENT_NAME: Record<string, string> = {
  infra: "인프라 AI",
  data: "데이터분석 AI",
  dev: "개발 AI",
  support: "고객지원 AI",
  marketing: "마케팅 AI",
  finance: "재무 AI",
  hr: "인사 AI",
};
export const agentName = (dept: string) => AGENT_NAME[dept] ?? dept;

// 색만으로 구분하지 않도록 모양(mark)과 글자(label)를 같이 쓴다
export const VERDICT: Record<Verdict, { label: string; mark: string; dot: string; chip: string }> = {
  normal: { label: "제때 끝냄", mark: "●", dot: "text-emerald-600", chip: "bg-emerald-50 text-emerald-700" },
  check: { label: "확인 필요", mark: "▲", dot: "text-amber-600", chip: "bg-amber-50 text-amber-700" },
  no_record: { label: "기록 없음", mark: "○", dot: "text-ink-400", chip: "bg-ink-100 text-ink-600" },
  blocked: { label: "실패·막힘", mark: "✕", dot: "text-red-600", chip: "bg-red-50 text-red-700" },
  // 사람에게 넘김은 설계된 동작이라 실패처럼 보이지 않게 빨강·노랑을 피하고 중립 색을 쓴다
  handed_off: { label: "사람에게 넘김", mark: "⇢", dot: "text-sky-700", chip: "bg-sky-50 text-sky-800" },
  approval_expired: { label: "승인 만료", mark: "✕", dot: "text-red-600", chip: "bg-red-50 text-red-700" },
  // 긴 작업 중 부서의 응답이 끊김. 일을 못한 것이 아니라 소식이 없었다는 뜻이라 실패(빨강)와 다른 색을 쓴다
  silent: { label: "응답 끊김", mark: "◌", dot: "text-orange-600", chip: "bg-orange-50 text-orange-700" },
  in_progress: { label: "진행 중", mark: "…", dot: "text-ink-400", chip: "bg-ink-100 text-ink-600" },
  absent: { label: "참여 안 함", mark: "–", dot: "text-ink-300", chip: "bg-ink-100 text-ink-500" },
};

/** 문장으로 요약할 최근 이벤트 수와, 이보다 적으면 판단을 미루는 기준 */
export const SENTENCE_WINDOW = 5;
export const MIN_EVENTS = 3;

export type Tone = "good" | "look" | "bad" | "none";

/** 에이전트 한 줄 요약. 이벤트가 적으면 단정하지 않는다. */
export function readout(a: AgentSummary): { text: string; tone: Tone } {
  const joined = a.recent.filter((d) => d.verdict !== "absent");
  if (joined.length === 0) return { text: "아직 참여한 이벤트가 없어요.", tone: "none" };
  if (joined.length < MIN_EVENTS) return { text: "아직 기록이 적어서 판단하기 이릅니다.", tone: "none" };
  const last = joined.slice(0, SENTENCE_WINDOW);
  const count = (...v: Verdict[]) => last.filter((d) => v.includes(d.verdict)).length;
  const ok = count("normal");
  if (ok === last.length) return { text: `최근 ${last.length}건 모두 제때 끝냈어요.`, tone: "good" };
  const rest: string[] = [];
  if (count("check")) rest.push(`${count("check")}건은 늦었거나 다시 시도했어요`);
  if (count("blocked")) rest.push(`${count("blocked")}건은 실패하거나 막혔어요`);
  if (count("silent")) rest.push(`${count("silent")}건은 긴 작업 중 응답이 끊겼어요`);
  if (count("approval_expired")) rest.push(`${count("approval_expired")}건은 승인이 만료됐어요`);
  if (count("handed_off")) rest.push(`${count("handed_off")}건은 사람의 확인이 필요해 사람에게 넘겼어요`);
  if (count("no_record")) rest.push(`${count("no_record")}건은 시간 기록이 없어요`);
  if (count("in_progress")) rest.push(`${count("in_progress")}건은 아직 끝나지 않았어요`);
  const bad = count("blocked", "approval_expired") > 0;
  const head = ok ? `최근 ${last.length}건 중 ${ok}건은 제때 끝냈고, ` : `최근 ${last.length}건 중 `;
  return { text: `${head}${rest.join(", ")}.`, tone: bad ? "bad" : count("check", "silent") ? "look" : "none" };
}

export const TONE_TEXT: Record<Tone, string> = {
  good: "text-emerald-800",
  look: "text-amber-800",
  bad: "text-red-800",
  none: "text-ink-600",
};

/** 3.5초 · 42초 · 2분 5초 */
export function secondsKo(s: number | null | undefined): string {
  if (s == null) return "–";
  if (s < 10) return `${s.toFixed(1)}초`;
  const r = Math.round(s);
  if (r < 60) return `${r}초`;
  const m = Math.floor(r / 60);
  return r % 60 === 0 ? `${m}분` : `${m}분 ${r % 60}초`;
}
