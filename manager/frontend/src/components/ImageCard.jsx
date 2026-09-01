import React, { useEffect, useRef, useState } from 'react';
import { api, downloadProgressSocket } from '../api/client.js';
import { formatBytes, formatEta, formatSpeed } from '../format.js';

const STATUS_LABEL = {
  not_downloaded: 'não baixado',
  downloading: 'baixando',
  downloaded: 'baixado',
  ready: 'pronto',
  corrupted: 'corrompido',
};

export default function ImageCard({ image, onChanged, onError }) {
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
      if (msg.state === 'complete' || msg.state === 'error') {
        onChanged();
      }
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

  const bootNow = async () => {
    await run('boot', async () => {
      await api.scheduleBoot(image.id);
      if (
        window.confirm(
          `${image.name} foi marcado para o próximo boot.\n\nReiniciar agora para iniciar a instalação?`
        )
      ) {
        await api.reboot();
      }
    });
  };

  const remove = async () => {
    if (!window.confirm(`Apagar ${image.name} do pendrive?`)) return;
    await run('delete', () => api.deleteImage(image.id));
  };

  const downloaded = image.status === 'downloaded' || image.status === 'ready';
  const caps = image.capabilities ?? {};

  const total = progress?.total_bytes ?? image.size_bytes;
  const done = progress?.progress_bytes ?? 0;
  const pct = total ? Math.min(100, (done / total) * 100) : 0;
  const eta = formatEta(total ? total - done : 0, progress?.speed_bps);

  return (
    <div className="card image-card">
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
                {eta ? ` · ${eta} restantes` : ''}
              </span>
            </div>
            {progress?.state === 'verifying' && (
              <div className="progress-meta">
                <span>
                  <span className="spinner" /> verificando SHA-256...
                </span>
              </div>
            )}
          </>
        )}

        {image.status === 'corrupted' && (
          <div className="progress-meta" style={{ color: 'var(--danger)' }}>
            Falha na verificação SHA-256 — o arquivo foi descartado.
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
            {busy === 'download' ? <span className="spinner" /> : 'Baixar'}
          </button>
        )}

        {isDownloading && (
          <button
            className="btn btn-danger"
            onClick={() => run('cancel', () => api.cancelDownload(image.id))}
            disabled={busy === 'cancel'}
          >
            Cancelar
          </button>
        )}

        {downloaded && (
          <>
            {caps.nativeBoot !== false && (
              <button className="btn btn-primary" onClick={bootNow} disabled={busy === 'boot'}>
                {busy === 'boot' ? <span className="spinner" /> : 'Bootar'}
              </button>
            )}
            {caps.vm && (
              <button
                className="btn"
                onClick={() => run('vm', () => api.startVm(image.id))}
                disabled={busy === 'vm'}
              >
                {busy === 'vm' ? <span className="spinner" /> : 'Rodar VM'}
              </button>
            )}
            {caps.mount !== false && (
              <button
                className="btn"
                onClick={() => run('mount', () => api.mountImage(image.id))}
                disabled={busy === 'mount'}
              >
                Montar
              </button>
            )}
            <button className="btn btn-danger" onClick={remove} disabled={busy === 'delete'}>
              Apagar
            </button>
          </>
        )}
      </div>
    </div>
  );
}
