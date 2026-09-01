import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { formatBytes } from '../format.js';
import ImageCard from './ImageCard.jsx';
import PendingBootBanner from './PendingBootBanner.jsx';

export default function Catalog({ networkStatus, onEditNetwork }) {
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

  const reboot = async () => {
    if (!window.confirm('Reiniciar o computador agora?')) return;
    try {
      await api.reboot();
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <div className="app">
      <div className="header">
        <h1>BootStack Manager</h1>
        <div className="header-meta">
          <button className="net-pill btn btn-sm" onClick={onEditNetwork}>
            <span className={`dot ${networkStatus?.connected ? 'online' : 'offline'}`} />
            {networkStatus?.connected ? networkStatus.ssid || 'Conectado' : 'Sem conexão'}
          </button>
          <button className="btn btn-sm" onClick={refreshCatalog} disabled={refreshing}>
            {refreshing ? <span className="spinner" /> : 'Atualizar catálogo'}
          </button>
        </div>
      </div>

      <div className="content">
        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn btn-sm" onClick={() => setError(null)}>
              Fechar
            </button>
          </div>
        )}

        <PendingBootBanner pending={pending} onClear={clearPending} onReboot={reboot} />

        <h2 className="section-title">Sistemas disponíveis</h2>

        {loading && (
          <div className="empty">
            <span className="spinner" /> Carregando catálogo...
          </div>
        )}

        {!loading && images.length === 0 && (
          <div className="empty">
            Nenhum sistema no catálogo.
            <br />
            Conecte-se à internet e clique em Atualizar catálogo.
          </div>
        )}

        {images.map((img) => (
          <ImageCard key={img.id} image={img} onChanged={load} onError={setError} />
        ))}
      </div>

      <div className="footer">
        <span>
          {storage
            ? `Pendrive: ${formatBytes(storage.data_free_bytes)} livres de ${formatBytes(
                storage.data_total_bytes
              )} · ISOs: ${formatBytes(storage.images_bytes)}`
            : 'Armazenamento indisponível'}
        </span>
        <span>{images.filter((i) => i.status === 'ready' || i.status === 'downloaded').length} baixado(s)</span>
      </div>
    </div>
  );
}
