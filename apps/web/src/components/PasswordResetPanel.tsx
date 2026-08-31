import { KeyRound } from "lucide-react";
import { FormEvent, useState } from "react";
import { clearAuthLink, friendlyAuthError } from "../lib/auth";
import { useLanguage } from "../lib/language";
import { requireSupabase } from "../lib/supabase";
import { GithubButton } from "./GithubButton";
import { LanguageToggle } from "./LanguageToggle";
import { ThemeToggle } from "./ThemeToggle";
import { Button } from "./ui/button";

export function PasswordResetPanel({ onComplete }: { onComplete: (message: string) => void }) {
  const { language, text } = useLanguage();
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault(); setMessage("");
    if (password.length < 8) { setMessage(text("新密码至少需要 8 位。", "The new password must be at least 8 characters.")); return; }
    if (password !== confirmation) { setMessage(text("两次输入的密码不一致。", "The passwords do not match.")); return; }
    setBusy(true);
    const client = requireSupabase();
    const { error } = await client.auth.updateUser({ password });
    if (error) { setBusy(false); setMessage(friendlyAuthError(error.message, language)); return; }
    await client.auth.signOut({ scope: "global" });
    clearAuthLink();
    onComplete(text("密码已更新，请使用新密码登录。", "Password updated. Sign in with your new password."));
  }

  return <main className="relative grid min-h-screen place-items-center bg-canvas px-5 py-20">
    <div className="fixed right-4 top-4 z-20 flex gap-2 sm:right-6 sm:top-5"><LanguageToggle/><ThemeToggle/><GithubButton/></div>
    <form className="panel w-full max-w-md p-6 sm:p-8" onSubmit={submit}>
      <span className="grid h-11 w-11 place-items-center rounded-xl bg-accent/10"><KeyRound className="h-5 w-5 text-accent-strong"/></span>
      <h1 className="mt-5 text-2xl font-semibold text-content">{text("设置新密码", "Set a new password")}</h1>
      <p className="mt-2 text-sm leading-6 text-muted">{text("设置成功后，所有设备将退出登录。", "After the update, all devices will be signed out.")}</p>
      <label className="mt-7 block"><span className="label">{text("新密码", "New password")}</span><input className="input" type="password" minLength={8} autoComplete="new-password" required value={password} onChange={(event) => setPassword(event.target.value)}/></label>
      <label className="mt-4 block"><span className="label">{text("再次输入新密码", "Confirm new password")}</span><input className="input" type="password" minLength={8} autoComplete="new-password" required value={confirmation} onChange={(event) => setConfirmation(event.target.value)}/></label>
      {message && <p className="mt-4 rounded-lg border border-warning/25 bg-warning/[.08] p-3 text-sm text-warning">{message}</p>}
      <Button className="mt-6 w-full" disabled={busy}>{busy ? text("正在更新…", "Updating…") : text("更新密码", "Update password")}</Button>
    </form>
  </main>;
}
