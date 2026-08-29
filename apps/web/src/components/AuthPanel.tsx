import { ArrowRight, LockKeyhole } from "lucide-react";
import { FormEvent, useCallback, useState } from "react";
import { requireSupabase } from "../lib/supabase";
import { TurnstileWidget } from "./TurnstileWidget";
import { Button } from "./ui/button";

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
    <div className="mx-auto grid min-h-[72vh] max-w-5xl items-center gap-12 lg:grid-cols-[1.15fr_.85fr]">
      <section>
        <p className="eyebrow">Computer Science Research Intelligence</p>
        <h1 className="mt-5 max-w-2xl text-5xl font-semibold leading-[1.08] text-paper md:text-6xl">从论文问题，到可验证的研究空白。</h1>
        <p className="mt-6 max-w-xl text-lg leading-8 text-slate-400">把 PDF 转化为形式化 Problem Statement，跨学术图谱和开放网络检索证据，再生成可追溯的比较报告。</p>
        <div className="mt-10 flex flex-wrap gap-3 text-xs text-slate-400"><span className="rounded-full border border-white/10 px-3 py-2">中英双语</span><span className="rounded-full border border-white/10 px-3 py-2">单篇 / 多篇</span><span className="rounded-full border border-white/10 px-3 py-2">Evidence-linked</span></div>
      </section>
      <form className="panel p-7" onSubmit={submit}>
        <LockKeyhole className="h-6 w-6 text-cyan" />
        <h2 className="mt-4 text-2xl font-semibold text-paper">{isSignup ? "创建研究账户" : "继续你的研究"}</h2>
        <p className="mt-2 text-sm text-slate-400">{isSignup ? "Create an account with email verification." : "Sign in to access private analyses."}</p>
        <label className="mt-7 block"><span className="label">邮箱 / Email</span><input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="mt-4 block"><span className="label">密码 / Password</span><input className="input" type="password" minLength={8} required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <div className="mt-5"><TurnstileWidget key={captchaRevision} onToken={onCaptcha} /></div>
        {message && <p className="mt-4 rounded-lg border border-amber/30 bg-amber/10 p-3 text-sm text-amber-100">{message}</p>}
        <Button className="mt-6 w-full" disabled={busy}>{busy ? "处理中…" : isSignup ? "注册" : "登录"}<ArrowRight className="h-4 w-4" /></Button>
        <button className="mt-4 w-full text-sm text-slate-400 hover:text-cyan" type="button" onClick={() => { setIsSignup(!isSignup); setCaptchaToken(""); setCaptchaRevision((value) => value + 1); setMessage(""); }}>{isSignup ? "已有账户？登录" : "没有账户？注册"}</button>
      </form>
    </div>
  );
}
