// 테스트 전용 예시 (trendReport.test.ts). 공통 계약 모양의 TrendReport 하나. 화면에서 부르지 않는다 (데모 데이터 아님).
// 일부러 넣은 경우: 해시태그 섹션 unknown(이유만) · 경쟁사 하나 못 읽음 · 한 줄 결론 없음(summary_reason) ·
// 검색 변화 null(이유) · 콘티 slot 순서가 섞여 옴.
import type { InstagramRadarPost, TrendReport, TrendReportListItem } from "./data.ts";

const post = (account: string, mediaId: string, ratio: number, value: number): InstagramRadarPost => ({
  account,
  media_id: mediaId,
  media_product_type: "REELS",
  posted_at: "2026-11-15T03:00:00Z",
  metric: "view_count",
  value,
  account_median: Math.round(value / ratio),
  ratio,
  permalink: `https://www.instagram.com/reel/${mediaId}/`,
  caption_excerpt: "예시 캡션 <b>태그는 글자로</b>",
  thumbnail_url: null,
  media_type: "VIDEO",
  standout: "평소보다 짧은 영상",
});

export const TREND_REPORT_EXAMPLE: TrendReport = {
  report_id: "rpt_example_1",
  status: "partial",
  created_at: "2026-11-18T01:00:00Z",
  finished_at: "2026-11-18T01:04:00Z",
  caller: "human",
  context_id: null,
  brief: {
    product: "예시 제품",
    target_gender: "f",
    target_ages: ["20", "30"],
    format: "인스타 릴스 30초",
    competitors: ["example_a", "example_b"],
    keywords: ["예시 키워드1", "예시 키워드2"],
    preset: "eco_beauty",
    sources: { product: "preset", target: "request_text", format: "preset", competitors: "input", keywords: "preset", goal: "input" },
    goal: "브랜드 인지도",
    format_parts: ["인스타 릴스", "30초"],
    competitor_names: { example_a: "예시 브랜드A", example_b: null },
  },
  summary_line: null,
  summary_reason: "추천 섹션이 끝나지 않아 결론을 쓰지 않았어요",
  sections: {
    search: {
      status: "known",
      reason: null,
      filter_note: "예시 필터 설명",
      rows: [
        { keyword: "예시 키워드1", search_change_pct: 12.34, search_reason: null, shopping_change_pct: -4, shopping_reason: null,
          basis: "filtered", monthly_volume: 12300 },
        { keyword: "예시 키워드2", search_change_pct: null, search_reason: "검색량이 적어 비교하지 않음", shopping_change_pct: 0, shopping_reason: null,
          basis: "none" },
        { keyword: "예시 키워드3", search_change_pct: 480, search_reason: null, shopping_change_pct: null, shopping_reason: null,
          basis: "filtered", small_base: true, monthly_volume: 90 },
        { keyword: "예시 키워드4", search_change_pct: 5, search_reason: null, shopping_change_pct: 9, shopping_reason: null, basis: "all" },
        // 월 검색이 10 미만이라 서버가 숫자 대신 note를 준 줄 ("월 검색 0"이 아니라 "월 검색 10 미만")
        { keyword: "예시 키워드5", search_change_pct: null, search_reason: null, shopping_change_pct: null, shopping_reason: null,
          basis: "volume_only", monthly_volume: null, monthly_volume_note: "under_10" },
      ],
    },
    hashtags: {
      status: "unknown",
      reason: "해시태그 검색 한도를 넘어 읽지 못함",
      rows: [],
      account_names: { example_a: "예시 브랜드A" },
    },
    content: {
      status: "known",
      reason: null,
      posts: [post("example_a", "EX1", 3.2, 32000), post("example_c", "EX2", 5.5, 11000)],
      memes: [{ name: "예시 밈", description: "설명은 <i>글자로만</i>", ad_material: "caution" }],
    },
    competitors: {
      status: "known",
      reason: null,
      rows: [
        { account: "example_a", posts_per_week: 3.5, reels_share: 0.62, reels_view_median: 8000, best_post: post("example_a", "EX1", 3.2, 32000),
          traits: ["첫 장면에 손", "자막 크게"], reason: null, name: "예시 브랜드A",
          traits_structured: [
            { label: "릴스 비율", value: "100%", baseline: "평소 35%" },
            { label: "올린 시간", value: "밤 9~12시", baseline: "평소 28%" },
          ] },
        { account: "example_d", posts_per_week: null, reels_share: null, reels_view_median: null, best_post: null, traits: [],
          reason: "최근 게시물이 적어 비교하지 않음" },
      ],
      accounts_failed: [{ account: "example_b", reason: "비즈니스 계정이 아니라 읽지 못함" }],
    },
    recommendation: {
      status: "running",
      reason: null,
      conclusion: null,
      beats: [
        { slot: "25-30", text: "마무리 예시", evidence: ["근거 예시 4"], evidence_items: [{ id: "content:1", text: "근거 예시 4", short: "예시 짧은 근거 4" }] },
        { slot: "0-3", text: "훅 예시", evidence: ["근거 예시 1"], evidence_items: [{ id: "search:1", text: "근거 예시 1", short: "예시 짧은 근거 1" }] },
        { slot: "15-25", text: "증거 예시", evidence: [] },
        { slot: "3-15", text: "전환 예시", evidence: ["근거 예시 2", "근거 예시 3"],
          evidence_items: [{ id: "search:1", text: "근거 예시 2", short: "예시 짧은 근거 1" }, { id: "competitor:1", text: "근거 예시 3", short: "예시 짧은 근거 3" }] },
      ],
      music_note: "음악 예시 한 줄",
    },
  },
  limits: ["예시 한계 1", "예시 한계 2"],
  limit_groups: { method: ["예시 한계 1"], cannot_see: ["예시 한계 2"], this_report: [] },
  calls: { instagram: 12, naver: 4, llm: 2 },
};

export const TREND_REPORT_LIST_EXAMPLE: TrendReportListItem[] = [
  { report_id: "rpt_example_1", status: "partial", created_at: "2026-11-18T01:00:00Z", caller: "human", product: "예시 제품", summary_line: null },
  { report_id: "rpt_example_0", status: "done", created_at: "2026-11-17T01:00:00Z", caller: "planning", product: null, summary_line: "예시 결론" },
];
