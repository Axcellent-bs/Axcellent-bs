import { useState, type ReactNode } from "react";
import { ago, shortDateTime, won } from "@/lib/format";
import {
  ACTIVITY, CHANNEL_KO, COUPONS, DRAFTS, HOLD_KO, KIND_KO, NOTICES, PURPOSE_KO, SKILL_KO, couponTotal,
  type NoticeFacts,
} from "@/lib/marketing";

// 마케팅 AI 화면 (인사 AI·재무 AI 화면과 같은 짜임). 전부 시연용 가상 데이터이고 API를 부르지 않는다.
// 공지는 확인된 사실 필드만 보여 주고 장애 원인은 적지 않는다. 쿠폰 금액 검증·등급 판정은 마케팅이 아니라
// 안전 장치 코드 규칙이 하며, 화면은 총액(건수 x 단가)만 보여 준다.

const INPUT = "w-full rounded-xl border border-ink-100 bg-app px-3 py-2 text-sm";
const Chip = ({ label, tone }: { label: string; tone: string }) => <span className={`chip ${tone}`}>{label}</span>;

function Section({ title, desc, children }: { title: string; desc?: ReactNode; children: ReactNode }) {
  return (
    <section className="card space-y-4 p-6">
      <div>
        <h2 className="text-lg font-semibold">{title}</h2>
        {desc && <p className="mt-1 max-w-3xl text-xs text-ink-500">{desc}</p>}
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

function Facts({ f }: { f: NoticeFacts }) {
  return (
    <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
      <div className="flex gap-2"><dt className="text-ink-500">종류</dt><dd>{KIND_KO[f.kind]}</dd></div>
      <div className="flex gap-2"><dt className="text-ink-500">구성요소</dt><dd>{f.component}</dd></div>
      <div className="flex gap-2"><dt className="text-ink-500">시작</dt><dd>{shortDateTime(f.starts_at)}</dd></div>
      <div className="flex gap-2"><dt className="text-ink-500">끝</dt><dd>{f.ends_at ? shortDateTime(f.ends_at) : "확인할 수 없음"}</dd></div>
      <div className="flex gap-2 sm:col-span-2"><dt className="shrink-0 text-ink-500">약속</dt><dd>{f.promises.length ? f.promises.join(" / ") : "없음"}</dd></div>
    </dl>
  );
}

export default function MarketingPage() {
  const held = COUPONS.filter((c) => c.grade === "C");
  const rejected = DRAFTS.filter((d) => d.status === "rejected");
  const waits = NOTICES.filter((n) => n.incident_started_at).map((n) => (new Date(n.published_at).getTime() - new Date(n.incident_started_at as string).getTime()) / 60000);
  const avgWait = waits.length ? Math.round(waits.reduce((a, b) => a + b, 0) / waits.length) : null;
  const heldRate = COUPONS.length ? Math.round((held.length / COUPONS.length) * 100) : null;

  return (
    <div className="w-full space-y-8 px-8 pb-12 pt-2">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">공지·쿠폰</h1>
          <p className="mt-1 max-w-2xl text-sm text-ink-500">고객에게 알리고(공지), 혜택을 주는(쿠폰) 일을 돕습니다. 공지는 확인된 사실로만 쓰고, 쿠폰 금액의 검증과 등급 판정은 안전 장치의 코드 규칙이 합니다.</p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
          <Chip label="시연용 가상 데이터" tone="bg-amber-50 text-amber-700" />
        </div>
      </header>

      <details className="rounded-2xl border border-ink-100 bg-surface px-5 py-3 text-sm">
        <summary className="cursor-pointer font-semibold text-ink-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500">처음이라면: 이 화면에서 볼 것</summary>
        <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-ink-700">
          <li><b>공지</b>는 초안을 만든 뒤 게시(A 등급)합니다. 장애 원인은 적지 않고 종류, 구성요소, 기간, 약속만 씁니다.</li>
          <li><b>쿠폰</b>은 고객지원이 대상과 단가를 정해 요청하면 마케팅이 총액(건수 x 단가)만 계산해 발급합니다. 기준 이상이거나 금액이 맞지 않으면 C 등급이 되어 사람이 결정합니다.</li>
          <li>공지 게시와 쿠폰 발급은 총괄·고객지원 AI가 요청하므로 이 화면에는 실행 버튼이 없습니다.</li>
        </ol>
      </details>

      <Section title="지표" desc="가상 데이터로 계산한 값입니다. 공지는 장애 시작에서 게시까지의 시간과 거절 건수, 쿠폰은 요청 수와 보류(C) 비율을 봅니다.">
        <div className="grid gap-3 sm:grid-cols-4">
          <Tile label="공지 게시까지 걸린 시간" value={avgWait === null ? "확인할 수 없음" : `${avgWait}분`} note="장애 시작에서 게시까지 평균" tone="bg-brand-50 text-brand-700" />
          <Tile label="게시 거절" value={`${rejected.length}건`} note="초안이 게시되지 못한 건수" tone="bg-red-50 text-red-800" />
          <Tile label="쿠폰 요청" value={`${COUPONS.length}건`} note="고객지원이 요청한 건수" tone="bg-emerald-50 text-emerald-800" />
          <Tile label="보류(C) 비율" value={heldRate === null ? "확인할 수 없음" : `${heldRate}%`} note={`사람 결정 대기 ${held.length}건`} tone="bg-amber-50 text-amber-800" />
        </div>
      </Section>

      <Section title="게시 중 공지" desc="지금 게시 중인 공지만 최근 게시 순으로 보여 줍니다. 끝난 공지와 시작 전 공지는 뺍니다.">
        {NOTICES.length === 0 ? <p className="py-6 text-center text-sm text-ink-500">게시 중인 공지가 없습니다.</p> : (
          <div className="space-y-3">
            {NOTICES.map((n) => (
              <article key={n.notice_id} className="space-y-2 rounded-2xl border border-ink-100 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <b className="text-sm">{n.notice_id}</b>
                  <Chip label={KIND_KO[n.facts.kind]} tone="bg-ink-100 text-ink-500" />
                  {n.channels.map((c) => <Chip key={c} label={CHANNEL_KO[c]} tone="bg-brand-50 text-brand-700" />)}
                  <Chip label="A 등급" tone="bg-emerald-50 text-emerald-700" />
                  <span className="ml-auto text-xs text-ink-500">{ago(n.published_at)} 게시</span>
                </div>
                <p className="rounded-lg bg-ink-50 px-3 py-2 text-sm">{n.text}</p>
                <Facts f={n.facts} />
              </article>
            ))}
          </div>
        )}
      </Section>

      <Section title="공지 초안" desc="초안 작성은 읽기 등급이라 상태를 바꾸지 않습니다. 게시는 A 등급이고 총괄 AI가 요청합니다.">
        <div className="space-y-3">
          {DRAFTS.map((d) => (
            <article key={d.draft_id} className="space-y-2 rounded-2xl border border-ink-100 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <b className="text-sm">{d.draft_id}</b>
                <Chip label={d.status === "draft" ? "초안" : "게시 거절"} tone={d.status === "draft" ? "bg-ink-100 text-ink-500" : "bg-red-50 text-red-700"} />
                <span className="ml-auto text-xs text-ink-500">{ago(d.created_at)} 작성</span>
              </div>
              <p className="rounded-lg bg-ink-50 px-3 py-2 text-sm">{d.text}</p>
              <Facts f={d.facts} />
              {d.reject_reason && <p className="text-xs text-red-700">{d.reject_reason}</p>}
            </article>
          ))}
        </div>
        <NoticeLookup />
      </Section>

      <Section title="쿠폰 발급 기록" desc="고객지원이 요청한 값으로 총액(건수 x 단가)을 코드가 계산합니다. 장애 사건에서는 영향 고객마다 순번(sequence) 1부터 이어서 처리합니다. 기준 이상이거나 금액이 맞지 않으면 C 등급으로 사람의 결정을 기다립니다.">
        {held.length > 0 && <p className="rounded-xl bg-amber-50 px-4 py-2 text-sm text-amber-800">사람 결정을 기다리는 쿠폰 {held.length}건 (안전 장치 보류 큐)</p>}
        <div className="overflow-x-auto rounded-2xl border border-ink-100">
          <table className="w-full text-left text-xs">
            <thead className="bg-ink-50 text-ink-500">
              <tr>{["기록", "사건", "순번", "목적", "건수 x 단가", "총액", "등급", "시각"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {COUPONS.map((c) => (
                <tr key={c.record_id}>
                  <td className="px-3 py-2 font-medium">{c.record_id}</td>
                  <td className="px-3 py-2">{c.context_id}</td>
                  <td className="px-3 py-2 tabular-nums">{c.sequence}</td>
                  <td className="px-3 py-2">{PURPOSE_KO[c.purpose_code]}</td>
                  <td className="px-3 py-2 tabular-nums">{c.count}건 x {won(c.unit_price)}</td>
                  <td className="px-3 py-2 font-semibold tabular-nums">{won(couponTotal(c))}</td>
                  <td className="px-3 py-2">
                    {c.grade === "A"
                      ? <Chip label="A 발급" tone="bg-emerald-50 text-emerald-700" />
                      : <Chip label={`C 보류 · ${c.hold_reason ? HOLD_KO[c.hold_reason] : ""}`} tone="bg-amber-50 text-amber-800" />}
                  </td>
                  <td className="px-3 py-2 text-ink-500">{shortDateTime(c.at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-ink-500">쿠폰 대상과 보상 단가는 고객지원이 정합니다. 마케팅은 금액을 검증하거나 등급을 정하지 않습니다.</p>
      </Section>

      <Section title="캠페인·SNS (Instagram)" desc="예정 기능입니다. 아직 구현되지 않았고 정책 체크리스트는 초안 상태(변호사·원문 확인 전)입니다.">
        <div className="flex flex-wrap gap-2">
          <Chip label="예정 · 구현 전" tone="bg-ink-100 text-ink-500" />
          {["캠페인 초안", "캠페인 생성", "캠페인 보고서"].map((s) => <Chip key={s} label={`${s} (예정)`} tone="bg-ink-100 text-ink-500" />)}
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <article className="space-y-1.5 rounded-2xl border border-ink-100 p-4 text-sm">
            <h3 className="font-semibold">게시물 규칙 (초안)</h3>
            <ul className="list-disc space-y-1 pl-5 text-xs text-ink-700">
              <li><b>AI 라벨은 무조건</b> 넣습니다. 계정 라벨, 게시물 API 자기공개, 캡션 안내문, 영상 안 안내문 모두입니다.</li>
              <li>쿠폰은 <b>공개 쿠폰</b>입니다. 누구나 볼 수 있고, 좋아요·팔로우·공유·댓글을 조건으로 걸지 않습니다.</li>
              <li><b>제약 사항</b>(사용 기간, 대상, 최소 주문 금액과 할인 상한, 1인당 횟수, 소진 시 종료, 중복 사용 여부, 환불 시 처리)을 게시물에 공개합니다. 없으면 평가 단계에서 거절합니다.</li>
              <li>"캣북스는 <b>시연용 가상 서비스</b>이고 실제 거래·배송이 없다"는 문구를 넣습니다.</li>
              <li>주문·결제·환불은 이용약관에 따른다고 표시하고 약관 링크를 둡니다.</li>
            </ul>
          </article>
          <article className="space-y-1.5 rounded-2xl border border-ink-100 p-4 text-sm">
            <h3 className="font-semibold">하지 않는 것 · 확인 필요</h3>
            <ul className="list-disc space-y-1 pl-5 text-xs text-ink-700">
              <li>좋아요·팔로워·계정 거래, 허위 행동 유도, 자동 DM 발송, 댓글·DM 수집은 하지 않습니다.</li>
              <li>스토리 게시는 정책 충돌 확인 전까지 하지 않고 피드·릴스부터 합니다.</li>
              <li>사람 승인 없이 쇼츠를 게시하지 않습니다(게시는 C 등급).</li>
              <li className="text-amber-800">확인 필요: 개인정보처리방침 공개 URL과 권리행사 창구, 게시 한도, 브랜디드 콘텐츠 해당 여부, 계정 단위 AI 라벨 설정 방법.</li>
              <li className="text-amber-800">확인 필요: 쿠폰 게시물의 "환불 시 쿠폰 처리"가 구매자 이용약관에서 확정되기 전에는 게시하지 않습니다.</li>
            </ul>
          </article>
        </div>
      </Section>

      <Section title="마케팅 AI가 한 일" desc="최근 처리 내역입니다. 실제 기록은 지금 메모리에만 있어 재시작하면 사라집니다(memory 부서 연결은 정해야 합니다).">
        <ol className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
          {ACTIVITY.map((a, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-x-3 px-4 py-2 text-xs">
              <span className="tabular-nums text-ink-500">{shortDateTime(a.at)}</span>
              <span className="font-medium">{SKILL_KO[a.skill] ?? a.skill}</span>
              <Chip label={a.grade === "read" ? "읽기" : `${a.grade} 등급`} tone={a.grade === "C" ? "bg-amber-50 text-amber-800" : a.grade === "A" ? "bg-emerald-50 text-emerald-700" : "bg-ink-100 text-ink-500"} />
              <span className="text-ink-700">{a.summary}</span>
            </li>
          ))}
        </ol>
      </Section>
    </div>
  );
}

// get_notice: 공지 번호로 사실·약속·기간·공개 문구를 본다. 게시되지 않았거나 없으면 '확인할 수 없음'.
function NoticeLookup() {
  const [id, setId] = useState("ntc_0012");
  const [searched, setSearched] = useState<string | null>(null);
  const found = searched ? NOTICES.find((n) => n.notice_id === searched) : undefined;
  return (
    <div className="space-y-3 border-t border-ink-100 pt-4">
      <h3 className="text-sm font-semibold">공지 번호로 보기</h3>
      <form onSubmit={(e) => { e.preventDefault(); setSearched(id.trim()); }} className="flex gap-2">
        <input className={INPUT} value={id} onChange={(e) => setId(e.target.value)} placeholder="공지 번호 (ntc_...)" />
        <button className="btn-ghost shrink-0">보기</button>
      </form>
      {searched && (found ? (
        <article className="space-y-2 rounded-2xl border border-ink-100 p-4">
          <p className="rounded-lg bg-ink-50 px-3 py-2 text-sm">{found.text}</p>
          <Facts f={found.facts} />
        </article>
      ) : <p className="text-xs text-ink-500">확인할 수 없음: 게시되지 않았거나 없는 공지 번호입니다.</p>)}
    </div>
  );
}
