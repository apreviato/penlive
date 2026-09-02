import React, { useEffect, useState } from 'react';
import { formatBytes, formatClock } from '../format.js';
import { KeyboardIcon, PenLiveMark, PowerIcon, StorageIcon } from './Icons.jsx';

/* The persistent OS-style bar. Everything here is glanceable state the user
   would otherwise have to leave the app to find — which, in a locked kiosk,
   they cannot do. */

export default function StatusBar({
  sysinfo,
  network,
  tabs,
  activeTab,
  onSelectTab,
  onOpenNetwork,
  onOpenKeyboard,
  onPower,
}) {
  const [clock, setClock] = useState(formatClock());

  useEffect(() => {
    const id = setInterval(() => setClock(formatClock()), 10_000);
    return () => clearInterval(id);
  }, []);

  const connected = network?.connected;
  const online = connected && network?.internet !== false;
  const ip = sysinfo?.ip_address;
  const layout = (sysinfo?.keyboard_layout || 'us').toUpperCase();
  const storage = sysinfo?.storage;

  return (
    <header className="statusbar">
      <div className="statusbar-brand">
        <span className="brand-mark"><PenLiveMark size={24} /></span>
        <span className="brand-name">PenLive</span>
      </div>

      <nav className="statusbar-tabs" aria-label="Main navigation">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`statusbar-tab ${activeTab === tab.id ? 'active' : ''}`}
            aria-current={activeTab === tab.id ? 'page' : undefined}
            onClick={() => onSelectTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <div className="statusbar-items">
        <button className="status-item status-network" onClick={onOpenNetwork} title="Network settings">
          <span className={`dot ${online ? 'online' : connected ? 'limited' : 'offline'}`} />
          <span className="status-label">
            {online ? network.ssid || 'Connected' : connected ? 'No internet' : 'Offline'}
          </span>
          {ip && <span className="status-sub">{ip}</span>}
        </button>

        <button className="status-item status-keyboard" onClick={onOpenKeyboard} title="Keyboard layout">
          <span className="status-icon"><KeyboardIcon size={17} /></span>
          <span className="status-label">{layout}</span>
        </button>

        {storage && (
          <div className="status-item status-storage static" title="Free space on the PenLive stick">
            <span className="status-icon"><StorageIcon size={17} /></span>
            <span className="status-label">{formatBytes(storage.free)} free</span>
          </div>
        )}

        <div className="status-item status-clock static" title="System time">
          <span className="status-label mono">{clock}</span>
        </div>

        <button className="status-item power" onClick={onPower} title="Power options">
          <span className="status-icon"><PowerIcon size={19} /></span>
        </button>
      </div>
    </header>
  );
}
