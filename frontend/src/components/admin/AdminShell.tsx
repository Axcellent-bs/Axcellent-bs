import { Link } from "@/lib/nav";
import { usePathname } from "@/lib/nav";
import { useState, type ComponentType, type ReactNode } from "react";
import { AccountSwitcher } from "@/demo/AccountSwitcher";
import { COMPANY, canOpen, homeOf, servicesOf, useSession } from "@/demo/session";
import { activeChild, matchPath, useEnabledServices, type SubItem } from "@/demo/services";
import { IconAgents, IconBell, IconDashboard, IconLayout, IconOffice, IconSearch } from "../icons";
import { DejaFixTools } from "@/demo/DejaFixTools";

/** DejaFix 장애 대응 화면이면 그 상단바 도구를 바깥 상단바에 둔다 */
const onDejaFix = (path: string) => matchPath("/infra", path);

// 메뉴는 부서가 아니라 서비스다. 이 계정에 배정됐고 회사가 켠 서비스만 보인다 (src/demo/services.ts, session.tsx).
type NavItem = { href: string; label: string; Icon: ComponentType<{ size?: number }>; logo?: string; children?: SubItem[]; match: string[] };

/** 메뉴 아이콘. logo가 있으면 아이콘 대신 그 이미지를 쓴다 (DejaFix) */
function NavIcon({ item, size }: { item: NavItem; size: number }) {
  return item.logo ? <img src={item.logo} alt="" width={size} height={size} className="shrink-0" /> : <item.Icon size={size} />;
}

const SHARED_NAV: NavItem[] = [
  { href: "/agents/chat", label: "AI 대화방", Icon: IconAgents, match: ["/agents/chat"] },
  { href: "/agents/office", label: "AI 오피스", Icon: IconOffice, match: ["/agents/office"] },
];
const SYSTEM_NAV: NavItem[] = [
  { href: "/system/services", label: "서비스 고르기", Icon: IconDashboard, match: ["/system/services"] },
  { href: "/system/integrations", label: "회사 서비스 연결", Icon: IconLayout, match: ["/system/integrations"] },
];

function useNav(): NavItem[] {
  const { account } = useSession();
  const enabled = useEnabledServices();
  if (!account) return [];
  const services = servicesOf(account, enabled).map((s) => ({ href: s.entry, label: s.name, Icon: s.Icon, logo: s.logo, children: s.children, match: s.paths }));
  return [...services, ...SHARED_NAV, ...(account.admin ? SYSTEM_NAV : [])];
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
            <Link href={home} className="-ml-2 flex items-center gap-3" aria-label="내 부서 홈">
              <img src={`${import.meta.env.BASE_URL}axcellent-wordmark.png`} alt="AXcellent" className="h-7 w-auto" />
            </Link>
          )}
        </div>
        {nav.map((item) => {
          const { href, label, children: subs } = item;
          const on = isOn(path, item);
          const open = on && subs && !collapsed;
          const subOn = open ? activeChild(path, subs) : undefined;
          return (
            <div key={href}>
              <Link href={href} title={collapsed ? label : undefined} aria-label={label} aria-current={on && !subs ? "page" : undefined}
                className={`flex items-center gap-3.5 py-[13px] text-sm font-semibold transition ${collapsed ? "justify-center" : "px-[18px]"} ${on ? (open ? "text-white" : "bg-[#5b5bd6] text-white") : "hover:bg-white/10"}`}>
                <NavIcon item={item} size={item.logo ? 22 : 18} />
                {!collapsed && <span className="truncate">{label}</span>}
              </Link>
              {open && subs.map((c) => {
                const sub = c.href === subOn;
                return (
                  <Link key={c.href} href={c.href} aria-current={sub ? "page" : undefined}
                    className={`flex items-center gap-2 py-2.5 pl-[50px] pr-[18px] text-[13px] transition ${sub ? "bg-[#5b5bd6] font-semibold text-white" : "text-[#c9c9e6] hover:bg-white/10"}`}>
                    <span className="truncate">{c.label}</span>
                    {c.soon && <span className="ml-auto shrink-0 bg-white/10 px-1.5 text-[11px] text-[#9a9ac4]">준비 중</span>}
                  </Link>
                );
              })}
            </div>
          );
        })}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-[60px] shrink-0 items-center gap-3 border-b border-ink-300 bg-surface px-8">
          <span className="shrink-0 text-[15px] font-semibold">AXcellent <span className="font-normal text-ink-500">with</span> {COMPANY.name}</span>
          <span className="chip hidden bg-emerald-50 text-emerald-700 xl:inline-flex" title="사내망에서만 열리고 외부 접근은 막혀 있습니다">사내망 {COMPANY.network}</span>
          <div className="flex-1" />
          {onDejaFix(path) && <DejaFixTools />}
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
