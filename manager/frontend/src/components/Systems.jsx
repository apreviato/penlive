import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client.js';
import { formatBytes } from '../format.js';
import ImageCard from './ImageCard.jsx';
import PendingBootBanner from './PendingBootBanner.jsx';

// What is already on the stick comes first: it is what the user can act on
// right now, and it is a handful of entries in a catalog of dozens. Anything
// mid-flight follows, so a running download does not jump to the bottom the
// moment it starts.
const ORDER = {
  ready: 0,
  downloaded: 0,
  downloading: 1,
  inspecting: 1,
  corrupted: 2,
  invalid: 2,
};
const ORDER_DEFAULT = 3;

function rank(image) {
  return ORDER[image.status] ?? ORDER_DEFAULT;
}

export function arrange(images, query) {
  const needle = query.trim().toLowerCase();
  const matches = needle
    ? images.filter((img) =>
        [img.name, img.family, img.version, img.architecture, img.id]
          .filter(Boolean)
          .some((field) => String(field).toLowerCase().includes(needle))
      )
    : images;
  // Sorting by rank alone would be at the mercy of the sort's stability for
  // everything else; falling back to the catalog's own order keeps the list
  // from reshuffling under the user between refreshes.
  return matches
    .map((img, index) => ({ img, index }))
    .sort((a, b) => rank(a.img) - rank(b.img) || a.index - b.index)
    .map(({ img }) => img);
}

export default function Systems({ network, onNotice, onOpenFiles, onOpenVm, onPowering }) {
  const [images, setImages] = useState([]);
  const [storage, setStorage] = useState(null);
  const [pending, setPending] = useState(null);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');

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
    // Progress itself comes from each card's WebSocket. This slower fallback
    // only heals a dropped socket; repeatedly listing the catalog and statting
    // every ISO during a healthy download just adds USB and database pressure.
    const id = setInterval(load, 5000);
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
        onPowering?.('reboot');
        try {
          await api.rebootPending();
        } catch (err) {
          onPowering?.(null);
          setError(err.message);
        }
      },
    });

  const shown = useMemo(() => arrange(images, query), [images, query]);
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

      <div className="catalog-search">
        <input
          type="search"
          className="input"
          placeholder="Search by name, family or version…"
          aria-label="Search the catalog"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button className="btn btn-sm" onClick={() => setQuery('')}>
            Clear
          </button>
        )}
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

      {!loading && images.length > 0 && shown.length === 0 && (
        <div className="empty">No system matches “{query}”.</div>
      )}

      {shown.map((img) => (
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
        <span>
          {query ? `${shown.length} of ${images.length} shown · ` : ''}
          {readyCount} ready
        </span>
      </div>
    </div>
  );
}
