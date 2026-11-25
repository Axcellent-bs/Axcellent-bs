import { call } from "./companyConnection";

// 장애 상세의 확정 원인 API (dashboard-api `incident_causes.py`, SPEC-30 R1·R3).
// 사람이 적어 AXcellent에 저장하는 기록이다. DejaFix에는 쓰지 않는다.
// 읽기는 로그인한 누구나, 저장은 로그인 세션만(누가 저장했는지 남긴다). 회사마다 따로 저장된다

export interface ConfirmedCause {
  text: string;
  /** 원인 후보에서 시작했으면 그 순위. 직접 적었으면 null */
  from_rank: number | null;
  saved_by: string;
  saved_at: string;
}

export interface IncidentCauseState {
  incident_id: number;
  confirmed: ConfirmedCause | null;
  /** 앞서 저장했다가 바뀐 것 (최근 것부터) */
  history: (ConfirmedCause & { replaced_at: string })[];
}

/** 서버와 같은 상한 (앞뒤 공백을 뗀 길이) */
export const CAUSE_TEXT_MAX = 1000;

const path = (id: number) => `/api/dashboard/incidents/${id}/cause`;

export const incidentCauseApi = {
  get: (id: number) => call<IncidentCauseState>("GET", path(id)),
  save: (id: number, text: string, fromRank: number | null) =>
    call<IncidentCauseState>("POST", path(id), fromRank === null ? { text } : { text, from_rank: fromRank }),
};
