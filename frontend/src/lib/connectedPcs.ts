import type { DejafixHost, Incident } from "./dejafix";

// 회원·서버 관리 › DejaFix 연결 › "연결한 PC" 칸이 그리는 값.
// 1) DejaFix 호스트 목록(`GET /api/hosts`, 계약 #7)이 있으면 그대로: Zabbix 감시 호스트 전부와
//    에이전트 가용성으로 정한 상태(up·down·unknown), 열린 Problem 수 (`fromHosts`)
// 2) DejaFix가 아직 호스트 목록을 주지 않으면(옛 버전, 404) 장애 목록에 나온 호스트를 모은다 (`connectedPcs`)
//    - 목록 = 최근 장애(limit=100) + 열린 장애(status=problem)의 host.name 합집합
//    - 응답 없음 = 그 호스트의 열린 장애 제목에 "Zabbix agent is not available"이 있음 (가장 이른 started_at부터)
//    - 그 밖은 "확인할 수 없음". 장애가 없다고 연결됨으로 보지 않는다(근거 없는 값을 지어내지 않는다)

/** Zabbix 기본 템플릿의 agent 응답 없음 트리거 이름 */
export const AGENT_DOWN = "Zabbix agent is not available";

export type PcState = "up" | "down" | "unknown";

export interface PcRow {
  name: string;
  state: PcState;
  /** 응답 없음이 시작된 시각 (장애 목록으로 정한 down일 때만) */
  since: string | null;
  /** 열린 Problem 수 (호스트 목록에서 온 줄만, 모르면 null) */
  problems: number | null;
}

const ORDER: Record<PcState, number> = { down: 0, unknown: 1, up: 2 };
const byStateThenName = (a: PcRow, b: PcRow) =>
  a.state === b.state ? a.name.localeCompare(b.name, "ko") : ORDER[a.state] - ORDER[b.state];

const isAgentDown = (i: Incident) => i.status === "problem" && i.title.toLowerCase().includes(AGENT_DOWN.toLowerCase());

/** DejaFix 호스트 목록 → 줄. 응답 없음 먼저, 확인할 수 없음, 연결됨 순 */
export function fromHosts(hosts: DejafixHost[]): PcRow[] {
  return hosts
    .map((h) => ({ name: h.name, state: h.state, since: null, problems: h.problems }))
    .sort(byStateThenName);
}

/** 두 장애 목록에서 PC 목록과 상태를 정한다(호스트 목록이 없을 때만). 연결됨은 정하지 못한다 */
export function connectedPcs(recent: Incident[], open: Incident[]): PcRow[] {
  const names = new Set<string>();
  const downSince = new Map<string, string>();
  for (const i of [...recent, ...open]) {
    const name = i.host.name?.trim();
    if (!name) continue;
    names.add(name);
    if (!isAgentDown(i)) continue;
    const prev = downSince.get(name);
    if (!prev || Date.parse(i.started_at) < Date.parse(prev)) downSince.set(name, i.started_at);
  }
  const rows: PcRow[] = [...names].map((name) => {
    const since = downSince.get(name) ?? null;
    return { name, state: since ? "down" : "unknown", since, problems: null };
  });
  return rows.sort(byStateThenName);
}

/** 요약 줄: "N / M곳 연결됨", 확인할 수 없는 수, 막대 폭(%) */
export function pcSummary(rows: PcRow[]): { connected: number; unknown: number; total: number; pct: number } {
  const connected = rows.filter((r) => r.state === "up").length;
  const unknown = rows.filter((r) => r.state === "unknown").length;
  const total = rows.length;
  return { connected, unknown, total, pct: total ? Math.round((connected / total) * 100) : 0 };
}

/** 연결한 PC 읽기 결과 (ConnectedPcs·회사 연결 지도가 같이 쓴다) */
export type PcLoad =
  | { rows: PcRow[]; checkedAt: string; fromIncidents: boolean }
  | { error: string }
  | { notConnected: true };

/** 회사 연결 지도의 "사내 PC" 칸. 읽기 전·DejaFix 연결 전·못 읽음은 숫자를 만들지 않는다 */
export function pcMapNode(load: PcLoad | null): { value: string; tone: "ok" | "wait" | "bad" | "off"; note: string } {
  if (!load) return { value: "확인 중…", tone: "off", note: "" };
  if ("notConnected" in load) return { value: "DejaFix 연결 전", tone: "off", note: "회원·서버 관리 › DejaFix 연결에서 이어요" };
  if ("error" in load) return { value: "읽지 못함", tone: "bad", note: load.error };
  const s = pcSummary(load.rows);
  if (s.total === 0) return { value: "0대", tone: "off", note: "Zabbix가 감시하는 PC가 없어요" };
  if (load.fromIncidents) return { value: `응답 없음 ${s.total - s.connected - s.unknown}대`, tone: "wait", note: "장애 기록 기준이라 연결 여부는 일부만 알아요" };
  const down = s.total - s.connected - s.unknown;
  const tone = down > 0 ? "wait" : s.unknown > 0 ? "off" : "ok";
  const note = down > 0 ? `${down}대 응답 없음` : s.unknown > 0 ? `${s.unknown}대 확인할 수 없음` : "모두 연결됨";
  return { value: `${s.connected} / ${s.total}대`, tone, note };
}
