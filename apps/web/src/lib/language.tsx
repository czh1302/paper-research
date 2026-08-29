import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from "react";

export type Language = "zh" | "en";

const STORAGE_KEY = "paper-research-language";

interface LanguageContextValue {
  language: Language;
  setLanguage: (language: Language) => void;
  toggleLanguage: () => void;
  text: (zh: string, en: string) => string;
  formatDate: (value: string | Date) => string;
  formatNumber: (value: number) => string;
}

const LanguageContext = createContext<LanguageContextValue | null>(null);

function readLanguage(): Language {
  if (typeof window === "undefined") return "zh";
  try { return window.localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "zh"; }
  catch { return "zh"; }
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Language>(readLanguage);

  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
    try { window.localStorage.setItem(STORAGE_KEY, language); } catch { /* Session-only fallback. */ }
  }, [language]);

  const value = useMemo<LanguageContextValue>(() => {
    const locale = language === "zh" ? "zh-CN" : "en-US";
    return {
      language,
      setLanguage,
      toggleLanguage: () => setLanguage((current) => current === "zh" ? "en" : "zh"),
      text: (zh, en) => language === "zh" ? zh : en,
      formatDate: (value) => new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)),
      formatNumber: (value) => new Intl.NumberFormat(locale).format(value),
    };
  }, [language]);

  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  const value = useContext(LanguageContext);
  if (!value) throw new Error("useLanguage must be used within LanguageProvider");
  return value;
}
