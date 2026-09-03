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

// VM is deliberately absent: it only appears once there is something behind
// it — a running machine, or a session frozen on the drive waiting to be
// resumed or deleted. An empty VM tab is a tab with nothing to say.
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
  const [vmSessions, setVmSessions] = useState({ sessions: [], saving: [] });
  const [powering, setPowering] = useState(null);
  const [poweringSlow, setPoweringSlow] = useState(false);

  const openFiles = (target = { source: 'pendata' }) => {
    setFileTarget({ ...target, nonce: Date.now() });
    setTab('files');
  };

  const openVm = (session) => {
    setVmSession(session);
    setTab('vm');
  };

  const refreshVmSessions = useCallback(async () => {
    try {
      const result = await api.vmSessions();
      setVmSessions({ sessions: result.sessions || [], saving: result.saving || [] });
    } catch {
      // Keep whatever was last known: a failed poll is not evidence that a
      // session on the drive has gone away.
    }
  }, []);

  const closeVm = (options = {}) => {
    setVmSession(null);
    refreshVmSessions();
    // A save leaves the tab worth staying on: the write reports its progress
    // there, and the result is a session to resume.
    if (!options.stay) setTab((current) => (current === 'vm' ? 'systems' : current));
  };

  const hasVmContent =
    Boolean(vmSession) || vmSessions.sessions.length > 0 || vmSessions.saving.length > 0;
  const tabs = useMemo(
    () => (hasVmContent ? [...BASE_TABS.slice(0, -1), VM_TAB, BASE_TABS.at(-1)] : BASE_TABS),
    [hasVmContent]
  );

  // Lock the page down in the kiosk, but never on the dev server — locking out
  // reload and devtools would make the UI impossible to work on.
  useEffect(() => installKioskLockdown({ enabled: !isDevServer() }), []);

  useEffect(() => {
    refreshVmSessions();
  }, [refreshVmSessions]);

  useEffect(() => {
    if (!powering) {
      setPoweringSlow(false);
      return undefined;
    }
    const id = setTimeout(() => setPoweringSlow(true), 90000);
    return () => clearTimeout(id);
  }, [powering]);

  // Writing a guest's memory to a USB stick takes minutes, and the only way to
  // know it finished is to ask. Polling stops as soon as nothing is in flight.
  const savingVm = vmSessions.saving.some((entry) => entry.status === 'saving');
  useEffect(() => {
    if (!savingVm) return undefined;
    const id = setInterval(refreshVmSessions, 1500);
    return () => clearInterval(id);
  }, [savingVm, refreshVmSessions]);

  const refreshStatus = useCallback(async () => {
    const [net, info] = await Promise.all([
      api.networkStatus().catch(() => ({ connected: false })),
      api.sysinfo().catch(() => null),
    ]);
    setNetwork(net);
    setSysinfo(info);
    return net;
  }, []);

  // index.html paints a splash before this bundle has even been parsed -- on
  // the live system it is read off a compressed squashfs on a USB stick, which
  // takes real seconds. Clearing it here rather than in main.jsx means it
  // survives until React has actually committed something to replace it with.
  useEffect(() => {
    document.getElementById('boot-splash')?.remove();
  }, []);

  useEffect(() => {
    let cancelled = false;
    // Deliberately not awaited: the status bar's contents must not decide when
    // the manager appears. Both of these end up at NetworkManager through a
    // daemon that answers one request at a time, and on a machine that is still
    // associating its Wi-Fi they are the slowest thing in the boot.
    refreshStatus();
    (async () => {
      let state = null;
      try {
        state = await api.setupState();
      } catch {
        // The first-run wizard is a convenience, not a gate, and it stays
        // reachable from Settings. Showing the manager beats showing nothing.
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
        // Shutting down takes X and the browser away before systemd is done, so
        // without this the last thing the user sees is the app vanishing into a
        // bare screen -- indistinguishable from the machine having hung.
        setPowering('poweroff');
        try {
          await api.poweroff();
        } catch (err) {
          setPowering(null);
          setNotice({ kind: 'info', title: 'Could not power off', message: err.message });
        }
      },
    });

  if (powering) {
    const rebooting = powering === 'reboot';
    return (
      <div className="loading-screen">
        <div className="loading-mark"><PenLiveMark size={42} /></div>
        <div className="loading-title">{rebooting ? 'Restarting' : 'Shutting down'}</div>
        <div className="loading-copy">
          <span className="spinner" /> Saving data and preparing {rebooting ? 'the selected system' : 'power off'}…
        </div>
        <div className="loading-copy">
          {rebooting
            ? 'The selected system will start automatically after the firmware screen.'
            : 'The screen goes blank when it is safe to unplug the stick.'}
        </div>
        {/* Flushing a USB stick with a download's worth of dirty pages behind it
            is genuinely slow, so a long wait here is normal rather than a sign
            of trouble. Saying so beats leaving someone in front of a spinner
            wondering whether the machine has hung — and leaves a way out for
            the case where it really has. */}
        {poweringSlow && (
          <>
            <div className="loading-copy">
              Still writing everything to the drive. This can take a few minutes when a
              download has just finished; the screen goes blank when it is done.
            </div>
            <button className="btn btn-sm" onClick={() => setPowering(null)}>
              Back to PenLive
            </button>
          </>
        )}
      </div>
    );
  }

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

      {tab === 'systems' && (
        <Systems
          network={network}
          onNotice={setNotice}
          onOpenFiles={openFiles}
          onOpenVm={openVm}
          onPowering={setPowering}
          onVmSessionsChanged={refreshVmSessions}
        />
      )}
      {tab === 'files' && <FileManager onNotice={setNotice} target={fileTarget} />}
      {tab === 'tools' && <Tools />}
      {tab === 'terminal' && <Terminal />}
      {tab === 'vm' && (
        <VmViewer
          session={vmSession}
          sessions={vmSessions.sessions}
          saving={vmSessions.saving}
          onSessionChanged={setVmSession}
          onSessionsChanged={refreshVmSessions}
          onClosed={closeVm}
        />
      )}
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
