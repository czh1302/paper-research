/// <reference types="vite/client" />

interface Window {
  turnstile?: {
    render: (element: HTMLElement, options: Record<string, unknown>) => string;
    remove: (widgetId: string) => void;
    reset: (widgetId: string) => void;
  };
}

