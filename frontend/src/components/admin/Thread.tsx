import { useEffect, useRef, useState } from "react";
import type { Conversation, Message } from "@/lib/types";

const ABUSE_LABEL = { profanity: "비속어 가림", insult: "모욕 표현 가림", threat: "위협 표현 있음" } as const;

function Bubble({ m }: { m: Message }) {
  const customer = m.role === "customer";
  const [showOriginal, setShowOriginal] = useState(false);
  const safe = m.softened_text ?? m.display_text ?? null; // AI 순화문이 있으면 그것, 없으면 규칙으로 가린 글
  const masked = customer && safe != null && m.abuse_level !== undefined && m.abuse_level !== "none";
  const body = masked && !showOriginal ? safe : m.text;
  const time = new Date(m.created_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
  return (
    <div className={`flex ${customer ? "justify-start" : "justify-end"}`}>
      <div className={`flex max-w-[78%] flex-col gap-1 ${customer ? "" : "items-end"}`}>
        <div className={`whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${customer ? "rounded-tl-sm border border-ink-100 bg-surface" : "rounded-tr-sm bg-brand-600 text-white"}`}>{body}</div>
        {masked && (
          <div className="flex items-center gap-2 text-[11px]">
            <span className={`rounded-full px-2 py-0.5 font-medium ${m.abuse_level === "threat" ? "bg-red-50 text-red-800" : "bg-amber-50 text-amber-800"}`}>
              {ABUSE_LABEL[m.abuse_level as keyof typeof ABUSE_LABEL]}
            </span>
            {m.softened_text != null && <span className="text-ink-500">AI가 순화함</span>}
            <button type="button" onClick={() => setShowOriginal((v) => !v)} className="text-ink-500 underline">
              {showOriginal ? "가려서 보기" : "원문 보기"}
            </button>
          </div>
        )}
        <span className={`text-[10px] text-ink-500 ${customer ? "" : "text-right"}`}>{customer ? "고객" : "상담원"} · {time}</span>
      </div>
    </div>
  );
}

export function Thread({ conv }: { conv: Conversation }) {
  const end = useRef<HTMLDivElement>(null);
  // 블록으로 감싼다: 최신 브라우저의 scrollIntoView는 Promise를 돌려줘서, 그대로 돌려주면 React가 정리 함수로 부르다 깨진다
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [conv.messages.length, conv.id]);
  return (
    <div className="scroll-thin flex-1 overflow-y-auto px-6 py-5">
      <div className="space-y-4">{conv.messages.map((m) => <Bubble key={m.id} m={m} />)}</div>
      <div ref={end} />
    </div>
  );
}
