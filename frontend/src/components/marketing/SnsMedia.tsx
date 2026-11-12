import { useCallback, useEffect, useState, type ReactNode } from "react";
import { shortDateTime, won } from "@/lib/format";
import { finance, type Review } from "@/lib/finance";
import { holdKo } from "@/lib/marketingLive";

// SNS·미디어 탭. 이미지·영상 생성과 SNS 게시 흐름을 안내하고,
// 안전 장치 보류 큐(finance.reviews)에서 읽을 수 있는 마케팅 항목(publish_sns, generate_media)만 읽기 전용으로 보여 준다.
// 보류 결정은 장애·공지·캠페인 탭의 보류 큐에서 한다. 이 탭은 결정하지 않는다.
// 비용 장부 숫자(오늘·이달 합계, 건당 상한)를 읽는 API는 화면에 연결돼 있지 않아 지어내지 않고 빈 상태로 둔다.

const REFRESH_MS = 15_000;
const SNS_ACTIONS = ["publish_sns", "generate_media"] as const;
const ACTION_NAME: Record<string, string> = { publish_sns: "SNS 게시", generate_media: "이미지·영상 만들기" };
const fromMarketing = (requester: string) => requester === "coupon" || requester === "marketing";

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

const Empty = ({ children }: { children: ReactNode }) => <p className="py-6 text-center text-sm text-ink-500">{children}</p>;
const NOT_WIRED = "아직 화면에 연결되지 않았습니다.";

const PIPELINE: { step: string; who: string; note: string }[] = [
  { step: "초안", who: "마케팅 AI", note: "게시된 공지나 공개 쿠폰 사실로 캡션 초안을 만듭니다(draft_sns_post). AI 생성 표시 문구가 들어갑니다." },
  { step: "검사", who: "마케팅 AI", note: "AI 표시 누락·승인한 문구와의 차이·쿠폰 필수 안내 누락을 코드 규칙으로 점검합니다(check_sns_post)." },
  { step: "사람 승인", who: "사람", note: "SNS 게시(publish_sns)는 C 등급이라 항상 안전 장치 보류 큐에서 사람이 승인해야 합니다." },
  { step: "게시", who: "마케팅 AI", note: "승인을 받은 뒤에만 게시합니다. 마케팅 AI가 스스로 승인하는 길은 없습니다." },
];

export function SnsMedia() {
  const [holds, setHolds] = useState<Review[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const all = await finance.reviews(true);
      setHolds(all.filter((r) => r.status === "pending" && fromMarketing(r.requester) && (SNS_ACTIONS as readonly string[]).includes(r.action)));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [load]);

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">SNS·미디어</h1>
          <p className="mt-1 text-sm text-ink-500">마케팅 AI가 만드는 이미지·영상과 SNS 게시 흐름입니다. 이 탭은 읽기만 하고, 보류 결정은 장애·공지 또는 캠페인·쿠폰 탭의 보류 큐에서 합니다.</p>
        </div>
        <button className="btn-ghost ml-auto text-xs" onClick={() => void load()} disabled={loading}>{loading ? "불러오는 중" : "새로고침"}</button>
      </header>

      <Section title="이미지·영상 만들기" desc="B 등급 일입니다. 안전 장치가 비용(cost_krw)을 코드 규칙으로 검사하고, 적어 낸 비용과 계산이 다르거나 기준을 넘으면 사람 확인으로 보류합니다.">
        <ul className="space-y-1 text-sm text-ink-700">
          <li>생성한 이미지·영상에는 AI 생성 표시를 넣습니다.</li>
          <li>비용은 건당 상한과 하루 합계 기준으로 막습니다. 기준을 넘은 건은 아래 보류 목록에 나옵니다.</li>
        </ul>
        <div className="rounded-2xl border border-ink-100 p-4">
          <p className="text-xs font-medium text-ink-700">비용 장부 (오늘·이달 합계, 건당 상한)</p>
          <Empty>{NOT_WIRED}</Empty>
        </div>
      </Section>

      <Section title="SNS 게시 흐름" desc="초안에서 게시까지 네 단계입니다. 사람 승인 없이는 게시되지 않습니다.">
        <ol className="grid gap-3 sm:grid-cols-4">
          {PIPELINE.map((p, i) => (
            <li key={p.step} className="space-y-1 rounded-2xl border border-ink-100 p-4">
              <p className="text-xs text-ink-500">{i + 1}단계 · {p.who}</p>
              <p className="text-sm font-semibold">{p.step}</p>
              <p className="text-xs text-ink-700">{p.note}</p>
            </li>
          ))}
        </ol>
      </Section>

      <Section title="AI 표시 라벨 안내" desc="AI 기본법을 적용 대상으로 보고, AI가 만든 이미지·영상·캡션에는 항상 표시합니다.">
        <ul className="list-disc space-y-1 pl-5 text-sm text-ink-700">
          <li>이미지·영상 위에 사람이 읽는 표시 문구를 넣고, 파일에도 기계가 읽는 표시를 함께 남깁니다.</li>
          <li>캡션에도 AI 생성 안내 문구가 들어갑니다. 표시가 빠지면 검사 단계에서 막힙니다.</li>
          <li>계정 프로필 쪽 라벨은 사람이 따로 확인합니다.</li>
        </ul>
      </Section>

      <Section title="사람 결정 대기 (SNS·미디어)" desc="안전 장치 보류 큐에서 마케팅 AI가 요청한 SNS 게시·이미지·영상 만들기만 읽어 옵니다.">
        {error ? <p className="rounded-xl bg-red-50 px-4 py-2 text-sm text-red-800">보류 건을 읽지 못했습니다. {error}</p>
          : holds === null ? <Empty>불러오는 중입니다.</Empty>
            : holds.length === 0 ? <Empty>기다리는 건이 없습니다.</Empty> : (
              <ul className="divide-y divide-ink-100 rounded-2xl border border-ink-100">
                {holds.map((r) => (
                  <li key={r.review_id} className="space-y-1 px-4 py-2 text-xs" title={`보류 건 ${r.review_id}`}>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="font-medium">{ACTION_NAME[r.action] ?? "기타 요청"}</span>
                      {r.report.computed_amount != null && <span className="tabular-nums">{won(r.report.computed_amount)}</span>}
                      {r.report.hold_reason && <span className="chip bg-amber-50 text-amber-800" title={r.report.hold_reason}>{holdKo(r.report.hold_reason)}</span>}
                      <span className="ml-auto text-ink-500">{shortDateTime(r.expires_at)}까지 결정</span>
                    </div>
                    {r.report.summary && <p className="text-ink-700">{r.report.summary}</p>}
                  </li>
                ))}
              </ul>
            )}
      </Section>
    </div>
  );
}
