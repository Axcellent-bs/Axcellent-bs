import type { Incident } from "./dejafix";

// 회원·서버 관리 › DejaFix 연결 › "연결한 PC" 칸이 그리는 값.
// DejaFix에는 호스트 목록 API가 없어서(계약은 incidents·summary만) 장애 목록에 나온 호스트를 모은다.
// - 목록 = 최근 장애(limit=100) + 열린 장애(status=problem)의 host.name 합집합
// - 응답 없음 = 그 호스트의 열린 장애 제목에 "Zabbix agent is not available"이 있음 (가장 이른 started_at부터)
// 장애 기록에 한 번도 나온 적 없는 PC는 목록에 없다. 화면이 그 사실을 안내한다. 숫자를 지어내지 않는다.

/** Zabbix 기본 템플릿의 agent 응답 없음 트리거 이름 */
export const AGENT_DOWN = "Zabbix agent is not available";

export type PcState = "up" | "down";

export interface PcRow {
  name: string;
  state: PcState;
  /** 응답 없음이 시작된 시각 (state=down일 때만) */
  since: string | null;
}

const isAgentDown = (i: Incident) => i.status === "problem" && i.title.toLowerCase().includes(AGENT_DOWN.toLowerCase());

/** 두 장애 목록에서 PC 목록과 연결 상태를 정한다. 응답 없음 먼저, 그다음 이름순 */
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
    return { name, state: since ? "down" : "up", since };
  });
  return rows.sort((a, b) => (a.state === b.state ? a.name.localeCompare(b.name, "ko") : a.state === "down" ? -1 : 1));
}

/** 요약 줄: "N / M곳 연결됨"과 막대 폭(%) */
export function pcSummary(rows: PcRow[]): { connected: number; total: number; pct: number } {
  const connected = rows.filter((r) => r.state === "up").length;
  const total = rows.length;
  return { connected, total, pct: total ? Math.round((connected / total) * 100) : 0 };
}
