import { test } from "node:test";
import assert from "node:assert/strict";
import { safeLink } from "./safeLink.ts";

test("http·https 주소는 그대로 연다", () => {
  assert.equal(safeLink("https://example.com/a/b/c"), "https://example.com/a/b/c");
  assert.equal(safeLink("http://example.com"), "http://example.com");
  assert.equal(safeLink("HTTPS://EXAMPLE.COM"), "HTTPS://EXAMPLE.COM");
  assert.equal(safeLink("  https://example.com "), "https://example.com");
});

test("그 밖의 주소와 빈 값은 null", () => {
  for (const bad of ["javascript:alert(1)", " javascript:alert(1)", "data:text/html,x", "vbscript:x", "//evil.example", "/work/prs", "ftp://x", "https:/x", "", null, undefined]) {
    assert.equal(safeLink(bad), null, String(bad));
  }
});
