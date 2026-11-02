// 콘텐츠 루프: 트렌드 감지 → 영상 소재 추천 → 영상 제작 → 게시·성과 측정 → 다음 추천에 반영 → (다시 1).
// 단계 이름·담당·상태는 이 파일에만 둔다. 단계가 생기거나 동작하기 시작하면 이 파일만 고친다.
// 숫자는 여기 두지 않는다: 1단계 개수는 트렌드 탭이 받은 trend_scan 응답에서 화면이 센다. 2~5단계는 상태 칩만.
// 1·4단계 칩은 트렌드 탭이 받은 trend_scan·campaign_effect 응답으로 정한다: 정상이면 "동작 중"(파란 테두리), 실패면 "연결 확인 필요"(회색 칩, 점선 회색 테두리).
// 트렌드 탭의 하위 탭 5개도 이 단계 정의를 그대로 쓴다(이름·순서·주소 `#trend/<tab>`·할 일). 루프 띠와 하위 탭이 같은 말을 한다.

/** "마케팅에 소재로 보내기"를 아직 못 하는 이유 (dashboard-api POST /handoffs는 에이전트 토큰만 받는다) */
export const SEND_TO_MARKETING_BLOCKED =
  "받은 요청(할 일)은 지금 에이전트 토큰으로만 넣을 수 있어서, 화면(사람 로그인)에서는 아직 보낼 수 없어요.";

export type LoopTone = "ok" | "warn" | "muted" | "info";

export interface LoopStage {
  no: number;
  /** 트렌드 탭의 하위 탭 주소 조각 (`#trend/<tab>`) */
  tab: string;
  title: string;
  /** 담당 (A → B는 A가 만들어 B에게 넘김) */
  owner: string;
  /** 실제 데이터로 동작하는 단계인지 (파란 테두리). 아니면 점선 + 회색 배경.
   *  probe가 있으면 응답 결과를 따른다: 확인 중은 회색 실선, 실패(연결 확인 필요)는 점선 + 회색 */
  live: boolean;
  /** 한 줄 설명 (1단계는 화면이 trend_scan 개수로 바꿔 쓴다) */
  description: string;
  status: { label: string; tone: LoopTone };
  /** 상태 아래 덧붙이는 한 줄 (아직 안 되는 것·이유) */
  note?: string;
  /** 이 단계가 화면에서 받은 값으로 숫자를 보여 주는지 */
  source?: "trend_scan";
  /** 상태 칩을 이 서버 응답으로 정하는 단계. status는 응답을 받기 전 기본값 */
  probe?: LoopProbeSkill;
  /** 아직 없는 단계의 할 일 한 줄 (준비 중 하위 탭에 보여 준다) */
  todo?: string;
  /** 그 일을 하려면 필요한 것 (준비 중 하위 탭에 보여 준다) */
  needs?: string[];
}

/** 트렌드 탭이 이미 받은 응답의 결과. 칩을 위해 따로 부르지 않는다 */
export type LoopProbeSkill = "trend_scan" | "campaign_effect";
/** idle = 아직 부르지 않음 (캠페인 효과는 사람이 "측정"을 눌러야 부른다) */
export type LoopProbe = "idle" | "checking" | "ok" | "failed";
export const PROBE_STATUS: Record<LoopProbe, { label: string; tone: LoopTone }> = {
  idle: { label: "측정 대기", tone: "muted" },
  checking: { label: "확인 중", tone: "muted" },
  ok: { label: "동작 중", tone: "ok" },
  failed: { label: "연결 확인 필요", tone: "muted" },
};

export const LOOP_STAGES: LoopStage[] = [
  {
    no: 1,
    tab: "detect",
    title: "트렌드·밈 감지",
    owner: "데이터분석 AI · trend_scan · trend_candidates",
    live: true,
    description: "네이버 검색어 급상승과 YouTube 인기 태그 후보를 판정합니다.",
    status: PROBE_STATUS.checking,
    source: "trend_scan",
    probe: "trend_scan",
  },
  {
    no: 2,
    tab: "recommend",
    title: "영상 소재 추천",
    owner: "데이터분석 AI → 마케팅",
    live: false,
    description: "급상승 소재를 순위·근거와 함께 마케팅으로 넘깁니다.",
    status: { label: "넘기는 창구 대기", tone: "warn" },
    note: SEND_TO_MARKETING_BLOCKED,
    todo: "급상승 결과로 소재 3~5개 추천",
    needs: [
      "급상승 결과에서 소재를 고르고 순서를 정하는 데이터 AI 기능 (아직 없어요)",
      `추천을 마케팅에 넘기는 창구: ${SEND_TO_MARKETING_BLOCKED}`,
    ],
  },
  {
    no: 3,
    tab: "video",
    title: "AI 인플루언서·배우·캐릭터",
    owner: "담당 미정",
    live: false,
    description: "추천 소재로 캐릭터 영상을 만듭니다.",
    status: { label: "준비 중", tone: "muted" },
    todo: "추천 소재로 AI 인플루언서·배우·캐릭터 영상 만들기",
  },
  {
    no: 4,
    tab: "campaign",
    title: "게시·성과 측정",
    owner: "마케팅 → 데이터분석 AI · campaign_effect",
    live: true,
    description: "캠페인 기간 주문 증분과 캠페인 쿠폰 누적 사용률을 잽니다.",
    status: PROBE_STATUS.checking,
    note: "매출 효과와 7일 넘는 캠페인은 아직 재지 않아요.",
    probe: "campaign_effect",
  },
  {
    no: 5,
    tab: "feedback",
    title: "다음 추천에 반영",
    owner: "데이터분석 AI",
    live: false,
    description: "잘 된 소재 유형을 다음 추천 순위에 반영합니다.",
    status: { label: "준비 중", tone: "muted" },
    todo: "게시·성과 측정 결과로 잘 된 소재 유형을 다음 추천 순위에 반영하기",
    needs: ["2단계 영상 소재 추천이 먼저 있어야 해요"],
  },
];

/** 트렌드 하위 탭 주소: `#trend/<tab>` */
export const trendHash = (stage: LoopStage) => `#trend/${stage.tab}`;

/** 주소(#trend/<tab>)로 단계 찾기. 없거나 모르는 값이면 1단계 */
export function stageFromHash(hash: string): LoopStage {
  const tab = hash.startsWith("#trend/") ? hash.slice("#trend/".length) : "";
  return LOOP_STAGES.find((s) => s.tab === tab) ?? LOOP_STAGES[0];
}

/** 5 → 1 돌아가는 줄의 설명 */
export const LOOP_BACK = "성과가 다음 트렌드 판정의 우선순위로 돌아갑니다";
