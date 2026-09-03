const BASE = '';

// Nothing here should be able to hang the UI forever. Most of these endpoints
// end up in the privileged daemon, which answers one request at a time and
// shells out to nmcli, smartctl or mount -- any of which can wedge on hardware
// that is misbehaving. A rejected promise the caller can show an error for is
// always better than a screen that never finishes loading.
const DEFAULT_TIMEOUT_MS = 20000;
// Matches the read timeout in the daemon client (app/daemon/client.py): calls
// that legitimately do slow work -- mounting a dirty NTFS volume, waiting on an
// nmcli association, copying a file -- must fail on the daemon's terms with a
// real error, not be cut off here first.
const SLOW_TIMEOUT_MS = 120000;
const SLOW = { timeout: SLOW_TIMEOUT_MS };

async function request(path, options = {}) {
  const { timeout = DEFAULT_TIMEOUT_MS, ...init } = options;
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), timeout);
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      signal: abort.signal,
      ...init,
    });
  } catch (err) {
    if (err.name === 'AbortError') {
      const timedOut = new Error(`The manager did not answer within ${Math.round(timeout / 1000)}s.`);
      timedOut.timeout = true;
      throw timedOut;
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
      if (detail && typeof detail === 'object') {
        detail = detail.message || detail.error || JSON.stringify(detail);
      }
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
  sysinfo: () => request('/api/sysinfo'),

  // setup
  setupState: () => request('/api/setup/state'),
  completeSetup: () => request('/api/setup/complete', { method: 'POST' }),

  // secure boot
  secureBootState: () => request('/api/system/secureboot'),
  enrolSecureBootKey: () => request('/api/system/secureboot/enrol', { method: 'POST', ...SLOW }),

  // network
  networkStatus: () => request('/api/network/status'),
  scanWifi: () => request('/api/network/wifi', SLOW),
  connectWifi: (ssid, password) =>
    request('/api/network/wifi/connect', {
      method: 'POST',
      body: JSON.stringify({ ssid, password }),
      ...SLOW,
    }),

  // keyboard
  keyboardLayouts: () => request('/api/keyboard/layouts'),
  keyboardVariants: (layout) => request(`/api/keyboard/variants/${layout}`),
  setKeyboard: (layout, variant) =>
    request('/api/keyboard', { method: 'PUT', body: JSON.stringify({ layout, variant }) }),

  // images / catalog
  listImages: () => request('/api/images'),
  rescanImages: () => request('/api/images/rescan', { method: 'POST', ...SLOW }),
  refreshCatalog: () => request('/api/catalog/refresh', { method: 'POST', ...SLOW }),
  deleteImage: (id) => request(`/api/images/${id}`, { method: 'DELETE', ...SLOW }),
  storage: () => request('/api/storage'),

  // PENDATA file manager
  fileSources: () => request('/api/files/sources'),
  listFiles: (path = '', source = 'pendata') =>
    request(`/api/files?source=${encodeURIComponent(source)}&path=${encodeURIComponent(path)}`),
  createFolder: (parent, name, source = 'pendata') =>
    request('/api/files/folder', { method: 'POST', body: JSON.stringify({ source, parent, name }) }),
  renameFile: (path, name, source = 'pendata') =>
    request('/api/files/rename', { method: 'POST', body: JSON.stringify({ source, path, name }) }),
  deleteFile: (path, recursive = false, source = 'pendata') =>
    request(`/api/files?source=${encodeURIComponent(source)}&path=${encodeURIComponent(path)}&recursive=${recursive}`, { method: 'DELETE', ...SLOW }),
  transferFile: (source, path, destination, destinationPath, move = false) =>
    request('/api/files/transfer', {
      method: 'POST',
      body: JSON.stringify({ source, path, destination, destination_path: destinationPath, move }),
      ...SLOW,
    }),
  mountDevice: (device) =>
    request('/api/files/device/mount', { method: 'POST', body: JSON.stringify({ device }), ...SLOW }),
  unmountDevice: (device) =>
    request('/api/files/device/unmount', { method: 'POST', body: JSON.stringify({ device }), ...SLOW }),

  startDownload: (imageId) =>
    request('/api/downloads', { method: 'POST', body: JSON.stringify({ image_id: imageId }), ...SLOW }),
  cancelDownload: (imageId) => request(`/api/downloads/${imageId}/cancel`, { method: 'POST' }),

  // boot
  scheduleBoot: (imageId, allowUnverified = false) =>
    request('/api/boot', {
      method: 'POST',
      body: JSON.stringify({ image_id: imageId, method: 'auto', allow_unverified: allowUnverified }),
      ...SLOW,
    }),
  pendingBoot: () => request('/api/boot/pending'),
  clearPendingBoot: () => request('/api/boot/pending', { method: 'DELETE' }),
  rebootPending: () => request('/api/boot/reboot', { method: 'POST' }),

  startVm: (imageId) =>
    request('/api/vm/start', { method: 'POST', body: JSON.stringify({ image_id: imageId }), ...SLOW }),
  attachVmDisk: (imageId, device, confirmation) =>
    request(`/api/vm/${imageId}/physical-disk`, {
      method: 'POST',
      body: JSON.stringify({ device, confirmation }),
      ...SLOW,
    }),
  stopVm: (imageId) => request(`/api/vm/${imageId}/stop`, { method: 'POST' }),
  vmStatus: (imageId) => request(`/api/vm/${imageId}/status`),
  mountImage: (imageId) => request(`/api/mount/${imageId}`, { method: 'POST', ...SLOW }),
  unmountImage: (imageId) => request(`/api/mount/${imageId}`, { method: 'DELETE', ...SLOW }),

  // tools / plugins
  listTools: () => request('/api/tools'),
  toolDevices: () => request('/api/tools/devices'),
  listBackups: () => request('/api/tools/backups'),
  runTool: (toolId, values) =>
    request(`/api/tools/${toolId}/run`, { method: 'POST', body: JSON.stringify(values), ...SLOW }),

  // jobs
  listJobs: () => request('/api/jobs'),
  getJob: (id) => request(`/api/jobs/${id}`),
  cancelJob: (id) => request(`/api/jobs/${id}/cancel`, { method: 'POST' }),

  // power
  reboot: () => request('/api/power/reboot', { method: 'POST' }),
  poweroff: () => request('/api/power/poweroff', { method: 'POST' }),
};

function socket(path, onMessage) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${window.location.host}${path}`);
  ws.onmessage = (evt) => onMessage(JSON.parse(evt.data));
  return ws;
}

export const downloadProgressSocket = (imageId, onMessage) =>
  socket(`/api/downloads/${imageId}/progress`, onMessage);

export const jobStreamSocket = (jobId, onMessage) =>
  socket(`/api/jobs/${jobId}/stream`, onMessage);
