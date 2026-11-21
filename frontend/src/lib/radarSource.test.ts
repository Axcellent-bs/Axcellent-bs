// node --test로 돈다 (npm test). 인스타 레이더 계정 목록 출처 글자 (dashboard-api `source`)
import assert from "node:assert/strict";
import { test } from "node:test";
import { RADAR_SOURCE } from "./radarSource.ts";

test("출처 auto는 '자동 (경쟁사 찾기로 확인한 계정)', 나머지 글자는 그대로", () => {
  assert.equal(RADAR_SOURCE.auto, "자동 (경쟁사 찾기로 확인한 계정)");
  assert.deepEqual(RADAR_SOURCE, {
    screen: "화면에서 저장한 목록",
    tenant: "회사 설정 파일",
    auto: "자동 (경쟁사 찾기로 확인한 계정)",
    none: "없음",
  });
});
