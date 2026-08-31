import type { Language } from "./language";

export type AuthLinkMode = "confirm" | "recovery";

export function authRedirectUrl(mode: AuthLinkMode): string {
  const url = new URL(import.meta.env.BASE_URL || "/", window.location.origin);
  url.searchParams.set("auth", mode);
  url.hash = "";
  return url.toString();
}

export function authLinkIssue(): "expired" | "invalid" | null {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const code = params.get("error_code");
  if (code === "otp_expired") return "expired";
  return params.has("error") ? "invalid" : null;
}

export function isPasswordRecoveryLink(): boolean {
  if (new URLSearchParams(window.location.search).get("auth") === "recovery") return true;
  return new URLSearchParams(window.location.hash.replace(/^#/, "")).get("type") === "recovery";
}

export function clearAuthLink(nextHash = "#/"): void {
  window.history.replaceState({}, "", `${window.location.pathname}${nextHash}`);
}

export function friendlyAuthError(message: string, language: Language): string {
  const normalized = message.toLowerCase();
  const zh = language === "zh";
  if (normalized.includes("invalid login credentials")) return zh ? "邮箱或密码不正确。" : "The email or password is incorrect.";
  if (normalized.includes("email not confirmed")) return zh ? "邮箱尚未验证，请先打开验证邮件。" : "Confirm your email before signing in.";
  if (normalized.includes("already registered") || normalized.includes("already been registered")) return zh ? "该邮箱已注册，请直接登录或重置密码。" : "This email is already registered. Sign in or reset your password.";
  if (normalized.includes("rate limit") || normalized.includes("too many")) return zh ? "请求过于频繁，请稍后再试。" : "Too many requests. Try again later.";
  if (normalized.includes("captcha") || normalized.includes("turnstile")) return zh ? "安全验证失败，请重新验证。" : "Security verification failed. Try again.";
  if (normalized.includes("same password")) return zh ? "新密码不能与当前密码相同。" : "Choose a password different from the current password.";
  return zh ? "操作未完成，请稍后重试。" : "The request could not be completed. Try again later.";
}
