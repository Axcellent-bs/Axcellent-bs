import { useEffect, useState, type FormEvent } from "react";
import { ErrorBox, Panel, Waiting } from "@/components/finance/bits";
import { useSkill } from "@/components/data/useSkill";
import { dataAi, type InstagramMemo as Memo, type InstagramMemoKind, type InstagramMemoListOutput } from "@/lib/data";

// 트렌드 1 감지 "인스타" 칩의 인스타 트렌드 메모 (instagram_memo_list · instagram_memo_add). 출처는 늘 "수동 입력".
// - 앱 화면 캡처를 사람이 정리해 주 1회 적는다. 목록은 서버 값 그대로 (메모는 사람이 쓴 글이라 텍스트로만)
// - 입력 줄은 서버가 can_add = true를 줄 때만 보인다. 아니면 add_reason 한 줄
// - 추가는 누를 때마다 서버로 보낸다(묶지 않음). 거절이면 서버 이유를 보여 준다

const KINDS: { key: InstagramMemoKind; label: string }[] = [
  { key: "audio", label: "오디오" },
  { key: "format", label: "형식" },
  { key: "hashtag", label: "해시태그" },
  { key: "other", label: "기타" },
];
const KIND_LABEL = Object.fromEntries(KINDS.map((k) => [k.key, k.label])) as Record<InstagramMemoKind, string>;

const DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit" });
/** UTC 시각 → 한국 날짜 "MM-DD" */
function monthDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const parts = DAY.formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("month")}-${get("day")}`;
}

function MemoForm({ onAdded }: { onAdded: () => void }) {
  const [kind, setKind] = useState<InstagramMemoKind>("audio");
  const [name, setName] = useState("");
  const [memo, setMemo] = useState("");
  const [author, setAuthor] = useState("");
  const [linked, setLinked] = useState("");
  const [sending, setSending] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (sending) return;
    if (!name.trim() || !author.trim()) {
      setNote({ ok: false, text: "이름과 적은 사람을 채워 주세요." });
      return;
    }
    setSending(true);
    setNote(null);
    try {
      const r = await dataAi.instagramMemoAdd({
        kind, name: name.trim(), memo: memo.trim(), author: author.trim(), linked_trend: linked.trim() || null,
      });
      if (r.output.outcome === "added") {
        setName("");
        setMemo("");
        setLinked("");
        setNote({ ok: true, text: "메모를 적었어요." });
        onAdded();
      } else {
        setNote({ ok: false, text: r.output.reason ?? "서버가 메모를 받지 않았어요 (이유 없음)." });
      }
    } catch (err) {
      setNote({ ok: false, text: (err as Error).message });
    } finally {
      setSending(false);
    }
  };

  return (
    <form onSubmit={submit} aria-label="인스타 트렌드 메모 적기" className="space-y-1.5">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-ink-500">
          종류
          <select className="field mt-0.5 block" value={kind} onChange={(e) => setKind(e.target.value as InstagramMemoKind)}>
            {KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
          </select>
        </label>
        <label className="min-w-[8rem] flex-1 text-xs text-ink-500">
          이름
          <input className="field mt-0.5 block w-full" value={name} maxLength={60} required onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="min-w-[12rem] flex-[2] text-xs text-ink-500">
          메모
          <input className="field mt-0.5 block w-full" value={memo} maxLength={200} onChange={(e) => setMemo(e.target.value)} />
        </label>
        <label className="w-28 text-xs text-ink-500">
          적은 사람
          <input className="field mt-0.5 block w-full" value={author} maxLength={40} required onChange={(e) => setAuthor(e.target.value)} />
        </label>
        <label className="min-w-[8rem] flex-1 text-xs text-ink-500">
          연결할 유행 (선택)
          <input className="field mt-0.5 block w-full" value={linked} maxLength={60} onChange={(e) => setLinked(e.target.value)} />
        </label>
        <button type="submit" className="btn-primary" disabled={sending}>{sending ? "적는 중…" : "추가"}</button>
      </div>
      {note && <p role="status" className={`text-xs ${note.ok ? "text-emerald-700" : "text-red-700"}`}>{note.text}</p>}
    </form>
  );
}

function MemoTable({ items }: { items: Memo[] }) {
  if (items.length === 0) return <p className="text-sm text-ink-500">아직 적은 메모가 없어요.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-ink-100 text-left text-xs text-ink-500">
            <th scope="col" className="whitespace-nowrap px-1 py-1.5 font-medium">종류</th>
            <th scope="col" className="whitespace-nowrap px-1 py-1.5 font-medium">이름</th>
            <th scope="col" className="whitespace-nowrap px-1 py-1.5 font-medium">메모</th>
            <th scope="col" className="whitespace-nowrap px-1 py-1.5 font-medium">적은 사람</th>
            <th scope="col" className="whitespace-nowrap px-1 py-1.5 font-medium">연결</th>
          </tr>
        </thead>
        <tbody>
          {items.map((m) => (
            <tr key={m.memo_id} className="border-b border-ink-100 align-top">
              <td className="whitespace-nowrap px-1 py-2 text-ink-700">{KIND_LABEL[m.kind] ?? m.kind}</td>
              <td className="min-w-[8rem] break-words px-1 py-2 font-semibold text-ink-900">{m.name}</td>
              <td className="min-w-[12rem] break-words px-1 py-2 text-ink-700">{m.memo || "-"}</td>
              <td className="whitespace-nowrap px-1 py-2 text-ink-700">{m.author}<small className="ml-1 text-xs text-ink-500">· {monthDay(m.created_at)}</small></td>
              <td className="min-w-[6rem] px-1 py-2 text-ink-700">{m.linked_trend || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function InstagramMemo() {
  const res = useSkill(() => dataAi.instagramMemoList());
  const s = res.state;
  // 추가 뒤 목록을 다시 받는 동안에도 입력 줄(과 결과 문장)이 사라지지 않게 마지막 응답을 들고 있는다
  const [last, setLast] = useState<InstagramMemoListOutput | null>(null);
  useEffect(() => {
    if (s.kind === "ok") setLast(s.out);
  }, [s]);
  const out = s.kind === "ok" ? s.out : s.kind === "loading" ? last : null;
  return (
    <Panel id="data-trend-ig-memo" title="인스타 트렌드 메모" hint="앱 화면 캡처를 정리해 주 1회 적어요"
      tools={<span className="text-xs text-ink-500">수동 입력</span>}>
      {out ? (
        <div className="space-y-3">
          {out.can_add ? <MemoForm onAdded={res.reload} />
            : <p className="text-xs text-ink-500">{out.add_reason ?? "지금은 메모를 적을 수 없어요 (서버가 이유를 주지 않았어요)."}</p>}
          {s.kind === "loading" && <p className="text-xs text-ink-500">목록을 다시 받는 중…</p>}
          <MemoTable items={out.items} />
        </div>
      ) : s.kind === "failed" ? <ErrorBox message={s.reason} onRetry={res.reload} />
        : <Waiting what="메모를 받는 중…" />}
    </Panel>
  );
}
