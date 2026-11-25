export type BrowserMode = 'launch' | 'attach';

export type BrowserSettings = {
  revision: number;
  local_attach_enabled: boolean;
  default_mode: BrowserMode;
  updated_at: string | null;
  deployment: {
    browser_enabled: boolean;
    local_attach_available: boolean;
    cdp_endpoint_display: string | null;
    entrypoint_ready: boolean;
    chromium_ready: boolean;
    attach_busy: boolean;
    max_concurrent_sessions: number;
    max_actions_per_run: number;
  };
};

export const browserSettingsPath = '/api/browser/settings';

export function effectiveBrowserMode(settings: BrowserSettings | null): BrowserMode {
  return settings?.local_attach_enabled && settings.deployment.local_attach_available
    ? settings.default_mode : 'launch';
}
