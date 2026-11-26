import { useEffect, useState } from "react";
import { ACCEPT, makeCharacterSheet, shrinkExtra, shrinkImage, shrinkReference, type ExtraLabel } from "@/lib/characterSheet";
import { MULTI_HINT, buildExtras, referenceChip, slotsFor } from "@/lib/characterRefs";
import { listBrandCharacters, markBrandCharacter, type BrandCharacter } from "@/lib/brandCharacters";
import { redrawBoardImage, thumbSrc } from "@/lib/creativeRecords";
import { brandSheetState } from "@/lib/sheetState";
import { orchestrator } from "@/lib/orchestrator";
import {
  CHARACTER_NOTE, CHARACTER_QUESTION, KIND_OPTIONS, PLAN_OUTDATED_LINE, RIGHTS_HINT, RIGHTS_LABEL, SPACE_NOTE,
  UPLOAD_HINT, VIRTUAL_CONFIRM_LABEL, VIRTUAL_HUMAN_LABEL, VIRTUAL_NOTE,
  PREPARING_LINE, REPREPARE_CONFIRM, VIDEO_LOCKED_LINE,
  askMode, canUploadKind, characterStage, chatCharacterLine, isPromoVideo, isReferenceKind, needsReprepare, planOutdated, savedLine,
  type CharacterRow, type UploadKind,
} from "@/lib/requestCharacter";
import { refreshWorkRequests } from "@/components/office/useWorkRequests";

// 고객 의뢰 넘김 카드의 회사 캐릭터 질문 (홍보 영상 의뢰, 11/23 시안 ①).
// - 저장된 대표 캐릭터가 있으면 먼저: [이 캐릭터로 만들기][다른 이미지 올리기][캐릭터 없이]
// - 없으면 [있어요][없어요]. [있어요]·[다른 이미지 올리기]는 올리기 칸 + 권리 체크(체크해야 올리기 버튼이 켜진다)
//   올리면 마케팅 1:1과 같은 함수(makeCharacterSheet)로 이미지 시트(CR)를 만들고 대표 캐릭터로 표시한 뒤 의뢰에 적는다
// - 11/24: 홍보 영상(새 계획)은 이 답을 받아야 데이터분석이 시작된다(총괄 character 단계). 영상 준비가 시작되면 고른 값만 보인다(서버도 409)
// - 대표 캐릭터 목록은 로그인한 회사의 것만 온다. 한 화면의 카드 여러 개가 1분에 한 번만 읽는다
// - 11/24 variant="chat": 대화방 의뢰 진행 카드 맨 위. 홍보 영상(promo_video) 의뢰만, 정하면 한 줄
//   ("회사 캐릭터: 이름(CR-…) · 바꾸기" / "캐릭터 없이 진행"), 영상 준비가 시작되면 [바꾸기] 없이 한 줄만.
//   고객 의뢰 넘김 카드와 같은 함수·같은 API·같은 의뢰 기록을 본다

let cache: { at: number; list: Promise<BrandCharacter[]> } | null = null;
function loadSaved(force = false): Promise<BrandCharacter[]> {
  if (!cache || force || Date.now() - cache.at > 60_000) {
    const list = listBrandCharacters();
    list.catch(() => { cache = null; });
    cache = { at: Date.now(), list };
  }
  return cache.list;
}

/** 11/26 의뢰 진행 카드 사진·외형 한 줄: 고른 대표 캐릭터 (같은 목록·같은 1분 캐시). 못 읽거나 안 골랐으면 null */
export function useChosenCharacter(recordId: string | null | undefined): BrandCharacter | null {
  const [saved, setSaved] = useState<BrandCharacter[] | null>(null);
  useEffect(() => {
    if (!recordId) return;
    let live = true;
    loadSaved().then((x) => live && setSaved(x), () => live && setSaved([]));
    return () => { live = false; };
  }, [recordId]);
  return (recordId && saved?.find((c) => c.record_id === recordId)) || null;
}

const errText = (e: unknown) => (e instanceof Error && e.message ? e.message : "처리하지 못했어요");

export function BrandCharacterAsk({ row, variant = "handoff" }: { row: CharacterRow & { request_id: string }; variant?: "handoff" | "chat" }) {
  const chat = variant === "chat";
  const [saved, setSaved] = useState<BrandCharacter[] | null>(null);
  const [upload, setUpload] = useState(false);
  const [change, setChange] = useState(false);
  const [confirm, setConfirm] = useState(false); // 11/26 영상 준비 뒤 바꾸기: 다시 준비 확인 단계
  const [file, setFile] = useState<File | null>(null);
  const [rights, setRights] = useState(false);
  const [kind, setKind] = useState<UploadKind>("real_photo");
  const [extras, setExtras] = useState<Partial<Record<ExtraLabel, { media_type: string; data_base64: string; preview: string }>>>({});
  const [virtualOk, setVirtualOk] = useState(false);
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
  if (chat && !isPromoVideo(row)) return null;
  const chosen = saved?.find((c) => c.record_id === row.brand_character_record_id);
  const line = chat ? chatCharacterLine(row, chosen?.name) : null;
  const def = saved?.find((c) => c.is_default) ?? saved?.[0];
  const stage = characterStage(row);
  // 영상 초안 준비 뒤면 [바꾸기] 전에 화면 안에서 한 번 더 묻는다(콘티·키프레임 다시 준비, 그림 크레딧)
  const startChange = () => (needsReprepare(row) ? setConfirm(true) : setChange(true));
  const changeButton = (
    <button type="button" className="text-xs text-brand-700 underline" disabled={busy} onClick={startChange}>바꾸기</button>
  );
  const lockedLine = stage === "video_requested" || stage === "preparing" ? (
    <p className="chip self-start bg-ink-100 text-[11px] text-ink-700">{stage === "video_requested" ? VIDEO_LOCKED_LINE : PREPARING_LINE}</p>
  ) : null;
  const confirmBox = confirm && !change ? (
    <div role="group" aria-label="캐릭터 바꾸기 확인" className="space-y-1.5 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
      <p>{REPREPARE_CONFIRM}</p>
      <span className="flex flex-wrap gap-1.5">
        <button type="button" className="btn-primary btn-card" disabled={busy} onClick={() => { setConfirm(false); setChange(true); }}>다시 준비하고 바꾸기</button>
        <button type="button" className="btn-ghost btn-card" disabled={busy} onClick={() => setConfirm(false)}>취소</button>
      </span>
    </div>
  ) : null;

  const pick = (recordId: string | null) => {
    setBusy(true);
    setError(null);
    orchestrator.setBrandCharacter(row.request_id, recordId)
      .then(() => { setUpload(false); setChange(false); void refreshWorkRequests(); })
      .catch((e: unknown) => { setError(errText(e)); void refreshWorkRequests(); })
      .finally(() => setBusy(false));
  };

  const pickExtra = (key: ExtraLabel, f: File) => {
    setError(null);
    shrinkExtra(f).then(
      (r) => setExtras((x) => ({ ...x, [key]: { media_type: r.media_type, data_base64: r.data_base64, preview: r.preview } })),
      (e: unknown) => setError(errText(e)),
    );
  };

  const send = () => {
    if (!file || !canUploadKind(true, kind, rights, virtualOk)) return;
    setBusy(true);
    setError(null);
    // 가상 인물·일러스트는 원본을 참조로 쓰므로 긴 변 2048px까지 (실제 사람 사진은 지금처럼 1024px)
    (isReferenceKind(kind) ? shrinkReference(file) : shrinkImage(file))
      .then((img) => makeCharacterSheet({ media_type: img.media_type, data_base64: img.data_base64 }, "", img.thumb_base64,
        { kind, virtualConfirmed: kind === "virtual_human" && virtualOk, extras: isReferenceKind(kind) ? buildExtras(extras) : [] }))
      .then((sheet) => {
        if (!sheet.record_id) throw new Error("이미지 시트 기록을 남기지 못했어요. 다시 올려 주세요");
        return markBrandCharacter(sheet.record_id).then(() => sheet.record_id as string);
      })
      .then((recordId) => {
        void loadSaved(true).then(setSaved, () => undefined);
        return orchestrator.setBrandCharacter(row.request_id, recordId);
      })
      .then(() => { setUpload(false); setChange(false); setFile(null); setExtras({}); setRights(false); setVirtualOk(false); setKind("real_photo"); void refreshWorkRequests(); })
      .catch((e: unknown) => setError(errText(e)))
      .finally(() => setBusy(false));
  };

  // 11/26 A: 가상 인물·일러스트 카드에 시트 상태와 [캐릭터 시트 만들기 · 약 1.5 크레딧](기존 다시 그리기 경로)
  const [sheetBusy, setSheetBusy] = useState<string | null>(null);
  const makeSheet = (recordId: string) => {
    setSheetBusy(recordId);
    redrawBoardImage(recordId)
      .then(() => loadSaved(true).then(setSaved, () => undefined))
      .catch((e: unknown) => setError(errText(e)))
      .finally(() => setSheetBusy(null));
  };

  const savedCard = (c: BrandCharacter) => {
    const src = thumbSrc(c.thumbnail_base64);
    const sheet = brandSheetState(c.preview_part, c.kind);
    return (
      <div className="flex flex-wrap items-center gap-2.5 rounded-xl border border-ink-100 bg-surface p-2">
        {src ? <img src={src} alt="" className="h-11 w-11 shrink-0 rounded-lg object-contain" /> : <span className="h-11 w-11 shrink-0 rounded-lg bg-ink-100" aria-hidden="true" />}
        <span className="min-w-0 flex-1">
          <b className="block truncate text-[13px] text-ink-900">{c.name}</b>
          {c.kind === "virtual_human" && <span className="chip text-[11px]">{VIRTUAL_HUMAN_LABEL}</span>}
          {referenceChip(c) && <span className="chip ml-1 text-[11px]">{referenceChip(c)}</span>}
          <small className="block truncate text-xs tabular-nums text-ink-500">{savedLine(c)}</small>
          {sheet.button && <small className={`block text-xs ${sheet.hasSheet ? "text-ink-500" : "text-amber-800"}`}>{sheet.title}</small>}
        </span>
        {sheet.button && (
          <button type="button" className="btn-ghost shrink-0 text-xs" disabled={sheetBusy === c.record_id} onClick={() => makeSheet(c.record_id)}>
            {sheetBusy === c.record_id ? "요청하는 중…" : sheet.button}
          </button>
        )}
      </div>
    );
  };

  return (
    <section aria-label="회사 캐릭터" className={`${chat ? "" : "mt-2 "}space-y-2 rounded-xl bg-ink-50 px-3 py-2.5 text-[13px]`}>
      {chat && line && (mode === "closed" || mode === "chosen" || mode === "none") && (
        <p className="text-ink-900">
          {line.text}
          {line.canChange && <> · {changeButton}</>}
        </p>
      )}
      {(mode === "closed" || mode === "chosen" || mode === "none") && lockedLine}
      {confirmBox}
      {/* 11/24 시안 ②: 기획이 시작된 뒤 바꿨으면 한 줄 (서버 plan_outdated와 같은 규칙) */}
      {(mode === "closed" || mode === "chosen" || mode === "none") && planOutdated(row) && (
        <p className="text-xs text-amber-800">{PLAN_OUTDATED_LINE}</p>
      )}
      {!chat && (mode === "closed" || mode === "chosen" || mode === "none") && (
        <>
          <p className="flex flex-wrap items-center gap-1.5">
            <b className="font-medium text-ink-900">회사 캐릭터</b>
            <span className="chip bg-ink-100 text-[11px] text-ink-700">
              {row.brand_character_record_id ? (chosen?.name ?? row.brand_character_record_id) : "캐릭터 없이"}
            </span>
            {mode !== "closed" && changeButton}
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
              <fieldset className="space-y-1.5" aria-label="올릴 이미지 종류">
                {KIND_OPTIONS.map((o) => (
                  <label key={o.kind} className={`flex cursor-pointer items-start gap-2 rounded-xl border px-3 py-2 text-xs ${kind === o.kind ? "border-brand-500 bg-brand-50" : "border-ink-100 bg-surface"}`}>
                    <input type="radio" name={`upload-kind-${row.request_id}`} className="mt-0.5" checked={kind === o.kind} onChange={() => setKind(o.kind)} />
                    <span className="min-w-0">
                      <b className="text-ink-900">{o.label}</b>
                      {o.isNew && <span className="chip ml-1.5 bg-emerald-50 text-[11px] text-emerald-700">새로</span>}
                      <small className="block text-ink-500">{o.hint}</small>
                    </span>
                  </label>
                ))}
              </fieldset>
              <label className="relative block cursor-pointer rounded-xl border-[1.5px] border-dashed border-brand-500 bg-surface px-3 py-2.5 text-center text-xs text-ink-700">
                {file ? file.name : kind === "real_photo" ? "캐릭터 이미지를 눌러서 고르기" : "정면 (대표) 이미지를 눌러서 고르기"}
                <small className="block text-ink-500">{kind === "real_photo" ? UPLOAD_HINT : "PNG·JPG · 9:16 권장 · 올린 이미지를 그대로 참조로 써요"}</small>
                <input type="file" accept={ACCEPT} className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </label>
              {slotsFor(kind).length > 1 && (
                <div className="space-y-1">
                  <p className="text-xs text-ink-500">{MULTI_HINT}</p>
                  <div className="grid grid-cols-3 gap-1.5">
                    {slotsFor(kind).slice(1).map((s) => {
                      const key = s.key as ExtraLabel;
                      const got = extras[key];
                      return (
                        <div key={s.key} className="relative">
                          <label className="relative flex h-20 cursor-pointer flex-col items-center justify-center overflow-hidden rounded-xl border border-dashed border-ink-200 bg-surface text-center text-[11px] text-ink-700">
                            {got ? <img src={got.preview} alt={s.label} className="h-full w-full object-contain" /> : <span>{s.label}<small className="block text-ink-500">눌러서 고르기</small></span>}
                            <input type="file" accept={ACCEPT} className="sr-only" disabled={busy}
                              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) pickExtra(key, f); }} />
                          </label>
                          {got && <button type="button" className="absolute right-1 top-1 rounded-full bg-surface px-1.5 text-[11px] text-ink-700 shadow" disabled={busy} aria-label={`${s.label} 빼기`} onClick={() => setExtras((x) => ({ ...x, [key]: undefined }))}>빼기</button>}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {kind === "virtual_human" ? (
                <>
                  <label className="flex items-start gap-1.5 text-xs text-ink-700">
                    <input type="checkbox" className="mt-0.5" checked={virtualOk} onChange={(e) => setVirtualOk(e.target.checked)} />
                    <span>{VIRTUAL_CONFIRM_LABEL} <small className="text-ink-500">{RIGHTS_HINT}</small></span>
                  </label>
                  <p className="text-xs text-ink-500">{VIRTUAL_NOTE}</p>
                </>
              ) : (
                <label className="flex items-start gap-1.5 text-xs text-ink-700">
                  <input type="checkbox" className="mt-0.5" checked={rights} onChange={(e) => setRights(e.target.checked)} />
                  <span>{RIGHTS_LABEL} <small className="text-ink-500">{RIGHTS_HINT}</small></span>
                </label>
              )}
              <span className="flex flex-wrap gap-1.5">
                <button type="button" className="btn-primary btn-card" disabled={!canUploadKind(!!file, kind, rights, virtualOk, busy)} onClick={send}>{busy ? "올리는 중…" : "올리기"}</button>
                <button type="button" className="btn-ghost btn-card" disabled={busy} onClick={() => { setUpload(false); setFile(null); setExtras({}); setRights(false); setVirtualOk(false); setKind("real_photo"); }}>취소</button>
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
