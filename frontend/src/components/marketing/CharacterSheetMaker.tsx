import { useState } from "react";
import { ACCEPT, makeCharacterSheet, shrinkImage, type CharacterSheetResult } from "@/lib/characterSheet";

// 마케팅 › SNS·미디어 › 캐릭터 시트 프롬프트.
// 캐릭터 이미지 한 장(+ 선택 메모)을 올리면 마케팅 AI가 같은 캐릭터의 캐릭터 시트 이미지 프롬프트를 만든다.
// 방향 7칸·표정 칸은 서버 코드가 넣는다. 이미지는 저장하지 않고, 이미지를 만들거나 게시하지 않는다.

type Picked = { media_type: string; data_base64: string; preview: string; name: string };

function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "ok" | "failed">("idle");
  const copy = () => navigator.clipboard.writeText(text).then(() => setState("ok"), () => setState("failed"));
  return (
    <button type="button" className="btn-ghost text-xs" onClick={() => void copy()} aria-label={`${label} 복사`}>
      {state === "ok" ? "복사했어요" : state === "failed" ? "복사 실패" : "복사"}
    </button>
  );
}

function PromptBox({ title, text }: { title: string; text: string }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-ink-900">{title}</h3>
        <span className="ml-auto"><CopyButton text={text} label={title} /></span>
      </div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface p-3 text-xs leading-relaxed text-ink-700 ring-1 ring-ink-100">{text}</pre>
    </div>
  );
}

function Result({ r }: { r: CharacterSheetResult }) {
  return (
    <div className="space-y-5">
      <div className="space-y-1">
        <p className="text-sm text-ink-900">{r.summary_ko}</p>
        {r.personality_ko && <p className="text-xs text-ink-500">성격 느낌: {r.personality_ko}</p>}
        {r.stylized_from_photo && <p className="chip bg-ink-100 text-ink-700">실제 사람 사진이라 그림체 캐릭터로 바꿨어요</p>}
        {r.note_applied && <p className="text-xs text-ink-500">반영한 메모: {r.note_applied}</p>}
      </div>
      <PromptBox title="영어 이미지 프롬프트" text={r.prompt_en} />
      <PromptBox title="네거티브 프롬프트" text={r.negative_prompt_en} />
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <h3 className="text-sm font-semibold text-ink-900">이미지에서 본 것</h3>
          {r.seen_ko.length ? <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-ink-700">{r.seen_ko.map((x) => <li key={x}>{x}</li>)}</ul> : <p className="mt-1 text-sm text-ink-500">없음</p>}
        </div>
        <div>
          <h3 className="text-sm font-semibold text-ink-900">보이지 않아 추정한 것</h3>
          {r.guessed_ko.length ? <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-ink-700">{r.guessed_ko.map((x) => <li key={x}>{x}</li>)}</ul> : <p className="mt-1 text-sm text-ink-500">없음</p>}
        </div>
      </div>
      <div>
        <h3 className="text-sm font-semibold text-ink-900">칸 배치</h3>
        <ul className="mt-1 space-y-0.5 text-sm text-ink-700">{r.layout_ko.map((x) => <li key={x}>{x}</li>)}</ul>
      </div>
      <div>
        <h3 className="text-sm font-semibold text-ink-900">색 팔레트 · 키 비율 {r.proportions}</h3>
        <ul className="mt-2 flex flex-wrap gap-2">
          {r.palette.map((c) => (
            <li key={c.hex + c.name} className="chip bg-surface text-ink-700 ring-1 ring-ink-100">
              <span className="mr-1.5 inline-block h-3 w-3 rounded-full ring-1 ring-ink-200" style={{ backgroundColor: c.hex }} aria-hidden="true" />
              {c.name} <span className="ml-1 tabular-nums text-ink-500">{c.hex}</span>{c.use && <span className="ml-1 text-ink-500">· {c.use}</span>}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function CharacterSheetMaker() {
  const [picked, setPicked] = useState<Picked | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CharacterSheetResult | null>(null);

  const pick = async (file: File | undefined) => {
    setError(null);
    if (!file) return;
    try {
      const s = await shrinkImage(file);
      setPicked({ ...s, name: file.name });
      setResult(null);
    } catch (e) {
      setPicked(null);
      setError(e instanceof Error ? e.message : "이미지를 읽을 수 없어요");
    }
  };

  const make = async () => {
    if (!picked) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await makeCharacterSheet({ media_type: picked.media_type, data_base64: picked.data_base64 }, note));
    } catch (e) {
      setError(e instanceof Error ? e.message : "캐릭터 시트 프롬프트를 만들지 못했어요");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card space-y-4 p-6" aria-labelledby="character-sheet-title">
      <div>
        <h2 id="character-sheet-title" className="text-lg font-semibold">캐릭터 시트 프롬프트</h2>
        <p className="mt-1 text-xs text-ink-500">캐릭터 그림·사진 한 장을 올리면 같은 캐릭터의 정면·뒷면·왼쪽·오른쪽·위·아래·3/4 각도와 표정·포즈·색 팔레트가 한 장에 들어간 이미지 프롬프트를 만들어요. 읽기 일이라 아무것도 바꾸지 않고, 올린 이미지는 저장하지 않아요. 이미지는 만들지 않아요.</p>
      </div>
      <div className="flex flex-wrap items-start gap-4">
        <div className="flex h-32 w-32 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-surface ring-1 ring-ink-100">
          {picked ? <img src={picked.preview} alt="올린 캐릭터 이미지 미리보기" className="h-full w-full object-contain" /> : <span className="px-2 text-center text-xs text-ink-500">미리보기</span>}
        </div>
        <div className="min-w-0 flex-1 space-y-3">
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-ink-700">캐릭터 이미지 (PNG·JPEG·WebP)</span>
            <input type="file" accept={ACCEPT} className="field w-full text-sm" onChange={(e) => void pick(e.target.files?.[0])} />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-ink-700">메모 (선택)</span>
            <input type="text" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} placeholder="예: 옷은 겨울옷으로, 더 귀엽게" className="field w-full text-sm" />
          </label>
          <button type="button" className="btn-primary" disabled={!picked || busy} onClick={() => void make()}>{busy ? "만드는 중…" : "만들기"}</button>
        </div>
      </div>
      {/* 결과 자리: 결과가 오기 전에도 높이를 잡아 아래 칸이 밀리지 않게 한다 */}
      <div className="min-h-[12rem] border-t border-ink-100 pt-4" aria-live="polite">
        {error ? <p className="text-sm text-accent" role="alert">{error}</p>
          : busy ? <p className="text-sm text-ink-500">이미지를 읽고 프롬프트를 만드는 중이에요…</p>
          : result ? <Result r={result} />
          : <p className="text-sm text-ink-500">이미지를 올리고 만들기를 누르면 여기에 프롬프트가 나와요.</p>}
      </div>
    </section>
  );
}
