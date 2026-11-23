import { useEffect, useState } from "react";
import { ACCEPT, makeCharacterSheet, shrinkImage } from "@/lib/characterSheet";
import { listBrandCharacters, markBrandCharacter, type BrandCharacter } from "@/lib/brandCharacters";
import { thumbSrc } from "@/lib/creativeRecords";
import { orchestrator } from "@/lib/orchestrator";
import {
  CHARACTER_NOTE, CHARACTER_QUESTION, RIGHTS_HINT, RIGHTS_LABEL, SPACE_NOTE, UPLOAD_HINT,
  askMode, canUpload, savedLine, type CharacterRow,
} from "@/lib/requestCharacter";
import { refreshWorkRequests } from "@/components/office/useWorkRequests";

// 고객 의뢰 넘김 카드의 회사 캐릭터 질문 (홍보 영상 의뢰, 11/23 시안 ①).
// - 저장된 대표 캐릭터가 있으면 먼저: [이 캐릭터로 만들기][다른 이미지 올리기][캐릭터 없이]
// - 없으면 [있어요][없어요]. [있어요]·[다른 이미지 올리기]는 올리기 칸 + 권리 체크(체크해야 올리기 버튼이 켜진다)
//   올리면 마케팅 1:1과 같은 함수(makeCharacterSheet)로 이미지 시트(CR)를 만들고 대표 캐릭터로 표시한 뒤 의뢰에 적는다
// - 질문 때문에 의뢰를 멈추지 않는다. 영상 준비가 시작되면 고른 값만 보인다(서버도 409)
// - 대표 캐릭터 목록은 로그인한 회사의 것만 온다. 한 화면의 카드 여러 개가 1분에 한 번만 읽는다

let cache: { at: number; list: Promise<BrandCharacter[]> } | null = null;
function loadSaved(force = false): Promise<BrandCharacter[]> {
  if (!cache || force || Date.now() - cache.at > 60_000) {
    const list = listBrandCharacters();
    list.catch(() => { cache = null; });
    cache = { at: Date.now(), list };
  }
  return cache.list;
}

const errText = (e: unknown) => (e instanceof Error && e.message ? e.message : "처리하지 못했어요");

export function BrandCharacterAsk({ row }: { row: CharacterRow & { request_id: string } }) {
  const [saved, setSaved] = useState<BrandCharacter[] | null>(null);
  const [upload, setUpload] = useState(false);
  const [change, setChange] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [rights, setRights] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shown = askMode(row, saved);
  const mode = change && (shown === "chosen" || shown === "none") ? (saved?.length ? "saved" : "ask") : shown;

  useEffect(() => {
    if (shown === "hidden") return;
    let live = true;
    loadSaved().then((x) => live && setSaved(x), () => live && setSaved([]));
    return () => { live = false; };
  }, [shown]);

  // 홍보 영상이 아니거나, 답하지 않은 채 영상 준비가 시작됐으면(캐릭터 없이 진행) 아무것도 두지 않는다
  if (mode === "hidden" || (mode === "closed" && row.brand_character_record_id === undefined)) return null;
  const chosen = saved?.find((c) => c.record_id === row.brand_character_record_id);
  const def = saved?.find((c) => c.is_default) ?? saved?.[0];

  const pick = (recordId: string | null) => {
    setBusy(true);
    setError(null);
    orchestrator.setBrandCharacter(row.request_id, recordId)
      .then(() => { setUpload(false); setChange(false); void refreshWorkRequests(); })
      .catch((e: unknown) => { setError(errText(e)); void refreshWorkRequests(); })
      .finally(() => setBusy(false));
  };

  const send = () => {
    if (!file || !rights) return;
    setBusy(true);
    setError(null);
    shrinkImage(file)
      .then((img) => makeCharacterSheet({ media_type: img.media_type, data_base64: img.data_base64 }, "", img.thumb_base64))
      .then((sheet) => {
        if (!sheet.record_id) throw new Error("이미지 시트 기록을 남기지 못했어요. 다시 올려 주세요");
        return markBrandCharacter(sheet.record_id).then(() => sheet.record_id as string);
      })
      .then((recordId) => {
        void loadSaved(true).then(setSaved, () => undefined);
        return orchestrator.setBrandCharacter(row.request_id, recordId);
      })
      .then(() => { setUpload(false); setChange(false); setFile(null); setRights(false); void refreshWorkRequests(); })
      .catch((e: unknown) => setError(errText(e)))
      .finally(() => setBusy(false));
  };

  const savedCard = (c: BrandCharacter) => {
    const src = thumbSrc(c.thumbnail_base64);
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-ink-100 bg-surface p-2">
        {src ? <img src={src} alt="" className="h-11 w-11 shrink-0 rounded-lg object-contain" /> : <span className="h-11 w-11 shrink-0 rounded-lg bg-ink-100" aria-hidden="true" />}
        <span className="min-w-0 flex-1">
          <b className="block truncate text-[13px] text-ink-900">{c.name}</b>
          <small className="block truncate text-xs tabular-nums text-ink-500">{savedLine(c)}</small>
        </span>
      </div>
    );
  };

  return (
    <section aria-label="회사 캐릭터" className="mt-2 space-y-2 rounded-xl bg-ink-50 px-3 py-2.5 text-[13px]">
      {(mode === "closed" || mode === "chosen" || mode === "none") && (
        <>
          <p className="flex flex-wrap items-center gap-1.5">
            <b className="font-medium text-ink-900">회사 캐릭터</b>
            <span className="chip bg-ink-100 text-[11px] text-ink-700">
              {row.brand_character_record_id ? (chosen?.name ?? row.brand_character_record_id) : "캐릭터 없이"}
            </span>
            {mode !== "closed" && <button type="button" className="text-xs text-brand-700 underline" disabled={busy} onClick={() => setChange(true)}>바꾸기</button>}
          </p>
          {row.brand_character_record_id && <p className="chip self-start bg-amber-50 text-[11px] text-amber-800">{CHARACTER_NOTE}</p>}
        </>
      )}
      {(mode === "ask" || mode === "saved") && (
        <>
          <p className="text-ink-900">{CHARACTER_QUESTION}</p>
          {mode === "saved" && def && !upload && (
            <>
              {savedCard(def)}
              <span className="flex flex-wrap gap-1.5">
                <button type="button" className="btn-primary btn-card" disabled={busy} onClick={() => pick(def.record_id)}>이 캐릭터로 만들기</button>
                <button type="button" className="btn-ghost btn-card" disabled={busy} onClick={() => setUpload(true)}>다른 이미지 올리기</button>
                <button type="button" className="btn-ghost btn-card" disabled={busy} onClick={() => pick(null)}>캐릭터 없이</button>
              </span>
            </>
          )}
          {mode === "ask" && !upload && (
            <span className="flex flex-wrap gap-1.5">
              <button type="button" className="btn-ghost btn-card" disabled={busy} onClick={() => setUpload(true)}>있어요</button>
              <button type="button" className="btn-ghost btn-card" disabled={busy} onClick={() => pick(null)}>없어요 (AI가 새로 그려요)</button>
            </span>
          )}
          {upload && (
            <div className="space-y-1.5">
              <label className="block cursor-pointer rounded-xl border-[1.5px] border-dashed border-brand-500 bg-surface px-3 py-2.5 text-center text-xs text-ink-700">
                {file ? file.name : "캐릭터 이미지를 눌러서 고르기"}
                <small className="block text-ink-500">{UPLOAD_HINT}</small>
                <input type="file" accept={ACCEPT} className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </label>
              <label className="flex items-start gap-1.5 text-xs text-ink-700">
                <input type="checkbox" className="mt-0.5" checked={rights} onChange={(e) => setRights(e.target.checked)} />
                <span>{RIGHTS_LABEL} <small className="text-ink-500">{RIGHTS_HINT}</small></span>
              </label>
              <span className="flex flex-wrap gap-1.5">
                <button type="button" className="btn-primary btn-card" disabled={!canUpload(!!file, rights, busy)} onClick={send}>{busy ? "올리는 중…" : "올리기"}</button>
                <button type="button" className="btn-ghost btn-card" disabled={busy} onClick={() => { setUpload(false); setFile(null); setRights(false); }}>취소</button>
              </span>
              <p className="chip self-start bg-amber-50 text-[11px] text-amber-800">{CHARACTER_NOTE}</p>
            </div>
          )}
          <p className="text-xs text-ink-500">{SPACE_NOTE}</p>
        </>
      )}
      {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
    </section>
  );
}
