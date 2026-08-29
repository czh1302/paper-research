import { Languages } from "lucide-react";
import { useLanguage } from "../lib/language";

export function LanguageToggle({ className = "" }: { className?: string }) {
  const { language, toggleLanguage, text } = useLanguage();
  const label = text("切换到 English", "Switch to Chinese");
  return (
    <button
      className={`button button-secondary !h-10 !min-h-10 !px-3 ${className}`}
      type="button"
      onClick={toggleLanguage}
      title={label}
      aria-label={label}
    >
      <Languages className="h-4 w-4" />
      <span className="text-xs font-semibold">{language === "zh" ? "EN" : "中文"}</span>
    </button>
  );
}
