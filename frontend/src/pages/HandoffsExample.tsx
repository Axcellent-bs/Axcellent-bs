import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { customerAccounts, useSession } from "@/demo/session";
import { ago, shortDateTime } from "@/lib/format";
import { RESULT_MAX } from "@/lib/handoffs";
import { useMediaQuery } from "@/lib/useMediaQuery";

// 받은 요청 예시 모드. 서버 대신 화면 안 예시로 흐름을 보여 준다(서버 데이터는 건드리지 않는다).
// - 일을 어느 AI에 맡길지는 총괄이 정한다. 사람에게 온 요청은 부서로 나누지 않고 회사 전체가 한 목록으로 본다
//   (소규모 회사 기준. 회원 부서는 표시·조직도용이다)
// - 누구든 "내가 하기"로 맡는다. 위험한 건(확인 권한 필요)은 확인 권한이 있는 사람만 승인한다
// - 2시간 동안 아무도 안 맡으면 확인 권한자에게 알린 것으로 보여 준다
// 처리 상태는 이 화면에서만 바뀐다(새로고침하면 처음으로).

type Kind = "사람 확인 요청" | "서비스 꺼짐 대신";
type ExStatus = "waiting" | "claimed" | "done" | "rejected";
type ExItem = {
  id: string;
  from: string;
  /** 요청 종류 */
  category: string;
  kind: Kind;
  title: string;
  reason: string;
  risky?: boolean;
  status: ExStatus;
  claimedBy?: string;
  result?: string;
  minutesAgo: number;
  closedMinutesAgo?: number;
  href: string;
  hrefLabel: string;
};

const ITEMS: ExItem[] = [
  { id: "ex-1", from: "개발·배포", category: "PR 교차검증", kind: "사람 확인 요청", title: "PR #12 교차검증 사람 확인",
    reason: "진행 중 사건과 구성요소 겹침", status: "waiting", minutesAgo: 25, href: "/dev/work/prs", hrefLabel: "작업·PR" },
  { id: "ex-2", from: "고객지원", category: "환불 승인", kind: "사람 확인 요청", title: "환불 120,000원 승인",
    reason: "주문 2310 파손 · 자동 환불 한도 50,000원 초과", risky: true, status: "waiting", minutesAgo: 40, href: "/claims", hrefLabel: "클레임·보상" },
  { id: "ex-3", from: "고객지원", category: "고객 문의 답변", kind: "서비스 꺼짐 대신", title: "고객 문의 직접 답변",
    reason: "고객지원 서비스 꺼짐 · 배송 지연 문의 3건", status: "waiting", minutesAgo: 15, href: "/inquiry", hrefLabel: "고객 문의" },
  { id: "ex-4", from: "마케팅", category: "쿠폰 발급", kind: "사람 확인 요청", title: "쿠폰 500장 발급 확인",
    reason: "발급 수량 평소의 5배 · 10% 할인", status: "waiting", minutesAgo: 70, href: "/marketing", hrefLabel: "공지·쿠폰" },
  { id: "ex-5", from: "인프라", category: "롤백 승인", kind: "사람 확인 요청", title: "결제 서버 롤백 승인",
    reason: "배포 뒤 결제 오류율 4.2% · 직전 버전으로 되돌림", risky: true, status: "waiting", minutesAgo: 180, href: "/infra", hrefLabel: "장애 대응" },
  { id: "ex-6", from: "데이터분석", category: "지표 이상 확인", kind: "사람 확인 요청", title: "주간 매출 지표 급감 확인",
    reason: "지난주 대비 매출 -38% · 집계 누락 의심", status: "waiting", minutesAgo: 20, href: "/data", hrefLabel: "데이터 분석", },
  { id: "ex-7", from: "인사", category: "법인카드 한도", kind: "사람 확인 요청", title: "법인카드 한도 변경 확인",
    reason: "신규 입사자 2명 · 월 한도 300만 원 요청", status: "claimed", claimedBy: "김서연", minutesAgo: 50, href: "/org", hrefLabel: "조직 변동" },
  { id: "ex-8", from: "개발·배포", category: "배포 후 점검", kind: "사람 확인 요청", title: "배포 후 점검 결과 확인",
    reason: "주문 API 배포 · 자동 점검 1건 경고", status: "done", claimedBy: "박지훈", result: "경고는 로그 설정 문제 · 서비스 영향 없음",
    minutesAgo: 300, closedMinutesAgo: 260, href: "/dev/work/ops", hrefLabel: "운영·배포" },
];

const ESCALATE_MIN = 120;

// ---------------------------------------------------------------- 상태 점 (초록 깜빡=진행 중, 초록=사람 필요·문제 없음, 주황=사람 필요·문제 있음, 빨강=반려, 회색=끝남)
type Tone = "live" | "green" | "amber" | "red" | "gray";
const DOT: Record<Tone, string> = {
  live: "bg-emerald-700 animate-pulse",
  green: "bg-emerald-700",
  amber: "bg-amber-700",
  red: "bg-red-700",
  gray: "bg-ink-300",
};
const Dot = ({ tone }: { tone: Tone }) => <span className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${DOT[tone]}`} aria-hidden="true" />;

type View = ExItem & { escalated: boolean; tone: Tone; label: string };

const STATUS_TABS: { value: ExStatus | "all"; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "waiting", label: "대기" },
  { value: "claimed", label: "처리 중" },
  { value: "done", label: "끝남" },
  { value: "rejected", label: "반려" },
];
// 목록 순서: 대기(2시간 넘음·확인 권한 필요가 먼저) → 처리 중 → 끝남 → 반려
const STATUS_ORDER: Record<ExStatus, number> = { waiting: 0, claimed: 1, done: 2, rejected: 3 };

const Chip = ({ className, children }: { className: string; children: ReactNode }) => <span className={`chip ${className}`}>{children}</span>;
const KindChip = ({ kind }: { kind: Kind }) => (
  <Chip className={kind === "서비스 꺼짐 대신" ? "bg-ink-100 text-ink-700" : "bg-brand-50 text-brand-700"}>{kind}</Chip>
);
const RiskyChip = () => <Chip className="bg-amber-50 text-amber-800">확인 권한 필요</Chip>;
const EscalatedChip = () => <Chip className="bg-amber-50 text-amber-800">2시간 넘음 · 확인 권한자에게 알림</Chip>;

const minAgo = (m: number, now: number) => new Date(now - m * 60_000).toISOString();

export default function HandoffsExample({ toggle }: { toggle: ReactNode }) {
  const { account } = useSession();
  const me = account?.name ?? "나";
  const approver = !!account?.approver;

  const [now] = useState(() => Date.now());
  const [items, setItems] = useState<ExItem[]>(ITEMS);
  const [scope, setScope] = useState<"all" | "mine">("all");
  const [service, setService] = useState("");
  const [status, setStatus] = useState<ExStatus | "all">("all");
  const [selectedId, setSelectedId] = useState<string | null>(ITEMS[0].id);
  // 넓은 화면은 상세를 오른쪽에, 좁은 화면은 누른 항목 바로 밑에 펼친다(다시 누르면 접힘)
  const wide = useMediaQuery("(min-width: 1280px)");
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  const views: View[] = useMemo(() => items.map((it) => {
    const escalated = it.status === "waiting" && it.minutesAgo >= ESCALATE_MIN;
    const tone: Tone =
      it.status === "claimed" ? "live"
      : it.status === "done" ? "gray"
      : it.status === "rejected" ? "red"
      : it.risky || escalated ? "amber" : "green";
    const label =
      it.status === "claimed" ? `${it.claimedBy} 처리 중`
      : it.status === "done" ? "끝남"
      : it.status === "rejected" ? "반려"
      : "대기";
    return { ...it, escalated, tone, label };
  }), [items]);

  const services = [...new Set(ITEMS.map((i) => i.from))];
  const scoped = views.filter((v) => (scope === "all" || v.claimedBy === me) && (!service || v.from === service));
  const count = (s: ExStatus | "all") => scoped.filter((v) => s === "all" || v.status === s).length;
  const urgency = (v: View) => (v.escalated ? 0 : v.risky ? 1 : 2);
  const shown = scoped
    .filter((v) => status === "all" || v.status === status)
    .sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || urgency(a) - urgency(b) || b.minutesAgo - a.minutesAgo);
  const selected = shown.find((v) => v.id === selectedId) ?? (wide ? shown[0] : undefined);

  const patch = (id: string, p: Partial<ExItem>) => setItems((xs) => xs.map((x) => (x.id === id ? { ...x, ...p } : x)));

  const detail = selected && (
    <Detail key={selected.id} v={selected} now={now} me={me} approver={approver}
      onClaim={() => { patch(selected.id, { status: "claimed", claimedBy: me }); setToast("내가 맡았어요. 다른 사람들에게 처리 중으로 보여요."); }}
      onRelease={() => { patch(selected.id, { status: "waiting", claimedBy: undefined }); setToast("놓았어요. 다시 목록에 대기로 보여요."); }}
      onClose={(st, result) => {
        patch(selected.id, { status: st, result, closedMinutesAgo: 0 });
        setToast(st === "done" ? (selected.risky ? "승인하고 끝냈어요." : "끝냈어요. 보낸 서비스에 결과가 전해져요.") : "반려했어요. 보낸 서비스에 이유가 전해져요.");
      }} />
  );

  return (
    <div className="space-y-6 px-8 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold">받은 요청</h1>
          <Chip className="bg-amber-50 text-amber-800">예시 데이터</Chip>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {account && (
            <span className="text-ink-500">
              {me}{account.dept && ` · ${account.dept}`}{approver && <span className="chip ml-1 bg-brand-50 text-brand-700">확인 권한</span>}
            </span>
          )}
          {toggle}
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        {([["all", "전체"], ["mine", "내가 맡은 것"]] as const).map(([v, label]) => (
          <button key={v} type="button" onClick={() => setScope(v)} aria-pressed={scope === v}
            className={`rounded-full px-3 py-1.5 text-xs font-medium ${scope === v ? "bg-accent text-white" : "bg-ink-50 text-ink-700 hover:bg-ink-100"}`}>{label}</button>
        ))}
        <select className="field w-auto py-1.5 text-xs" aria-label="보낸 서비스" value={service} onChange={(e) => setService(e.target.value)}>
          <option value="">모든 서비스</option>
          {services.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <div className="ml-auto flex flex-wrap gap-1">
          {STATUS_TABS.map((t) => (
            <button key={t.value} type="button" onClick={() => setStatus(t.value)} aria-pressed={status === t.value}
              className={`chip py-1 ${status === t.value ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>{t.label} {count(t.value)}</button>
          ))}
        </div>
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div className="space-y-6">
          <section className="card min-w-0 overflow-hidden" aria-label="받은 요청 목록">
            {shown.length === 0 ? (
              <p className="px-5 py-8 text-center text-sm text-ink-500">고른 조건에 맞는 요청이 없어요</p>
            ) : (
              <ul className="divide-y divide-ink-100">
                {shown.map((v) => (
                  <li key={v.id}>
                    <button type="button" onClick={() => setSelectedId(!wide && v.id === selected?.id ? null : v.id)} aria-expanded={wide ? undefined : v.id === selected?.id} aria-selected={v.id === selected?.id}
                      className={`w-full border-l-2 px-4 py-3 text-left transition ${v.id === selected?.id ? "border-brand-600 bg-brand-50" : "border-transparent hover:bg-app"}`}>
                      <div className="flex items-center gap-2 text-sm">
                        <Dot tone={v.tone} />
                        <span className="font-medium">{v.title}</span>
                        <span className="ml-auto whitespace-nowrap text-[11px] text-ink-500" title={shortDateTime(minAgo(v.minutesAgo, now))}>{ago(minAgo(v.minutesAgo, now))}</span>
                      </div>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1 pl-[18px] text-xs">
                        <span className="text-ink-700">{v.from}</span>
                        <KindChip kind={v.kind} />
                        {v.risky && <RiskyChip />}
                        {v.escalated && <EscalatedChip />}
                        <span className={`chip ${v.status === "claimed" ? "bg-emerald-50 text-emerald-700" : v.status === "rejected" ? "bg-red-50 text-red-700" : "bg-ink-100 text-ink-700"}`}>{v.label}</span>
                      </div>
                    </button>
                    {!wide && v.id === selected?.id && <div className="row-expand border-t border-ink-100 bg-app p-3">{detail}</div>}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        {wide && detail}
      </div>

      {toast && (
        <div role="status" className="card fixed bottom-6 right-6 z-50 border border-ink-100 px-4 py-3 text-sm shadow-lg">{toast}</div>
      )}
    </div>
  );
}

function Detail({ v, now, me, approver, onClaim, onRelease, onClose }: {
  v: View; now: number; me: string; approver: boolean;
  onClaim: () => void; onRelease: () => void; onClose: (st: "done" | "rejected", result: string) => void;
}) {
  const [result, setResult] = useState("");
  const mine = v.status === "claimed" && v.claimedBy === me;
  const approvers = customerAccounts().filter((a) => a.approver).map((a) => a.name);
  const canFinish = result.trim().length > 0 && (!v.risky || approver);

  return (
    <section className="card space-y-5 px-5 py-5" aria-label="요청 상세">
      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Dot tone={v.tone} />
          <span className="text-sm font-medium">{v.label}</span>
          <KindChip kind={v.kind} />
          {v.risky && <RiskyChip />}
        </div>
        <h2 className="card-title">{v.title}</h2>
      </header>

      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div><dt className="text-xs text-ink-500">보낸 서비스</dt><dd className="mt-0.5 font-semibold">{v.from}</dd></div>
        <div><dt className="text-xs text-ink-500">종류</dt><dd className="mt-0.5">{v.category}</dd></div>
        <div><dt className="text-xs text-ink-500">받은 시각</dt><dd className="mt-0.5">{shortDateTime(minAgo(v.minutesAgo, now))} <span className="text-ink-500">({ago(minAgo(v.minutesAgo, now))})</span></dd></div>
        {v.claimedBy && <div><dt className="text-xs text-ink-500">맡은 사람</dt><dd className="mt-0.5 font-semibold">{v.claimedBy}</dd></div>}
        {v.risky && <div><dt className="text-xs text-ink-500">확인 권한자</dt><dd className="mt-0.5">{approvers.length ? approvers.join(", ") : "없음"}</dd></div>}
      </dl>

      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-ink-700">이유</h3>
        <p className="text-sm text-ink-900">{v.reason}</p>
      </div>

      {v.escalated && <EscalatedChip />}

      <Link to={v.href} className="btn-ghost inline-flex px-3 py-1.5 text-xs">{v.hrefLabel} 바로가기 →</Link>

      {v.status === "waiting" && (
        <div className="border-t border-ink-100 pt-4">
          <button type="button" className="btn-primary" onClick={onClaim}>내가 하기</button>
        </div>
      )}

      {v.status === "claimed" && !mine && (
        <p className="border-t border-ink-100 pt-4 text-sm text-ink-700"><Dot tone="live" /> <span className="ml-1">{v.claimedBy} 처리 중</span></p>
      )}

      {mine && (
        <div className="space-y-2 border-t border-ink-100 pt-4">
          <label htmlFor="ex-result" className="text-sm font-semibold text-ink-700">처리 결과</label>
          <textarea id="ex-result" className="field min-h-[96px]" maxLength={RESULT_MAX} value={result} onChange={(e) => setResult(e.target.value)}
            placeholder="한 일이나 반려 이유" />
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn-primary disabled:opacity-50" disabled={!canFinish} onClick={() => onClose("done", result.trim())}
              title={v.risky && !approver ? "확인 권한이 있는 사람만 승인할 수 있어요" : undefined}>
              {v.risky ? (approver ? "승인하고 끝내기" : "확인 권한자만") : "끝내기"}
            </button>
            <button type="button" className="btn-ghost disabled:opacity-50" disabled={!result.trim()} onClick={() => onClose("rejected", result.trim())}>반려</button>
            <button type="button" className="btn-quiet" onClick={onRelease}>놓기</button>
            <span className="ml-auto text-xs text-ink-500">{result.length}/{RESULT_MAX}</span>
          </div>
        </div>
      )}

      {(v.status === "done" || v.status === "rejected") && (
        <div className="space-y-1 border-t border-ink-100 pt-4">
          <h3 className="text-sm font-semibold text-ink-700">{v.status === "done" ? "처리 결과" : "반려 이유"}</h3>
          <p className="whitespace-pre-wrap text-sm text-ink-900">{v.result}</p>
          <p className="text-xs text-ink-500">
            {v.claimedBy}{v.closedMinutesAgo !== undefined && ` · ${ago(minAgo(v.closedMinutesAgo, now))}`}
          </p>
        </div>
      )}
    </section>
  );
}
