import { Link } from "@/lib/nav";
import { usePathname } from "@/lib/nav";
import { useState, type ComponentType, type ReactNode } from "react";
import { AccountSwitcher } from "@/demo/AccountSwitcher";
import { canOpen, homeOf, servicesOf, useSession } from "@/demo/session";
import { useCompany } from "@/demo/company";
import { AGENTS, activeChild, matchPath, useEnabledServices, type Service, type SubItem } from "@/demo/services";
import { IconAgents, IconBell, IconBolt, IconBox, IconChart, IconDashboard, IconLayout, IconOffice, IconSearch } from "../icons";

// 메뉴: AI가 상위 탭이고, 그 AI가 맡은 서비스가 하위 탭이다. 서비스 안 화면(예: 고객 문의의 상담 콘솔)은 그 아래 한 단 더.
// AI 7개는 모든 직원 메뉴에 보인다(회사가 서비스 고르기에서 끈 서비스만 빠짐). DejaFix 서비스는 배정된 계정에만 (src/demo/session.tsx servicesOf).
// AI가 맡지 않은 서비스(DejaFix 장애 대응·PC·서버 연결)와 공용 화면은 구분선 아래에 둔다.
type SubNav = { href: string; label: string; soon?: boolean; match: string[]; children?: SubItem[] };
type NavItem = { href: string; label: string; Icon: ComponentType<{ size?: number }>; logo?: string; children?: SubNav[]; match: string[]; divider?: boolean };

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

const SHARED_NAV: NavItem[] = [
  { href: "/agents/chat", label: "AI 대화방", Icon: IconAgents, match: ["/agents/chat"] },
  { href: "/agents/office", label: "AI 오피스", Icon: IconOffice, match: ["/agents/office"] },
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

const SYSTEM_NAV: NavItem[] = [
  { href: "/system/connect", label: "AXcellent 연결", Icon: IconBolt, match: ["/system/connect"] },
  { href: "/system/services", label: "서비스 고르기", Icon: IconDashboard, match: ["/system/services"] },
  { href: "/system/hosts", label: "사내 PC 연결", Icon: IconBox, match: ["/system/hosts"] },
  { href: "/system/integrations", label: "회사 서비스 연결", Icon: IconLayout, match: ["/system/integrations"] },
];

function useNav(): NavItem[] {
  const { account } = useSession();
  const enabled = useEnabledServices();
  const company = useCompany();
  if (!account) return [];
  if (account.operator) return CONTROL_NAV;
  // 회사 연결 전에는 AXcellent 연결만 보인다
  if (!company) return account.admin ? SYSTEM_NAV.filter((n) => n.href === "/system/connect") : [];
  const services = servicesOf(account, enabled);
  const agents = agentNav(services);
  const rest = [...services.filter((s) => !s.agent).map(serviceNav), ...SHARED_NAV];
  if (agents.length) rest[0] = { ...rest[0], divider: true };
  // 고객사 시스템 관리자는 관리 메뉴를 맨 위에
  return [...(account.admin ? SYSTEM_NAV : []), ...agents, ...rest];
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
  const nav = useNav();
  const home = account ? homeOf(account) : "/";
  const company = useCompany();
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
              <img src={`${import.meta.env.BASE_URL}axcellent-wordmark.png`} alt="AXcellent" className="h-7 w-auto" />
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
              <Link href={href} title={collapsed ? label : undefined} aria-label={label} aria-current={on && !subs ? "page" : undefined}
                className={`flex items-center gap-3.5 py-[13px] text-sm font-semibold transition ${collapsed ? "justify-center" : "px-[18px]"} ${on ? (open ? "text-white" : "bg-[#5b5bd6] text-white") : "hover:bg-white/10"}`}>
                <NavIcon item={item} size={item.logo ? 22 : 18} />
                {!collapsed && <span className="truncate">{label}</span>}
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
                <span className="shrink-0 text-[15px] font-semibold">AXcellent <span className="font-normal text-ink-500">with</span> {company.name}</span>
                <span className="chip hidden bg-emerald-50 text-emerald-700 xl:inline-flex" title="사내망에서만 열리고 외부 접근은 막혀 있습니다">사내망 {company.network}</span>
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
            {account && canOpen(account, "/console") && <Link href="/console" title="문의 찾기" aria-label="문의 찾기"><IconSearch size={20} /></Link>}
            <IconBell size={20} />
            <AccountSwitcher />
          </div>
        </header>
        <main className="relative min-h-0 flex-1 overflow-y-auto pt-6">{children}</main>
      </div>
    </div>
  );
}
