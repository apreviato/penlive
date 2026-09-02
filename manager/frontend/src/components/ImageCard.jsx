import React, { useEffect, useRef, useState } from 'react';
import { api, downloadProgressSocket } from '../api/client.js';
import { formatBytes, formatEta, formatSpeed } from '../format.js';
import DistroLogo from './DistroLogo.jsx';

const STATUS_LABEL = {
  not_downloaded: 'not downloaded',
  downloading: 'downloading',
  downloaded: 'downloaded',
  ready: 'ready',
  corrupted: 'corrupted',
};

export default function ImageCard({ image, onChanged, onError, onNotice }) {
  const [progress, setProgress] = useState(null);
  const [busy, setBusy] = useState(null);
  const socketRef = useRef(null);

  const isDownloading = image.status === 'downloading';

  useEffect(() => {
    if (!isDownloading) {
      socketRef.current?.close();
      socketRef.current = null;
      setProgress(null);
      return undefined;
    }
    if (socketRef.current) return undefined;

    const ws = downloadProgressSocket(image.id, (msg) => {
      setProgress(msg);
      if (msg.state === 'complete' || msg.state === 'error') onChanged();
    });
    socketRef.current = ws;
    return () => {
      ws.close();
      socketRef.current = null;
    };
  }, [isDownloading, image.id, onChanged]);

  const run = async (label, fn) => {
    setBusy(label);
    try {
      await fn();
      onChanged();
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(null);
    }
  };

  const bootNow = () =>
    run('boot', async () => {
      await api.scheduleBoot(image.id);
      onNotice({
        kind: 'boot',
        title: 'Scheduled for next boot',
        message: `${image.name} will start the next time this machine reboots.`,
      });
    });

  const mount = () =>
    run('mount', async () => {
      const result = await api.mountImage(image.id);
      onNotice({
        kind: 'info',
        title: 'Image mounted',
        message: `Contents available at ${result.mountpoint}`,
      });
    });

  const runVm = () =>
    run('vm', async () => {
      await api.startVm(image.id);
      onNotice({
        kind: 'info',
        title: 'Virtual machine started',
        message: `${image.name} is running in a window. Close that window to stop it.`,
      });
    });

  const remove = () =>
    onNotice({
      kind: 'confirm',
      title: 'Delete this image?',
      message: `${image.name} (${formatBytes(image.size_bytes)}) will be removed from the stick. You can download it again later.`,
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: () => run('delete', () => api.deleteImage(image.id)),
    });

  const downloaded = image.status === 'downloaded' || image.status === 'ready';
  const caps = image.capabilities ?? {};

  const total = progress?.total_bytes ?? image.size_bytes;
  const done = progress?.progress_bytes ?? 0;
  const pct = total ? Math.min(100, (done / total) * 100) : 0;
  const eta = formatEta(total ? total - done : 0, progress?.speed_bps);

  return (
    <div className="card image-card">
      <DistroLogo family={image.family} />

      <div className="image-main">
        <div className="image-name">{image.name}</div>
        <div className="image-sub">
          <span className={`badge badge-${image.status}`}>
            {STATUS_LABEL[image.status] ?? image.status}
          </span>
          <span>{formatBytes(image.size_bytes)}</span>
          {image.version && <span>v{image.version}</span>}
          {image.adapter && <span>adapter: {image.adapter}</span>}
        </div>

        {isDownloading && (
          <>
            <div className="progress">
              <div className="progress-bar" style={{ width: `${pct}%` }} />
            </div>
            <div className="progress-meta">
              <span>
                {formatBytes(done)} / {formatBytes(total)} ({pct.toFixed(0)}%)
              </span>
              <span>
                {formatSpeed(progress?.speed_bps)}
                {eta ? ` · ${eta} left` : ''}
              </span>
            </div>
            {progress?.state === 'verifying' && (
              <div className="progress-meta">
                <span>
                  <span className="spinner" /> Verifying SHA-256…
                </span>
              </div>
            )}
          </>
        )}

        {image.status === 'corrupted' && (
          <div className="progress-meta danger-text">
            SHA-256 check failed — the file was discarded. Try downloading again.
          </div>
        )}
      </div>

      <div className="image-actions">
        {!downloaded && !isDownloading && (
          <button
            className="btn btn-primary"
            onClick={() => run('download', () => api.startDownload(image.id))}
            disabled={busy === 'download'}
          >
            {busy === 'download' ? <span className="spinner" /> : 'Download'}
          </button>
        )}

        {isDownloading && (
          <button
            className="btn btn-danger"
            onClick={() => run('cancel', () => api.cancelDownload(image.id))}
            disabled={busy === 'cancel'}
          >
            Cancel
          </button>
        )}

        {downloaded && (
          <>
            {caps.nativeBoot !== false && (
              <button className="btn btn-primary" onClick={bootNow} disabled={busy === 'boot'}>
                {busy === 'boot' ? <span className="spinner" /> : 'Boot'}
              </button>
            )}
            {caps.vm && (
              <button className="btn" onClick={runVm} disabled={busy === 'vm'}>
                {busy === 'vm' ? <span className="spinner" /> : 'Run VM'}
              </button>
            )}
            {caps.mount !== false && (
              <button className="btn" onClick={mount} disabled={busy === 'mount'}>
                Mount
              </button>
            )}
            <button className="btn btn-danger" onClick={remove} disabled={busy === 'delete'}>
              Delete
            </button>
          </>
        )}
      </div>
    </div>
  );
}
