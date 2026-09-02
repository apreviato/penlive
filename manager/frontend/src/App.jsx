import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from './api/client.js';
import { installKioskLockdown, isDevServer } from './kiosk.js';

import Dialog from './components/Dialog.jsx';
import FileManager from './components/FileManager.jsx';
import SettingsPanel from './components/SettingsPanel.jsx';
import SetupWizard from './components/SetupWizard.jsx';
import StatusBar from './components/StatusBar.jsx';
import Systems from './components/Systems.jsx';
import Tools from './components/Tools.jsx';
import Terminal from './components/Terminal.jsx';
import VmViewer from './components/VmViewer.jsx';
import { PenLiveMark } from './components/Icons.jsx';

// VM is deliberately absent: it only appears once a machine is actually
// running, because the tab is useless without a session behind it.
const BASE_TABS = [
  { id: 'systems', label: 'Systems' },
  { id: 'files', label: 'Files' },
  { id: 'tools', label: 'Tools' },
  { id: 'terminal', label: 'Terminal' },
  { id: 'settings', label: 'Settings' },
];
const VM_TAB = { id: 'vm', label: 'VM' };

export default function App() {
  const [phase, setPhase] = useState('loading'); // loading | setup | main
  const [setupReason, setSetupReason] = useState(null);
  const [tab, setTab] = useState('systems');
  const [network, setNetwork] = useState(null);
  const [sysinfo, setSysinfo] = useState(null);
  const [notice, setNotice] = useState(null);
  const [fileTarget, setFileTarget] = useState(null);
  const [vmSession, setVmSession] = useState(null);

  const openFiles = (target = { source: 'pendata' }) => {
    setFileTarget({ ...target, nonce: Date.now() });
    setTab('files');
  };

  const openVm = (session) => {
    setVmSession(session);
    setTab('vm');
  };

  const closeVm = () => {
    setVmSession(null);
    setTab((current) => (current === 'vm' ? 'systems' : current));
  };

  const tabs = useMemo(
    () => (vmSession ? [...BASE_TABS.slice(0, -1), VM_TAB, BASE_TABS.at(-1)] : BASE_TABS),
    [vmSession]
  );

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
      <div className="loading-screen">
        <div className="loading-mark"><PenLiveMark size={42} /></div>
        <div className="loading-title">PenLive</div>
        <div className="loading-copy"><span className="spinner" /> Preparing your workspace…</div>
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
        tabs={tabs}
        activeTab={tab}
        onSelectTab={setTab}
        onOpenNetwork={() => setTab('settings')}
        onOpenKeyboard={() => setTab('settings')}
        onPower={powerMenu}
      />

      {tab === 'systems' && <Systems network={network} onNotice={setNotice} onOpenFiles={openFiles} onOpenVm={openVm} />}
      {tab === 'files' && <FileManager onNotice={setNotice} target={fileTarget} />}
      {tab === 'tools' && <Tools />}
      {tab === 'terminal' && <Terminal />}
      {tab === 'vm' && <VmViewer session={vmSession} onClosed={closeVm} />}
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
