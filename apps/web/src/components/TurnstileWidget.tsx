import { useEffect, useRef } from "react";
import { useTheme } from "../lib/theme";

type TurnstileAppearance = "always" | "execute" | "interaction-only";
type TurnstileSize = "normal" | "flexible" | "compact";

export function TurnstileWidget({ onToken, appearance = "always", size = "normal" }: { onToken: (token: string) => void; appearance?: TurnstileAppearance; size?: TurnstileSize }) {
  const container = useRef<HTMLDivElement>(null);
  const { theme } = useTheme();
  const siteKey = import.meta.env.VITE_TURNSTILE_SITE_KEY as string | undefined;
  useEffect(() => {
    onToken("");
    if (!siteKey) { onToken("local-dev-token"); return; }
    let widgetId: string | undefined;
    let cancelled = false;
    const interval = window.setInterval(() => {
      if (!cancelled && container.current && window.turnstile) {
        window.clearInterval(interval);
        widgetId = window.turnstile.render(container.current, {
          sitekey: siteKey,
          appearance,
          size,
          theme,
          language: "auto",
          callback: onToken,
          "expired-callback": () => onToken(""),
          "error-callback": () => onToken(""),
        });
      }
    }, 100);
    return () => { cancelled = true; window.clearInterval(interval); if (widgetId && window.turnstile) window.turnstile.remove(widgetId); };
  }, [appearance, onToken, siteKey, size, theme]);
  return <div className="max-w-full overflow-hidden" ref={container}>{!siteKey && <span className="text-xs text-warning">Turnstile local development mode</span>}</div>;
}
