import { Link, useRouter } from "@/lib/nav";
import { usePathname } from "@/lib/nav";
import { useEffect, useState, type ComponentType, type ReactNode } from "react";
import { findSiteIconOrNull } from "@/components/planning-common/company";
import { AccountSwitcher } from "@/demo/AccountSwitcher";
import { homeOf, servicesOf, supportViewer, useSession, type Account } from "@/demo/session";
import { useCompany } from "@/demo/company";
import { endSupport, supportActive, useSupport } from "@/demo/support";
import { AGENTS, activeChild, matchPath, type Service, type SubItem } from "@/demo/services";
import { useOpenHandoffCount } from "@/lib/handoffs";
import { DEV_TASKS_HREF, ORCHESTRATOR_HREF, PLANNING_REQUESTS_HREF, useNavCounts } from "@/lib/navCounts";
import { IconAgents, IconChart, IconGear, IconInbox } from "../icons";
import { preConnectMenu, systemMenuFor } from "@/demo/systemMenu";

// 메뉴: AI가 상위 탭이고, 그 AI가 맡은 서비스가 하위 탭이다. 서비스 안 화면(예: 고객 문의의 상담 콘솔)은 그 아래 한 단 더.
// 고객사 계정은 시스템 관리자 하나이고, 서비스는 모두 필수라 전부 메뉴에 보인다(src/demo/session.tsx).
// AI가 맡지 않은 서비스(DejaFix 장애 대응 등)와 공용 화면은 구분선 아래에 둔다.
type SubNav = { href: string; label: string; soon?: boolean; match: string[]; children?: SubItem[]; badge?: number };
type NavItem = { href: string; label: string; Icon: ComponentType<{ size?: number }>; logo?: string; children?: SubNav[]; match: string[]; divider?: boolean; badge?: number | null };

const subNav = (items?: SubItem[]): SubNav[] | undefined => items?.map((c) => ({ ...c, match: [c.href] }));
const serviceNav = (s: Service): NavItem => ({ href: s.entry, label: s.name, Icon: s.Icon, logo: s.logo, children: subNav(s.children), match: s.paths });

/** AI별 탭. 맡은 서비스가 하나면 AI 탭이 그 서비스를 바로 열고, 둘 이상이면 서비스가 하위 탭이 된다 */
function agentNav(services: Service[]): NavItem[] {
  return AGENTS.flatMap((g) => {
    const mine = services.filter((s) => s.agent === g.id);
    if (mine.length === 0) return [];
    const match = mine.flatMap((s) => s.paths);
    if (mine.length === 1) return [{ href: mine[0].entry, label: g.name, Icon: g.Icon, children: subNav(mine[0].children), match }];
    return [{
      href: mine[0].entry, label: g.name, Icon: g.Icon, match,
      children: mine.map((s) => ({ href: s.entry, label: s.name, match: s.paths, children: s.children })),
    }];
  });
}

/** 하위 탭 중 지금 주소에 가장 길게 맞는 것 (/control 과 /control/keys 가 같이 켜지지 않게) */
function activeSub(path: string, subs: SubNav[]): SubNav | undefined {
  let best: SubNav | undefined;
  let len = -1;
  for (const c of subs) for (const m of c.match) if (matchPath(m, path) && m.length > len) { best = c; len = m.length; }
  return best;
}

/** 메뉴 아이콘. logo가 있으면 아이콘 대신 그 이미지를 쓴다 (DejaFix) */
function NavIcon({ item, size }: { item: NavItem; size: number }) {
  return item.logo ? <img src={item.logo} alt="" width={size} height={size} className="shrink-0" /> : <item.Icon size={size} />;
}

// 할 일함은 어느 서비스를 샀든 보이는 기본 메뉴다 (SHARED_PATHS). 열린 일이 있으면 개수를 띄운다
const HANDOFFS_HREF = "/handoffs";
const CHAT_HREF = "/agents/chat";
const SHARED_NAV: NavItem[] = [
  { href: HANDOFFS_HREF, label: "받은 요청", Icon: IconInbox, match: [HANDOFFS_HREF] },
  // AI 대화방: 대화 기록(전체 대화방·사건 방·직접 대화)과 AI 오피스(픽셀 사무실)를 하위 탭으로
  {
    href: CHAT_HREF, label: "AI 대화방", Icon: IconAgents, match: ["/agents/chat", "/agents/office"],
    children: [
      { href: "/agents/chat", label: "대화 기록", match: ["/agents/chat"] },
      { href: "/agents/office", label: "AI 오피스", match: ["/agents/office"] },
    ],
  },
];
// 관제 센터: 고객사에 설치된 AXcellent 상태를 모아 보는 AXcellent 운영팀 전용 화면 (src/control). 고객사 계정에는 안 보인다
const CONTROL_MENU: SubItem[] = [
  { href: "/control", label: "전체 현황" },
  { href: "/control/companies", label: "고객사" },
  { href: "/control/services", label: "서비스 관리" },
  { href: "/control/incidents", label: "장애·사건" },
  { href: "/control/quality", label: "AI 품질" },
  { href: "/control/cost", label: "비용·사용량" },
  { href: "/control/keys", label: "연결 키 발급" },
];

const CONTROL_NAV: NavItem[] = [
  { href: "/control", label: "관제 센터", Icon: IconChart, children: subNav(CONTROL_MENU), match: ["/control"] },
];

// 관리 화면은 "시스템 관리" 탭 하나 아래에 모은다 (메뉴 목록: src/demo/systemMenu.ts).
// 회원·서버 관리: 회원(연결·부서·확인 권한)과 PC·서버 연결(DejaFix), 접속 허용 IP를 한 화면 탭으로 묶는다.
// PC·서버 연결은 기본 시스템으로 보고 여기 고정으로 둔다 (서비스 메뉴에서는 뺌).
// 도구 연결(형상관리·배포·문서·AI)은 모두에게 보인다. 관리자가 아니면 시스템 관리 아래에 이것만 있다
const systemNav = (menu: SubItem[]): NavItem => ({ href: menu[0].href, label: "시스템 관리", Icon: IconGear, children: subNav(menu), match: ["/system"] });

function useNav(): NavItem[] {
  const { account } = useSession();
  const company = useCompany();
  const support = useSupport();
  if (!account) return [];
  if (account.operator) {
    // 지원 모드: 관제 센터 아래에 들어간 고객사의 관리자 메뉴를 붙인다
    if (!supportActive(support, company)) return CONTROL_NAV;
    const [first, ...more] = customerNav(supportViewer(account));
    return first ? [...CONTROL_NAV, { ...first, divider: true }, ...more] : CONTROL_NAV;
  }
  // 회사 연결 전에는 회사 연결만 보인다
  if (!company) return account.admin ? [systemNav(preConnectMenu())] : [];
  return customerNav(account);
}

const INFRA_ID = "incident";

/** 인프라 AI는 AI 묶음의 일원이다: 구분선 아래로 떨어지지 않게 기획 AI 바로 밑(개발 AI 앞)에 둔다 */
function withInfra(agents: NavItem[], services: Service[]): NavItem[] {
  const infra = services.find((s) => s.id === INFRA_ID);
  if (!infra) return agents;
  const planning = AGENTS.find((g) => g.id === "planning");
  const at = agents.findIndex((a) => a.label === planning?.name);
  const item = serviceNav(infra);
  return at < 0 ? [...agents, item] : [...agents.slice(0, at + 1), item, ...agents.slice(at + 1)];
}

/** 안전 처리 로그는 AI가 아니라 기록이라(11/17 멘토링) AI 묶음에서 빼서 따로 돌려준다 */
function splitSafety(agents: NavItem[]): [NavItem[], NavItem | undefined] {
  const name = AGENTS.find((g) => g.id === "safety")?.name;
  const at = agents.findIndex((a) => a.label === name);
  return at < 0 ? [agents, undefined] : [[...agents.slice(0, at), ...agents.slice(at + 1)], agents[at]];
}

/** 회사에 연결된 고객사 계정의 메뉴 */
function customerNav(account: Account): NavItem[] {
  const services = servicesOf(account);
  const [agents, safety] = splitSafety(withInfra(agentNav(services), services));
  const others = services.filter((s) => !s.agent && s.id !== INFRA_ID && !(account.admin && s.id === "hosts"));
  const rest = [...others.map(serviceNav), ...SHARED_NAV];
  // 안전 처리 로그는 "AI 대화방" 바로 밑 (AI들이 주고받은 대화 → 그 실행 요청을 안전 규칙으로 판단한 기록)
  if (safety) {
    const chat = rest.findIndex((n) => n.href === CHAT_HREF);
    rest.splice(chat < 0 ? rest.length : chat + 1, 0, safety);
  }
  if (agents.length) rest[0] = { ...rest[0], divider: true };
  // 고객사 시스템 관리자는 관리 메뉴를 맨 위에, 일반회원은 맨 아래(예전 "연결" 자리)에 도구 연결만
  const system = systemNav(systemMenuFor(!!account.admin));
  return account.admin ? [system, ...agents, ...rest] : [...agents, ...rest, system];
}

const isOn = (path: string, item: NavItem) => item.match.some((m) => matchPath(m, path));

// 화면 틀: DejaFix common-frontend AppShell 모양. 어두운 보라 사이드바(접기) + 흰 상단바.
// common-frontend CSS는 .btn·.card 같은 이름이 업무 화면과 겹쳐서, 틀만 같은 값으로 다시 그렸다 (vendor/common-frontend/src/styles/shell.css).
const SIDE_KEY = "axcellent-demo-side";
const readSide = () => {
  try { return localStorage.getItem(SIDE_KEY) === "collapsed"; } catch { return false; }
};

export function AdminShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { account } = useSession();
  const baseNav = useNav();
  const hasInbox = baseNav.some((n) => n.href === HANDOFFS_HREF);
  const openHandoffs = useOpenHandoffCount(hasInbox);
  // 하위 탭 숫자(기획 들어온 요청·개발 사람 확인)는 하위 탭 옆에, AI 탭에는 그 합을 늘 단다 (열고 닫아도 자리가 그대로)
  const hasSub = (href: string) => baseNav.some((n) => n.children?.some((c) => c.href === href));
  const hasOrchestrator = baseNav.some((n) => n.href === ORCHESTRATOR_HREF);
  const subCounts = useNavCounts(hasSub(PLANNING_REQUESTS_HREF), hasSub(DEV_TASKS_HREF), hasOrchestrator);
  const nav = baseNav.map((n) => {
    if (n.href === HANDOFFS_HREF) return { ...n, badge: openHandoffs };
    if (n.href === ORCHESTRATOR_HREF) return { ...n, badge: subCounts[ORCHESTRATOR_HREF] };  // 사람 승인 대기
    if (!n.children?.some((c) => subCounts[c.href])) return n;
    const children = n.children.map((c) => ({ ...c, badge: subCounts[c.href] }));
    return { ...n, children, badge: children.reduce((sum, c) => sum + (c.badge ?? 0), 0) };
  });
  const home = account ? homeOf(account) : "/";
  const company = useCompany();
  const router = useRouter();
  // 지원 모드: 운영자가 고객사 화면을 보는 동안 위에 띠를 띄우고, 고객사 화면 안의 버튼·입력은 끈다(보기 전용)
  const support = useSupport();
  const supporting = !!account?.operator && supportActive(support, company);
  const viewOnly = supporting && !matchPath("/control", path);
  const leaveSupport = () => { endSupport(); router.push("/control/companies"); };
  const [collapsed, setCollapsed] = useState(readSide);
  const toggle = () => setCollapsed((v) => {
    try { localStorage.setItem(SIDE_KEY, v ? "open" : "collapsed"); } catch { /* 저장이 막혀도 화면은 동작한다 */ }
    return !v;
  });

  return (
    <div className="flex h-screen overflow-hidden bg-app text-ink-900">
      <aside className={`flex shrink-0 flex-col overflow-y-auto bg-[#1d1d3a] pb-3 text-[#e4e4f4] ${collapsed ? "w-16" : "w-[248px]"}`}>
        <div className={`mb-2 flex h-[60px] shrink-0 items-center gap-3 border-b border-white/10 ${collapsed ? "justify-center" : "px-3.5"}`}>
          <button type="button" onClick={toggle} aria-label={collapsed ? "메뉴 펼치기" : "메뉴 접기"} aria-expanded={!collapsed}
            className="grid h-9 w-9 shrink-0 place-items-center hover:bg-white/10">
            <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
          </button>
          {!collapsed && (
            <Link href={home} className="-ml-2 flex items-center gap-3" aria-label="내 첫 화면">
              <img src={`${import.meta.env.BASE_URL}axcellent-wordmark-nav.webp`} alt="AXcellent" width={146} height={28} className="h-7 w-auto" />
            </Link>
          )}
        </div>
        {nav.map((item) => {
          const { href, label, children: subs } = item;
          const on = isOn(path, item);
          const open = on && subs && !collapsed;
          const subOn = open ? activeSub(path, subs) : undefined;
          return (
            <div key={href}>
              {item.divider && <div className={`my-2 border-t border-white/10 ${collapsed ? "mx-3" : "mx-[18px]"}`} aria-hidden="true" />}
              <Link href={href} title={collapsed ? label : undefined} aria-label={collapsed ? (item.badge ? `${label} 열린 일 ${item.badge}건` : label) : undefined} aria-current={on && !subs ? "page" : undefined}
                className={`relative flex items-center gap-3.5 py-[13px] text-sm font-semibold transition ${collapsed ? "justify-center" : "px-[18px]"} ${on ? (open ? "text-white" : "bg-[#5b5bd6] text-white") : "hover:bg-white/10"}`}>
                <NavIcon item={item} size={item.logo ? 22 : 18} />
                {!collapsed && <span className="truncate">{label}</span>}
                {!!item.badge && (collapsed
                  ? <span className="absolute right-3 top-2.5 h-2 w-2 rounded-full bg-amber-400" aria-label={`열린 일 ${item.badge}건`} />
                  : <span className="ml-auto shrink-0 rounded-full bg-amber-400 px-1.5 text-[11px] font-semibold leading-[18px] text-[#1d1d3a]">{item.badge > 99 ? "99+" : item.badge}<span className="sr-only">건 열림</span></span>)}
              </Link>
              {open && subs.map((c) => {
                const sub = c === subOn;
                // 서비스 안 화면이 있으면 그 서비스가 열려 있을 때 한 단 더 보여 준다
                const inner = sub && c.children ? c.children : undefined;
                const innerOn = inner ? activeChild(path, inner) : undefined;
                return (
                  <div key={c.href}>
                    <Link href={c.href} aria-current={sub && !inner ? "page" : undefined}
                      className={`flex items-center gap-2 py-2.5 pl-[50px] pr-[18px] text-[13px] transition ${sub ? (inner ? "font-semibold text-white" : "bg-[#5b5bd6] font-semibold text-white") : "text-[#c9c9e6] hover:bg-white/10"}`}>
                      <span className="truncate">{c.label}</span>
                      {!!c.badge && <span className="ml-auto shrink-0 rounded-full bg-amber-400 px-1.5 text-[11px] font-semibold leading-[18px] text-[#1d1d3a]" aria-label={`${c.badge}건`}>{c.badge > 99 ? "99+" : c.badge}</span>}
                      {c.soon && <span className="ml-auto shrink-0 bg-white/10 px-1.5 text-[11px] text-[#9a9ac4]">준비 중</span>}
                    </Link>
                    {inner?.map((g) => (
                      <Link key={g.href} href={g.href} aria-current={g.href === innerOn ? "page" : undefined}
                        className={`flex items-center gap-2 py-2 pl-[66px] pr-[18px] text-[12.5px] transition ${g.href === innerOn ? "bg-[#5b5bd6] font-semibold text-white" : "text-[#a9a9d0] hover:bg-white/10"}`}>
                        <span className="truncate">{g.label}</span>
                      </Link>
                    ))}
                  </div>
                );
              })}
            </div>
          );
        })}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-[60px] shrink-0 items-center gap-3 border-b border-ink-300 bg-surface px-8">
          {/* 관제 센터는 여러 고객사를 보는 AXcellent 운영팀 화면이라 특정 회사 이름·사내망을 붙이지 않는다 */}
          {matchPath("/control", path) ? (
            <span className="shrink-0 text-[15px] font-semibold">AXcellent <span className="font-normal text-ink-500">관제 센터</span></span>
          ) : (
            /* 지금 연결된 고객사 이름. 연결을 끊으면 with 뒤를 비운다 (demo/company.ts) */
            company ? (
              <>
                <span className="shrink-0 text-[15px] font-semibold">AXcellent <span className="font-normal text-ink-500">with</span> <CompanyBrand name={company.name} siteUrl={company.siteUrl} /></span>
                <span className="chip hidden bg-emerald-50 text-emerald-700 xl:inline-flex" title="허용된 IP에서만 열립니다. 허용 목록 밖의 접근은 막혀 있습니다">접속 범위 허용 IP</span>
              </>
            ) : (
              <>
                <span className="shrink-0 text-[15px] font-semibold">AXcellent</span>
                <span className="chip hidden bg-ink-100 text-ink-500 xl:inline-flex">연결된 고객사 없음</span>
              </>
            )
          )}
          <div className="flex-1" />
          <div className="flex shrink-0 items-center gap-4 text-ink-700">
            <AccountSwitcher />
          </div>
        </header>
        {supporting && (
          <div className="flex shrink-0 flex-wrap items-center gap-3 bg-brand-600 px-8 py-2 text-sm !text-white" role="status">
            <span className="font-semibold">AXcellent 운영자로 {company?.name} 보는 중</span>
            <span className="chip bg-white/15 !text-white">보기 전용</span>
            <button type="button" onClick={leaveSupport} className="ml-auto bg-white px-3 py-1 text-xs font-semibold text-brand-700 hover:bg-brand-50">나가기</button>
          </div>
        )}
        <main className="relative min-h-0 flex-1 overflow-y-auto pt-6">
          {/* 보기 전용: fieldset disabled가 안의 버튼·입력을 모두 끈다. 링크(화면 이동)는 그대로 된다 */}
          {viewOnly ? <fieldset disabled className="contents">{children}</fieldset> : children}
        </main>
      </div>
    </div>
  );
}

/** 머리글의 연결된 회사 이름. 사이트 주소가 있으면 이름을 누르면 회사 사이트가 새 탭으로 열리고, 오른쪽에 사이트 파비콘을 띄운다 */
function CompanyBrand({ name, siteUrl }: { name: string; siteUrl?: string }) {
  const [icon, setIcon] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    setIcon(null)
    if (siteUrl) void findSiteIconOrNull(siteUrl).then((found) => { if (live) setIcon(found) })
    return () => { live = false }
  }, [siteUrl])
  const mark = icon && <img src={icon} alt="" aria-hidden="true" className="ml-1.5 inline h-4 w-4 rounded-sm align-[-2px]" />
  if (!siteUrl) return <>{name}</>
  return (
    <a href={siteUrl} target="_blank" rel="noopener noreferrer" title={`${name} 사이트 열기`} className="hover:underline">
      {name}{mark}
    </a>
  )
}
