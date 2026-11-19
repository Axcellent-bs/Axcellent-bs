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

test("대문자·섞인 대소문자 위험 주소, 상대 경로, 깨진 URL도 null (보안 리뷰 10번)", () => {
  for (const bad of [
    "JAVASCRIPT:alert(1)", "JavaScript:alert(1)", "\tjavascript:alert(1)", "DATA:text/html,x", "VBScript:x",
    "report.html", "./a", "../a", "?q=1", "#top", "   ",
    "https://", "http://", "https://exa mple.com", "https://[::1", "javascript:alert(1)//https://example.com",
  ]) {
    assert.equal(safeLink(bad), null, JSON.stringify(bad));
  }
});

test("인스타·유튜브·네이버 같은 실제 바깥 주소는 그대로 연다", () => {
  for (const ok of [
    "https://www.instagram.com/reel/ABC123/", "https://www.youtube.com/watch?v=abc_DEF-123", "https://youtu.be/abc",
    "https://news.naver.com/main/read?oid=1&aid=2", "http://example.com:8080/a?b=c#d",
  ]) {
    assert.equal(safeLink(ok), ok, ok);
  }
});
