import React from 'react';
import { formatBytes, formatUptime } from '../format.js';
import KeyboardPicker from './KeyboardPicker.jsx';
import WifiPanel from './WifiPanel.jsx';

export default function SettingsPanel({ sysinfo, network, onNetworkChanged, onKeyboardChanged }) {
  return (
    <div className="content">
      <h2 className="section-title">Network</h2>
      <WifiPanel status={network} onConnected={onNetworkChanged} />

      <h2 className="section-title">Keyboard</h2>
      <KeyboardPicker onChanged={onKeyboardChanged} />

      <h2 className="section-title">System</h2>
      <div className="card">
        <dl className="info-grid">
          <dt>Hostname</dt>
          <dd>{sysinfo?.hostname || '—'}</dd>

          <dt>IP address</dt>
          <dd>
            {sysinfo?.ip_address || 'Not connected'}
            {sysinfo?.interface ? ` (${sysinfo.interface})` : ''}
          </dd>

          <dt>Kernel</dt>
          <dd>
            {sysinfo?.kernel || '—'} {sysinfo?.arch ? `· ${sysinfo.arch}` : ''}
          </dd>

          <dt>Uptime</dt>
          <dd>{formatUptime(sysinfo?.uptime_seconds)}</dd>

          <dt>Keyboard</dt>
          <dd>
            {(sysinfo?.keyboard_layout || 'us').toUpperCase()}
            {sysinfo?.keyboard_variant ? ` · ${sysinfo.keyboard_variant}` : ''}
          </dd>

          <dt>Hardware virtualization</dt>
          <dd>{sysinfo?.kvm ? 'Available (KVM)' : 'Not available — VMs will be slow'}</dd>

          <dt>Storage</dt>
          <dd>
            {sysinfo?.storage
              ? `${formatBytes(sysinfo.storage.free)} free of ${formatBytes(sysinfo.storage.total)}`
              : '—'}
          </dd>
        </dl>
      </div>
    </div>
  );
}
