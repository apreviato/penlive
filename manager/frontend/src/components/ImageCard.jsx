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
  inspecting: 'inspecting',
  invalid: 'invalid',
};

export default function ImageCard({
  image, online = false, onChanged, onError, onNotice, onOpenFiles, onOpenVm,
}) {
  const [progress, setProgress] = useState(null);
  const [busy, setBusy] = useState(null);
  const socketRef = useRef(null);
  const settleRef = useRef(null);

  const isDownloading = image.status === 'downloading';

  useEffect(() => {
    if (!isDownloading) {
      socketRef.current?.close();
      socketRef.current = null;
      setProgress(null);
      return undefined;
    }
    if (socketRef.current) return undefined;

    let closedByUs = false;
    const ws = downloadProgressSocket(image.id, (msg) => {
      setProgress(msg);
      if (msg.state === 'error') {
        onError(`${image.name}: ${msg.error || 'The download stopped unexpectedly. Try again.'}`);
        onChanged();
      } else if (msg.state === 'complete' || msg.state === 'cancelled') {
        onChanged();
        // Adapter detection runs just after the download row is finished, so
        // look again once it has had time to flip the image to "ready".
        clearTimeout(settleRef.current);
        settleRef.current = setTimeout(onChanged, 3000);
      }
    });
    // A socket that drops without a terminal message (API restart, lost Wi-Fi)
    // used to leave the card frozen on its last progress frame until the user
    // hit Refresh catalog. Reload instead and let the image row speak.
    ws.onclose = () => {
      if (!closedByUs) onChanged();
    };
    socketRef.current = ws;
    return () => {
      closedByUs = true;
      ws.close();
      socketRef.current = null;
    };
  }, [isDownloading, image.id, image.name, onChanged, onError]);

  useEffect(() => () => clearTimeout(settleRef.current), []);

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

  const scheduleBoot = (allowUnverified = false) =>
    run('boot', async () => {
      const result = await api.scheduleBoot(image.id, allowUnverified);
      onNotice({
        kind: 'boot',
        title: 'Scheduled for next boot',
        message: result?.warning
          ? `${image.name} will start the next time this machine reboots. ${result.warning}.`
          : `${image.name} will start the next time this machine reboots.`,
      });
    });

  const bootNow = () => {
    if (!image.verified) {
      onNotice({
        kind: 'confirm',
        title: 'Boot an unverified ISO?',
        message: `${image.name} was added locally or has no published checksum. Boot it only if you trust where it came from.`,
        confirmLabel: 'Boot anyway',
        danger: true,
        onConfirm: () => scheduleBoot(true),
      });
      return;
    }
    scheduleBoot(false);
  };

  const mount = () =>
    run('mount', async () => {
      const result = await api.mountImage(image.id);
      onOpenFiles?.({ source: `iso:${image.id}`, label: image.name });
    });

  const runVm = () =>
    run('vm', async () => {
      const result = await api.startVm(image.id);
      onOpenVm?.({ image, ...result });
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
          {image.origin === 'local' && <span className="tag">local ISO</span>}
          {!image.verified && downloaded && <span className="tag tag-caution">unverified</span>}
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

        {image.status === 'invalid' && (
          <div className="progress-meta danger-text">
            Could not inspect this ISO — {image.inspection_error || 'the file may be incomplete or invalid'}.
          </div>
        )}
      </div>

      <div className="image-actions">
        {!downloaded && !isDownloading && image.origin !== 'local' && (
          <button
            className={`btn ${online ? 'btn-primary' : ''}`}
            onClick={() => {
              if (!online) {
                onNotice({
                  kind: 'info',
                  title: 'No internet connection',
                  message: 'Connect to Wi-Fi or Ethernet in Settings, then try the download again.',
                });
                return;
              }
              run('download', () => api.startDownload(image.id));
            }}
            disabled={busy === 'download'}
            title={online ? `Download ${image.name}` : 'Connect to the internet before downloading'}
          >
            {busy === 'download' ? <span className="spinner" /> : online ? 'Download' : 'Connect to download'}
          </button>
        )}

        {!downloaded && !isDownloading && image.origin === 'local' && (
          <button className="btn btn-danger" onClick={remove} disabled={busy === 'delete'}>
            Delete
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
