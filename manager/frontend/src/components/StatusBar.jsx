import React, { useEffect, useState } from 'react';
import { formatBytes, formatClock } from '../format.js';

/* The persistent OS-style bar. Everything here is glanceable state the user
   would otherwise have to leave the app to find — which, in a locked kiosk,
   they cannot do. */

export default function StatusBar({ sysinfo, network, onOpenNetwork, onOpenKeyboard, onPower }) {
  const [clock, setClock] = useState(formatClock());

  useEffect(() => {
    const id = setInterval(() => setClock(formatClock()), 10_000);
    return () => clearInterval(id);
  }, []);

  const online = network?.connected;
  const ip = sysinfo?.ip_address;
  const layout = (sysinfo?.keyboard_layout || 'us').toUpperCase();
  const storage = sysinfo?.storage;

  return (
    <header className="statusbar">
      <div className="statusbar-brand">
        <span className="brand-mark">▣</span>
        <span className="brand-name">PenLive</span>
      </div>

      <div className="statusbar-items">
        <button className="status-item" onClick={onOpenNetwork} title="Network settings">
          <span className={`dot ${online ? 'online' : 'offline'}`} />
          <span className="status-label">
            {online ? network.ssid || 'Connected' : 'Offline'}
          </span>
          {ip && <span className="status-sub">{ip}</span>}
        </button>

        <button className="status-item" onClick={onOpenKeyboard} title="Keyboard layout">
          <span className="status-icon">⌨</span>
          <span className="status-label">{layout}</span>
        </button>

        {storage && (
          <div className="status-item static" title="Free space on the PenLive stick">
            <span className="status-icon">▤</span>
            <span className="status-label">{formatBytes(storage.free)} free</span>
          </div>
        )}

        <div className="status-item static" title="System time">
          <span className="status-label mono">{clock}</span>
        </div>

        <button className="status-item power" onClick={onPower} title="Power options">
          <span className="status-icon">⏻</span>
        </button>
      </div>
    </header>
  );
}
