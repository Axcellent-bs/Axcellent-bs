import { useRef, useState, type KeyboardEvent } from "react";
import { ACCEPT, makeCharacterSheet, shrinkImage } from "@/lib/characterSheet";
import { TALK_KEYS, TALK_LABEL, type OfficeTalk } from "@/components/office/useOfficeTalk";
import { ALL_ROOM } from "@/lib/agentChat";
import { Link } from "@/lib/nav";

// 부서 AI에게 직접 말 거는 입력창 (받는 곳 칩 · @태그 · 안내 줄).
// AI 오피스 단톡방과 AI 대화방 전체 대화방이 같이 쓴다. 단톡방 대화는 모두에게 보이고, 1:1 직접 대화와는 따로 잇는다(useOfficeTalk).

const TAG_KEY: Record<string, string> = Object.fromEntries(TALK_KEYS.map((k) => [TALK_LABEL[k], k]));
const DEV_NOTE = "개발 AI는 '진행해'를 받으면 기획에 요청으로 넘겨요. 기획에서 사람이 기획서를 확정해야 개발이 시작돼요.";

/** 맨 앞 "@부서"(뒤에 AI가 붙어도 됨)를 떼고 받는 곳을 고른다. 태그가 없으면 key null */
export function parseTag(text: string): { key: string | null; bad: boolean; body: string } {
  const m = text.match(/^@(\S+)\s*/);
  if (!m) return { key: null, bad: false, body: text };
  let name = m[1];
  let rest = text.slice(m[0].length);
  if (name.endsWith("AI") && name.length > 2) name = name.slice(0, -2);
  else if (/^AI(\s|$)/.test(rest)) rest = rest.replace(/^AI\s*/, "");
  const key = TAG_KEY[name] ?? null;
  return { key, bad: !key, body: rest.trim() };
}

/**
 * `where`: 놓인 화면. 오피스는 대화 기록으로 가는 링크를, 대화 기록은 직접 대화로 이어진다는 안내를 단다.
 * `room`: 말을 거는 방 (전체 대화방 ALL_ROOM 또는 장애·이슈 방 context_id). `roomName`이 있으면 장애 방 안내 문구
 */
export function TalkInput({ talk, onSent, where, room = ALL_ROOM, roomName }: {
  talk: OfficeTalk; onSent: () => void; where: "office" | "chat"; room?: string; roomName?: string;
}) {
  const [to, setTo] = useState<string>("data");
  const [text, setText] = useState("");
  const [warn, setWarn] = useState<string | null>(null);
  // 받는 곳이 마케팅일 때만: 올릴 캐릭터 이미지. 보내면 먼저 이미지 시트를 만들고 그 기록 번호를 말과 함께 보낸다
  const [picked, setPicked] = useState<{ media_type: string; data_base64: string; preview: string; thumb_base64: string } | null>(null);
  const [sheetBusy, setSheetBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  if (!talk.account) {
    return <p className="border-t border-ink-100 px-4 py-3 text-sm text-ink-500">로그인하면 부서 AI에게 말을 걸 수 있어요</p>;
  }
  const chip = talk.chips.find((c) => c.key === to);
  const label = TALK_LABEL[to] ?? to;
  const pickImage = async (file: File | undefined) => {
    if (!file) return;
    try {
      setPicked(await shrinkImage(file));
      setWarn(null);
    } catch (err) {
      setWarn(err instanceof Error ? err.message : "이미지를 읽을 수 없어요");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };
  const go = async () => {
    const image = to === "marketing" ? picked : null;
    const raw = text.trim() || (image ? "이 캐릭터로 이미지 시트 만들어 줘" : "");
    if (!raw) return;
    const tag = parseTag(raw);
    if (tag.bad) { setWarn("그 부서는 아직 말을 받을 수 없어요"); return; }
    const key = tag.key ?? to;
    const target = talk.chips.find((c) => c.key === key);
    if (!target || target.disabled) { setWarn("그 부서는 아직 말을 받을 수 없어요"); return; }
    if (!tag.body) return;
    if (talk.busy(key, room) || sheetBusy) return;
    setWarn(null);
    setTo(key);
    let recordId: string | undefined;
    if (image && key === "marketing") {
      setSheetBusy(true);
      try {
        const sheet = await makeCharacterSheet({ media_type: image.media_type, data_base64: image.data_base64 }, "", image.thumb_base64);
        if (!sheet.record_id) throw new Error("이미지 시트는 만들었지만 기록 번호를 받지 못했어요. 다시 올려 주세요.");
        recordId = sheet.record_id;
        setPicked(null);
      } catch (err) {
        setWarn(err instanceof Error ? err.message : "이미지 시트를 만들지 못했어요");
        return;
      } finally {
        setSheetBusy(false);
      }
    }
    setText("");
    onSent();
    const ok = await talk.send(key, tag.body, room, recordId);
    if (!ok) setText(raw);
    else onSent();
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void go(); }
  };
  return (
    <div className="space-y-2 border-t border-ink-100 px-3 py-3">
      <div role="radiogroup" aria-label="받는 곳" className="flex flex-wrap gap-1.5">
        {talk.chips.map((c) => (
          <button key={c.key} type="button" role="radio" aria-checked={to === c.key} disabled={c.disabled} title={c.reason ?? undefined}
            onClick={() => { setTo(c.key); setWarn(null); }}
            className={`chip py-1 text-xs transition disabled:cursor-not-allowed disabled:opacity-50 ${to === c.key ? "bg-brand-600 text-white" : "bg-ink-50 text-ink-700 ring-1 ring-ink-100 hover:bg-ink-100"}`}>
            {c.label}
          </button>
        ))}
      </div>
      {to === "marketing" && picked && (
        <div className="flex items-center gap-2 text-xs text-ink-700">
          <img src={picked.preview} alt="올릴 캐릭터 이미지" className="h-14 w-14 rounded-lg bg-surface object-contain ring-1 ring-ink-100" />
          <span>{sheetBusy ? "이 이미지로 이미지 시트를 만드는 중… (10~40초)" : "보내면 이 이미지로 이미지 시트를 만들고, 말에 줄거리가 있으면 스토리보드까지 만들어요."}</span>
          <button type="button" className="btn-ghost ml-auto text-xs" onClick={() => setPicked(null)} disabled={sheetBusy}>빼기</button>
        </div>
      )}
      <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); void go(); }}>
        {to === "marketing" && (
          <>
            <input ref={fileRef} type="file" accept={ACCEPT} className="hidden" onChange={(e) => void pickImage(e.target.files?.[0])} />
            <button type="button" className="btn-ghost px-3 py-2 text-sm" onClick={() => fileRef.current?.click()} disabled={sheetBusy || !!chip?.disabled} aria-label="캐릭터 이미지 올리기" title="캐릭터 이미지 올리기 (PNG·JPEG·WebP)">이미지</button>
          </>
        )}
        <textarea value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} rows={2}
          aria-label={`${label} AI에게 보낼 말`}
          placeholder={roomName ? `이 사건 방에서 ${label} AI에게 말 걸기 (@부서로도 고를 수 있어요)` : `${label} AI에게 물어보세요 (@부서로도 고를 수 있어요)`}
          className="field min-h-[44px] flex-1 resize-none text-sm" />
        <button type="submit" className="btn-primary px-3 py-2 text-sm" disabled={(!text.trim() && !(to === "marketing" && picked)) || sheetBusy || talk.busy(to, room) || !!chip?.disabled}>{sheetBusy ? "시트 만드는 중…" : "보내기"}</button>
      </form>
      {warn && <p role="alert" className="text-xs text-amber-800">{warn}</p>}
      {to === "dev" && <p className="text-[11px] text-ink-700">{DEV_NOTE}</p>}
      <p className="text-[11px] text-ink-500">
        {roomName ? <>이 방에서 건 말은 {roomName} 방에만 보이고, 회사 사람 모두가 볼 수 있어요. </> : <>단톡방 대화는 회사 사람 모두에게 보여요. </>}
        대화 기록과 AI 오피스는 같은 방을 봐요. 1:1 대화는 이 브라우저에만 목록이 남아요(서버 기록은 팀이 같이 써요). 하루 상한은 같이 세요.
        {where === "office" && (
          <>{" "}<Link href={`/agents/chat?room=${encodeURIComponent(room)}`} className="font-medium text-brand-700 underline-offset-2 hover:underline">대화 기록에서 보기</Link></>
        )}
      </p>
    </div>
  );
}
