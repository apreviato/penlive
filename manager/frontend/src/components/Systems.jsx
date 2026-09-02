import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { formatBytes } from '../format.js';
import ImageCard from './ImageCard.jsx';
import PendingBootBanner from './PendingBootBanner.jsx';

export default function Systems({ network, onNotice, onOpenFiles, onOpenVm }) {
  const [images, setImages] = useState([]);
  const [storage, setStorage] = useState(null);
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [imgs, st, pb] = await Promise.all([
        api.listImages(),
        api.storage().catch(() => null),
        api.pendingBoot().catch(() => null),
      ]);
      setImages(imgs);
      setStorage(st);
      setPending(pb);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Belt and braces for the per-card progress socket: if it never opens, or
  // dies mid-transfer, the list still converges on its own instead of looking
  // stuck until someone presses Refresh catalog.
  const anyDownloading = images.some((image) => image.status === 'downloading');
  useEffect(() => {
    if (!anyDownloading) return undefined;
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, [anyDownloading, load]);

  const refreshCatalog = async () => {
    setRefreshing(true);
    setError(null);
    try {
      await api.refreshCatalog();
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setRefreshing(false);
    }
  };

  const clearPending = async () => {
    try {
      await api.clearPendingBoot();
      setPending(null);
    } catch (err) {
      setError(err.message);
    }
  };

  const rebootNow = () =>
    onNotice({
      kind: 'confirm',
      title: 'Restart now?',
      message: 'The machine will restart and boot the scheduled system.',
      confirmLabel: 'Restart',
      onConfirm: async () => {
        try {
          await api.reboot();
        } catch (err) {
          setError(err.message);
        }
      },
    });

  const readyCount = images.filter((i) => i.status === 'ready' || i.status === 'downloaded').length;
  // NetworkManager's captive-portal probe is advisory: some otherwise working
  // networks block that URL. Let aria2 make the real request whenever a link
  // is connected instead of disabling Download on a false negative.
  const online = Boolean(network?.connected);

  return (
    <div className="content">
      {error && (
        <div className="banner banner-error">
          <span>{error}</span>
          <button className="btn btn-sm" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {network && !network.connected && (
        <div className="banner banner-warning offline-banner">
          <span>
            <strong>{network.connected ? 'No internet access.' : 'You’re offline.'}</strong>
            {' '}Reconnect in Settings before downloading a new system.
          </span>
        </div>
      )}

      <PendingBootBanner pending={pending} onClear={clearPending} onReboot={rebootNow} />

      <div className="section-header">
        <h2 className="section-title">Available systems</h2>
        <button className="btn btn-sm" onClick={refreshCatalog} disabled={refreshing}>
          {refreshing ? <span className="spinner" /> : 'Refresh catalog'}
        </button>
      </div>

      {loading && (
        <div className="empty">
          <span className="spinner" /> Loading catalog…
        </div>
      )}

      {!loading && images.length === 0 && (
        <div className="empty">
          No systems in the catalog.
          <br />
          Connect to the internet and choose Refresh catalog.
        </div>
      )}

      {images.map((img) => (
        <ImageCard
          key={img.id}
          image={img}
          online={online}
          onChanged={load}
          onError={setError}
          onNotice={onNotice}
          onOpenFiles={onOpenFiles}
          onOpenVm={onOpenVm}
        />
      ))}

      <div className="content-foot">
        <span>
          {storage
            ? `${formatBytes(storage.data_free_bytes)} free of ${formatBytes(
                storage.data_total_bytes
              )} · images use ${formatBytes(storage.images_bytes)}`
            : 'Storage unavailable'}
        </span>
        <span>{readyCount} ready</span>
      </div>
    </div>
  );
}
