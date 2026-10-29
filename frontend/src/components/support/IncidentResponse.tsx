import { useMemo, useState } from "react";
import { Chip, Empty, Panel, ResourceBody, TONE, useResource } from "@/components/finance/bits";
import { Link } from "@/lib/nav";
import { STEP_STATUS, adminRelay, relayEnabled, type Incident } from "@/lib/adminRelay";
import { incidentTitle, skillKo, stepLabel } from "@/lib/agentChat";
import { shortDateTime } from "@/lib/format";
import { support, type ExecutionRow } from "@/lib/support";

// 사건 대응 기록: 결제 서버 오류 같은 사건이 났을 때 고객지원이 처리한 일이 언제 적용됐는지 시간순으로 남긴다.
// 자료는 두 곳이고 화면이 새로 만들지 않는다: 총괄 사건(시작 시각, 고객지원 단계의 시각)과 안전 관문 실행 기록(처리 시각).
// 안전 관문이 "실행함"으로 판정한 건만 적용됐다고 본다. 보류(C)·검증 중·거부는 아직 적용 전이다.

/** 고객지원이 하는 일(상태를 바꾸는 action). 고객지원이 요청해서 마케팅이 지급하는 쿠폰도 함께 본다 */
const SUPPORT_ACTIONS = new Set([
  "create_inquiry", "add_customer_message", "send_reply", "reject_draft", "set_inquiry_status", "record_claim_activity",
  "create_claim", "decide_claim", "receive_return", "inspect_return", "refund_request", "request_change", "issue_coupon",
]);

type Applied = "applied" | "waiting" | "blocked";
export interface Row { id: string; at: string; source: "step" | "exec"; label: string; who: string; state: Applied; stateLabel: string; grade: string | null }

const EXEC_STATE: Record<ExecutionRow["decision"]["status"], { state: Applied; label: string }> = {
  executed: { state: "applied", label: "적용됨" },
  verifying: { state: "waiting", label: "검증 중 (적용 전)" },
  held: { state: "waiting", label: "승인 대기 (적용 전)" },
  rejected: { state: "blocked", label: "거부됨" },
  failed: { state: "blocked", label: "실행 실패" },
};
const STATE_TONE: Record<Applied, string> = { applied: TONE.ok, waiting: TONE.warn, blocked: TONE.bad };
// 안전 관문 기록의 요청자는 부서 이름이나 서비스 이름(inquiry·claims·coupon 등)으로 온다
const SUPPORT_REQUESTERS = new Set(["support", "inquiry", "claims", "reviews"]);
const WHO: Record<string, string> = {
  support: "고객지원 AI", inquiry: "고객지원 AI", claims: "고객지원 AI", reviews: "고객지원 AI",
  marketing: "마케팅 AI", coupon: "마케팅 AI", orchestrator: "총괄 AI", "dashboard-api": "상담원(화면)", finance: "재무 AI",
};

/** 총괄 단계와 안전 관문 실행 기록을 한 줄씩 시간순으로 합친다 */
export function buildRows(incident: Incident, executions: ExecutionRow[]): Row[] {
  const steps: Row[] = incident.steps
    .filter((s) => s.dept === "support" && s.started_at)
    .map((s) => {
      const done = s.status === "completed";
      const blocked = s.status === "failed" || s.status === "rejected_by_human" || s.status === "skipped";
      return {
        id: `step-${s.step_id}`, at: (done ? s.finished_at : null) ?? s.started_at!, source: "step" as const, label: stepLabel(s.label), who: "고객지원 AI",
        state: done ? "applied" : blocked ? "blocked" : "waiting",
        stateLabel: done ? "적용됨" : STEP_STATUS[s.status]?.label ?? s.status, grade: s.grade,
      };
    });
  const execs: Row[] = executions
    .filter((e) => SUPPORT_REQUESTERS.has(e.request.requester) || SUPPORT_ACTIONS.has(e.request.action))
    .map((e) => {
      const st = EXEC_STATE[e.decision.status] ?? { state: "waiting" as const, label: e.decision.status };
      return {
        id: `exec-${e.execution_id}`, at: e.created_at, source: "exec" as const, label: skillKo(e.request.action), who: WHO[e.request.requester] ?? e.request.requester,
        state: st.state, stateLabel: st.label, grade: e.decision.grade,
      };
    });
  return [...steps, ...execs].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

/** 사건이 열린 뒤 몇 분 몇 초 만인지 */
export function elapsed(fromIso: string, atIso: string): string {
  const sec = Math.round((Date.parse(atIso) - Date.parse(fromIso)) / 1000);
  if (Number.isNaN(sec)) return "–";
  if (sec < 0) return "사건 시작 전";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return `+${h ? `${h}시간 ` : ""}${m || h ? `${m}분 ` : ""}${s}초`;
}
const secondsOf = (iso: string) => new Date(iso).toLocaleTimeString("ko-KR", { hour12: false });

function Detail({ incident, tick }: { incident: Incident; tick: number }) {
  const res = useResource(`support-executions-${incident.context_id}`, () => support.executions(incident.context_id), tick);
  return (
    <ResourceBody res={res}>
      {(executions) => {
        const rows = buildRows(incident, executions);
        const applied = rows.filter((r) => r.state === "applied");
        const waiting = rows.filter((r) => r.state === "waiting").length;
        const first = applied[0], last = applied[applied.length - 1];
        const stats: [string, string, string][] = [
          ["사건이 열린 시각", secondsOf(incident.opened_at), shortDateTime(incident.opened_at)],
          ["첫 대응이 적용되기까지", first ? elapsed(incident.opened_at, first.at).replace("+", "") : "아직 없음", first ? `${secondsOf(first.at)} · ${first.label}` : "적용된 처리가 없습니다"],
          ["마지막으로 적용된 시각", last ? secondsOf(last.at) : "–", last ? `${elapsed(incident.opened_at, last.at)} · ${last.label}` : ""],
          ["적용 / 적용 전", `${applied.length}건 / ${waiting}건`, rows.length ? `전체 ${rows.length}건` : ""],
        ];
        return (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {stats.map(([label, value, sub]) => (
                <div key={label} className="rounded-2xl bg-surface-2 px-3 py-2">
                  <dt className="text-[11px] text-ink-500">{label}</dt>
                  <dd className="text-base font-semibold tabular-nums">{value}</dd>
                  <dd className="break-words text-[11px] text-ink-500">{sub}</dd>
                </div>
              ))}
            </dl>
            {rows.length === 0 ? (
              <Empty>이 사건에서 고객지원이 처리한 기록이 아직 없습니다. 문의가 이 사건에 묶여 처리되거나 보상이 요청되면 여기에 시간순으로 쌓입니다.</Empty>
            ) : (
              <div className="scroll-thin overflow-x-auto">
                <table className="w-full min-w-[640px] text-left text-sm">
                  <thead className="text-[11px] text-ink-500">
                    <tr><th className="pb-1 pr-3 font-medium">적용 시각</th><th className="pb-1 pr-3 font-medium">사건 시작 후</th><th className="pb-1 pr-3 font-medium">처리한 내용</th><th className="pb-1 pr-3 font-medium">담당</th><th className="pb-1 font-medium">적용 여부</th></tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id} className="border-t border-ink-100 align-top">
                        <td className="whitespace-nowrap py-2 pr-3 tabular-nums"><b>{secondsOf(r.at)}</b><span className="block text-[11px] text-ink-500">{shortDateTime(r.at)}</span></td>
                        <td className="whitespace-nowrap py-2 pr-3 tabular-nums text-ink-700">{elapsed(incident.opened_at, r.at)}</td>
                        <td className="py-2 pr-3 text-ink-900">{r.label}<span className="ml-1.5 text-[11px] text-ink-500">{r.source === "step" ? "총괄 단계" : "안전 관문 기록"}</span></td>
                        <td className="whitespace-nowrap py-2 pr-3 text-ink-700">{r.who}</td>
                        <td className="whitespace-nowrap py-2"><Chip label={r.stateLabel} tone={STATE_TONE[r.state]} />{r.grade && <span className="ml-1.5 text-[11px] text-ink-500">{r.grade}등급</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="text-[11px] text-ink-500">
              적용 시각은 안전 관문이 &quot;실행함&quot;으로 판정한 시각(또는 총괄 단계가 끝난 시각)입니다. 보류·검증 중인 건은 아직 적용 전이라 시각이 요청 시각입니다.
              문의 단위 처리 내용은 <Link href="/console" className="underline">상담 콘솔</Link>의 AI 처리 내력에서 볼 수 있습니다.
            </p>
          </div>
        );
      }}
    </ResourceBody>
  );
}

export function IncidentResponse({ tick }: { tick: number }) {
  const list = useResource("support-incident-list", async () => ({ data: (await adminRelay.incidents()).data, meta: { mock: false } }), tick);
  const [picked, setPicked] = useState<string | null>(null);
  const incidents = useMemo(
    () => [...(list.env?.data ?? [])].sort((a, b) => Date.parse(b.opened_at) - Date.parse(a.opened_at)),
    [list.env],
  );
  // 기본은 가장 최근의 결제 사건, 없으면 가장 최근 사건
  const current = incidents.find((i) => i.context_id === picked) ?? incidents.find((i) => i.component === "payment") ?? incidents[0];

  if (!relayEnabled) return <Panel id="incident-response" title="사건 대응 기록"><Empty>총괄 읽기 연결이 꺼져 있어 사건을 불러올 수 없습니다.</Empty></Panel>;
  const tools = incidents.length > 1 && current ? (
    <select className="field py-1 text-xs" value={current.context_id} onChange={(e) => setPicked(e.target.value)} aria-label="사건 고르기">
      {incidents.map((i) => <option key={i.context_id} value={i.context_id}>{shortDateTime(i.opened_at)} · {incidentTitle(i)}</option>)}
    </select>
  ) : undefined;
  return (
    <Panel id="incident-response" title="사건 대응 기록" meta={list.env?.meta} tools={tools}
      hint="결제 서버 오류 같은 사건이 났을 때 고객지원이 처리한 일(문의 답변·상태 변경·보상 쿠폰·클레임 등)이 언제 적용됐는지, 사건이 열린 뒤 얼마 만인지 시간순으로 보여 줍니다. 읽기 전용입니다.">
      <ResourceBody res={list}>
        {() => current ? (
          <div className="space-y-3">
            <p className="text-sm"><b>{incidentTitle(current)}</b> <span className="text-ink-500">· {shortDateTime(current.opened_at)} 시작 · {current.status === "open" ? "대응 중" : current.status === "resolved" ? "해결됨" : "사람 확인 필요"}</span></p>
            <Detail incident={current} tick={tick} />
          </div>
        ) : <Empty>아직 열린 사건이 없습니다. 쇼핑몰에서 문제가 감지되면 총괄이 사건을 열고 여기에 대응 기록이 쌓입니다.</Empty>}
      </ResourceBody>
    </Panel>
  );
}
