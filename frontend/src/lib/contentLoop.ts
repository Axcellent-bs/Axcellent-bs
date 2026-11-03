// 콘텐츠 루프: 감지 → 추천 → 넘김 → 성과 → (다시 감지). 트렌드&콘텐츠 화면(/data/trend)의 하위 탭 4개와 하나씩 같다.
// 단계 이름·담당·상태는 이 파일에만 둔다. 단계가 생기거나 동작하기 시작하면 이 파일만 고친다.
// 숫자는 여기 두지 않는다: 1단계 개수는 트렌드 탭이 받은 trend_scan 응답에서 화면이 센다. 2~4단계는 상태 칩만.
// 1·2·4단계 칩은 트렌드 탭이 받은 trend_scan·promo_concepts·campaign_effect 응답으로 정한다: 정상이면 "동작 중"(파란 테두리), 실패면 "연결 확인 필요"(회색 칩, 점선 회색 테두리).

/** "마케팅에 소재로 보내기"를 아직 못 하는 이유 (dashboard-api POST /handoffs는 에이전트 토큰만 받는다) */
export const SEND_TO_MARKETING_BLOCKED =
  "받은 요청(할 일)은 지금 에이전트 토큰으로만 넣을 수 있어서, 화면(사람 로그인)에서는 아직 보낼 수 없어요.";

export type LoopTone = "ok" | "warn" | "muted" | "info";

export interface LoopStage {
  no: number;
  /** 단계 주소 조각 (옛 주소 `#trend/<tab>` 호환용) */
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
export type LoopProbeSkill = "trend_scan" | "promo_concepts" | "campaign_effect";
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
    title: "감지",
    owner: "데이터분석 AI · trend_popular · trend_scan · trend_candidates",
    live: true,
    description: "지금 인기, 네이버 검색어 급상승과 YouTube 인기 태그 후보를 봅니다.",
    status: PROBE_STATUS.checking,
    source: "trend_scan",
    probe: "trend_scan",
  },
  {
    no: 2,
    tab: "recommend",
    title: "추천",
    owner: "데이터분석 AI · shorts_stats · promo_concepts",
    live: true,
    description: "인기 숏츠 형식과 지금 인기 트렌드로 회사 상품 광고·홍보 숏츠 기획안 초안을 만듭니다.",
    status: PROBE_STATUS.checking,
    probe: "promo_concepts",
  },
  {
    no: 3,
    tab: "handoff",
    title: "넘김",
    owner: "기획 → 마케팅",
    live: false,
    // 루프 띠와 탭 안 안내가 같은 문장을 쓴다
    description: "기획팀이 초안을 확정하고 마케팅이 제작·게시하는 단계 (준비 중)",
    status: { label: "준비 중", tone: "muted" },
  },
  {
    no: 4,
    tab: "result",
    title: "성과",
    owner: "마케팅 → 데이터분석 AI · campaign_effect",
    live: true,
    description: "게시한 캠페인의 주문 증분과 쿠폰 누적 사용률을 재고, 다음 추천에 반영합니다.",
    status: PROBE_STATUS.checking,
    note: "매출 효과·7일 넘는 캠페인과 다음 추천 반영은 아직 안 해요.",
    probe: "campaign_effect",
    todo: "게시·성과 측정 결과로 잘 된 기획안 유형을 다음 추천에 반영하기",
  },
];

/** 트렌드&콘텐츠 화면 하위 탭 4개: 감지 → 추천 → 넘김 → 성과. 루프 4단계와 하나씩 같다 */
export interface TrendTab {
  key: "detect" | "recommend" | "handoff" | "result";
  title: string;
  /** 이 탭이 보여 주는 루프 단계의 tab 값(LoopStage.tab) */
  stages: string[];
}
export const TREND_TABS: TrendTab[] = LOOP_STAGES.map((s) => ({
  key: s.tab as TrendTab["key"],
  title: s.title,
  stages: [s.tab],
}));

/** 예전 5단계 주소 조각 → 지금 탭 (옛 주소 `#trend/<단계>` 호환) */
const OLD_STAGE_TAB: Record<string, TrendTab["key"]> = { video: "handoff", campaign: "result", feedback: "result" };

/** 트렌드 하위 탭 주소: `/data/trend#<key>` */
export const trendTabHash = (t: TrendTab) => `#${t.key}`;

/** 주소 해시로 탭 찾기. 없거나 모르는 값이면 감지. 옛 주소(#trend/<단계>)도 알아본다 */
export function trendTabFromHash(hash: string): TrendTab {
  const raw = hash.replace(/^#(trend\/)?/, "");
  const key = OLD_STAGE_TAB[raw] ?? raw;
  return TREND_TABS.find((t) => t.key === key || t.stages.includes(key)) ?? TREND_TABS[0];
}

/** 루프 단계가 속한 탭 */
export const trendTabOfStage = (stage: LoopStage): TrendTab => TREND_TABS.find((t) => t.stages.includes(stage.tab)) ?? TREND_TABS[0];

/** 4 → 1 돌아가는 줄의 설명 */
export const LOOP_BACK = "성과가 다음 트렌드 판정의 우선순위로 돌아갑니다";
