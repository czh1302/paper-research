import { BookOpen, FlaskConical, LogOut, Plus, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { requireSupabase } from "../lib/supabase";
import { ThemeToggle } from "./ThemeToggle";

function navClass({ isActive }: { isActive: boolean }) {
  return `button !min-h-10 !px-3 ${isActive ? "border-accent/25 bg-accent/10 text-accent-strong" : "button-secondary"}`;
}

export function Layout({ children, email, isAdmin = false }: { children: ReactNode; email?: string; isAdmin?: boolean }) {
  return (
    <div className="relative min-h-screen bg-canvas text-content">
      <header className="no-print sticky top-0 z-20 border-b border-line bg-surface/90 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <Link to="/" className="flex min-w-0 items-center gap-3 rounded-lg">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary shadow-sm"><BookOpen className="h-5 w-5 text-primary-foreground" /></span>
            <span className="min-w-0"><span className="block truncate text-sm font-semibold tracking-tight text-content">Research Atlas</span><span className="hidden font-mono text-[9px] tracking-[.14em] text-faint sm:block">EVIDENCE, NOT HYPE</span></span>
          </Link>
          <nav className="flex shrink-0 items-center gap-1.5 sm:gap-2" aria-label="主导航">
            {email && <NavLink className={navClass} end to="/"><FlaskConical className="h-4 w-4" /><span className="hidden lg:inline">任务</span></NavLink>}
            {isAdmin && <NavLink className={navClass} to="/admin"><ShieldCheck className="h-4 w-4" /><span className="hidden lg:inline">管理</span></NavLink>}
            {email && <NavLink className={({ isActive }) => `button button-primary !min-h-10 !px-3 ${isActive ? "ring-2 ring-accent/20" : ""}`} to="/new"><Plus className="h-4 w-4" /><span className="hidden sm:inline">新建分析</span></NavLink>}
            <ThemeToggle />
            {email && <button className="button button-secondary !h-10 !min-h-10 !w-10 !p-0" title={`退出 ${email}`} aria-label="退出登录" onClick={() => requireSupabase().auth.signOut()}><LogOut className="h-4 w-4" /></button>}
          </nav>
        </div>
      </header>
      <main className="relative mx-auto max-w-7xl px-4 py-8 sm:px-6 sm:py-10">{children}</main>
      <footer className="no-print relative mt-16 border-t border-line px-5 py-8 text-center text-xs text-muted">
        PDF parsing powered by <a className="font-medium text-accent-strong hover:underline" href="https://github.com/opendatalab/MinerU" target="_blank" rel="noreferrer">MinerU</a> · 检索结果不构成绝对新颖性证明
      </footer>
    </div>
  );
}
