// node --test로 돈다 (npm test). 대화방 정리 (11/23): 장애 방 삭제(모두에게 숨김)·/clear(나만 지우기).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  CLEAR_HINT, DIRECT_CLEAR_HINT, HIDE_LABEL, HIDE_WAIT, afterClear, clearDividerText, clearMap, commandHint,
  hiddenRows, hideConfirm, incidentHideItem, isClearCommand, withoutServerHidden,
} from "./roomCleanup.ts";

const msg = (id: string, at: string, lane?: "log") => ({ id, at, kind: "msg" as const, ...(lane ? { lane } : {}) });

test("서버가 숨긴 장애 방은 방 목록에서 빠지고, 숨긴 방 보기에는 서버 숨김·내 숨김이 함께", () => {
  const rooms = [{ id: "ctx_inc_a" }, { id: "ctx_inc_b" }, { id: "ctx_inc_c" }];
  assert.deepEqual(withoutServerHidden(rooms, new Set(["ctx_inc_b"])).map((r) => r.id), ["ctx_inc_a", "ctx_inc_c"]);
  assert.deepEqual(hiddenRows(["ctx_inc_b"], ["ctx_inc_c", "ctx_inc_b"]), [
    { id: "ctx_inc_b", source: "server" }, // 같은 방은 서버 숨김(되돌리기)으로 한 번만
    { id: "ctx_inc_c", source: "local" }, // 내 브라우저 숨김(다시 보이기)
  ]);
});

test("/clear 앞 말풍선·카드는 내 화면에서 빠지고, 지운 적 없으면 그대로", () => {
  const items = [msg("a", "2026-11-23T02:00:00Z"), msg("b", "2026-11-23T02:42:00Z"), msg("c", "2026-11-23T02:43:00Z"), msg("d", "")];
  assert.deepEqual(afterClear(items, undefined).map((x) => x.id), ["a", "b", "c", "d"]);
  // 지운 시각과 같은 줄은 지운 쪽, 시각 없는 줄도 지운 쪽
  assert.deepEqual(afterClear(items, "2026-11-23T02:42:00Z").map((x) => x.id), ["c"]);
  assert.deepEqual(clearMap([{ room_id: "all", cleared_at: "2026-11-23T02:42:00Z" }, { room_id: "x", cleared_at: null }]), { all: "2026-11-23T02:42:00Z" });
});

test("구분선 문구: HH:mm · 여기까지 지웠어요 (나만 안 보여요)", () => {
  const at = new Date(2026, 10, 23, 11, 42).toISOString(); // 화면 시각(로컬) 11:42
  assert.equal(clearDividerText(at), "11:42 · 여기까지 지웠어요 (나만 안 보여요)");
});

test("안 읽은 수는 지운 시각 뒤만 센다 (작업 기록 줄은 원래처럼 세지 않음)", () => {
  // 방 목록 계산(lib/chatRooms buildRooms)은 afterClear 뒤 목록으로 센다: 대화 줄만
  const items = [msg("a", "2026-11-23T01:00:00Z"), msg("b", "2026-11-23T03:00:00Z"), msg("c", "2026-11-23T03:05:00Z", "log"), msg("d", "2026-11-23T03:10:00Z")];
  const shown = afterClear(items, "2026-11-23T02:00:00Z");
  assert.equal(shown.filter((x) => !("lane" in x)).length, 2);
});

test("/clear는 메시지로 보내지 않는다: 명령 판정과 '/' 안내 한 줄", () => {
  assert.equal(isClearCommand("/clear"), true);
  assert.equal(isClearCommand("  /CLEAR  "), true);
  assert.equal(isClearCommand("/clear 해 줘"), false); // 말이 붙으면 보통 메시지
  assert.equal(isClearCommand("clear"), false);
  assert.equal(commandHint("/"), `/clear · ${CLEAR_HINT}`);
  assert.equal(commandHint("/cl"), `/clear · ${CLEAR_HINT}`);
  assert.equal(commandHint("/x"), null);
  assert.equal(commandHint("안녕 /"), null);
  // 1:1 AI 대화: /clear = 새 대화 시작 (다음 말이 새 thread를 만든다)
  assert.equal(commandHint("/", true), `/clear · ${DIRECT_CLEAR_HINT}`);
});

test("장애 방 ⋯ 메뉴: 해결된 사건만 삭제(모두에게 숨김), 진행 중·모름은 흐리게", () => {
  assert.deepEqual(incidentHideItem("resolved"), { label: HIDE_LABEL, sub: "팀 모두의 방 목록에서 사라져요 · 되돌릴 수 있어요", disabled: false, danger: true });
  for (const s of ["open", "degraded", undefined]) {
    const item = incidentHideItem(s);
    assert.equal(item.disabled, true);
    assert.equal(item.sub, HIDE_WAIT);
  }
  assert.deepEqual(hideConfirm("배송 지연 #0198"), {
    head: "배송 지연 #0198 방을 삭제할까요?",
    body: "팀 모두의 방 목록에서 사라져요. 장애 기록과 대화는 서버에 남고, '숨긴 방 보기'에서 되돌릴 수 있어요.",
  });
});
