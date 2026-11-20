// node --test로 돈다 (npm test). 말풍선에 영어 skill id가 그대로 나오지 않게 (lib/skillNames.ts)
import assert from "node:assert/strict";
import { test } from "node:test";
import { actionKo, humanizeSkills, skillKo } from "./skillNames.ts";

test("skillKo: 11/20 의뢰 흐름 id를 한국어로, 매핑 없는 id만 원문(밑줄 띄움)", () => {
  assert.equal(skillKo("generate_media"), "영상 제작");
  assert.equal(skillKo("prepare_video_draft_from_plan"), "영상 초안 준비");
  assert.equal(skillKo("trend_report_create"), "트렌드 분석 보고서");
  assert.equal(skillKo("trend_report_insight"), "기획에 넘길 인사이트");
  assert.equal(skillKo("receive_insight"), "기획 제안 받기");
  assert.equal(skillKo("impact_analysis"), "영향 분석"); // 원래 있던 매핑 그대로
  assert.equal(skillKo("brand_new_skill"), "brand new skill");
  assert.equal(skillKo(null), "");
  assert.equal(actionKo("brand_new_skill"), "brand_new_skill");
});

test("humanizeSkills: 문장 속 매핑 있는 id만 바꾼다 (단어 경계, memory.card_write 같은 점 id 포함)", () => {
  assert.equal(humanizeSkills("[의뢰 wr_ada8f37811] generate_media 실패: in_progress"), "[의뢰 wr_ada8f37811] 영상 제작 실패: in_progress");
  assert.equal(humanizeSkills("memory.card_write 끝났어요"), "회사 기억에 남기기 끝났어요");
  assert.equal(humanizeSkills("my_generate_media_x 그대로"), "my_generate_media_x 그대로");
  assert.equal(humanizeSkills(""), "");
});
