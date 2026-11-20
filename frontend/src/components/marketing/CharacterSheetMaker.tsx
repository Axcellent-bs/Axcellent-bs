import { useState } from "react";
import { ACCEPT, makeCharacterSheet, shrinkImage, type CharacterSheetResult } from "@/lib/characterSheet";
import { MAX_STORY, SHOT_COUNTS, makeStoryboard, type ShotCount, type StoryboardResult } from "@/lib/storyboard";
import { Link } from "react-router";
import { PromptBox } from "./PromptParts";
import { SheetBoard } from "./SheetBoard";
import { RenderPanel } from "./RenderPanel";
import { StoryboardView } from "./StoryboardMaker";

// 마케팅 › SNS·미디어 › 이미지 시트 · 스토리보드 만들기.
// 캐릭터 이미지와 줄거리를 넣고 한 번 누르면 ① 이미지 시트(캐릭터 시트) 프롬프트를 만들고, 줄거리가 있으면
// ② 그 시트의 캐릭터 고정 문장(consistency_en)을 넣어 장면 구도를 정한 스토리보드까지 이어서 만든다.
// 그림체는 따로 고르지 않는다. 줄거리에 적으면 마케팅 AI가 읽어 쓴다.
// 캐릭터 이미지 한 장(+ 선택 메모)을 올리면 마케팅 AI가 같은 캐릭터의 캐릭터 시트 이미지 프롬프트를 만든다.
// 방향 7칸·표정 칸은 서버 코드가 넣는다. 이미지는 저장하지 않고, 이미지를 만들거나 게시하지 않는다.

type Picked = { media_type: string; data_base64: string; preview: string; thumb_base64: string; name: string };

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
  const [story, setStory] = useState("");
  const [shots, setShots] = useState<ShotCount>(12);
  const [step, setStep] = useState<"sheet" | "board" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CharacterSheetResult | null>(null);
  const [board, setBoard] = useState<StoryboardResult | null>(null);
  const busy = step !== null;

  const pick = async (file: File | undefined) => {
    setError(null);
    if (!file) return;
    try {
      const s = await shrinkImage(file);
      setPicked({ ...s, name: file.name });
      setResult(null);
      setBoard(null);
    } catch (e) {
      setPicked(null);
      setError(e instanceof Error ? e.message : "이미지를 읽을 수 없어요");
    }
  };

  // ① 이미지 시트 → (줄거리가 있으면) ② 스토리보드. 같은 이미지·메모로 시트를 이미 만들었으면 ①을 건너뛴다
  const make = async () => {
    if (!picked) return;
    setError(null);
    setBoard(null);
    let sheet = result;
    try {
      if (!sheet) {
        setStep("sheet");
        sheet = await makeCharacterSheet({ media_type: picked.media_type, data_base64: picked.data_base64 }, note, picked.thumb_base64);
        setResult(sheet);
      }
      if (story.trim()) {
        setStep("board");
        setBoard(await makeStoryboard({ story, shots, character_en: sheet.consistency_en, record_id: sheet.record_id }));
      }
    } catch (e) {
      const what = sheet ? "스토리보드" : "이미지 시트";
      setError(e instanceof Error ? e.message : `${what}를 만들지 못했어요`);
    } finally {
      setStep(null);
    }
  };

  const hasStory = story.trim().length > 0;
  const label = hasStory ? (result ? "스토리보드 만들기" : "이미지 시트 + 스토리보드 만들기") : "이미지 시트 만들기";
  return (
    <section className="card space-y-4 p-6" aria-labelledby="character-sheet-title">
      <div>
        <h2 id="character-sheet-title" className="text-lg font-semibold">이미지 시트 · 스토리보드 만들기</h2>
        <p className="mt-1 text-xs text-ink-500">캐릭터 그림·사진 한 장과 줄거리를 넣으면 같은 캐릭터의 방향·표정·포즈·색 팔레트가 든 이미지 시트를 만들고, 그 캐릭터로 칸마다 샷 크기·카메라 앵글·화면 배치를 정한 스토리보드까지 이어서 만들어요. 그림체는 줄거리에 적어 주세요. 줄거리를 비우면 이미지 시트만 만들어요. 올린 이미지는 저장하지 않고, 지금은 이미지가 아니라 프롬프트를 만들어요.</p>
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
            <span className="mb-1 block text-xs font-medium text-ink-700">캐릭터 메모 (선택)</span>
            <input type="text" maxLength={200} value={note} onChange={(e) => { setNote(e.target.value); setResult(null); }} placeholder="예: 옷은 겨울옷으로, 더 귀엽게" className="field w-full text-sm" />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-ink-700">줄거리 (그림체도 같이 적어 주세요)</span>
            <textarea value={story} maxLength={MAX_STORY} rows={3} onChange={(e) => setStory(e.target.value)} placeholder="예: 손그림 애니메이션 느낌. 가을 들판에서 자전거를 타던 소녀가 길가의 고양이를 만나 바구니에 태우고, 노을을 보며 주먹밥을 나눠 먹는다" className="field w-full text-sm" />
            <span className="mt-1 block text-right text-xs tabular-nums text-ink-500">{story.length}/{MAX_STORY}</span>
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <fieldset className="flex items-center gap-1" disabled={!hasStory}>
              <legend className="sr-only">스토리보드 칸 수</legend>
              <span className="mr-1 text-xs font-medium text-ink-700">칸 수</span>
              {SHOT_COUNTS.map((n) => (
                <button key={n} type="button" aria-pressed={shots === n} onClick={() => setShots(n)} className={shots === n ? "btn-primary text-xs" : "btn-ghost text-xs"}>{n}칸</button>
              ))}
            </fieldset>
            <button type="button" className="btn-primary ml-auto" disabled={!picked || busy} onClick={() => void make()}>{busy ? "만드는 중…" : label}</button>
          </div>
        </div>
      </div>
      {/* 결과 자리: 결과가 오기 전에도 높이를 잡아 아래 칸이 밀리지 않게 한다 */}
      <div className="min-h-[12rem] space-y-6 border-t border-ink-100 pt-4" aria-live="polite">
        {busy && (
          <ol className="flex flex-wrap gap-2 text-xs" aria-label="진행 단계">
            <li className={`chip ${step === "sheet" ? "bg-brand-50 text-brand-700" : "bg-ink-50 text-ink-500"}`}>1. 이미지 시트 {step === "sheet" ? "만드는 중" : "완료"}</li>
            {hasStory && <li className={`chip ${step === "board" ? "bg-brand-50 text-brand-700" : "bg-ink-50 text-ink-500"}`}>2. 스토리보드 {step === "board" ? "만드는 중" : "대기"}</li>}
          </ol>
        )}
        {error && <p className="text-sm text-accent" role="alert">{error}</p>}
        {result && (
          <details open={!board}>
            <summary className="cursor-pointer text-sm font-semibold text-ink-900">이미지 시트</summary>
            <div className="space-y-4 pt-3">
              <SheetBoard r={result} thumb={picked?.preview} />
              <details>
                <summary className="cursor-pointer text-xs text-ink-500">프롬프트와 자세한 내용</summary>
                <div className="pt-3"><Result r={result} /></div>
              </details>
            </div>
          </details>
        )}
        {board && (
          <div className="space-y-3">
            <h3 className="text-sm font-semibold text-ink-900">스토리보드</h3>
            <StoryboardView r={board} />
          </div>
        )}
        {result?.record_id && !busy && <RenderPanel key={`${result.record_id}:${board ? 1 : 0}`} recordId={result.record_id} />}
        {result?.record_id && (
          <p className="text-xs text-ink-500">제작 기록 <Link className="underline" to={`/marketing/records?id=${result.record_id}`}>{result.record_id}</Link>에 남겼어요.</p>
        )}
        {!busy && !error && !result && <p className="text-sm text-ink-500">이미지와 줄거리를 넣고 만들기를 누르면 여기에 이미지 시트와 스토리보드가 나와요.</p>}
      </div>
    </section>
  );
}
