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
  image, online = false, onChanged, onError, onNotice, onOpenFiles, onOpenVm, onRebootNow,
  onVmSessionsChanged,
}) {
  const [progress, setProgress] = useState(null);
  const [busy, setBusy] = useState(null);
  const socketRef = useRef(null);
  const settleRef = useRef(null);

  const isDownloading = image.status === 'downloading';
  const isQueued = progress?.state === 'queued';

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
    } catch (err) {
      onError(err.message);
    } finally {
      setBusy(null);
      // Reload even when the action failed. A Download that errors after aria2
      // has already taken the job would otherwise leave the card offering a
      // Download button for a transfer that is actually running.
      onChanged();
    }
  };

  const scheduleBoot = (allowUnverified = false) =>
    run('boot', async () => {
      const result = await api.scheduleBoot(image.id, allowUnverified);
      // Secure Boot enrolment used to be a separate errand in Settings before
      // the ISO could be scheduled at all. It is done by now; what comes back
      // is only what the user has to type at the firmware's own MOK screen.
      const detail = [result?.secure_boot?.message, result?.warning]
        .filter(Boolean)
        .join(' ');
      onNotice({
        kind: 'confirm',
        title: 'Scheduled for the next restart',
        message: `${image.name} starts the next time this machine restarts, this once.`,
        detail: detail || null,
        steps: result?.secure_boot?.steps || null,
        code: result?.secure_boot?.password || null,
        codeLabel: 'Code to type',
        confirmLabel: 'Restart now',
        cancelLabel: 'Later',
        onConfirm: onRebootNow,
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

  const launchVm = (resume) =>
    run('vm', async () => {
      const result = await api.startVm(image.id, { resume });
      onOpenVm?.({ image, ...result });
      if (resume) onVmSessionsChanged?.();
    });

  const runVm = async () => {
    // Silently booting the ISO would throw away a machine the user deliberately
    // froze, so ask before doing either. A lookup that fails says nothing about
    // whether a session exists, so fall through to the ordinary start rather
    // than blocking Run VM on it.
    let saved = null;
    try {
      saved = await api.vmSession(image.id);
    } catch {
      saved = null;
    }
    if (!saved || !saved.usable) {
      launchVm(false);
      return;
    }
    onNotice({
      kind: 'confirm',
      title: 'Resume the saved session?',
      message: `${image.name} has a session saved on ${new Date(saved.saved_at).toLocaleString()}. Resuming picks it up exactly where it was left.`,
      detail: 'Starting fresh boots the ISO from the beginning and leaves the saved session untouched.',
      confirmLabel: 'Resume session',
      cancelLabel: 'Start fresh',
      onConfirm: () => launchVm(true),
      onCancel: () => launchVm(false),
    });
  };

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
            {isQueued ? 'queued' : (STATUS_LABEL[image.status] ?? image.status)}
          </span>
          {image.origin === 'local' && <span className="tag">local ISO</span>}
          {!image.verified && downloaded && <span className="tag tag-caution">unverified</span>}
          <span>{formatBytes(image.size_bytes)}</span>
          {image.version && <span>v{image.version}</span>}
          {image.adapter && <span>adapter: {image.adapter}</span>}
        </div>

        {isDownloading && (
          <>
            {isQueued ? (
              <div className="progress-meta">
                <span>Waiting for the current download to finish…</span>
              </div>
            ) : (
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
              </>
            )}
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
