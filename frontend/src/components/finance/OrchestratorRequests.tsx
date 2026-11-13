import { useEffect, useState } from "react";
import { Link } from "@/lib/nav";
import { api } from "@/lib/api";
import { humanId, shortDateTime } from "@/lib/format";
import { skillLabel } from "@/lib/labels";
import { orchestrator } from "@/lib/orchestrator";
import { ALL_ROOM } from "@/lib/agentChat";
import type { CommLine } from "@/lib/types";
import type { Envelope } from "@/lib/finance";
import { Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// 총괄이 재무에 맡긴 일: AI 대화방과 같은 공통 로그(dashboard-api GET /api/dashboard/communications)에서
// 총괄 → 재무 요청 줄만 받아 최근 것부터 보여 준다. 결과는 같은 사건·같은 일에 재무가 총괄에 돌려준 응답 줄에서 읽는다.
// 읽기 전용이다. 로그에 없는 줄은 만들지 않는다.

const PAGE = 30;

// 재무 AI skill(backend/services/finance/agent-card.json)의 쉬운 이름. 없는 skill은 skillLabel()이 원래 이름 그대로
const FINANCE_SKILL_KO: Record<string, string> = {
  record_incident_finance: "장애 비용 장부 기록",
  record_support_cost: "고객지원 비용 기록",
  closing_report: "결산 보고서",
  record_campaign_result: "캠페인 결과 장부 기록",
  review_campaign_budget: "캠페인 예산 검토",
  verify_campaign_budget: "캠페인 예산 한도 확인",
  cancel_campaign: "캠페인 취소 기록",
  finance_insights: "재무 요약 보기",
};
const skillName = (skill: string | null) => (skill ? FINANCE_SKILL_KO[skill] ?? skillLabel(skill) : "–");

// 응답 결과(communications.py CommunicationOutcome) → 이 표의 말과 색
const RESULT: Record<string, { label: string; tone: string }> = {
  completed: { label: "완료", tone: TONE.ok },
  failed: { label: "실패", tone: TONE.bad },
  rejected: { label: "거절", tone: TONE.bad },
  held: { label: "보류(안전 관문)", tone: TONE.warn },
  needs_human: { label: "보류(사람 판단 필요)", tone: TONE.warn },
  in_progress: { label: "진행 중", tone: TONE.muted },
};

interface RequestRow { request: CommLine; reply: CommLine | null }
interface RequestsData { rows: RequestRow[]; truncated: boolean; incidentIds: Set<string> }

const keyOf = (l: CommLine) => `${l.context_id ?? ""}|${l.skill ?? ""}`;
const time = (l: CommLine) => new Date(l.occurred_at).getTime();

/** 요청마다 같은 사건·같은 skill의 응답 중 그 요청 뒤, 다음 같은 요청 전의 마지막 응답을 붙인다. 최근 요청이 위로 */
function pair(requests: CommLine[], replies: CommLine[]): RequestRow[] {
  const asc = [...requests].sort((a, b) => time(a) - time(b));
  const rows = asc.map((request, i) => {
    const k = keyOf(request);
    const start = time(request);
    const next = asc.slice(i + 1).find((r) => keyOf(r) === k);
    const end = next ? time(next) : Infinity;
    const mine = replies.filter((r) => keyOf(r) === k && time(r) >= start && time(r) < end);
    return { request, reply: mine.length ? mine.reduce((a, b) => (time(b) >= time(a) ? b : a)) : null };
  });
  return rows.reverse();
}

async function load(): Promise<Envelope<RequestsData>> {
  const [requests, replies] = await Promise.all([
    api.communications({ from_agent: "orchestrator", to_agent: "finance", message_type: "request" }),
    api.communications({ from_agent: "finance", to_agent: "orchestrator", message_type: "reply" }),
  ]);
  // 대화방의 장애·이슈 방은 총괄 사건 하나 = 방 하나(방 id = context_id). 총괄이 꺼져 있으면 전체 대화방으로 잇는다
  const incidents = await orchestrator.incidents().then((r) => r.data, () => []);
  return {
    data: {
      rows: pair(requests.data, replies.data),
      truncated: requests.meta.truncated || replies.meta.truncated,
      incidentIds: new Set(incidents.map((i) => i.context_id)),
    },
    meta: { mock: false },
  };
}

function Row({ row, incidentIds }: { row: RequestRow; incidentIds: Set<string> }) {
  const { request, reply } = row;
  const ctx = request.context_id;
  const result = reply ? RESULT[reply.outcome as string] ?? { label: reply.outcome, tone: TONE.muted } : null;
  const room = ctx && incidentIds.has(ctx) ? ctx : ALL_ROOM;
  return (
    <tr className="border-t border-ink-100 align-top">
      <td className="py-2 pr-3 whitespace-nowrap tabular-nums text-ink-500">{shortDateTime(request.occurred_at)}</td>
      <td className="py-2 pr-3 text-ink-900">
        <span className="font-medium">{skillName(request.skill)}</span>
        {request.skill && FINANCE_SKILL_KO[request.skill] && <span className="ml-1 text-xs text-ink-500">{request.skill}</span>}
      </td>
      <td className="py-2 pr-3">
        {ctx ? (
          <Link href={`/orchestrator?ctx=${encodeURIComponent(ctx)}`} className="font-medium text-brand-700 underline" title={ctx}>{humanId(ctx)}</Link>
        ) : <span className="text-ink-500">이슈 전</span>}
      </td>
      <td className="py-2 pr-3">
        {result ? <Chip label={result.label} tone={result.tone} /> : <span className="text-sm text-ink-500">응답 기록 없음</span>}
      </td>
      <td className="py-2">
        <Link href={`/agents/chat?room=${encodeURIComponent(room)}`} className="whitespace-nowrap text-sm font-medium text-brand-700 underline">
          {room === ALL_ROOM ? "AI 대화방(전체)에서 보기 →" : "AI 대화방에서 보기 →"}
        </Link>
      </td>
    </tr>
  );
}

/** 최근 7일 맡은 일 중 실패·거절 건수. 지금 할 일에 쓴다 */
const FAIL_WINDOW_MS = 7 * 24 * 3600 * 1000;
export function failedCount(rows: RequestRow[], now = Date.now()): number {
  return rows.filter((r) => r.reply && ["failed", "rejected"].includes(r.reply.outcome as string) && now - time(r.request) < FAIL_WINDOW_MS).length;
}

export function OrchestratorRequestsSection({ tick, onSummary }: { tick: number; onSummary?: (s: { total: number; failed: number }) => void }) {
  const res = useResource("orchestrator-requests", load, tick);
  const [shown, setShown] = useState(PAGE);
  const rows = res.env?.data.rows;
  useEffect(() => { if (rows && onSummary) onSummary({ total: rows.length, failed: failedCount(rows) }); }, [rows, onSummary]);
  return (
    <Panel id="orchestrator-requests" title="총괄이 재무에 맡긴 일"
      hint="총괄 AI가 재무 AI에 보낸 요청입니다. AI 대화방에 보이는 것과 같은 공통 로그에서 총괄 → 재무 요청만 골랐고, 결과는 재무 AI가 총괄에 돌려준 응답에서 읽습니다. 읽기 전용입니다.">
      <ResourceBody res={res}>
        {({ rows, truncated, incidentIds }) => rows.length === 0 ? (
          <Empty>아직 총괄이 재무에 맡긴 일이 없어요.</Empty>
        ) : (
          <>
            <div className="scroll-thin overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead className="text-sm text-ink-500">
                  <tr><th className="pb-1 pr-3 font-medium">시각</th><th className="pb-1 pr-3 font-medium">맡긴 일</th><th className="pb-1 pr-3 font-medium">이슈·문의</th><th className="pb-1 pr-3 font-medium">결과</th><th /></tr>
                </thead>
                <tbody>{rows.slice(0, shown).map((r) => <Row key={r.request.log_id} row={r} incidentIds={incidentIds} />)}</tbody>
              </table>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-ink-500">
              <span>{Math.min(shown, rows.length)} / {rows.length}건</span>
              {shown < rows.length && (
                <button type="button" className="btn-ghost" onClick={() => setShown((n) => n + PAGE)}>더 보기 ({Math.min(PAGE, rows.length - shown)}건)</button>
              )}
              {truncated && <span>공통 로그의 최근 기록 안에서만 보여 줍니다. 더 오래된 요청은 빠졌을 수 있어요.</span>}
            </div>
          </>
        )}
      </ResourceBody>
    </Panel>
  );
}
