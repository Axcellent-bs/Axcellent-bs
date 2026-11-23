// node --test로 돈다 (npm test). 기획서·제안 "PDF로 저장": 인쇄 머리글 문구 · 열 경로(?print=1) · 기획서 만든 시각 (lib/planDoc.ts)
import assert from "node:assert/strict";
import { test } from "node:test";
import { planDocPath, planPrintHeader, specCreatedAt, wantsPrint } from "./planDoc.ts";

test("인쇄 머리글: 기획서 'SPEC-23' → '기획서 23 · 회사 · 시각 · 상태', 제안 → '제안 15', 모르는 칸은 뺀다", () => {
  assert.equal(planPrintHeader({ id: "SPEC-23", company: "캣북스", at: "11-20 16:55", status: "초안" }), "기획서 23 · 캣북스 · 11-20 16:55 · 초안");
  assert.equal(planPrintHeader({ id: "PROP-15", company: "캣북스", at: "11-20 16:40", status: "기획안" }), "제안 15 · 캣북스 · 11-20 16:40 · 기획안");
  assert.equal(planPrintHeader({ id: "SPEC-23", company: " ", at: null, status: "확정" }), "기획서 23 · 확정");
  assert.equal(planPrintHeader({ id: "PROP-15" }), "제안 15");
});

test("열 경로: 기획서가 있으면 기획서 화면, 없으면 제안 화면 · PDF는 ?print=1", () => {
  assert.equal(planDocPath({ id: "PROP-15", spec: "SPEC-23" }), "/planning/work/specs/SPEC-23");
  assert.equal(planDocPath({ id: "PROP-15", spec: "SPEC-23" }, true), "/planning/work/specs/SPEC-23?print=1");
  assert.equal(planDocPath({ id: "PROP-15" }), "/planning/proposals/PROP-15");
  assert.equal(planDocPath({ id: "PROP-15", spec: null }, true), "/planning/proposals/PROP-15?print=1");
  assert.equal(wantsPrint("?print=1"), true);
  assert.equal(wantsPrint("?print=0"), false);
  assert.equal(wantsPrint(""), false);
});

test("기획서 만든 시각: 활동 기록 write_spec 줄 중 그 번호의 가장 이른 것 (SPEC-2와 SPEC-23을 섞지 않음)", () => {
  const act = [
    { t: "11-20 17:02", point: "write_spec", text: "SPEC-23 수정" },
    { t: "11-20 16:55", point: "write_spec", text: "SPEC-23 작성 (제안 PROP-15)" },
    { t: "11-19 09:00", point: "write_spec", text: "SPEC-2 작성" },
    { t: "11-18 09:00", point: "confirm_spec", text: "SPEC-23 확정" },
  ];
  assert.equal(specCreatedAt(act, "SPEC-23"), "11-20 16:55");
  assert.equal(specCreatedAt(act, "SPEC-2"), "11-19 09:00");
  assert.equal(specCreatedAt(act, "SPEC-9"), null);
});

test("대화 카드에서 열면 ?request=wr_… 가 붙고, PDF와 같이 쓸 수 있다", () => {
  assert.equal(planDocPath({ id: "PROP-18", request: "wr_d8cf3a9691" }), "/planning/proposals/PROP-18?request=wr_d8cf3a9691");
  assert.equal(planDocPath({ id: "PROP-18", spec: "SPEC-30", request: "wr_d8cf3a9691" }, true), "/planning/work/specs/SPEC-30?print=1&request=wr_d8cf3a9691");
  assert.equal(planDocPath({ id: "PROP-18", request: null }), "/planning/proposals/PROP-18");
});
