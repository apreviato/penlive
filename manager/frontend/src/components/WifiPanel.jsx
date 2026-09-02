import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { LockIcon, WifiIcon } from './Icons.jsx';

export default function WifiPanel({ status, onConnected }) {
  const [networks, setNetworks] = useState([]);
  const [selected, setSelected] = useState(null);
  const [password, setPassword] = useState('');
  const [scanning, setScanning] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState(null);
  const [unavailable, setUnavailable] = useState(false);

  const scan = useCallback(async () => {
    setScanning(true);
    setError(null);
    try {
      setNetworks(await api.scanWifi());
      setUnavailable(false);
    } catch (err) {
      if (err.status === 503) {
        setUnavailable(true);
        setError(err.message || 'Wi-Fi control is unavailable. A wired connection will still work.');
      } else {
        setError(err.message);
      }
    } finally {
      setScanning(false);
    }
  }, []);

  useEffect(() => {
    scan();
  }, [scan]);

  const connect = async () => {
    if (!selected) return;
    setConnecting(true);
    setError(null);
    try {
      const result = await api.connectWifi(selected.ssid, password || null);
      if (result.connected) {
        setSelected(null);
        setPassword('');
        setNetworks((current) => current.map((network) => ({
          ...network,
          connected: network.ssid === result.ssid || network.ssid === selected.ssid,
          known: network.known || network.ssid === selected.ssid,
        })));
        onConnected?.(result);
      } else {
        setError('Could not connect. Check the password and try again.');
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setConnecting(false);
    }
  };

  const needsPassword = selected && selected.security && selected.security !== 'open';

  return (
    <div>
      {error && <div className="banner banner-error">{error}</div>}

      {status?.connected && status?.internet !== false && (
        <div className="banner banner-success">
          Connected to <strong>{status.ssid}</strong>
          {status.ip_address ? ` — ${status.ip_address}` : ''}
        </div>
      )}

      {status?.connected && status?.internet === false && (
        <div className="banner banner-warning">
          Connected to <strong>{status.ssid}</strong>, but the internet is not reachable yet.
        </div>
      )}

      <div className="section-header">
        <h2 className="section-title">Available networks</h2>
        <button className="btn btn-sm" onClick={scan} disabled={scanning}>
          {scanning ? <span className="spinner" /> : 'Scan again'}
        </button>
      </div>

      {scanning && networks.length === 0 && (
        <div className="empty">
          <span className="spinner" /> Scanning for Wi-Fi networks...
        </div>
      )}

      {!scanning && networks.length === 0 && (
        <div className="empty">
          {unavailable
            ? 'Wi-Fi could not be scanned. Check the message above, or plug in an Ethernet cable.'
            : 'No networks found. Move closer to your router, or use an Ethernet cable.'}
        </div>
      )}

      <div className="wifi-list">
        {networks.map((net) => (
          <button
            key={net.ssid}
            className={`wifi-row ${selected?.ssid === net.ssid ? 'selected' : ''}`}
            onClick={() => {
              if (net.connected) {
                setSelected(null);
                setPassword('');
                return;
              }
              setSelected(net);
              setPassword('');
            }}
          >
            <span className="wifi-network-main">
              <WifiIcon signal={net.signal} />
              <span className="wifi-ssid">
                {net.ssid}
                {net.connected && <span className="connected-label">Connected</span>}
              </span>
            </span>
            <span className="wifi-meta">
              {net.security && net.security !== 'open' ? <LockIcon /> : null}
              {net.signal}%{net.known ? ' · saved' : ''}
            </span>
          </button>
        ))}
      </div>

      {selected && (
        <div className="card">
          <div className="image-name">{selected.ssid}</div>
          {needsPassword && (
            <div className="form-row">
              <input
                className="input"
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                placeholder="Network password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && connect()}
                autoFocus
              />
            </div>
          )}
          <div className="button-row">
            <button
              className="btn btn-primary"
              onClick={connect}
              disabled={connecting || (needsPassword && !password)}
            >
              {connecting ? <span className="spinner" /> : 'Connect'}
            </button>
            <button className="btn" onClick={() => setSelected(null)} disabled={connecting}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
