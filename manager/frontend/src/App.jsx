import React, { useCallback, useEffect, useState } from 'react';
import { api } from './api/client.js';
import { installKioskLockdown, isDevServer } from './kiosk.js';

import Dialog from './components/Dialog.jsx';
import SettingsPanel from './components/SettingsPanel.jsx';
import SetupWizard from './components/SetupWizard.jsx';
import StatusBar from './components/StatusBar.jsx';
import Systems from './components/Systems.jsx';
import Tools from './components/Tools.jsx';

const TABS = [
  { id: 'systems', label: 'Systems' },
  { id: 'tools', label: 'Tools' },
  { id: 'settings', label: 'Settings' },
];

export default function App() {
  const [phase, setPhase] = useState('loading'); // loading | setup | main
  const [setupReason, setSetupReason] = useState(null);
  const [tab, setTab] = useState('systems');
  const [network, setNetwork] = useState(null);
  const [sysinfo, setSysinfo] = useState(null);
  const [notice, setNotice] = useState(null);

  // Lock the page down in the kiosk, but never on the dev server — locking out
  // reload and devtools would make the UI impossible to work on.
  useEffect(() => installKioskLockdown({ enabled: !isDevServer() }), []);

  const refreshStatus = useCallback(async () => {
    const [net, info] = await Promise.all([
      api.networkStatus().catch(() => ({ connected: false })),
      api.sysinfo().catch(() => null),
    ]);
    setNetwork(net);
    setSysinfo(info);
    return net;
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await refreshStatus();
      let state = null;
      try {
        state = await api.setupState();
      } catch {
        state = { needs_setup: false };
      }
      if (cancelled) return;
      setSetupReason(state.reason);
      setPhase(state.needs_setup ? 'setup' : 'main');
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshStatus]);

  // Keep the status bar honest without the user having to do anything.
  useEffect(() => {
    if (phase !== 'main') return undefined;
    const id = setInterval(refreshStatus, 15000);
    return () => clearInterval(id);
  }, [phase, refreshStatus]);

  const powerMenu = () =>
    setNotice({
      kind: 'confirm',
      title: 'Shut down?',
      message:
        'Downloads and running jobs will stop. Anything already downloaded and verified stays on the stick.',
      confirmLabel: 'Shut down',
      danger: true,
      onConfirm: async () => {
        try {
          await api.poweroff();
        } catch (err) {
          setNotice({ kind: 'info', title: 'Could not power off', message: err.message });
        }
      },
    });

  if (phase === 'loading') {
    return (
      <div className="app">
        <div className="empty">
          <span className="spinner" /> Starting PenLive…
        </div>
      </div>
    );
  }

  if (phase === 'setup') {
    return (
      <div className="app">
        <SetupWizard
          reason={setupReason}
          networkStatus={network}
          onNetworkChanged={(net) => setNetwork(net)}
          onFinish={async () => {
            await refreshStatus();
            setPhase('main');
          }}
        />
        <Dialog notice={notice} onClose={() => setNotice(null)} />
      </div>
    );
  }

  return (
    <div className="app">
      <StatusBar
        sysinfo={sysinfo}
        network={network}
        onOpenNetwork={() => setTab('settings')}
        onOpenKeyboard={() => setTab('settings')}
        onPower={powerMenu}
      />

      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`tab ${tab === t.id ? 'active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === 'systems' && <Systems onNotice={setNotice} />}
      {tab === 'tools' && <Tools />}
      {tab === 'settings' && (
        <SettingsPanel
          sysinfo={sysinfo}
          network={network}
          onNetworkChanged={(net) => {
            setNetwork(net);
            refreshStatus();
          }}
          onKeyboardChanged={refreshStatus}
        />
      )}

      <Dialog notice={notice} onClose={() => setNotice(null)} />
    </div>
  );
}
