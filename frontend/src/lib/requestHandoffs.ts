import { STEP_STATUS, type StepStatus, type WorkRequestRow } from "./workRequests.ts";

// 고객 의뢰 하나가 부서 사이로 넘어가는 모습 ("누가 누구에게 보냈나"). 의뢰의 plan(단계·부서)과 steps(상태)만 쓴다.
// 장애 이슈(OrchIncident)와 섞지 않는다: 장애 통계·대화방 이슈 목록에 들어가지 않는 의뢰 전용 줄이다.
// 부서가 돌려준 자유 문장(step.note 등)과 고객 글(row.text)은 옮기지 않는다. 단계 이름·부서·상태·번호만 쓴다.

export interface RequestHandoff {
  /** `${request_id}:${step_id}` */
  id: string;
  /** 보낸 쪽·받는 쪽 부서 이름(코드). 화면 이름은 agentChat의 speaker()로 */
  from: string;
  to: string;
  /** 시킨 일 이름 (총괄이 정한 단계 이름) */
  label: string;
  status: StepStatus | null;
  statusLabel: string;
  tone: string;
  /** 아직 시작 전이라 "보낼 예정"인 줄 */
  planned: boolean;
  at: string | null;
}

const CENTRAL = "orchestrator";

/** 앞 단계(after) 중 가장 가까운 부서. 사람 확인 단계(부서 없음)는 건너뛰어 그 앞 단계를 본다 */
function senderOf(after: string[], plan: Map<string, WorkRequestRow["plan"][number]>, seen: Set<string>): string | undefined {
  for (const id of [...after].reverse()) {
    if (seen.has(id)) continue;
    seen.add(id);
    const p = plan.get(id);
    if (!p) continue;
    if (p.dept) return p.dept;
    const up = senderOf(p.after, plan, seen);
    if (up) return up;
  }
  return undefined;
}

/** 의뢰 하나의 넘김 줄. 부서가 없는 단계(사람 확인)는 줄에서 뺀다. 앞 단계(after)에 부서가 있으면 그 부서가 보낸 것, 없으면 총괄이 보낸 것 */
export function requestHandoffs(row: WorkRequestRow): RequestHandoff[] {
  const plan = new Map(row.plan.map((p) => [p.step_id, p] as const));
  const out: RequestHandoff[] = [];
  for (const p of row.plan) {
    if (!p.dept) continue;
    const s = row.steps[p.step_id];
    const prevDept = senderOf(p.after, plan, new Set());
    const tone = s ? STEP_STATUS[s.status] : null;
    const planned = !s || s.status === "pending";
    out.push({
      id: `${row.request_id}:${p.step_id}`,
      from: prevDept ?? CENTRAL,
      to: p.dept,
      label: p.label,
      status: s?.status ?? null,
      statusLabel: tone?.label ?? "대기",
      tone: tone?.tone ?? "bg-ink-100 text-ink-700",
      planned,
      at: s?.started_at ?? null,
    });
  }
  return out;
}

/** 넘김이 하나라도 있는 의뢰만, 최근 것부터 */
export function handoffRequests(rows: WorkRequestRow[]): { row: WorkRequestRow; handoffs: RequestHandoff[] }[] {
  return rows
    .map((row) => ({ row, handoffs: requestHandoffs(row) }))
    .filter((x) => x.handoffs.length > 0)
    .sort((a, b) => Date.parse(b.row.created_at) - Date.parse(a.row.created_at));
}
