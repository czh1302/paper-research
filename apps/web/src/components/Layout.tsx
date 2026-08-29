import { BookOpen, FlaskConical, LogOut, Plus, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { requireSupabase } from "../lib/supabase";

export function Layout({ children, email, isAdmin = false }: { children: ReactNode; email?: string; isAdmin?: boolean }) {
  return (
    <div className="relative min-h-screen">
      <header className="no-print sticky top-0 z-20 border-b border-white/10 bg-ink/85 backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4">
          <Link to="/" className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-xl border border-cyan/40 bg-cyan/10"><BookOpen className="h-5 w-5 text-cyan" /></span>
            <span><span className="block text-sm font-semibold text-paper">Research Atlas</span><span className="block font-mono text-[10px] tracking-widest text-slate-500">EVIDENCE, NOT HYPE</span></span>
          </Link>
          <nav className="flex items-center gap-2">
            <Link className="button button-secondary hidden sm:inline-flex" to="/"><FlaskConical className="h-4 w-4" />任务</Link>
            {isAdmin && <Link className="button button-secondary" to="/admin"><ShieldCheck className="h-4 w-4" /><span className="hidden md:inline">管理</span></Link>}
            <Link className="button button-primary" to="/new"><Plus className="h-4 w-4" />新建分析</Link>
            {email && <button className="button button-secondary" title={email} onClick={() => requireSupabase().auth.signOut()}><LogOut className="h-4 w-4" /><span className="hidden md:inline">退出</span></button>}
          </nav>
        </div>
      </header>
      <main className="relative mx-auto max-w-7xl px-5 py-10">{children}</main>
      <footer className="no-print relative mt-16 border-t border-white/10 px-5 py-8 text-center text-xs text-slate-500">
        PDF parsing powered by <a className="text-cyan hover:underline" href="https://github.com/opendatalab/MinerU" target="_blank" rel="noreferrer">MinerU</a> · 检索结果不构成绝对新颖性证明
      </footer>
    </div>
  );
}
