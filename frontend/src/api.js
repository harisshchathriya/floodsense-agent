const API_URL = import.meta.env.VITE_FLOODSENSE_API_URL || "http://127.0.0.1:8000";

export async function apiGet(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map((item) => `${item.loc?.join(".")}: ${item.msg}`).join("; ")
      : payload.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return payload;
}

export { API_URL };
