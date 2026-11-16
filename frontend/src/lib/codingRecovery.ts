// 개발 AI 코딩이 멈췄을 때 누가 어떻게 이어 가나. 멈춘 이유(서버 오류 한 줄)로 정한다 (LLM 없음).
// agent: 에이전트가 스스로 이어 간다 (잠깐 생긴 오류는 한 번 다시 · CI는 계속 확인). 사람은 기다리거나 바로 할 수도 있다
// human: 사람이 할 일. 화면은 그 오류에 맞는 버튼만 보여 준다
import type { CodingRun } from "./planningData";

export type RecoveryAction =
  | "pick" // 파일 직접 고르기
  | "chat" // AI 대화로 넘기기
  | "retry" // 다시 시도
  | "return" // 기획으로 돌려보내기
  | "covered" // 이미 반영됨으로 닫기
  | "instruct" // 같은 PR에 추가 지시
  | "merge" // 사람 머지
  | "connect" // 연결 화면 (레포·AI 키)
  | "reviews"; // AI 교차검증 화면

export interface Recovery {
  who: "agent" | "human";
  say: string; // 다음 할 일 한 줄 (해요체)
  actions: RecoveryAction[];
}

interface Rule {
  when: (e: string, c: CodingRun) => boolean;
  get: (c: CodingRun) => Recovery;
}

const has = (...ws: string[]) => (e: string) => ws.every((w) => e.includes(w));
const any = (...ws: string[]) => (e: string) => ws.some((w) => e.includes(w));
const AUTO_MAX = 1; // 백엔드 AUTO_RETRY_MAX와 같은 값
const agentWillRetry = (c: CodingRun) => !!c.transient && (c.autoRetries ?? 0) < AUTO_MAX;

const RULES: Rule[] = [
  // 파일 고르기 문제: 사람이 파일을 정하면 바로 이어 간다
  { when: has("고칠 파일을 고르지 못함"), get: () => ({ who: "human", say: "AI가 고칠 파일을 찾지 못했어요. 파일을 직접 고르거나 AI 대화로 같이 정해 주세요.", actions: ["pick", "chat", "return"] }) },
  { when: (e) => /고칠 파일 \d+개 > \d+개/.test(e), get: () => ({ who: "human", say: "고칠 파일이 한도(5개)보다 많아요. 꼭 필요한 파일만 고르거나, 기획으로 돌려보내 작업을 나눠 주세요.", actions: ["pick", "chat", "return"] }) },
  { when: any("바뀐 내용이 없음", "이미 반영돼 바뀐 내용이 없음"), get: () => ({ who: "human", say: "바꿀 게 없었어요. 앞 작업이 이미 반영했으면 닫고, 아니면 고칠 파일을 다시 골라 주세요.", actions: ["covered", "pick", "chat"] }) },
  { when: any("수정 지시가 어느 파일인지 모름"), get: () => ({ who: "human", say: "추가 지시에 고칠 파일 이름을 적어 다시 보내 주세요.", actions: ["instruct"] }) },
  { when: any("지시를 반영한 수정이 없음"), get: () => ({ who: "human", say: "지시대로 바꿀 곳을 찾지 못했어요. 더 구체적으로 지시하거나, 지금 PR이 괜찮으면 머지해 주세요.", actions: ["instruct", "merge", "chat"] }) },
  // 크기 한도: 작업을 나눠야 한다
  { when: (e) => /\d+줄 > \d+줄/.test(e), get: () => ({ who: "human", say: "바꿀 양이 한도보다 커서 멈췄어요. AI 대화로 범위를 줄이거나 기획으로 돌려보내 작업을 나눠 주세요.", actions: ["chat", "return", "pick"] }) },
  // 코드 일인지 애매함
  { when: any("코드 변경이 아닐 수 있음"), get: () => ({ who: "human", say: "코드 일이 아닐 수 있어요. 코드를 바꿀 일이면 개발에 다시 맡기고, 아니면 기획으로 돌려보내 주세요.", actions: ["retry", "return"] }) },
  // PR이 있고 사람이 볼 것
  { when: any("위험도 높음"), get: () => ({ who: "human", say: "위험도가 높은 파일이라 사람이 PR을 보고 머지해요. 고칠 게 있으면 추가 지시로 보내 주세요.", actions: ["merge", "instruct"] }) },
  { when: has("CI 없음"), get: () => ({ who: "human", say: "이 레포엔 CI가 없어서 확인할 곳이 없어요. PR을 보고 머지하거나 추가 지시를 보내 주세요.", actions: ["merge", "instruct"] }) },
  { when: has("CI 실패"), get: () => ({ who: "human", say: "CI가 실패했어요. PR의 CI 기록을 보고 추가 지시로 고칠 점을 알려 주세요. 나중에 CI가 통과하면 에이전트가 머지를 다시 판단해요.", actions: ["instruct", "merge"] }) },
  { when: any("CI 상태를 읽지 못함", "넘게 안 끝남"), get: () => ({ who: "agent", say: "에이전트가 CI를 계속 확인하고, 통과하면 자동 머지 규칙으로 이어 가요.", actions: ["merge"] }) },
  { when: has("리뷰 불일치"), get: () => ({ who: "human", say: "리뷰 지적을 보고 추가 지시로 고치거나, 괜찮으면 머지해 주세요.", actions: ["instruct", "merge"] }) },
  { when: any("교차검증", "자동 머지 안 함"), get: () => ({ who: "human", say: "안전 규칙에 걸려 자동 머지하지 않았어요. AI 교차검증에서 판단하거나 PR을 보고 머지해 주세요.", actions: ["reviews", "merge"] }) },
  { when: has("머지 충돌"), get: (c) => (agentWillRetry(c)
    ? { who: "agent", say: "머지 충돌이라 에이전트가 곧 PR을 닫고 최신 코드로 다시 써요.", actions: ["retry"] }
    : { who: "human", say: "머지 충돌이 남았어요. 최신 코드 기준으로 다시 작성해 주세요.", actions: ["retry", "chat"] }) },
  { when: any("병합 실패", "사람 머지 버튼으로"), get: () => ({ who: "human", say: "GitHub 머지가 실패했어요. 머지 버튼으로 다시 시도해 주세요.", actions: ["merge"] }) },
  // 환경·설정
  { when: any("서버 AI 키"), get: () => ({ who: "human", say: "서버 AI 키가 없어요. 연결 화면에서 AI 키를 넣은 뒤 다시 시도해 주세요.", actions: ["connect", "retry"] }) },
  { when: (e) => /호출 \d+회|토큰 .*한도|한도 .*토큰/.test(e) || e.includes("사람 확인 (다시 시도하면 새로 셈)"), get: () => ({ who: "human", say: "실행 한 번의 AI 사용 한도를 넘었어요. 다시 시도하면 새로 세요. 계속 넘으면 AI 대화로 작업을 줄여 주세요.", actions: ["retry", "chat", "return"] }) },
  { when: has("LLM"), get: (c) => (agentWillRetry(c)
    ? { who: "agent", say: "AI 응답 오류라 에이전트가 잠시 뒤 한 번 다시 해요. 써 둔 코드는 이어서 써요.", actions: ["retry"] }
    : { who: "human", say: "AI 응답 오류가 반복됐어요. 잠시 뒤 다시 시도해 주세요.", actions: ["retry"] }) },
  { when: any("rate_limited", "호출 한도"), get: (c) => (agentWillRetry(c)
    ? { who: "agent", say: "GitHub 호출 한도라 에이전트가 잠시 뒤 다시 해요.", actions: ["retry"] }
    : { who: "human", say: "GitHub 호출 한도가 계속 걸려요. 잠시 뒤 다시 시도해 주세요.", actions: ["retry"] }) },
  { when: has("읽기 전용"), get: () => ({ who: "human", say: "읽기 전용 연결이라 쓸 수 없어요. 쓰기 권한이 있는 연결로 바꾸거나 직접 반영해 주세요.", actions: ["connect", "return"] }) },
  { when: has("형상관리"), get: () => ({ who: "human", say: "레포 연결(토큰·권한)을 확인한 뒤 다시 시도해 주세요.", actions: ["connect", "retry"] }) },
  { when: has("코딩 에이전트 오류"), get: () => ({ who: "human", say: "예상하지 못한 오류예요. 다시 시도해 보고, 반복되면 개발팀에 알려 주세요.", actions: ["retry", "chat"] }) },
];

const STOPPED = ["failed", "waiting_human"];

/** 멈춘 코딩의 다음 할 일. 진행 중·끝남·사람이 멈춘 것(중단·돌려보냄)·PR 보류 승인은 null (기존 버튼) */
export function recoveryOf(c: CodingRun): Recovery | null {
  if (!STOPPED.includes(c.status) || !c.error) return null;
  if (c.error.includes("PR 열기") && c.error.includes("안전 점검")) return null; // 승인하고 PR 열기 (따로 있음)
  for (const r of RULES) if (r.when(c.error, c)) return r.get(c);
  return { who: "human", say: "멈춘 이유를 보고 다시 시도하거나 AI 대화로 넘겨 주세요.", actions: ["retry", "chat"] };
}
