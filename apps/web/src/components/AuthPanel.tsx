import { ArrowLeft, ArrowRight, LockKeyhole, Mail } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { authRedirectUrl, friendlyAuthError } from "../lib/auth";
import { useLanguage } from "../lib/language";
import { requireSupabase } from "../lib/supabase";
import { GithubButton } from "./GithubButton";
import { LanguageToggle } from "./LanguageToggle";
import { ThemeToggle } from "./ThemeToggle";
import { TurnstileWidget } from "./TurnstileWidget";
import { Button } from "./ui/button";

type AuthMode = "signin" | "signup" | "forgot";

export function AuthPanel({ initialMode = "signin", initialMessage = "" }: { initialMode?: AuthMode; initialMessage?: string }) {
  const navigate = useNavigate();
  const { language, text } = useLanguage();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<AuthMode>(initialMode);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(initialMessage);
  const [registeredEmail, setRegisteredEmail] = useState(false);
  const [captchaToken, setCaptchaToken] = useState("");
  const [captchaRevision, setCaptchaRevision] = useState(0);
  const onCaptcha = useCallback((token: string) => setCaptchaToken(token), []);
  useEffect(() => { setMode(initialMode); setMessage(initialMessage); }, [initialMessage, initialMode]);

  function resetCaptcha() {
    setCaptchaToken("");
    setCaptchaRevision((value) => value + 1);
  }

  function switchMode(next: AuthMode) {
    setMode(next); setMessage(""); setPassword(""); setRegisteredEmail(false); resetCaptcha();
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage(""); setRegisteredEmail(false);
    if (!captchaToken) {
      setBusy(false); setMessage(text("请先完成人机验证。", "Complete the CAPTCHA first.")); return;
    }
    const client = requireSupabase();
    if (mode === "forgot") {
      const { error } = await client.auth.resetPasswordForEmail(email, { redirectTo: authRedirectUrl("recovery"), captchaToken });
      resetCaptcha(); setBusy(false);
      setMessage(error ? friendlyAuthError(error.message, language) : text("如果该邮箱对应账户，密码重置邮件已经发送。", "If an account exists for this email, a password reset message has been sent."));
      return;
    }
    const result = mode === "signup"
      ? await client.auth.signUp({ email, password, options: { captchaToken, emailRedirectTo: authRedirectUrl("confirm") } })
      : await client.auth.signInWithPassword({ email, password, options: { captchaToken } });
    resetCaptcha(); setBusy(false);
    if (result.error) setMessage(friendlyAuthError(result.error.message, language));
    else if (mode === "signup" && Array.isArray(result.data.user?.identities) && result.data.user.identities.length === 0) {
      setRegisteredEmail(true);
      setMessage(text("该邮箱已注册，请直接登录或重置密码。", "This email is already registered. Sign in or reset your password."));
    }
    else if (mode === "signup") setMessage(text("验证邮件已发送，请先完成邮箱验证。", "Check your verification email."));
    else navigate("/new", { replace: true });
  }

  async function resendConfirmation() {
    if (!email || !captchaToken || busy) {
      setMessage(text("请填写邮箱并完成人机验证。", "Enter your email and complete the CAPTCHA.")); return;
    }
    setBusy(true); setMessage("");
    const { error } = await requireSupabase().auth.resend({ type: "signup", email, options: { emailRedirectTo: authRedirectUrl("confirm"), captchaToken } });
    resetCaptcha(); setBusy(false);
    setMessage(error ? friendlyAuthError(error.message, language) : text("新的验证邮件已发送。", "A new verification email has been sent."));
  }

  const signup = mode === "signup";
  const forgot = mode === "forgot";
  return (
    <div className="relative mx-auto grid min-h-screen w-full min-w-0 max-w-6xl items-center gap-10 overflow-x-hidden py-20 lg:grid-cols-[1.12fr_.88fr] lg:gap-16 lg:overflow-visible">
      <div className="fixed right-4 top-4 z-20 flex gap-2 sm:right-6 sm:top-5"><LanguageToggle/><ThemeToggle/><GithubButton/></div>
      <section className="relative min-w-0">
        <div className="absolute -left-20 -top-24 -z-10 h-72 w-72 rounded-full bg-accent/[.08] blur-3xl" />
        <p className="eyebrow">{text("计算机科学研究智能", "Computer Science Research Intelligence")}</p>
        <h1 className="mt-5 max-w-2xl break-words text-4xl font-semibold leading-[1.1] tracking-[-.035em] text-content sm:text-5xl md:text-6xl">{text("从论文问题，到可验证的研究空白。", "From paper questions to testable research gaps.")}</h1>
        <p className="mt-6 max-w-xl break-words text-lg leading-8 text-muted">{text("把 PDF 转化为形式化问题定义，跨学术图谱和开放网络检索证据，再生成可追溯的比较报告。", "Turn PDFs into formal problem definitions, retrieve evidence across scholarly graphs and the open web, and produce traceable comparison reports.")}</p>
        <div className="mt-9 flex flex-wrap gap-3 text-xs font-medium text-muted"><span className="rounded-full border border-line bg-surface px-3 py-2">{text("中英双语", "Chinese / English")}</span><span className="rounded-full border border-line bg-surface px-3 py-2">{text("深度论文调研", "In-depth paper research")}</span><span className="rounded-full border border-line bg-surface px-3 py-2">{text("证据可追溯", "Evidence-linked")}</span></div>
      </section>
      <form className="panel min-w-0 overflow-hidden p-6 sm:p-8" onSubmit={submit}>
        <span className="grid h-11 w-11 place-items-center rounded-xl bg-accent/10">{forgot ? <Mail className="h-5 w-5 text-accent-strong"/> : <LockKeyhole className="h-5 w-5 text-accent-strong" />}</span>
        <h2 className="mt-5 text-2xl font-semibold tracking-tight text-content">{forgot ? text("重置密码", "Reset your password") : signup ? text("创建研究账户", "Create a research account") : text("继续你的研究", "Continue your research")}</h2>
        <p className="mt-2 text-sm text-muted">{forgot ? text("输入注册邮箱，我们会发送安全重置链接。", "Enter your account email to receive a secure reset link.") : signup ? text("使用邮箱验证创建账户。", "Create an account with email verification.") : text("登录以访问你的私有分析。", "Sign in to access private analyses.")}</p>
        <label className="mt-7 block"><span className="label">{text("邮箱", "Email")}</span><input className="input" type="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        {!forgot && <label className="mt-4 block"><span className="label">{text("密码", "Password")}</span><input className="input" type="password" minLength={8} required value={password} onChange={(event) => setPassword(event.target.value)} /></label>}
        {!forgot && !signup && <button className="mt-2 text-sm text-accent-strong hover:underline" type="button" onClick={() => switchMode("forgot")}>{text("忘记密码？", "Forgot password?")}</button>}
        <div className="mt-5"><TurnstileWidget key={captchaRevision} onToken={onCaptcha} /></div>
        {message && <p className="mt-4 rounded-lg border border-warning/25 bg-warning/[.08] p-3 text-sm text-warning">{message}</p>}
        {registeredEmail && <div className="mt-3 grid grid-cols-2 gap-2"><button className="button button-secondary justify-center" type="button" onClick={() => switchMode("signin")}>{text("直接登录", "Sign in")}</button><button className="button button-secondary justify-center" type="button" onClick={() => switchMode("forgot")}>{text("重置密码", "Reset password")}</button></div>}
        <Button className="mt-6 w-full" disabled={busy}>{busy ? text("处理中…", "Working…") : forgot ? text("发送重置邮件", "Send reset email") : signup ? text("注册", "Sign up") : text("登录", "Sign in")}<ArrowRight className="h-4 w-4" /></Button>
        {signup && <button className="mt-3 w-full rounded-lg py-2 text-sm text-accent-strong hover:bg-subtle" type="button" disabled={busy} onClick={() => void resendConfirmation()}>{text("重新发送验证邮件", "Resend verification email")}</button>}
        {forgot ? <button className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg py-2 text-sm text-muted hover:bg-subtle hover:text-content" type="button" onClick={() => switchMode("signin")}><ArrowLeft className="h-4 w-4"/>{text("返回登录", "Back to sign in")}</button> : <button className="mt-4 w-full rounded-lg py-2 text-sm text-muted transition hover:bg-subtle hover:text-accent-strong" type="button" onClick={() => switchMode(signup ? "signin" : "signup")}>{signup ? text("已有账户？登录", "Already have an account? Sign in") : text("没有账户？注册", "New here? Create an account")}</button>}
      </form>
    </div>
  );
}
