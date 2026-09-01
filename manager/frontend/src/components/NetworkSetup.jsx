import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client.js';

export default function NetworkSetup({ status, onConnected }) {
  const [networks, setNetworks] = useState([]);
  const [selected, setSelected] = useState(null);
  const [password, setPassword] = useState('');
  const [scanning, setScanning] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState(null);

  const scan = useCallback(async () => {
    setScanning(true);
    setError(null);
    try {
      setNetworks(await api.scanWifi());
    } catch (err) {
      setError(
        err.status === 503
          ? 'Wi-Fi indisponível: NetworkManager não está acessível (esperado fora do sistema live).'
          : err.message
      );
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
        onConnected(result);
      } else {
        setError('Não foi possível conectar. Verifique a senha e tente novamente.');
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
      <div className="header">
        <h1>Configuração de rede</h1>
        <div className="header-meta">
          <button className="btn btn-sm" onClick={scan} disabled={scanning}>
            {scanning ? <span className="spinner" /> : 'Procurar redes'}
          </button>
        </div>
      </div>

      <div className="content">
        {error && <div className="banner banner-error">{error}</div>}

        {status?.connected && (
          <div className="banner banner-info">
            <span>
              Conectado a <strong>{status.ssid}</strong>
              {status.ip_address ? ` (${status.ip_address})` : ''}
            </span>
            <button className="btn btn-sm btn-primary" onClick={() => onConnected(status)}>
              Continuar
            </button>
          </div>
        )}

        <h2 className="section-title">Redes disponíveis</h2>

        {scanning && networks.length === 0 && (
          <div className="empty">
            <span className="spinner" /> Procurando redes Wi-Fi...
          </div>
        )}

        {!scanning && networks.length === 0 && (
          <div className="empty">
            Nenhuma rede encontrada.
            <br />
            Uma conexão por cabo também funciona — clique em Procurar redes novamente.
          </div>
        )}

        <div className="wifi-list">
          {networks.map((net) => (
            <button
              key={net.ssid}
              className={`wifi-row ${selected?.ssid === net.ssid ? 'selected' : ''}`}
              onClick={() => {
                setSelected(net);
                setPassword('');
              }}
            >
              <span className="wifi-ssid">
                {net.ssid}
                {net.connected && ' ✓'}
              </span>
              <span className="wifi-meta">
                {net.security && net.security !== 'open' ? '🔒 ' : ''}
                {net.signal}%{net.known ? ' · salva' : ''}
              </span>
            </button>
          ))}
        </div>

        {selected && (
          <div className="card">
            <div className="image-name">{selected.ssid}</div>
            {needsPassword && (
              <div className="field">
                <input
                  className="input"
                  type="password"
                  placeholder="Senha da rede"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && connect()}
                  autoFocus
                />
              </div>
            )}
            <div className="field">
              <button
                className="btn btn-primary"
                onClick={connect}
                disabled={connecting || (needsPassword && !password)}
              >
                {connecting ? <span className="spinner" /> : 'Conectar'}
              </button>
              <button className="btn" onClick={() => setSelected(null)} disabled={connecting}>
                Cancelar
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="footer">
        <span>É necessário estar online para baixar sistemas.</span>
        <button className="btn btn-sm" onClick={() => onConnected(status)}>
          Pular por enquanto
        </button>
      </div>
    </div>
  );
}
