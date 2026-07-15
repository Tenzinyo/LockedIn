const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function request(path, options) {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

export const api = {
  getStats: () => request("/api/v1/stats"),
  getAlerts: (status) => request(`/api/v1/alerts${status ? `?status=${status}` : ""}`),
  updateAlertStatus: (id, status) =>
    request(`/api/v1/alerts/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  submitTransaction: (payload) =>
    request("/api/v1/transaction", { method: "POST", body: JSON.stringify(payload) }),
};
