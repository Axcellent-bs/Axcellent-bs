// node --test로 돈다 (npm test). 배포 뒤 옛 코드 조각 받기 실패 → 한 번 새로고침 (lib/chunkReload.ts)
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CHUNK_RELOAD_BUTTON, CHUNK_RELOAD_KEY, CHUNK_RELOAD_TITLE, CHUNK_RELOAD_WINDOW_MS,
  isChunkLoadError, reloadOnce, type ReloadEnv,
} from "./chunkReload.ts";

function memoryStorage() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
}

function env(storage: ReloadEnv["storage"], now: number, href = "https://app.axcellent.cloud/data/trend") {
  const calls = { reload: 0 };
  return { calls, env: { storage, href, now, reload: () => void calls.reload++ } satisfies ReloadEnv };
}

test("preloadError → 한 번 새로고침하고 기록을 남긴다", () => {
  const s = memoryStorage();
  const { calls, env: e } = env(() => s, 1_000);
  assert.equal(reloadOnce(e), true);
  assert.equal(calls.reload, 1);
  assert.deepEqual(JSON.parse(s.m.get(CHUNK_RELOAD_KEY)!), { href: e.href, at: 1_000 });
});

test("같은 주소에서 60초 안 두 번째는 새로고침하지 않는다", () => {
  const s = memoryStorage();
  const first = env(() => s, 1_000);
  reloadOnce(first.env);
  const second = env(() => s, 1_000 + CHUNK_RELOAD_WINDOW_MS - 1);
  assert.equal(reloadOnce(second.env), false);
  assert.equal(second.calls.reload, 0);
});

test("60초가 지났거나 다른 주소면 다시 한 번 새로고침한다", () => {
  const s = memoryStorage();
  reloadOnce(env(() => s, 1_000).env);
  const later = env(() => s, 1_000 + CHUNK_RELOAD_WINDOW_MS);
  assert.equal(reloadOnce(later.env), true);
  const other = env(() => s, 1_000 + CHUNK_RELOAD_WINDOW_MS + 1, "https://app.axcellent.cloud/data/sales");
  assert.equal(reloadOnce(other.env), true);
});

test("sessionStorage가 막혀도 던지지 않고, 기록을 못 남기면 새로고침하지 않는다 (끝없는 새로고침 막기)", () => {
  const blocked = env(() => { throw new Error("SecurityError"); }, 1_000);
  assert.doesNotThrow(() => reloadOnce(blocked.env));
  assert.equal(reloadOnce(blocked.env), false);
  assert.equal(blocked.calls.reload, 0);
  const noWrite = env(() => ({ getItem: () => null, setItem: () => { throw new Error("QuotaExceededError"); } }), 1_000);
  assert.equal(reloadOnce(noWrite.env), false);
  assert.equal(noWrite.calls.reload, 0);
});

test("깨진 기록은 없는 것으로 보고 한 번 새로고침한다", () => {
  const s = memoryStorage();
  s.setItem(CHUNK_RELOAD_KEY, "{not json");
  const e = env(() => s, 1_000);
  assert.equal(reloadOnce(e.env), true);
});

test("조각 받기 실패 에러만 알아본다", () => {
  assert.equal(isChunkLoadError(new TypeError("Failed to fetch dynamically imported module: https://app.axcellent.cloud/assets/TrendScan-abc.js")), true);
  assert.equal(isChunkLoadError(new TypeError("error loading dynamically imported module")), true);
  assert.equal(isChunkLoadError(new TypeError("Importing a module script failed.")), true);
  assert.equal(isChunkLoadError(new Error("Unable to preload CSS for /assets/x.css")), true);
  assert.equal(isChunkLoadError(Object.assign(new Error("x"), { name: "ChunkLoadError" })), true);
  assert.equal(isChunkLoadError(new Error("Cannot read properties of undefined (reading 'items')")), false);
  assert.equal(isChunkLoadError(null), false);
  assert.equal(isChunkLoadError("Failed to fetch dynamically imported module"), false);
});

test("ErrorBoundary 문구", () => {
  assert.equal(CHUNK_RELOAD_TITLE, "새 버전이 나왔어요");
  assert.equal(CHUNK_RELOAD_BUTTON, "새로고침");
});
