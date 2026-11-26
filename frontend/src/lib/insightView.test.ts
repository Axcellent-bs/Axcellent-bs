// node --test (npm test). 인사이트 탭 보고서 카드 = "기획에 넘긴 것" (11/26, lib/insightView.ts · components/data/ReportInsights.tsx)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  FIRST_ROWS, cardHead, directionChips, evidenceFacts, factFlags, insightSourceLabel, isWeakFact, orderFacts, planResult, reportSection,
  reportStatusLabel, themeCandidates, weakWarning, type FactLike,
} from "./insightView.ts";

const RID = "tr_bb81aa00";
const f = (label: string, value: string, source = "trend_report_create"): FactLike => ({ label, value, source });

// TR-BB81 모양 (데이터 AI row_facts label 규칙 그대로)
const BB81: FactLike[] = [
  f("방향", "재미 먼저 · 브랜드는 끝 카드에"),
  f("네이버 검색 '음악방송' (20대 여성) 최근 7일 vs 직전 28일", "-54.9%"),
  f("인스타 #책추천를 쓴 계정 (이번 7일)", "2곳 (직전 7일 1곳)"),
  f("네이버 검색 '공연 티켓' (20대 여성) 최근 7일 vs 직전 28일", "+12.3%"),
  f("릴스 조회 (@memezar, 3일 전)", "2,465만"),
];

test("출처 매핑: 보고서 근거는 섹션 6종 + 모름 → 트렌드 보고서, 그 밖 source는 지금 출처 이름", () => {
  const cases: [string, string][] = [
    ["네이버 검색 '음악방송' (20대) 최근 7일 vs 직전 28일", "네이버 검색"],
    ["네이버 쇼핑 클릭 '굿즈' (20대) 최근 7일 vs 직전 28일", "네이버 쇼핑"],
    ["인스타 #책추천를 쓴 계정 (이번 7일)", "인스타 해시태그"],
    ["릴스 조회 (@memezar, 3일 전)", "콘텐츠"],
    ["유튜브 쇼츠 조회 (웃긴채널)", "콘텐츠"],
    ["쇼츠 '상황극' 조회 (쇼츠채널)", "콘텐츠"],
    ["영상 조회 (채널)", "콘텐츠"],
    ["캣북스 릴스 터짐", "콘텐츠"],
    ["경쟁사 북클럽", "경쟁사"],
    ["주제 '공연' 후보 뮤지컬 (20대 여성) 네이버 검색 관심", "주제 후보"],
  ];
  for (const [label, want] of cases) {
    assert.equal(reportSection(label), want, label);
    assert.equal(insightSourceLabel(f(label, "1"), RID), `${want} · TR-BB81`);
  }
  assert.equal(reportSection("알 수 없는 줄"), null);
  assert.equal(insightSourceLabel(f("알 수 없는 줄", "1"), RID), "트렌드 보고서 · TR-BB81");
  // 다른 source는 지금 출처 이름(planningLabels.sourceLabel), 모르면 그대로
  assert.equal(insightSourceLabel(f("검색", "1", "trend_history"), RID), "검색 기록");
  assert.equal(insightSourceLabel(f("검색", "1", "something_else"), RID), "something_else");
});

test("방향 칩은 \" · \"로 나누고, 근거 목록에서는 뺀다", () => {
  assert.deepEqual(directionChips(BB81), ["재미 먼저", "브랜드는 끝 카드에"]);
  assert.equal(evidenceFacts(BB81).some((x) => x.label === "방향"), false);
  assert.equal(evidenceFacts(BB81).length, 4);
  assert.deepEqual(directionChips([f("검색", "1")]), []);
});

test("주제 후보는 최대 3, 같은 이름 한 번, 없으면 빈 목록(줄 생략)", () => {
  const themes = ["뮤지컬", "콘서트", "연극", "오페라"].map((n) => f(`주제 '공연' 후보 ${n} (20대 여성) 네이버 검색 관심`, "50/100"));
  assert.deepEqual(themeCandidates([...themes, themes[0]]), ["뮤지컬", "콘서트", "연극"]);
  assert.deepEqual(themeCandidates(BB81), []);
});

test("값 칩: 음수 변화율 → 줄어드는 중, 해시태그 N<5곳 → 표본 작음", () => {
  assert.deepEqual(factFlags("-54.9%"), { falling: true, smallSample: false });
  assert.deepEqual(factFlags("37/100 · 최근 7일 -3.0%"), { falling: true, smallSample: false });
  assert.deepEqual(factFlags("+12.3%"), { falling: false, smallSample: false });
  assert.deepEqual(factFlags("2곳 (직전 7일 1곳)"), { falling: false, smallSample: true });
  assert.deepEqual(factFlags("4곳 (직전 7일 0곳)"), { falling: false, smallSample: true });
  assert.deepEqual(factFlags("5곳 (직전 7일 0곳)"), { falling: false, smallSample: false });
  assert.deepEqual(factFlags("2,465만"), { falling: false, smallSample: false });
  assert.deepEqual(factFlags("릴스 40% · 주당 3개"), { falling: false, smallSample: false });
});

test("약한 근거는 뒤로(같은 무리 안 순서 그대로), 처음 3줄이 모두 약하면 경고", () => {
  const ordered = orderFacts(evidenceFacts(BB81));
  assert.deepEqual(ordered.map((x) => x.value), ["+12.3%", "2,465만", "-54.9%", "2곳 (직전 7일 1곳)"]);
  assert.equal(isWeakFact(ordered[0]), false);
  assert.equal(weakWarning(ordered), null);
  const allWeak = orderFacts([f("네이버 검색 'a'", "-1.0%"), f("인스타 #b를 쓴 계정", "1곳 (직전 7일 0곳)"), f("네이버 검색 'c'", "-2.0%"), f("릴스 조회 (@x)", "9,800")]);
  assert.equal(allWeak[0].value, "9,800"); // 약하지 않은 것이 하나라도 있으면 그것이 먼저
  assert.equal(weakWarning(allWeak), null);
  const weakOnly = [f("네이버 검색 'a'", "-1.0%"), f("인스타 #b를 쓴 계정", "1곳 (직전 7일 0곳)"), f("네이버 검색 'c'", "-2.0%")];
  assert.equal(weakWarning(orderFacts(weakOnly)), "기획이 기댈 근거가 약해요");
  assert.equal(weakWarning([]), null);
  assert.equal(FIRST_ROWS, 3);
});

test("기획 결과 문구 3종과 상태 톤, 기획 기록이 없을 때", () => {
  const p = { title: "공연 티켓 반전 영상", status: "기획안" as const };
  assert.deepEqual(planResult({ ...p, writtenBy: "llm" }), { who: "AI 제작안", title: p.title, status: "기획안", tone: "ok" });
  assert.equal(planResult({ ...p, writtenBy: "rule" })?.who, "규칙 초안");
  assert.equal(planResult({ ...p, writtenBy: "human" })?.who, "사람이 씀");
  assert.equal(planResult({ ...p, writtenBy: "llm", status: "폐기" })?.tone, "bad");
  assert.equal(planResult({ ...p, writtenBy: "llm", status: "확인 대기" })?.tone, "warn");
  assert.equal(planResult(null), null);
  assert.equal(cardHead(RID, { id: "PROP-31" }), "TR-BB81 → 기획 PROP-31");
  assert.equal(cardHead(RID, null), "TR-BB81 → 아직 기획에 안 감");
  assert.equal(reportStatusLabel("done"), "완료");
  assert.equal(reportStatusLabel("partial"), "일부 완료");
  assert.equal(reportStatusLabel("running"), null);
});

test("카드 화면: 근거 표는 약한 근거 뒤로 + 처음 3줄 + 모두 보기, 출처는 한국어, 11px 글씨 없음", () => {
  const src = readFileSync(new URL("../components/data/ReportInsights.tsx", import.meta.url), "utf8");
  assert.match(src, /orderFacts\(evidenceFacts\(insight\.facts\)\)/);
  assert.match(src, /facts\.slice\(0, FIRST_ROWS\)/);
  assert.match(src, /근거 \{facts\.length\}개 모두/);
  assert.match(src, /insightSourceLabel\(f, reportId\)/);
  assert.match(src, /줄어드는 중/);
  assert.match(src, /표본 작음/);
  assert.match(src, /\{warn && <p[^>]*>⚠ \{warn\}<\/p>\}/);
  assert.match(src, /기획에 넘긴 것/);
  assert.doesNotMatch(src, /text-\[(10|10\.5|11|11\.5)px\]/);
  // 원래 기능: 보고서 열기 · 기획에 보내기 그대로, PROP 열기 추가
  assert.match(src, /보고서 열기/);
  assert.match(src, /<HandoffButton reportId=\{reportId\}/);
  assert.match(src, /\/planning\/proposals\/\$\{encodeURIComponent\(proposal\.id\)\}/);
});
