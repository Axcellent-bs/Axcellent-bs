import { useEffect, useRef } from "react";
import type { Conversation, Message } from "@/lib/types";

function Bubble({ m }: { m: Message }) {
  const customer = m.role === "customer";
  const time = new Date(m.created_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
  return (
    <div className={`flex ${customer ? "justify-start" : "justify-end"}`}>
      <div className={`flex max-w-[78%] flex-col gap-1 ${customer ? "" : "items-end"}`}>
        <div className={`whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${customer ? "rounded-tl-sm border border-ink-100 bg-surface" : "rounded-tr-sm bg-brand-600 text-white"}`}>{m.text}</div>
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
