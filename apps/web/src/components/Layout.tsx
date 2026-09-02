import { BookOpen, FlaskConical, LogOut, Plus, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { requireSupabase } from "../lib/supabase";
import { useLanguage } from "../lib/language";
import { LanguageToggle } from "./LanguageToggle";
import { GithubButton } from "./GithubButton";
import { ThemeToggle } from "./ThemeToggle";

function navClass({ isActive }: { isActive: boolean }) {
  return `button !min-h-10 !px-3 ${isActive ? "border-accent/25 bg-accent/10 text-accent-strong" : "button-secondary"}`;
}

export function Layout({ children, email, isAdmin = false, workspace = false }: { children: ReactNode; email?: string; isAdmin?: boolean; workspace?: boolean }) {
  const { text } = useLanguage();
  const location = useLocation();
  const workspaceMode = workspace || /^\/(?:admin\/)?experiments\//.test(location.pathname);
  return (
    <div className={`relative min-h-screen bg-canvas text-content ${workspaceMode ? "layout-workspace" : ""}`}>
      <header className="no-print sticky top-0 z-20 border-b border-line bg-surface/90 backdrop-blur-xl">
        <div className={`mx-auto flex items-center justify-between gap-3 px-4 py-3 sm:px-6 ${workspaceMode ? "max-w-none" : "max-w-7xl"}`}>
          <Link to="/" className="flex min-w-0 items-center gap-3 rounded-lg">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary shadow-sm"><BookOpen className="h-5 w-5 text-primary-foreground" /></span>
            <span className="min-w-0"><span className="block truncate text-sm font-semibold tracking-tight text-content">Research Atlas</span><span className="hidden text-[9px] font-medium tracking-[.14em] text-faint sm:block">{text("证据驱动研究", "EVIDENCE-LED RESEARCH")}</span></span>
          </Link>
          <nav className="flex shrink-0 items-center gap-1.5 sm:gap-2" aria-label={text("主导航", "Main navigation")}>
            {email && <NavLink className={(state) => `${navClass(state)} ${workspaceMode ? "workspace-secondary-nav" : ""}`} end to="/"><FlaskConical className="h-4 w-4" /><span className="hidden lg:inline">{text("任务", "Jobs")}</span></NavLink>}
            {isAdmin && <NavLink className={(state) => `${navClass(state)} ${workspaceMode ? "workspace-secondary-nav" : ""}`} to="/admin"><ShieldCheck className="h-4 w-4" /><span className="hidden lg:inline">{text("管理", "Admin")}</span></NavLink>}
            {email && <NavLink className={({ isActive }) => `button button-primary !min-h-10 !px-3 ${isActive ? "ring-2 ring-accent/20" : ""} ${workspaceMode ? "workspace-secondary-nav" : ""}`} to="/new"><Plus className="h-4 w-4" /><span className="hidden sm:inline">{text("新建分析", "New analysis")}</span></NavLink>}
            <LanguageToggle />
            <ThemeToggle />
            <GithubButton className={workspaceMode ? "workspace-secondary-nav" : ""} />
            {email && <button className="button button-secondary !h-10 !min-h-10 !w-10 !p-0" title={text(`退出 ${email}`, `Sign out ${email}`)} aria-label={text("退出登录", "Sign out")} onClick={() => requireSupabase().auth.signOut({ scope: "local" })}><LogOut className="h-4 w-4" /></button>}
          </nav>
        </div>
      </header>
      <main className={workspaceMode ? "relative min-w-0" : "relative mx-auto max-w-7xl px-4 py-8 sm:px-6 sm:py-10"}>{children}</main>
      {!workspaceMode && <footer className="no-print relative mt-16 border-t border-line px-5 py-8 text-center text-xs text-muted">
        PDF parsing powered by <a className="font-medium text-accent-strong hover:underline" href="https://github.com/opendatalab/MinerU" target="_blank" rel="noreferrer">MinerU</a> · {text("检索结果不构成绝对新颖性证明", "Retrieval results are not proof of absolute novelty")}
      </footer>}
    </div>
  );
}
