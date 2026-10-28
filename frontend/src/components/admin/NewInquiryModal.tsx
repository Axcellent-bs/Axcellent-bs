import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Me } from "@/lib/types";

const PRESETS = [
  { label: "책이 찢어져 왔어요", subject: "표지가 찢어진 채로 왔어요", message: "어제 받은 책 표지가 찢어져 있어요. 사진도 찍어 뒀습니다. 환불이나 교환 받을 수 있을까요?" },
  { label: "이중 결제", subject: "카드가 두 번 결제됐어요", message: "주문은 한 번만 했는데 카드 결제 문자가 두 번 왔어요!! 확인 부탁드립니다. 연락처는 010-1234-5678 입니다." },
  { label: "단순변심 반품", subject: "마음이 바뀌어 반품하고 싶어요", message: "받은 지 3일 됐는데 단순 변심으로 반품하고 싶어요. 배송비는 얼마인가요?" },
  { label: "강한 항의", subject: "소비자원에 신고할 겁니다", message: "환불이 계속 거절돼서 어이없네요!! 소비자원에 신고하겠습니다." },
];

export function NewInquiryModal({ onClose, onCreate }: {
  onClose: () => void; onCreate: (b: { user_id: number; subject: string; message: string; order_id?: number | null }) => Promise<void>;
}) {
  const [users, setUsers] = useState<Me[]>([]);
  const [userId, setUserId] = useState<number | null>(null);
  const [orders, setOrders] = useState<{ id: number; order_no: string; status_ko: string; title: string }[]>([]);
  const [orderId, setOrderId] = useState<number | null>(null);
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.users().then((u) => { setUsers(u); setUserId(u[0]?.id ?? null); }).catch(() => setError("고객 목록을 불러오지 못했습니다.")); }, []);
  useEffect(() => { setOrderId(null); if (userId) api.admin.orders(userId).then(setOrders).catch(() => setOrders([])); }, [userId]);

  async function submit() {
    if (!userId) return;
    setBusy(true);
    setError(null);
    try { await onCreate({ user_id: userId, subject, message, order_id: orderId }); } catch (e) { setError((e as Error).message); setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose} role="presentation">
      <div role="dialog" aria-modal="true" aria-label="새 문의" className="w-full max-w-lg rounded-2xl bg-surface p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-center justify-between"><h2 className="text-base font-semibold">새 문의 (고객 시뮬레이션)</h2><button className="btn-quiet px-2 py-1" onClick={onClose} aria-label="닫기">✕</button></div>
        <p className="mb-4 text-xs text-ink-500">고객이 보낸 문의를 입력하면 분류·정책 검색·영수증 대조·초안 생성·검증까지 바로 실행됩니다.</p>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {PRESETS.map((p) => <button key={p.label} className="chip border border-ink-300 bg-surface py-1 text-ink-700 hover:bg-ink-100" onClick={() => { setSubject(p.subject); setMessage(p.message); }}>{p.label}</button>)}
        </div>
        <div className="space-y-2.5">
          <div className="grid grid-cols-2 gap-2">
            <select className="field" value={userId ?? ""} onChange={(e) => setUserId(Number(e.target.value))} aria-label="고객">{users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
            <select className="field" value={orderId ?? ""} onChange={(e) => setOrderId(e.target.value ? Number(e.target.value) : null)} aria-label="연결할 주문">
              <option value="">주문 연결 안 함</option>{orders.map((o) => <option key={o.id} value={o.id}>{o.order_no} · {o.status_ko} · {o.title.slice(0, 12)}</option>)}
            </select>
          </div>
          <input className="field" placeholder="제목" value={subject} onChange={(e) => setSubject(e.target.value)} />
          <textarea className="field h-32 resize-none" placeholder="고객 메시지" value={message} onChange={(e) => setMessage(e.target.value)} />
        </div>
        {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
        <div className="mt-4 flex justify-end gap-2"><button className="btn-ghost" onClick={onClose}>취소</button><button className="btn-primary" disabled={!userId || !subject.trim() || !message.trim() || busy} onClick={submit}>{busy ? "분석 중…" : "문의 접수"}</button></div>
      </div>
    </div>
  );
}
