import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useMediaQuery } from "@/lib/useMediaQuery";

export type Mode = "light" | "dark";
export type Choice = Mode | "system";

const KEY = "theme";

function readStored(): Mode | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null; // storage blocked (private mode, sandboxed iframe)
  }
}

interface ThemeValue {
  mode: Mode; // what is shown right now
  choice: Choice; // what the user picked ("system" = follow the OS)
  setChoice: (c: Choice) => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const prefersDark = useMediaQuery("(prefers-color-scheme: dark)");
  // Only an explicit user pick is stored; null means "follow the OS setting".
  const [picked, setPicked] = useState<Mode | null>(null);

  // Read storage after mount: never during render, so server rendering cannot crash.
  useEffect(() => setPicked(readStored()), []);

  const mode: Mode = picked ?? (prefersDark ? "dark" : "light");

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = mode;
    root.classList.toggle("dark", mode === "dark");
  }, [mode]);

  const setChoice = useCallback((c: Choice) => {
    const next = c === "system" ? null : c;
    setPicked(next);
    try {
      if (next) localStorage.setItem(KEY, next);
      else localStorage.removeItem(KEY);
    } catch {
      /* still applies for this session */
    }
  }, []);

  const value = useMemo<ThemeValue>(() => ({ mode, choice: picked ?? "system", setChoice }), [mode, picked, setChoice]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}

/** Inline script for <head>: applies the theme before first paint (no white flash). */
export const themeInitScript = `try{var t=localStorage.getItem('${KEY}');if(t!=='light'&&t!=='dark')t=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';var r=document.documentElement;r.dataset.theme=t;if(t==='dark')r.classList.add('dark')}catch(e){}`;
