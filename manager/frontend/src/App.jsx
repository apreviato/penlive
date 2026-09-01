import React, { useEffect, useState } from 'react';
import { api } from './api/client.js';
import Catalog from './components/Catalog.jsx';
import NetworkSetup from './components/NetworkSetup.jsx';

/* First run shows network setup; once online (or explicitly skipped) it goes
   straight to the catalog on every later boot. The "did the user already get
   past setup" bit is intentionally derived from live network state rather
   than persisted — a stored flag would strand the user on the catalog screen
   with no way back if their Wi-Fi later stopped working. */

export default function App() {
  const [status, setStatus] = useState(null);
  const [screen, setScreen] = useState('loading');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let net = null;
      try {
        net = await api.networkStatus();
      } catch {
        net = { connected: false };
      }
      if (cancelled) return;
      setStatus(net);
      setScreen(net.connected ? 'catalog' : 'network');
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the header's connection pill honest without the user refreshing.
  useEffect(() => {
    if (screen !== 'catalog') return undefined;
    const id = setInterval(async () => {
      try {
        setStatus(await api.networkStatus());
      } catch {
        // A transient failure shouldn't clear a good status; keep the last one.
      }
    }, 15000);
    return () => clearInterval(id);
  }, [screen]);

  if (screen === 'loading') {
    return (
      <div className="app">
        <div className="empty">
          <span className="spinner" /> Iniciando BootStack...
        </div>
      </div>
    );
  }

  if (screen === 'network') {
    return (
      <div className="app">
        <NetworkSetup
          status={status}
          onConnected={(net) => {
            if (net) setStatus(net);
            setScreen('catalog');
          }}
        />
      </div>
    );
  }

  return <Catalog networkStatus={status} onEditNetwork={() => setScreen('network')} />;
}
