import { Moon, Sun } from "lucide-react";
import { useTheme } from "../lib/theme";

export function ThemeToggle({ className = "" }: { className?: string }) {
  const { theme, toggleTheme } = useTheme();
  const nextLabel = theme === "light" ? "切换到暗色主题" : "切换到浅色主题";
  return (
    <button className={`button button-secondary !h-10 !min-h-10 !w-10 !p-0 ${className}`} type="button" onClick={toggleTheme} title={nextLabel} aria-label={nextLabel}>
      {theme === "light" ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
    </button>
  );
}
