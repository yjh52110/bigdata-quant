export const API_BASE_URL = 'http://localhost:8000';

const STORAGE_KEY = 'chainquant_api_key';

export function getApiKey(): string {
  return sessionStorage.getItem(STORAGE_KEY) || '';
}

export function setApiKey(key: string): void {
  sessionStorage.setItem(STORAGE_KEY, key);
}

export function clearApiKey(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}

/**
 * fetch() wrapper that attaches the admin password as X-API-Key. The backend
 * only enforces this header when QUANT_API_KEY is set server-side (see
 * api_key_guard in backend/api_server.py) -- if it's unset, every request
 * still goes through untouched, matching the backend's own open-by-default
 * dev behavior.
 */
export function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers || {});
  const key = getApiKey();
  if (key) headers.set('X-API-Key', key);
  return fetch(`${API_BASE_URL}${path}`, { ...options, headers });
}
