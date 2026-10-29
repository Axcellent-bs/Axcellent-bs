import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { DeptPicker } from "@/demo/DeptPicker";
import { customerAccounts, useSession } from "@/demo/session";
import { ago, shortDateTime } from "@/lib/format";
import { RESULT_MAX } from "@/lib/handoffs";

// 받은 요청 예시 모드. 서버 대신 화면 안 예시로 흐름을 보여 준다(서버 데이터는 건드리지 않는다).
// - 요청은 사람이 아니라 부서(팀)로 간다. 팀이 정해지지 않은 종류는 AI가 팀을 제안하고, 사람이 고르면 "이 종류 → 팀" 규칙으로 저장한다
// - 팀 안에서는 같이 보는 목록이고, 누구든 "내가 하기"로 맡는다. 위험한 건(확인 권한 필요)은 확인 권한이 있는 사람만 승인한다
// - 2시간 동안 아무도 안 맡으면 그 팀 확인 권한자에게 알린 것으로 보여 준다
// 처리 상태는 이 화면에서만 바뀌고(새로고침하면 처음으로), 보내기 규칙만 브라우저에 기억한다.

type Kind = "사람 확인 요청" | "서비스 꺼짐 대신";
type ExStatus = "waiting" | "claimed" | "done" | "rejected";
type ExItem = {
  id: string;
  from: string;
  /** 받을 부서. null이면 아직 팀이 없다(보내기 규칙이나 AI 제안으로 정한다) */
  dept: string | null;
  /** 요청 종류. 보내기 규칙의 열쇠다 */
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
  suggest?: { dept: string; reasons: string[] };
};

const ITEMS: ExItem[] = [
  { id: "ex-1", from: "개발·배포", dept: "개발팀", category: "PR 교차검증", kind: "사람 확인 요청", title: "PR #12 교차검증 사람 확인",
    reason: "진행 중 사건과 구성요소 겹침", status: "waiting", minutesAgo: 25, href: "/dev#/work/prs", hrefLabel: "작업·PR" },
  { id: "ex-2", from: "고객지원", dept: "재무팀", category: "환불 승인", kind: "사람 확인 요청", title: "환불 120,000원 승인",
    reason: "주문 2310 파손 · 자동 환불 한도 50,000원 초과", risky: true, status: "waiting", minutesAgo: 40, href: "/claims", hrefLabel: "클레임·보상" },
  { id: "ex-3", from: "고객지원", dept: "고객지원팀", category: "고객 문의 답변", kind: "서비스 꺼짐 대신", title: "고객 문의 직접 답변",
    reason: "고객지원 서비스 꺼짐 · 배송 지연 문의 3건", status: "waiting", minutesAgo: 15, href: "/inquiry", hrefLabel: "고객 문의" },
  { id: "ex-4", from: "마케팅", dept: "마케팅팀", category: "쿠폰 발급", kind: "사람 확인 요청", title: "쿠폰 500장 발급 확인",
    reason: "발급 수량 평소의 5배 · 10% 할인", status: "waiting", minutesAgo: 70, href: "/marketing", hrefLabel: "공지·쿠폰" },
  { id: "ex-5", from: "인프라", dept: "인프라팀", category: "롤백 승인", kind: "사람 확인 요청", title: "결제 서버 롤백 승인",
    reason: "배포 뒤 결제 오류율 4.2% · 직전 버전으로 되돌림", risky: true, status: "waiting", minutesAgo: 180, href: "/infra", hrefLabel: "장애 대응" },
  { id: "ex-6", from: "데이터분석", dept: null, category: "지표 이상 확인", kind: "사람 확인 요청", title: "주간 매출 지표 급감 확인",
    reason: "지난주 대비 매출 -38% · 집계 누락 의심", status: "waiting", minutesAgo: 20, href: "/data", hrefLabel: "데이터 분석",
    suggest: { dept: "데이터분석팀", reasons: ["지표·집계 관련 요청", "데이터분석 서비스에서 보냄", "비슷한 요청 3건을 데이터분석팀이 처리"] } },
  { id: "ex-7", from: "인사", dept: "인사팀", category: "법인카드 한도", kind: "사람 확인 요청", title: "법인카드 한도 변경 확인",
    reason: "신규 입사자 2명 · 월 한도 300만 원 요청", status: "claimed", claimedBy: "김서연", minutesAgo: 50, href: "/hr", hrefLabel: "인수인계" },
  { id: "ex-8", from: "개발·배포", dept: "개발팀", category: "배포 후 점검", kind: "사람 확인 요청", title: "배포 후 점검 결과 확인",
    reason: "주문 API 배포 · 자동 점검 1건 경고", status: "done", claimedBy: "박지훈", result: "경고는 로그 설정 문제 · 서비스 영향 없음",
    minutesAgo: 300, closedMinutesAgo: 260, href: "/dev#/work/ops", hrefLabel: "운영·배포" },
];

const ESCALATE_MIN = 120;

// ---------------------------------------------------------------- 보내기 규칙 (이 종류 → 팀)
type Rule = { category: string; dept: string };
const RULES_KEY = "axcellent-handoff-rules";
const SEED_RULES: Rule[] = [
  { category: "환불 승인", dept: "재무팀" },
  { category: "롤백 승인", dept: "인프라팀" },
];
const loadRules = (): Rule[] => {
  try {
    const raw = localStorage.getItem(RULES_KEY);
    if (raw === null) return SEED_RULES;
    const rows = JSON.parse(raw);
    return Array.isArray(rows) ? rows.filter((r) => r && typeof r.category === "string" && typeof r.dept === "string") : SEED_RULES;
  } catch {
    return SEED_RULES;
  }
};
const saveRules = (rules: Rule[]) => {
  try {
    localStorage.setItem(RULES_KEY, JSON.stringify(rules));
  } catch {
    /* 저장이 막혀도 이번 화면에서는 쓴다 */
  }
};

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

type View = ExItem & { to: string | null; byRule: boolean; escalated: boolean; tone: Tone; label: string };

const STATUS_TABS: { value: ExStatus | "all"; label: string }[] = [
  { value: "waiting", label: "대기" },
  { value: "claimed", label: "처리 중" },
  { value: "done", label: "끝남" },
  { value: "rejected", label: "반려" },
  { value: "all", label: "전체" },
];
// 목록 순서: 대기(팀 정하기·2시간 넘음·확인 권한 필요가 먼저) → 처리 중 → 끝남 → 반려
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
  const myDept = account?.dept ?? "";
  const approver = !!account?.approver;

  const [now] = useState(() => Date.now());
  const [items, setItems] = useState<ExItem[]>(ITEMS);
  const [rules, setRules] = useState<Rule[]>(loadRules);
  const [scope, setScope] = useState<"all" | "mine">("all");
  const [service, setService] = useState("");
  const [status, setStatus] = useState<ExStatus | "all">("waiting");
  const [selectedId, setSelectedId] = useState<string>(ITEMS[0].id);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  const views: View[] = useMemo(() => items.map((it) => {
    const rule = rules.find((r) => r.category === it.category);
    const to = it.dept ?? rule?.dept ?? null;
    const byRule = !!rule && rule.dept === to;
    const escalated = it.status === "waiting" && !!to && it.minutesAgo >= ESCALATE_MIN;
    const tone: Tone =
      it.status === "claimed" ? "live"
      : it.status === "done" ? "gray"
      : it.status === "rejected" ? "red"
      : !to || it.risky || escalated ? "amber" : "green";
    const label =
      it.status === "claimed" ? `${it.claimedBy} 처리 중`
      : it.status === "done" ? "끝남"
      : it.status === "rejected" ? "반려"
      : !to ? "팀 정하기" : "대기";
    return { ...it, to, byRule, escalated, tone, label };
  }), [items, rules]);

  const services = [...new Set(ITEMS.map((i) => i.from))];
  const scoped = views.filter((v) => (scope === "all" || (!!myDept && v.to === myDept)) && (!service || v.from === service));
  const count = (s: ExStatus | "all") => scoped.filter((v) => s === "all" || v.status === s).length;
  const urgency = (v: View) => (!v.to ? 0 : v.escalated ? 1 : v.risky ? 2 : 3)
  const shown = scoped
    .filter((v) => status === "all" || v.status === status)
    .sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || urgency(a) - urgency(b) || b.minutesAgo - a.minutesAgo);
  const selected = shown.find((v) => v.id === selectedId) ?? shown[0];

  const patch = (id: string, p: Partial<ExItem>) => setItems((xs) => xs.map((x) => (x.id === id ? { ...x, ...p } : x)));
  const addRule = (category: string, dept: string) => {
    const next = [...rules.filter((r) => r.category !== category), { category, dept }];
    setRules(next);
    saveRules(next);
    setToast(`${dept}에 보냈어요. 다음부터 "${category}"은(는) ${dept}로 바로 가요.`);
  };
  const removeRule = (category: string) => {
    const next = rules.filter((r) => r.category !== category);
    setRules(next);
    saveRules(next);
    setToast(`"${category}" 규칙을 지웠어요.`);
  };

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
              {me} · {myDept || "부서 없음"}{approver && <span className="chip ml-1 bg-brand-50 text-brand-700">확인 권한</span>}
            </span>
          )}
          {toggle}
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        {([["all", "전체"], ["mine", `내 부서${myDept ? ` · ${myDept}` : ""}`]] as const).map(([v, label]) => (
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
                    <button type="button" onClick={() => setSelectedId(v.id)} aria-selected={v.id === selected?.id}
                      className={`w-full border-l-2 px-4 py-3 text-left transition ${v.id === selected?.id ? "border-brand-600 bg-brand-50" : "border-transparent hover:bg-app"}`}>
                      <div className="flex items-center gap-2 text-sm">
                        <Dot tone={v.tone} />
                        <span className="font-medium">{v.title}</span>
                        <span className="ml-auto whitespace-nowrap text-[11px] text-ink-500" title={shortDateTime(minAgo(v.minutesAgo, now))}>{ago(minAgo(v.minutesAgo, now))}</span>
                      </div>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1 pl-[18px] text-xs">
                        <span className="text-ink-700">{v.from} → {v.to ?? <span className="text-amber-800">팀 없음</span>}</span>
                        <KindChip kind={v.kind} />
                        {v.risky && <RiskyChip />}
                        {v.escalated && <EscalatedChip />}
                        <span className={`chip ${v.status === "claimed" ? "bg-emerald-50 text-emerald-700" : v.status === "rejected" ? "bg-red-50 text-red-700" : "bg-ink-100 text-ink-700"}`}>{v.label}</span>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card space-y-3 px-5 py-4" aria-label="보내기 규칙">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">보내기 규칙</h2>
              <span className="text-xs text-ink-500">{rules.length}개</span>
            </div>
            {rules.length === 0 ? (
              <p className="text-sm text-ink-500">규칙 없음</p>
            ) : (
              <ul className="flex flex-wrap gap-2">
                {rules.map((r) => (
                  <li key={r.category} className="flex items-center gap-2 rounded-xl border border-ink-100 bg-app px-3 py-1.5 text-sm">
                    <span>{r.category}</span>
                    <span className="text-ink-500">→</span>
                    <span className="font-medium">{r.dept}</span>
                    <button type="button" className="btn-quiet px-1.5 py-0.5 text-xs" aria-label={`${r.category} 규칙 지우기`} onClick={() => removeRule(r.category)}>지우기</button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        {selected && (
          <Detail key={selected.id} v={selected} now={now} me={me} approver={approver} rules={rules}
            onRoute={(dept) => addRule(selected.category, dept)}
            onClaim={() => { patch(selected.id, { status: "claimed", claimedBy: me }); setToast("내가 맡았어요. 팀 사람들에게 처리 중으로 보여요."); }}
            onRelease={() => { patch(selected.id, { status: "waiting", claimedBy: undefined }); setToast("놓았어요. 다시 팀 목록에 대기로 보여요."); }}
            onClose={(st, result) => {
              patch(selected.id, { status: st, result, closedMinutesAgo: 0 });
              setToast(st === "done" ? (selected.risky ? "승인하고 끝냈어요." : "끝냈어요. 보낸 서비스에 결과가 전해져요.") : "반려했어요. 보낸 서비스에 이유가 전해져요.");
            }} />
        )}
      </div>

      {toast && (
        <div role="status" className="card fixed bottom-6 right-6 z-50 border border-ink-100 px-4 py-3 text-sm shadow-lg">{toast}</div>
      )}
    </div>
  );
}

function Detail({ v, now, me, approver, rules, onRoute, onClaim, onRelease, onClose }: {
  v: View; now: number; me: string; approver: boolean; rules: Rule[];
  onRoute: (dept: string) => void; onClaim: () => void; onRelease: () => void; onClose: (st: "done" | "rejected", result: string) => void;
}) {
  const [result, setResult] = useState("");
  const [other, setOther] = useState(false);
  const [pick, setPick] = useState("");
  const mine = v.status === "claimed" && v.claimedBy === me;
  const approvers = v.to ? customerAccounts().filter((a) => a.dept === v.to && a.approver).map((a) => a.name) : [];
  const canFinish = result.trim().length > 0 && (!v.risky || approver);
  const rule = rules.find((r) => r.category === v.category);

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
        <div>
          <dt className="text-xs text-ink-500">받을 부서</dt>
          <dd className="mt-0.5 flex items-center gap-1 font-semibold">{v.to ?? "–"}{v.byRule && <span className="chip bg-ink-100 font-normal text-ink-500">규칙</span>}</dd>
        </div>
        <div><dt className="text-xs text-ink-500">종류</dt><dd className="mt-0.5">{v.category}</dd></div>
        <div><dt className="text-xs text-ink-500">받은 시각</dt><dd className="mt-0.5">{shortDateTime(minAgo(v.minutesAgo, now))} <span className="text-ink-500">({ago(minAgo(v.minutesAgo, now))})</span></dd></div>
        {v.claimedBy && <div><dt className="text-xs text-ink-500">맡은 사람</dt><dd className="mt-0.5 font-semibold">{v.claimedBy}</dd></div>}
        {v.risky && v.to && <div><dt className="text-xs text-ink-500">확인 권한자</dt><dd className="mt-0.5">{approvers.length ? approvers.join(", ") : "없음"}</dd></div>}
      </dl>

      <div className="space-y-1">
        <h3 className="text-sm font-semibold text-ink-700">이유</h3>
        <p className="text-sm text-ink-900">{v.reason}</p>
      </div>

      {v.escalated && <EscalatedChip />}

      <Link to={v.href} className="btn-ghost inline-flex px-3 py-1.5 text-xs">{v.hrefLabel} 바로가기 →</Link>

      {!v.to && v.status === "waiting" && v.suggest && (
        <div className="space-y-3 rounded-xl border border-amber-100 bg-amber-50 px-4 py-3">
          <p className="text-sm font-semibold text-amber-800">AI 제안 · {v.suggest.dept}에 보낼까요?</p>
          <ul className="space-y-0.5 text-xs text-amber-800">
            {v.suggest.reasons.map((r) => <li key={r}>· {r}</li>)}
          </ul>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn-primary px-3 py-1.5 text-xs" onClick={() => onRoute(v.suggest!.dept)}>보내기</button>
            <button type="button" className="btn-ghost px-3 py-1.5 text-xs" aria-expanded={other} onClick={() => setOther((x) => !x)}>다른 팀 ▾</button>
          </div>
          {other && (
            <div className="flex flex-wrap items-center gap-2">
              <DeptPicker size="sm" label="보낼 팀" value={pick} onChange={setPick} />
              <button type="button" className="btn-primary px-3 py-1.5 text-xs disabled:opacity-50" disabled={!pick.trim()} onClick={() => onRoute(pick.trim())}>이 팀에 보내기</button>
            </div>
          )}
        </div>
      )}

      {rule && !v.dept && (
        <p className="flex flex-wrap items-center gap-1 text-xs text-ink-500">
          저장된 규칙 <span className="chip bg-ink-100 text-ink-700">{rule.category} → {rule.dept}</span>
        </p>
      )}

      {v.status === "waiting" && v.to && (
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
