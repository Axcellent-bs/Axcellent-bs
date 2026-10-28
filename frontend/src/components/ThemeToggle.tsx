import { IconAuto, IconMoon, IconSun } from "./icons";
import { useTheme, type Choice } from "./ThemeProvider";

const MODES: { value: Choice; title: string; Icon: typeof IconSun }[] = [
  { value: "light", title: "라이트 모드", Icon: IconSun },
  { value: "dark", title: "다크 모드", Icon: IconMoon },
  { value: "system", title: "시스템 설정 따르기", Icon: IconAuto },
];

export function ThemeToggle({ vertical = false }: { vertical?: boolean }) {
  const { choice, setChoice } = useTheme();
  return (
    <div role="radiogroup" aria-label="화면 테마" className={`flex rounded-full bg-ink-100 p-0.5 text-xs ${vertical ? "flex-col" : ""}`}>
      {MODES.map(({ value, title, Icon }) => (
        <button
          key={value}
          role="radio"
          aria-checked={choice === value}
          title={title}
          onClick={() => setChoice(value)}
          className={`flex h-7 min-w-[28px] items-center justify-center rounded-full px-2 font-medium transition ${
            choice === value ? "bg-surface text-ink-900 shadow-sm" : "text-ink-500 hover:text-ink-900"
          }`}
        >
          <Icon size={14} />
        </button>
      ))}
    </div>
  );
}
