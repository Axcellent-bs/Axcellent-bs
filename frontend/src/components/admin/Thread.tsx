import { useEffect, useRef } from "react";
import { LANGUAGE_KO } from "@/lib/labels";
import { useOriginal } from "@/lib/useOriginal";
import type { Conversation, Message } from "@/lib/types";

const ABUSE_LABEL = { profanity: "비속어 가림", insult: "모욕 표현 가림", threat: "위협 표현 있음" } as const;

/** 한국어가 아닌 고객 메시지 밑에 한국어 번역과 핵심 요청을 보여 준다 (AI 번역이라 원문과 함께 본다) */
function Translation({ m }: { m: Message }) {
  const c = m.analysis?.classification;
  const points = c?.request_points_ko ?? [];
  if (!c) return null;
  if (!c.message_ko && !points.length) {
    // 한국어가 아닌데 번역이 비었다: 분류 AI가 실패해 규칙 분류로 대체됐을 수 있다. 조용히 숨기지 않고 알린다
    if (!c.language || c.language === "ko") return null;
    return (
      <div className="rounded-xl border border-ink-100 bg-surface-2 px-3 py-2 text-xs leading-relaxed text-ink-500" role="note" aria-label="한국어 번역 없음">
        한국어 번역을 만들지 못했습니다. 원문 {LANGUAGE_KO[c.language] ?? "기타"} 그대로 확인하세요. AI 연결이 불안정했을 수 있습니다 (AI 처리 내력에서 대체 경로 여부를 볼 수 있습니다).
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-ink-700" aria-label="한국어 번역">
      <div className="mb-1 flex items-center gap-1.5 font-medium text-amber-800">
        <span>한국어 번역</span><span className="font-normal text-ink-500">AI 번역 · 원문 {LANGUAGE_KO[c.language ?? "other"] ?? "기타"}</span>
      </div>
      {c.message_ko && <p className="whitespace-pre-wrap">{c.message_ko}</p>}
      {points.length > 0 && (
        <ul className="mt-1.5 list-disc space-y-0.5 pl-4 font-medium text-ink-900">
          {points.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
    </div>
  );
}

function Bubble({ m, inquiryId }: { m: Message; inquiryId: number }) {
  const customer = m.role === "customer";
  const orig = useOriginal(inquiryId, m.id);
  const safe = m.softened_text ?? m.display_text ?? null; // AI 순화문이 있으면 그것, 없으면 규칙으로 가린 글
  const masked = customer && safe != null && m.abuse_level !== undefined && m.abuse_level !== "none";
  const body = masked ? (orig.show ? orig.text : safe) : m.text;
  const time = new Date(m.created_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
  return (
    <div className={`flex ${customer ? "justify-start" : "justify-end"}`}>
      <div className={`flex max-w-[78%] flex-col gap-1 ${customer ? "" : "items-end"}`}>
        <div className={`whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${customer ? "rounded-tl-sm border border-ink-100 bg-surface" : "rounded-tr-sm bg-brand-600 text-white"}`}>{body}</div>
        {customer && <Translation m={m} />}
        {masked && (
          <div className="flex items-center gap-2 text-[11px]">
            <span className={`rounded-full px-2 py-0.5 font-medium ${m.abuse_level === "threat" ? "bg-red-50 text-red-800" : "bg-amber-50 text-amber-800"}`}>
              {ABUSE_LABEL[m.abuse_level as keyof typeof ABUSE_LABEL]}
            </span>
            {m.softened_text != null && <span className="text-ink-500">AI가 순화함</span>}
            <button type="button" disabled={orig.opening} onClick={orig.toggle} className="text-ink-500 underline">
              {orig.show ? "가려서 보기" : orig.opening ? "여는 중…" : "원문 보기"}
            </button>
            {orig.failed && <span className="text-red-700">원문을 열지 못했어요</span>}
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
      <div className="space-y-4">{conv.messages.map((m) => <Bubble key={m.id} m={m} inquiryId={conv.id} />)}</div>
      <div ref={end} />
    </div>
  );
}
