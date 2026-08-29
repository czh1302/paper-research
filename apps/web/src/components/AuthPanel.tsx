import { ArrowRight, LockKeyhole } from "lucide-react";
import { FormEvent, useCallback, useState } from "react";
import { requireSupabase } from "../lib/supabase";
import { TurnstileWidget } from "./TurnstileWidget";
import { Button } from "./ui/button";
import { ThemeToggle } from "./ThemeToggle";

export function AuthPanel() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSignup, setIsSignup] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [captchaToken, setCaptchaToken] = useState("");
  const [captchaRevision, setCaptchaRevision] = useState(0);
  const onCaptcha = useCallback((token: string) => setCaptchaToken(token), []);

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage("");
    const client = requireSupabase();
    if (!captchaToken) {
      setBusy(false); setMessage("请先完成人机验证。 / Complete the CAPTCHA first."); return;
    }
    const result = isSignup
      ? await client.auth.signUp({ email, password, options: { captchaToken } })
      : await client.auth.signInWithPassword({ email, password, options: { captchaToken } });
    setCaptchaToken("");
    setCaptchaRevision((value) => value + 1);
    setBusy(false);
    if (result.error) setMessage(result.error.message);
    else if (isSignup) setMessage("验证邮件已发送，请先完成邮箱验证。 / Check your verification email.");
  }

  return (
    <div className="relative mx-auto grid min-h-screen w-full min-w-0 max-w-6xl items-center gap-10 overflow-x-hidden py-20 lg:grid-cols-[1.12fr_.88fr] lg:gap-16 lg:overflow-visible">
      <ThemeToggle className="fixed right-4 top-4 z-20 sm:right-6 sm:top-5" />
      <section className="relative min-w-0">
        <div className="absolute -left-20 -top-24 -z-10 h-72 w-72 rounded-full bg-accent/[.08] blur-3xl" />
        <p className="eyebrow">Computer Science Research Intelligence</p>
        <h1 className="mt-5 max-w-2xl break-words text-4xl font-semibold leading-[1.1] tracking-[-.035em] text-content sm:text-5xl md:text-6xl">从论文问题，到可验证的研究空白。</h1>
        <p className="mt-6 max-w-xl break-words text-lg leading-8 text-muted">把 PDF 转化为形式化 Problem Statement，跨学术图谱和开放网络检索证据，再生成可追溯的比较报告。</p>
        <div className="mt-9 flex flex-wrap gap-3 text-xs font-medium text-muted"><span className="rounded-full border border-line bg-surface px-3 py-2">中英双语</span><span className="rounded-full border border-line bg-surface px-3 py-2">单篇 / 多篇</span><span className="rounded-full border border-line bg-surface px-3 py-2">Evidence-linked</span></div>
      </section>
      <form className="panel min-w-0 overflow-hidden p-6 sm:p-8" onSubmit={submit}>
        <span className="grid h-11 w-11 place-items-center rounded-xl bg-accent/10"><LockKeyhole className="h-5 w-5 text-accent-strong" /></span>
        <h2 className="mt-5 text-2xl font-semibold tracking-tight text-content">{isSignup ? "创建研究账户" : "继续你的研究"}</h2>
        <p className="mt-2 text-sm text-muted">{isSignup ? "Create an account with email verification." : "Sign in to access private analyses."}</p>
        <label className="mt-7 block"><span className="label">邮箱 / Email</span><input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="mt-4 block"><span className="label">密码 / Password</span><input className="input" type="password" minLength={8} required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <div className="mt-5"><TurnstileWidget key={captchaRevision} onToken={onCaptcha} /></div>
        {message && <p className="mt-4 rounded-lg border border-warning/25 bg-warning/[.08] p-3 text-sm text-warning">{message}</p>}
        <Button className="mt-6 w-full" disabled={busy}>{busy ? "处理中…" : isSignup ? "注册" : "登录"}<ArrowRight className="h-4 w-4" /></Button>
        <button className="mt-4 w-full rounded-lg py-2 text-sm text-muted transition hover:bg-subtle hover:text-accent-strong" type="button" onClick={() => { setIsSignup(!isSignup); setCaptchaToken(""); setCaptchaRevision((value) => value + 1); setMessage(""); }}>{isSignup ? "已有账户？登录" : "没有账户？注册"}</button>
      </form>
    </div>
  );
}
