import { useState } from "react";
import { writeScript } from "@/lib/creativeScriptApi";
import { DEFAULT_SCRIPT_SECONDS, SCRIPT_SECONDS, boardArrived, nearestCount, type CreativeScript, type ScriptSeconds } from "@/lib/creativeScript";
import { getCreativeRecord, type CreativeRecord } from "@/lib/creativeRecords";
import { MAX_STORY, startStoryboard, storyboardJob } from "@/lib/storyboard";

// 제작 기록 상세에서 대본 시작하기 (11/25).
// 총괄 홍보 영상 의뢰로 생긴 기록은 대본 없이 스토리보드만 있어 대사·자막을 고칠 수 없었다.
// - 대본이 없으면: 줄거리(기록 줄거리로 채움)와 길이를 정해 [대본 쓰기] → POST /creative-records/{id}/script
// - 대본이 있는데 스토리보드가 대본을 안 따르면: [대본대로 스토리보드 다시 만들기]
//   → POST /marketing/storyboard (record_id·background: 바로 답하고 뒤에서 만든다, 칸 = 대본 장면)
//   6칸 이상은 60초를 넘겨 nginx에서 끊겨서 기록을 5초마다 다시 읽어 붙었는지 본다(최대 5분)

const POLL_MS = 5000;
const POLL_LIMIT_MS = 5 * 60 * 1000;
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function RecordScriptWrite({ recordId, story, scenes, onWritten }: {
  recordId: string;
  story: string;
  /** 대본 장면 수. 지금 스토리보드 칸 수에 가까운 6·9·12 */
  scenes: number;
  onWritten: (script: CreativeScript, story: string) => void;
}) {
  const [text, setText] = useState(story.slice(0, MAX_STORY));
  const [seconds, setSeconds] = useState<ScriptSeconds>(DEFAULT_SCRIPT_SECONDS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const write = async () => {
    setBusy(true);
    setError(null);
    try {
      onWritten(await writeScript(recordId, { story: text, scenes: nearestCount(scenes), seconds }), text.trim());
    } catch (e) {
      setError(e instanceof Error ? e.message : "대본을 쓰지 못했어요");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2" role="group" aria-label="대본 쓰기">
      <p className="text-sm text-ink-500">이 기록에는 대본이 없어요. 줄거리를 확인하고 대본을 쓰면 장면별 대사·자막을 고칠 수 있어요.</p>
      <label className="block text-xs text-ink-500" htmlFor={`story-${recordId}`}>줄거리</label>
      <textarea id={`story-${recordId}`} className="field min-h-24 w-full" value={text} maxLength={MAX_STORY} onChange={(e) => setText(e.target.value)} />
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-ink-500">길이</span>
        {SCRIPT_SECONDS.map((s) => (
          <button key={s} type="button" className={`chip ${s === seconds ? "bg-brand-50 text-brand-700" : "bg-ink-50 text-ink-500"}`} aria-pressed={s === seconds} onClick={() => setSeconds(s)}>{s}초</button>
        ))}
        <button type="button" className="btn-primary ml-auto text-xs" disabled={busy || !text.trim()} onClick={() => void write()}>{busy ? "대본 쓰는 중…" : "대본 쓰기"}</button>
      </div>
      {error && <p className="text-xs text-accent" role="alert">{error}</p>}
    </div>
  );
}

export function RecordBoardFromScript({ recordId, story, characterEn, scenes, onRecord }: {
  recordId: string;
  story: string;
  characterEn?: string;
  scenes: number;
  /** 새 스토리보드가 붙은 기록 */
  onRecord: (rec: CreativeRecord) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remake = async () => {
    setBusy(true);
    setError(null);
    const startedAt = Date.now() - 2000; // 서버 시계와 조금 어긋나도 놓치지 않게
    try {
      await startStoryboard({ story, character_en: characterEn, shots: nearestCount(scenes), record_id: recordId });
      while (Date.now() - startedAt < POLL_LIMIT_MS) {
        await wait(POLL_MS);
        const rec = await getCreativeRecord(recordId).catch(() => null);
        if (rec && boardArrived(rec, startedAt)) {
          onRecord(rec);
          return;
        }
        // 서버가 실패를 알려 주면 5분을 다 기다리지 않고 이유를 보여 준다
        const job = await storyboardJob(recordId).catch(() => null);
        if (job?.status === "failed") {
          setError(`스토리보드를 만들지 못했어요${job.message ? ` — ${job.message}` : ""}${job.code ? ` (${job.code})` : ""}`);
          return;
        }
      }
      setError("5분 안에 스토리보드가 붙지 않았어요. 잠시 뒤 새로고침해 보세요");
    } catch (e) {
      setError(e instanceof Error ? e.message : "스토리보드를 만들지 못했어요");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <p className="text-xs text-ink-500">지금 스토리보드는 대본을 따르지 않아요. 영상은 스토리보드 장면으로 만들어져요.</p>
      <button type="button" className="btn-ghost ml-auto text-xs" disabled={busy || !story.trim()} onClick={() => void remake()}>{busy ? "스토리보드 만드는 중… (1~3분)" : "대본대로 스토리보드 다시 만들기"}</button>
      {error && <p className="w-full text-xs text-accent" role="alert">{error}</p>}
    </div>
  );
}
