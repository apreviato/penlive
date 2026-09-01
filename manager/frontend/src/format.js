export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** i;
  return `${value.toFixed(value >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatSpeed(bytesPerSecond) {
  if (!bytesPerSecond) return '—';
  return `${formatBytes(bytesPerSecond)}/s`;
}

export function formatEta(remainingBytes, bytesPerSecond) {
  if (!bytesPerSecond || remainingBytes <= 0) return null;
  const seconds = Math.round(remainingBytes / bytesPerSecond);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
