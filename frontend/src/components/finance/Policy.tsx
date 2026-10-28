import { barWidth, finance, percent, plainText, sourceFileKo, withUnit, type PolicyAlert, type PolicyRule } from "@/lib/finance";
import { Chip, Empty, Panel, ResourceBody, TONE, useResource } from "./bits";

// ⑤ 예산·한도 정책: 안전 장치가 건별 검사에 쓰는 한도와 얼마나 썼는지, 재무 AI의 한도 변경 제안.
// 재무 AI는 제안만 한다. 실제 한도는 사람이 기준표(thresholds.toml) PR로 바꾼다.

const ALERT_KO: Record<PolicyAlert, { label: string; tone: string; bar: string }> = {
  ok: { label: "여유 있음", tone: TONE.ok, bar: "bg-emerald-700" },
  warn: { label: "거의 다 씀", tone: TONE.warn, bar: "bg-amber-700" },
  over: { label: "한도 넘음", tone: TONE.bad, bar: "bg-red-700" },
};
const EVIDENCE_KO: Record<string, string> = { sample: "살펴본 건수", p95: "100건 중 95건이 이 금액 이하", p50: "가운데 값", max: "가장 큰 값", days: "살펴본 기간(일)" };

function Usage({ rule }: { rule: PolicyRule }) {
  if (rule.ratio == null) return <span className="text-xs text-ink-500">사용량을 확인할 수 없음</span>;
  const a = rule.alert ? ALERT_KO[rule.alert] : null;
  const w = barWidth(rule.ratio);
  return (
    <div className="min-w-[140px]">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="tabular-nums text-ink-700">{percent(rule.ratio, 0)} 사용</span>
        {a && <Chip label={a.label} tone={a.tone} />}
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded-full bg-ink-100" role="img" aria-label={`한도의 ${percent(rule.ratio, 0)} 사용${a ? `, ${a.label}` : ""}`}>
        <div className={`h-full rounded-full ${a?.bar ?? "bg-brand-500"}`} style={{ width: `${w}%` }} />
      </div>
    </div>
  );
}

export function PolicySection({ tick }: { tick: number }) {
  const res = useResource("policy", () => finance.policy(), tick);
  return (
    <Panel id="policy" title="⑤ 예산·한도 정책" titleHidden meta={res.env?.meta}
      hint="쿠폰·환불·보상을 얼마까지 자동으로 처리할지 정한 한도입니다. 안전 장치가 이 한도로 한 건씩 검사하고, 재무 AI는 얼마나 썼는지 보고 한도를 바꾸자고 제안합니다.">
      <ResourceBody res={res}>
        {(p) => {
          const labelOf = (key: string) => p.rules.find((r) => r.key === key)?.label ?? key;
          const unitOf = (key: string) => p.rules.find((r) => r.key === key)?.unit ?? "원";
          return (
            <div className="space-y-5">
              {p.rules.length ? (
                <ul className="divide-y divide-ink-100">
                  {p.rules.map((r) => (
                    <li key={r.key} className="grid gap-2 py-3 sm:grid-cols-[1fr_auto] md:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_200px] md:items-center md:gap-4">
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-ink-900">{r.label}</p>
                        <p className="text-xs text-ink-500">한도 <b className="tabular-nums text-ink-700">{withUnit(r.limit, r.unit)}</b>{r.source && ` · ${sourceFileKo(r.source)}`}</p>
                      </div>
                      <p className="text-xs tabular-nums text-ink-500">
                        오늘 {r.used_today == null ? "확인할 수 없음" : withUnit(r.used_today, r.unit)} · 이번 달 {r.used_month == null ? "확인할 수 없음" : withUnit(r.used_month, r.unit)}
                      </p>
                      <Usage rule={r} />
                    </li>
                  ))}
                </ul>
              ) : <Empty>정책표를 받지 못했습니다.</Empty>}

              <div>
                <h3 className="text-sm font-semibold text-ink-900">재무 AI의 한도 변경 제안 <span className="font-normal text-ink-500">{p.proposals.length}건</span></h3>
                {p.proposals.length ? (
                  <ul className="mt-2 space-y-2">
                    {p.proposals.map((x) => {
                      const unit = unitOf(x.key);
                      const down = x.current != null && x.proposed < x.current;
                      return (
                        <li key={x.key} className="rounded-2xl border border-ink-100 px-4 py-3 text-sm">
                          <div className="flex flex-wrap items-center gap-2">
                            <b className="text-ink-900">{labelOf(x.key)}</b>
                            <span className="tabular-nums text-ink-700">{withUnit(x.current, unit)} → <b>{withUnit(x.proposed, unit)}</b></span>
                            {x.current != null && <Chip label={down ? "더 엄격하게" : "더 넉넉하게"} tone={down ? TONE.info : TONE.warn} />}
                          </div>
                          <p className="mt-1 text-ink-700">{plainText(x.reason)}</p>
                          {x.evidence && Object.keys(x.evidence).length > 0 && (
                            <dl className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-ink-500">
                              {Object.entries(x.evidence).map(([k, v]) => (
                                <div key={k} className="flex gap-1">
                                  <dt>{EVIDENCE_KO[k] ?? "기타 근거"}:</dt>
                                  <dd className="tabular-nums text-ink-700">{typeof v === "number" ? (k === "sample" || k === "days" ? v.toLocaleString("ko-KR") : withUnit(v, unit)) : String(v)}</dd>
                                </div>
                              ))}
                            </dl>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                ) : <p className="mt-2 text-sm text-ink-500">지금은 바꿀 만한 한도가 없습니다.</p>}
              </div>

              <p className="rounded-xl bg-brand-50 px-4 py-3 text-xs leading-relaxed text-brand-700">
                <b>한도는 어떻게 바꾸나요?</b> 재무 AI는 제안만 하고 직접 바꾸지 않습니다. {p.how_to_change || "기준표(thresholds.toml) PR로 사람이 바꿉니다."} 제안이 마음에 들면 재무 담당자(태윤)에게 기준표 수정을 요청하세요.
              </p>
            </div>
          );
        }}
      </ResourceBody>
    </Panel>
  );
}
