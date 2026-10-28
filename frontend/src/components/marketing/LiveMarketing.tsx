import { useCallback, useEffect, useState, type ReactNode } from "react";
import { ago, shortDateTime, won } from "@/lib/format";
import {
  CHANNEL_LIVE_KO, PURPOSE_LIVE_KO, actionKo, holdKo, incidentClosed, incidentName, loadMarketingLive, statusKo,
  type MarketingLive,
} from "@/lib/marketingLive";

// 마케팅 AI 화면. 캣북스 쇼핑몰 배너와 안전 장치 기록을 읽기만 한다.
// 기록 번호(공지 번호·처리 번호·사건 번호)는 화면 글자로 쓰지 않고 마우스를 올리면 보이게(title) 둔다.

const REFRESH_MS = 15_000;
const Chip = ({ label, tone, title }: { label: string; tone: string; title?: string }) => <span className={`chip ${tone}`} title={title}>{label}</span>;

function Section({ title, desc, children }: { title: string; desc?: ReactNode; children: ReactNode }) {
  return (
    <section className="card space-y-4 p-6">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        {desc && <p className="mt-1 text-xs text-ink-500">{desc}</p>}
      </div>
      {children}
    </section>
  );
}

function Tile({ label, value, note, tone }: { label: string; value: string; note: string; tone: string }) {
  return (
    <div className={`rounded-2xl p-4 ${tone}`}>
      <p className="text-xs">{label}</p>
      <p className="mt-1 text-3xl font-bold tabular-nums">{value}</p>
      <p className="mt-1 text-xs opacity-80">{note}</p>
    </div>
  );
}

const Empty = ({ children }: { children: ReactNode }) => <p className="py-6 text-center text-sm text-ink-500">{children}</p>;

function gradeChip(grade: string, status: string, hold: string | null) {
  if (status === "executed") return <Chip label={`${grade} 등급 · 실행됨`} tone="bg-emerald-50 text-emerald-700" />;
  if (status === "held" || status === "verifying") return <Chip label={`${grade} 등급 · ${statusKo(status)}${hold ? ` · ${holdKo(hold)}` : ""}`} tone="bg-amber-50 text-amber-800" title={hold ?? undefined} />;
  return <Chip label={`${statusKo(status)}${hold ? ` · ${holdKo(hold)}` : ""}`} tone="bg-red-50 text-red-700" title={hold ?? undefined} />;
}

export default function LiveMarketing() {
  const [live, setLive] = useState<MarketingLive | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try { setLive(await loadMarketingLive()); } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => { void load(); }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const incidents = live?.incidents ?? new Map();
  const coupons = live?.coupons ?? [];
  const issued = coupons.filter((c) => c.status === "executed");
  const issuedTotal = issued.reduce((s, c) => s + c.total, 0);
  const heldCount = live?.holds?.length ?? coupons.filter((c) => c.status === "held").length;
  const publishedCount = live?.notices?.filter((n) => n.status === "executed").length ?? 0;

  return (
    <div className="w-full space-y-8 px-8 pb-12 pt-2">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">공지·쿠폰</h1>
          <p className="mt-1 text-sm text-ink-500">마케팅 AI가 캣북스에 실제로 올린 공지와 고객에게 준 쿠폰입니다. 공지 게시와 쿠폰 발급은 안전 장치를 거쳐 실행되고, 이 화면은 읽기만 합니다.</p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
          <Chip label="실제 데이터" tone="bg-emerald-50 text-emerald-700" title="캣북스 쇼핑몰 배너와 안전 장치 실행 기록을 읽습니다" />
          {live && <span className="text-ink-500">{ago(live.loaded_at)} 갱신 · 15초마다 자동</span>}
          <button className="btn-ghost" onClick={() => void load()} disabled={loading}>{loading ? "불러오는 중" : "새로고침"}</button>
        </div>
      </header>

      {live?.errors.map((e) => <p key={e} className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">{e}</p>)}
      {!live && <p className="text-sm text-ink-500">불러오는 중입니다.</p>}

      {live && (
        <>
          <Section title="지표" desc="안전 장치 실행 기록으로 센 값입니다. 총액은 건수 x 단가를 안전 장치가 계산한 값입니다.">
            <div className="grid gap-3 sm:grid-cols-4">
              <Tile label="지금 걸린 공지" value={live.banners ? `${live.banners.length}건` : "확인할 수 없음"} note="캣북스 쇼핑몰 배너" tone="bg-brand-50 text-brand-700" />
              <Tile label="게시한 공지" value={`${publishedCount}건`} note="지금까지 게시 요청이 실행된 건수" tone="bg-ink-50 text-ink-700" />
              <Tile label="발급한 쿠폰" value={`${issued.reduce((s, c) => s + c.count, 0)}장`} note={`총 ${won(issuedTotal)}`} tone="bg-emerald-50 text-emerald-800" />
              <Tile label="사람 결정 대기" value={`${heldCount}건`} note="안전 장치 보류 큐" tone="bg-amber-50 text-amber-800" />
            </div>
          </Section>

          <Section title="지금 캣북스에 걸린 공지" desc="고객이 쇼핑몰에서 보고 있는 배너입니다. 게시 기간 안이고 끝나지 않은 공지만 나옵니다.">
            {live.banners === null ? <Empty>쇼핑몰 서버에서 공지를 읽지 못했습니다.</Empty>
              : live.banners.length === 0 ? <Empty>지금 게시 중인 공지가 없습니다.</Empty> : (
                <div className="space-y-3">
                  {live.banners.map((b) => (
                    <article key={b.notice_id} className="space-y-2 rounded-2xl border border-ink-100 p-4" title={`공지 번호 ${b.notice_id}`}>
                      <div className="flex flex-wrap items-center gap-2">
                        <b className="text-sm" title={b.context_id ?? undefined}>{incidentName(b.context_id, incidents)}</b>
                        <Chip label="쇼핑몰 배너" tone="bg-brand-50 text-brand-700" />
                        <Chip label="게시 중" tone="bg-emerald-50 text-emerald-700" />
                        <span className="ml-auto text-xs text-ink-500">{ago(b.created_at)} 게시</span>
                      </div>
                      <p className="rounded-lg bg-ink-50 px-3 py-2 text-sm">{b.text}</p>
                      <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                        <div className="flex gap-2"><dt className="text-ink-500">게시 시작</dt><dd>{shortDateTime(b.valid_from ?? b.created_at)}</dd></div>
                        <div className="flex gap-2"><dt className="text-ink-500">게시 끝</dt><dd>{b.valid_to ? shortDateTime(b.valid_to) : "정하지 않음 (복구 공지가 나가면 끝남)"}</dd></div>
                      </dl>
                    </article>
                  ))}
                </div>
              )}
          </Section>

          <Section title="공지 게시 기록" desc="마케팅 AI가 사건마다 올린 공지입니다. 지금 걸려 있지 않은 공지의 본문은 쇼핑몰에서 내려가 여기서 볼 수 없습니다.">
            {live.notices === null ? <Empty>공지 게시 기록을 읽지 못했습니다.</Empty>
              : live.notices.length === 0 ? <Empty>아직 게시한 공지가 없습니다.</Empty> : (
                <ol className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
                  {live.notices.map((n) => (
                    <li key={n.execution_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-xs" title={`처리 기록 ${n.execution_id}`}>
                      <span className="tabular-nums text-ink-500">{shortDateTime(n.at)}</span>
                      <span className="font-medium" title={n.context_id}>{incidentName(n.context_id, incidents)}</span>
                      {n.channel && <Chip label={CHANNEL_LIVE_KO[n.channel] ?? "기타 채널"} tone="bg-brand-50 text-brand-700" />}
                      {gradeChip(n.grade, n.status, n.hold_reason)}
                      <span className="ml-auto text-ink-500">
                        {n.banner ? "지금 게시 중" : incidentClosed(n.context_id, incidents) ? "사건이 끝나 내려감" : "지금은 걸려 있지 않음"}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
          </Section>

          <Section title="쿠폰 발급" desc="사건과 목적별로 묶었습니다. 대상 고객과 단가는 고객지원 AI가 정하고, 마케팅 AI가 발급을 요청하면 안전 장치가 금액을 계산해 등급을 정합니다.">
            {live.coupons === null ? <Empty>쿠폰 발급 기록을 읽지 못했습니다.</Empty>
              : live.groups.length === 0 ? <Empty>아직 발급한 쿠폰이 없습니다.</Empty> : (
                <div className="overflow-x-auto rounded-2xl border border-ink-100">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-ink-50 text-ink-500">
                      <tr>{["사건", "목적", "고객", "발급", "발급 총액", "보류", "실패·거절", "마지막 발급"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
                    </thead>
                    <tbody className="divide-y divide-ink-100">
                      {live.groups.map((g) => (
                        <tr key={g.key}>
                          <td className="px-3 py-2 font-medium" title={g.context_id}>{incidentName(g.context_id, incidents)}</td>
                          <td className="px-3 py-2">{g.purpose ? PURPOSE_LIVE_KO[g.purpose] ?? "기타" : "확인할 수 없음"}</td>
                          <td className="px-3 py-2 tabular-nums">{g.customers}명</td>
                          <td className="px-3 py-2 tabular-nums">{g.issued}장</td>
                          <td className="px-3 py-2 font-semibold tabular-nums">{won(g.issued_total)}</td>
                          <td className="px-3 py-2 tabular-nums">{g.held ? `${g.held}장 · ${won(g.held_total)}` : "없음"}</td>
                          <td className="px-3 py-2 tabular-nums">{g.failed ? `${g.failed}건` : "없음"}</td>
                          <td className="px-3 py-2 text-ink-500">{shortDateTime(g.last_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            {coupons.length > 0 && (
              <details className="rounded-2xl border border-ink-100 px-4 py-2 text-xs">
                <summary className="cursor-pointer font-medium text-ink-700">한 건씩 보기 ({coupons.length}건)</summary>
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-left">
                    <thead className="text-ink-500">
                      <tr>{["시각", "고객", "건수 x 단가", "총액", "판정"].map((h) => <th key={h} className="px-2 py-1 font-medium">{h}</th>)}</tr>
                    </thead>
                    <tbody className="divide-y divide-ink-100">
                      {coupons.map((c) => (
                        <tr key={c.execution_id} title={`처리 기록 ${c.execution_id}`}>
                          <td className="px-2 py-1 text-ink-500">{shortDateTime(c.at)}</td>
                          <td className="px-2 py-1" title={c.buyer_id ?? undefined}>{c.buyer_id ? `고객 ${c.buyer_id.replace(/^buy_/, "")}` : "확인할 수 없음"}</td>
                          <td className="px-2 py-1 tabular-nums">{c.count}건 x {c.unit_value === null ? "확인할 수 없음" : won(c.unit_value)}</td>
                          <td className="px-2 py-1 font-semibold tabular-nums">{won(c.total)}</td>
                          <td className="px-2 py-1">{gradeChip(c.grade, c.status, c.hold_reason)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            )}
          </Section>

          <Section title="사람 결정 대기" desc="금액 기준을 넘거나 계산이 맞지 않아 안전 장치가 멈춘 공지·쿠폰입니다. 승인·거절은 AI 대화방이나 재무 화면의 보류 큐에서 합니다.">
            {live.holds === null ? <Empty>보류 건을 읽지 못했습니다.</Empty>
              : live.holds.length === 0 ? <Empty>기다리는 건이 없습니다.</Empty> : (
                <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
                  {live.holds.map((r) => (
                    <li key={r.review_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-xs" title={`보류 건 ${r.review_id}`}>
                      <span className="font-medium">{actionKo(r.action)}</span>
                      <span title={r.context_id}>{incidentName(r.context_id, incidents)}</span>
                      {r.report.computed_amount != null && <span className="tabular-nums">{won(r.report.computed_amount)}</span>}
                      {r.report.hold_reason && <Chip label={holdKo(r.report.hold_reason)} tone="bg-amber-50 text-amber-800" title={r.report.hold_reason} />}
                      <span className="ml-auto text-ink-500">{shortDateTime(r.expires_at)}까지 결정</span>
                    </li>
                  ))}
                </ul>
              )}
          </Section>
        </>
      )}
    </div>
  );
}
