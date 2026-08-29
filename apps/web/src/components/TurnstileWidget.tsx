import { useEffect, useRef } from "react";

export function TurnstileWidget({ onToken }: { onToken: (token: string) => void }) {
  const container = useRef<HTMLDivElement>(null);
  const siteKey = import.meta.env.VITE_TURNSTILE_SITE_KEY as string | undefined;
  useEffect(() => {
    if (!siteKey) { onToken("local-dev-token"); return; }
    let widgetId: string | undefined;
    let cancelled = false;
    const interval = window.setInterval(() => {
      if (!cancelled && container.current && window.turnstile) {
        window.clearInterval(interval);
        widgetId = window.turnstile.render(container.current, { sitekey: siteKey, callback: onToken, "expired-callback": () => onToken("") });
      }
    }, 100);
    return () => { cancelled = true; window.clearInterval(interval); if (widgetId && window.turnstile) window.turnstile.remove(widgetId); };
  }, [siteKey, onToken]);
  return <div ref={container}>{!siteKey && <span className="text-xs text-amber">Turnstile local development mode</span>}</div>;
}

