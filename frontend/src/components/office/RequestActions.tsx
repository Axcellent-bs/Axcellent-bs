import { useState } from "react";
import { orchestrator } from "@/lib/orchestrator";
import { requestEditError } from "@/lib/requestCards";
import { EDIT_MAX, EDIT_NOTE, deleteConfirm, editProblem, type WorkRequestRow } from "@/lib/workRequests";
import { refreshWorkRequests } from "@/components/office/useWorkRequests";

// 고객 의뢰 하나의 정리 메뉴 (11/23): [의뢰문 수정] [의뢰 삭제]. 고객 의뢰 넘김 카드·대화방 의뢰 진행 카드가 같이 쓴다.
// - 삭제 = 멈추고 숨김 (POST /api/requests/{id}/hide). 확인은 화면 안 상자 (confirm() 쓰지 않음). 기록은 서버에 남고 되돌릴 수 있다
// - 수정 = 고쳐서 다시 접수 (POST /api/requests/{id}/edit). 원문을 채운 입력칸 + [고쳐서 다시 접수] [취소]
// - 실패는 버튼 아래 한 줄 (403·404·409는 결정 실패 문구와 같은 함수, 422·502·503은 서버 문장)
// - 원문은 사람 토큰이 있는 화면에서만 보인다 (부르는 카드가 토큰이 있을 때만 그린다)

type Mode = "closed" | "menu" | "edit" | "delete";

export function RequestActions({ row, title, onDone }: { row: WorkRequestRow; title: string; onDone?: (newId: string | null) => void }) {
  const [mode, setMode] = useState<Mode>("closed");
  const [text, setText] = useState(row.text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const problem = editProblem(text, row.text);
  const confirm = deleteConfirm(title, row);

  const close = () => { setMode("closed"); setError(null); setText(row.text); };
  const run = (job: () => Promise<string | null>) => {
    setBusy(true);
    setError(null);
    job()
      .then((newId) => { close(); void refreshWorkRequests(); onDone?.(newId); })
      .catch((e: unknown) => {
        const err = requestEditError(e);
        setError(err.text);
        if (err.reload) void refreshWorkRequests();
      })
      .finally(() => setBusy(false));
  };

  return (
    <div className="min-w-0">
      {mode === "closed" && (
        <button type="button" className="btn-ghost px-1.5 py-0 text-base leading-none text-ink-500" aria-label="의뢰 정리 메뉴" aria-expanded={false}
          onClick={() => setMode("menu")}>⋯</button>
      )}
      {mode === "menu" && (
        <span className="flex flex-wrap gap-1.5" role="group" aria-label="의뢰 정리">
          <button type="button" className="btn-ghost px-2.5 py-1 text-xs" onClick={() => setMode("edit")}>의뢰문 수정</button>
          <button type="button" className="btn-ghost px-2.5 py-1 text-xs text-red-700" onClick={() => setMode("delete")}>의뢰 삭제</button>
          <button type="button" className="btn-ghost px-2.5 py-1 text-xs" onClick={close}>닫기</button>
        </span>
      )}
      {mode === "edit" && (
        <div className="mt-1.5 space-y-1.5" role="group" aria-label="의뢰문 수정">
          <b className="block text-[13px] text-ink-900">의뢰문 수정</b>
          <textarea className="field min-h-24 w-full text-[13px]" value={text} maxLength={EDIT_MAX} onChange={(e) => setText(e.target.value)} aria-label="고칠 의뢰문" />
          <p className="text-xs text-ink-500">{EDIT_NOTE}</p>
          <span className="flex flex-wrap gap-1.5">
            <button type="button" className="btn-primary px-2.5 py-1 text-xs" disabled={busy || problem !== null}
              title={problem ?? undefined}
              onClick={() => run(() => orchestrator.editRequest(row.request_id, text).then((r) => r.request_id))}>고쳐서 다시 접수</button>
            <button type="button" className="btn-ghost px-2.5 py-1 text-xs" disabled={busy} onClick={close}>취소</button>
          </span>
          {problem && text !== row.text && <p className="text-xs text-ink-500">{problem}</p>}
        </div>
      )}
      {mode === "delete" && (
        <div role="alertdialog" aria-label={confirm.head} className="mt-1.5 space-y-1.5 rounded-xl border border-red-300 bg-red-50 px-3 py-2 text-[12.5px] text-red-800">
          <b className="block">{confirm.head}</b>
          <p>{confirm.body}</p>
          <span className="flex flex-wrap gap-1.5">
            <button type="button" className="btn-primary bg-red-700 px-2.5 py-1 text-xs hover:bg-red-800" disabled={busy}
              onClick={() => run(() => orchestrator.hideRequest(row.request_id).then(() => null))}>삭제</button>
            <button type="button" className="btn-ghost px-2.5 py-1 text-xs" disabled={busy} onClick={close}>취소</button>
          </span>
        </div>
      )}
      {error && <p role="alert" className="mt-1 text-xs text-red-700">{error}</p>}
    </div>
  );
}

/** 목록 아래 "숨긴 의뢰 N건 보기" → 숨긴 의뢰 목록 + [되돌리기] (펼칠 때만 읽는다) */
export function HiddenRequests({ titleOf }: { titleOf: (row: WorkRequestRow) => string }) {
  const [rows, setRows] = useState<WorkRequestRow[] | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = () =>
    orchestrator.hiddenRequests()
      .then((r) => setRows(r.data))
      .catch((e: unknown) => setError(requestEditError(e).text));
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) void load();
  };
  const restore = (id: string) => {
    setError(null);
    orchestrator.unhideRequest(id)
      .then(() => { void load(); void refreshWorkRequests(); })
      .catch((e: unknown) => setError(requestEditError(e).text));
  };
  return (
    <div className="mt-2 text-[13px]">
      <button type="button" className="text-brand-700 underline hover:text-brand-800" aria-expanded={open} onClick={toggle}>
        {open && rows ? `숨긴 의뢰 ${rows.length}건 닫기` : rows ? `숨긴 의뢰 ${rows.length}건 보기` : "숨긴 의뢰 보기"}
      </button>
      {open && rows && rows.length === 0 && <p className="mt-1 text-ink-500">숨긴 의뢰가 없어요</p>}
      {open && rows && rows.length > 0 && (
        <ul className="mt-1.5 space-y-1">
          {rows.map((r) => (
            <li key={r.request_id} className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-ink-700" title={r.request_id}>{titleOf(r)} · {r.request_id}</span>
              <button type="button" className="btn-ghost px-2.5 py-1 text-xs" onClick={() => restore(r.request_id)}>되돌리기</button>
            </li>
          ))}
        </ul>
      )}
      {error && <p role="alert" className="mt-1 text-xs text-red-700">{error}</p>}
    </div>
  );
}
