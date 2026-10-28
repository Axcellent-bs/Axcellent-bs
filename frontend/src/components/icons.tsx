import type { SVGProps } from "react";

// Minimal stroke icon set (24px grid, currentColor) so no icon dependency is needed.
type P = SVGProps<SVGSVGElement> & { size?: number };

function Icon({ size = 18, children, ...rest }: P & { children: React.ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...rest}>
      {children}
    </svg>
  );
}

export const IconDashboard = (p: P) => <Icon {...p}><rect x="3" y="3" width="7" height="9" rx="2" /><rect x="14" y="3" width="7" height="5" rx="2" /><rect x="14" y="12" width="7" height="9" rx="2" /><rect x="3" y="16" width="7" height="5" rx="2" /></Icon>;
export const IconChat = (p: P) => <Icon {...p}><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1.1-4.4A8 8 0 1 1 21 12Z" /></Icon>;
export const IconPlan = (p: P) => <Icon {...p}><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></Icon>;
export const IconBook = (p: P) => <Icon {...p}><path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2V5Z" /><path d="M4 19a2 2 0 0 1 2-2h13" /></Icon>;
export const IconMail = (p: P) => <Icon {...p}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></Icon>;
export const IconPlus = (p: P) => <Icon {...p}><path d="M12 5v14M5 12h14" /></Icon>;
export const IconDownload = (p: P) => <Icon {...p}><path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14" /></Icon>;
export const IconFilter = (p: P) => <Icon {...p}><path d="M4 6h16M7 12h10M10 18h4" /></Icon>;
export const IconSearch = (p: P) => <Icon {...p}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></Icon>;
export const IconBell = (p: P) => <Icon {...p}><path d="M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8" /><path d="M10 20a2 2 0 0 0 4 0" /></Icon>;
export const IconSun = (p: P) => <Icon {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></Icon>;
export const IconMoon = (p: P) => <Icon {...p}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" /></Icon>;
export const IconAuto = (p: P) => <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M12 3v18" /><path d="M12 3a9 9 0 0 1 0 18" fill="currentColor" stroke="none" /></Icon>;
export const IconOffice = (p: P) => <Icon {...p}><rect x="3" y="11" width="18" height="10" rx="2" /><path d="M7 11V8a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v3" /><path d="M8 15h2M14 15h2" /><circle cx="12" cy="3.5" r="1.5" /></Icon>;
export const IconBolt = (p: P) => <Icon {...p}><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" /></Icon>;
export const IconClock = (p: P) => <Icon {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></Icon>;
export const IconInbox = (p: P) => <Icon {...p}><path d="M3 13h5l2 3h4l2-3h5" /><path d="M5 5h14l2 8v6H3v-6l2-8Z" /></Icon>;
export const IconShield = (p: P) => <Icon {...p}><path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3Z" /><path d="m9 12 2 2 4-4" /></Icon>;
export const IconUsers = (p: P) => <Icon {...p}><circle cx="9" cy="8" r="3.2" /><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" /><path d="M16 5.2a3.2 3.2 0 0 1 0 5.6M18 14.4c1.8.8 3 2.6 3 5.6" /></Icon>;
export const IconMegaphone = (p: P) => <Icon {...p}><path d="M3 10v4h3l8 4V6L6 10H3Z" /><path d="M18 9a4 4 0 0 1 0 6" /></Icon>;
export const IconArrowIn = (p: P) => <Icon {...p}><path d="M17 7 7 17M7 9v8h8" /></Icon>;
export const IconArrowOut = (p: P) => <Icon {...p}><path d="M7 17 17 7M9 7h8v8" /></Icon>;
export const IconChevronRight = (p: P) => <Icon {...p}><path d="m9 6 6 6-6 6" /></Icon>;
export const IconTable = (p: P) => <Icon {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M9 4v16" /></Icon>;
export const IconLayout = (p: P) => <Icon {...p}><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M9 3v18M9 12h12" /></Icon>;
export const IconPower = (p: P) => <Icon {...p}><path d="M12 3v8" /><path d="M6.3 6.3a8 8 0 1 0 11.4 0" /></Icon>;
export const IconCart = (p: P) => <Icon {...p}><circle cx="9" cy="20" r="1.5" /><circle cx="18" cy="20" r="1.5" /><path d="M2 3h3l2.6 12.2a1 1 0 0 0 1 .8h9.3a1 1 0 0 0 1-.8L21 7H6" /></Icon>;
export const IconUser = (p: P) => <Icon {...p}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></Icon>;
export const IconStar = (p: P) => <Icon {...p}><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9L12 3Z" /></Icon>;
export const IconChart = (p: P) => <Icon {...p}><path d="M3 3v18h18" /><path d="M8 17v-5M13 17V8M18 17v-8" /></Icon>;
export const IconCode = (p: P) => <Icon {...p}><path d="m8 7-5 5 5 5M16 7l5 5-5 5M14 4l-4 16" /></Icon>;
export const IconGear = (p: P) => <Icon {...p}><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1" /></Icon>;
export const IconBox = (p: P) => <Icon {...p}><path d="m3 7 9-4 9 4v10l-9 4-9-4V7Z" /><path d="m3 7 9 4 9-4M12 11v10" /></Icon>;
export const IconReceipt = (p: P) => <Icon {...p}><path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" /><path d="M9 8h6M9 12h6" /></Icon>;
export const IconRefresh = (p: P) => <Icon {...p}><path d="M20 11a8 8 0 0 0-14.5-4M4 13a8 8 0 0 0 14.5 4" /><path d="M5 3v4h4M19 21v-4h-4" /></Icon>;
export const IconCheck = (p: P) => <Icon {...p}><path d="m5 12 5 5L20 7" /></Icon>;
export const IconAlert = (p: P) => <Icon {...p}><path d="M12 3 2 20h20L12 3Z" /><path d="M12 10v4M12 17.5v.01" /></Icon>;
export const IconTruck = (p: P) => <Icon {...p}><path d="M2 6h11v10H2zM13 9h4l3 3v4h-7" /><circle cx="7" cy="18" r="1.8" /><circle cx="17" cy="18" r="1.8" /></Icon>;
export const IconAgents = (p: P) => <Icon {...p}><path d="M4 6a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H9l-3 2.5V12H6a2 2 0 0 1-2-2V6Z" /><path d="M15 9h3a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2v2.5L15 17h-3a2 2 0 0 1-2-2v-1" /></Icon>;
