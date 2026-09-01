const BASE = '';

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // Non-JSON error body (e.g. a proxy timeout page); statusText is the best we have.
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  health: () => request('/api/health'),

  networkStatus: () => request('/api/network/status'),
  scanWifi: () => request('/api/network/wifi'),
  connectWifi: (ssid, password) =>
    request('/api/network/wifi/connect', {
      method: 'POST',
      body: JSON.stringify({ ssid, password }),
    }),

  listImages: () => request('/api/images'),
  refreshCatalog: () => request('/api/catalog/refresh', { method: 'POST' }),
  deleteImage: (id) => request(`/api/images/${id}`, { method: 'DELETE' }),
  storage: () => request('/api/storage'),

  startDownload: (imageId) =>
    request('/api/downloads', { method: 'POST', body: JSON.stringify({ image_id: imageId }) }),
  cancelDownload: (imageId) => request(`/api/downloads/${imageId}/cancel`, { method: 'POST' }),

  scheduleBoot: (imageId) =>
    request('/api/boot', { method: 'POST', body: JSON.stringify({ image_id: imageId, method: 'auto' }) }),
  pendingBoot: () => request('/api/boot/pending'),
  clearPendingBoot: () => request('/api/boot/pending', { method: 'DELETE' }),
  reboot: () => request('/api/boot/reboot', { method: 'POST' }),

  startVm: (imageId) =>
    request('/api/vm/start', { method: 'POST', body: JSON.stringify({ image_id: imageId }) }),
  mountImage: (imageId) => request(`/api/mount/${imageId}`, { method: 'POST' }),
  unmountImage: (imageId) => request(`/api/mount/${imageId}`, { method: 'DELETE' }),
};

export function downloadProgressSocket(imageId, onMessage) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${window.location.host}/api/downloads/${imageId}/progress`);
  ws.onmessage = (evt) => onMessage(JSON.parse(evt.data));
  return ws;
}
