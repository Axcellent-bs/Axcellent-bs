// 테스트 전용 예시 (trendReportRead.test.ts · PR 인쇄 쪽 수 재기). 11/23 11:20 서버 보고서 TR-3F0D(의뢰 wr_d8cf3a9691
// "20~30대 청년 대상 브랜드 인지도 광고 영상, 트렌드 밈")와 같은 모양: 검색 5 · 해시태그 6 · 콘텐츠 6 · 경쟁사 6 · 이번 주의 8.
// 숫자는 화면 시안(①)에 적힌 것만 옮기고 나머지는 모양을 맞춘 자리 값이다. 화면에서 부르지 않는다 (데모 데이터 아님)
import type { InstagramRadarPost, TrendReport, TrendReportCompetitorRow } from "./data.ts";

const post = (account: string, id: string, ratio: number, value: number, reels: boolean, standout: string | null): InstagramRadarPost => ({
  account, media_id: id, media_product_type: reels ? "REELS" : "FEED", posted_at: "2026-11-20T06:00:00Z",
  metric: "like_count", value, account_median: Math.round(value / ratio), ratio,
  permalink: `https://www.instagram.com/p/${id}/`, caption_excerpt: "예시 캡션", thumbnail_url: null,
  media_type: reels ? "VIDEO" : "CAROUSEL_ALBUM", standout,
});

const comp = (account: string, name: string, ppw: number, share: number, median: number, best: InstagramRadarPost | null, trait: string | null): TrendReportCompetitorRow => ({
  account, name, posts_per_week: ppw, reels_share: share, reels_view_median: median, best_post: best, traits: [], reason: null,
  traits_structured: trait === "q" ? [{ label: "첫 줄 질문", value: "40%", baseline: "평소 10%" }]
    : trait === "c" ? [{ label: "캡션 길이", value: "360자", baseline: "평소 577자" }] : [],
});

const POSTS = [
  post("hanbitbiz", "P1", 14.8, 2100, true, "캡션 495자"),
  post("munhakdongne", "P2", 8, 1500, false, "오후 15~18시에 올림"),
  post("minumsa_books", "P3", 3.7, 1300, true, "이 계정 평소 5%만 릴스"),
  post("yes24", "P4", 5, 900, true, null),
  post("trevari", "P5", 7.4, 700, true, null),
  post("changbi", "P6", 2.6, 600, false, null),
];

export const TREND_REPORT_TR3F0D: TrendReport = {
  report_id: "tr_3f0d91c2",
  status: "done",
  created_at: "2026-11-23T02:20:00Z",
  finished_at: "2026-11-23T02:24:00Z",
  caller: "orchestrator",
  context_id: "ctx_wr_d8cf3a9691",
  brief: {
    product: "캣북스", target_gender: null, target_ages: ["20", "30"], format: "광고 영상",
    competitors: ["minumsa_books", "trevari", "yes24", "hanbitbiz", "munhakdongne", "changbi"],
    keywords: ["소설", "다꾸", "트렌드 밈", "시·에세이", "경제경영"], preset: null,
    sources: { product: "company", target: "request_text", format: "request_text", competitors: "company", keywords: "request_text", goal: "request_text", theme: "request_text" },
    goal: "브랜드 인지도", format_parts: ["광고 영상"], theme: "트렌드 밈", theme_mode: "use",
    competitor_names: { minumsa_books: "민음사", trevari: "트레바리", yes24: "예스24", hanbitbiz: "한빛비즈", munhakdongne: "문학동네", changbi: "창비" },
  },
  summary_line: "북토크 해시태그가 늘고 있으니 트렌드 밈 형식으로 책 읽는 일상을 짧고 가볍게 보여 주자",
  summary_reason: null,
  sections: {
    search: {
      status: "known", reason: null, filter_note: "20·30대로 거른 값",
      rows: [
        { keyword: "소설", search_change_pct: 361.3, search_reason: null, shopping_change_pct: -12.7, shopping_reason: null, basis: "filtered" },
        { keyword: "다꾸", search_change_pct: 6.9, search_reason: null, shopping_change_pct: null, shopping_reason: "쇼핑 분야에 없음", basis: "filtered" },
        { keyword: "트렌드 밈", search_change_pct: null, search_reason: "검색량이 적어 네이버가 값을 안 줌", shopping_change_pct: null, shopping_reason: null, basis: "none" },
        { keyword: "시·에세이", search_change_pct: null, search_reason: "검색량이 적어 네이버가 값을 안 줌", shopping_change_pct: null, shopping_reason: null, basis: "none" },
        { keyword: "경제경영", search_change_pct: null, search_reason: "검색량이 적어 네이버가 값을 안 줌", shopping_change_pct: null, shopping_reason: null, basis: "none" },
      ],
    },
    hashtags: {
      status: "known", reason: null,
      rows: [
        { tag: "북토크", accounts: 2, previous_accounts: 0, burst_posts: 1, used_by: ["minumsa_books", "munhakdongne"], is_new: true },
        { tag: "책추천", accounts: 5, previous_accounts: 5, burst_posts: 2, used_by: ["yes24"] },
        { tag: "북스타그램", accounts: 4, previous_accounts: 4, burst_posts: 1 },
        { tag: "신간", accounts: 3, previous_accounts: 4, burst_posts: 0 },
        { tag: "소설추천", accounts: 2, previous_accounts: 1, burst_posts: 1 },
        { tag: "독서기록", accounts: 2, previous_accounts: 2, burst_posts: 0 },
      ],
    },
    content: { status: "known", reason: null, posts: POSTS, memes: [] },
    competitors: {
      status: "known", reason: null, accounts_failed: [],
      rows: [
        comp("minumsa_books", "민음사", 12.9, 0.1, 80006, POSTS[2], "c"),
        comp("trevari", "트레바리", 1.1, 0.05, 70938, POSTS[4], null),
        comp("yes24", "예스24", 14.4, 0.25, 20877, POSTS[3], "q"),
        comp("hanbitbiz", "한빛비즈", 3.2, 0.3, 15020, POSTS[0], null),
        comp("munhakdongne", "문학동네", 9.8, 0.2, 30110, POSTS[1], null),
        comp("changbi", "창비", 6.1, 0.15, 12004, POSTS[5], null),
      ],
    },
    recommendation: {
      status: "known", reason: null,
      conclusion: "북토크 해시태그가 늘고 있으니 트렌드 밈 형식으로 책 읽는 일상을 짧고 가볍게 보여 주자",
      beats: [
        { slot: "0-3", text: "책 더미가 밈처럼 튀어나오는 책상", evidence: ["네이버 검색어 '소설' (20·30대) 최근 7일이 직전 28일보다 +361.3%"],
          evidence_items: [{ id: "search:1", text: "네이버 검색어 '소설' (20·30대) 최근 7일이 직전 28일보다 +361.3%", short: "소설 검색 +361%" }] },
        { slot: "3-15", text: "주말 독서 일상을 짧게", evidence: ["#북토크: 이번 7일 2곳 · 직전 7일 0곳 사용, 터진 게시물 1개"],
          evidence_items: [{ id: "hashtag:1", text: "#북토크: 이번 7일 2곳 · 직전 7일 0곳 사용, 터진 게시물 1개", short: "#북토크 2곳 사용" }] },
        { slot: "15-25", text: "북토크 후기 한 줄", evidence: ["minumsa_books 릴스: 좋아요 1,300 · 평소의 3.7배"],
          evidence_items: [{ id: "content:3", text: "minumsa_books 릴스: 좋아요 1,300 · 평소의 3.7배", short: "minumsa_books 릴스 좋아요 ×3.7" }] },
        { slot: "25-30", text: "다음 책 예고로 마무리", evidence: [] },
      ],
      music_note: "",
    },
  },
  limits: Array.from({ length: 8 }, (_, i) => `이번 보고서 주의 ${i + 1}`),
  limit_groups: {
    method: ["검색은 최근 7일 평균 ÷ 직전 28일 평균", "배수는 계정 평소 중앙값 대비"],
    cannot_see: ["경쟁사 광고비", "릴스 안쪽 장면"],
    this_report: Array.from({ length: 8 }, (_, i) => `이번 보고서 주의 ${i + 1}`),
  },
  calls: { instagram: 31, naver: 8, llm: 3 },
};
