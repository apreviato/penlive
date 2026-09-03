import React, { useCallback, useEffect, useRef, useState } from 'react';
import RFB from '@novnc/novnc';
import { api } from '../api/client.js';
import { formatBytes } from '../format.js';
import VmSessions from './VmSessions.jsx';

// QEMU can drop the first connection while it is still setting the display up,
// so a single failure is not yet a reason to tell the user anything is wrong.
const MAX_ATTEMPTS = 5;
const RETRY_DELAY_MS = 800;

function VmStatus({ status }) {
  const connected = status === 'connected';
  // 'restoring' deliberately reads as waiting, not as connected: the display
  // answers long before the guest inside it does.
  const label = status.startsWith('reconnecting')
    ? status.replace('reconnecting', 'Reconnecting')
    : {
        connecting: 'Connecting…',
        connected: 'Connected',
        disconnected: 'Disconnected',
        restoring: 'Restoring session…',
      }[status] || status;

  return (
    <span
      className={`vm-status ${connected ? 'vm-status-connected' : 'vm-status-waiting'}`}
      role="status"
      aria-live="polite"
    >
      <span className="vm-status-dot" />
      {label}
    </span>
  );
}

function describeDisk(disk) {
  return [disk.model, disk.size ? formatBytes(disk.size) : null, disk.path]
    .filter(Boolean)
    .join(' · ');
}

export default function VmViewer({
  session, sessions = [], saving = [], onSessionChanged, onClosed, onSessionsChanged,
}) {
  const screenRef = useRef(null);
  const [status, setStatus] = useState('connecting');
  const [error, setError] = useState(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [diskDialog, setDiskDialog] = useState(false);
  const [disks, setDisks] = useState([]);
  const [selectedDisk, setSelectedDisk] = useState('');
  const [diskConfirmation, setDiskConfirmation] = useState('');
  const [diskLoading, setDiskLoading] = useState(false);
  const [diskBusy, setDiskBusy] = useState(false);
  const [diskError, setDiskError] = useState(null);
  const [sessionBusy, setSessionBusy] = useState(null);
  const [restoring, setRestoring] = useState(false);
  // Read inside the noVNC callbacks, which capture their closure once.
  const restoringRef = useRef(false);
  restoringRef.current = restoring;

  useEffect(() => {
    if (!session || !screenRef.current) return undefined;

    let attempt = 0;
    let retryTimer = null;
    let rfb = null;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      attempt += 1;
      if (!restoringRef.current) {
        setStatus(attempt === 1 ? 'connecting' : `reconnecting (${attempt}/${MAX_ATTEMPTS})`);
      }
      const host = window.location.hostname || '127.0.0.1';
      rfb = new RFB(screenRef.current, `ws://${host}:${session.websocket_port}`);
      rfb.scaleViewport = true;
      rfb.resizeSession = true;
      rfb.background = '#050607';
      rfb.addEventListener('connect', () => {
        if (cancelled) return;
        attempt = 0;
        setStatus('connected');
        setError(null);
      });
      rfb.addEventListener('disconnect', (event) => {
        if (cancelled) return;
        if (event.detail?.clean) {
          setStatus('disconnected');
          return;
        }
        // While a saved session is still being read back, QEMU is busy with
        // the stream and can drop a connection or two. Giving up after five
        // tries and announcing "the virtual machine stopped" would be wrong
        // about a machine that is in the middle of coming back.
        if (attempt < MAX_ATTEMPTS || restoringRef.current) {
          retryTimer = setTimeout(connect, RETRY_DELAY_MS);
          return;
        }
        setStatus('disconnected');
        // Say which of the two things went wrong: the machine died, or we
        // could not reach its display.
        api
          .vmStatus(session.image.id)
          .then(({ running }) =>
            setError(
              running
                ? 'Could not reach the virtual machine display. It is still running — try Stop VM and start it again.'
                : 'The virtual machine stopped. Start it again from Systems.'
            )
          )
          .catch(() => setError('The virtual-machine display disconnected and could not be restored.'));
      });
      rfb.addEventListener('securityfailure', (event) =>
        setError(event.detail?.reason || 'VNC security negotiation failed.')
      );
    };

    connect();
    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
      rfb?.disconnect();
    };
  }, [session]);

  // A resumed guest is loaded in the background: the display is up long before
  // the machine is, and without this the user watches a frozen frame with no
  // way to tell it apart from a resume that failed.
  const startedRestoring = Boolean(session?.restoring);
  useEffect(() => {
    if (!startedRestoring) {
      setRestoring(false);
      return undefined;
    }
    setRestoring(true);
    let cancelled = false;
    const poll = async () => {
      try {
        const state = await api.vmStatus(session.image.id);
        if (cancelled) return;
        if (state.restore_error) {
          setError(`Could not resume the saved session: ${state.restore_error}`);
          setRestoring(false);
          clearInterval(id);
        } else if (!state.restoring) {
          setRestoring(false);
          clearInterval(id);
          onSessionsChanged?.();
        }
      } catch {
        // A failed poll says nothing; the next one will.
      }
    };
    const id = setInterval(poll, 1500);
    poll();
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [startedRestoring, session, onSessionsChanged]);

  useEffect(() => {
    if (!fullscreen) return undefined;
    const leaveFullscreen = (event) => {
      if (event.ctrlKey && event.altKey && event.key.toLowerCase() === 'f') {
        event.preventDefault();
        event.stopPropagation();
        setFullscreen(false);
      }
    };
    window.addEventListener('keydown', leaveFullscreen, true);
    return () => window.removeEventListener('keydown', leaveFullscreen, true);
  }, [fullscreen]);

  useEffect(() => {
    // noVNC scales on window resize. Expanding an element with CSS does not
    // emit one by itself, so notify it after the new layout is committed.
    const frame = requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    return () => cancelAnimationFrame(frame);
  }, [fullscreen]);

  const stop = useCallback(async () => {
    try {
      await api.stopVm(session.image.id);
    } catch (err) {
      setError(err.message);
      return;
    }
    onClosed();
  }, [session, onClosed]);

  const saveSession = useCallback(async () => {
    setSessionBusy(session.image.id);
    try {
      await api.saveVmSession(session.image.id);
    } catch (err) {
      setError(err.message);
      setSessionBusy(null);
      return;
    }
    // The VM is on its way down as its memory is written out. Hand the tab back
    // to the session list, where the write reports its own progress.
    onClosed({ stay: true });
  }, [session, onClosed]);

  const resumeSession = useCallback(
    async (saved) => {
      setSessionBusy(saved.image_id);
      setError(null);
      try {
        const started = await api.startVm(saved.image_id, { resume: true });
        onSessionChanged({
          ...started,
          image: { id: saved.image_id, name: saved.image_name || saved.image_id },
        });
      } catch (err) {
        setError(err.message);
      } finally {
        setSessionBusy(null);
        onSessionsChanged?.();
      }
    },
    [onSessionChanged, onSessionsChanged]
  );

  const deleteSession = useCallback(
    async (saved) => {
      setSessionBusy(saved.image_id);
      setError(null);
      try {
        await api.deleteVmSession(saved.image_id);
      } catch (err) {
        setError(err.message);
      } finally {
        setSessionBusy(null);
        onSessionsChanged?.();
      }
    },
    [onSessionsChanged]
  );

  const openDiskDialog = useCallback(async () => {
    setFullscreen(false);
    setDiskDialog(true);
    setDiskLoading(true);
    setDiskError(null);
    setSelectedDisk('');
    setDiskConfirmation('');
    try {
      const result = await api.toolDevices();
      if (!result.available) throw new Error('The physical-drive service is not available.');
      setDisks((result.disks || []).filter((disk) => !disk.penlive && !disk.readonly));
    } catch (err) {
      setDiskError(err.message);
      setDisks([]);
    } finally {
      setDiskLoading(false);
    }
  }, []);

  const attachDisk = useCallback(async () => {
    setDiskBusy(true);
    setDiskError(null);
    try {
      const result = await api.attachVmDisk(
        session.image.id,
        selectedDisk,
        diskConfirmation.trim()
      );
      onSessionChanged({ ...result, image: session.image });
      setDiskDialog(false);
      setStatus('connecting');
      setError(null);
    } catch (err) {
      setDiskError(err.message);
    } finally {
      setDiskBusy(false);
    }
  }, [session, selectedDisk, diskConfirmation, onSessionChanged]);

  if (!session) {
    // "No virtual machine is running" is only worth saying when that is the
    // whole story. With a session on the drive — or one being written right
    // now — it is a true sentence about the wrong thing, sitting above the
    // list the user actually came here for.
    const nothingToShow = sessions.length === 0 && saving.length === 0;
    return (
      <div className="content">
        {error && (
          <div className="banner banner-error">
            <span>{error}</span>
            <button className="btn btn-sm" onClick={() => setError(null)}>Dismiss</button>
          </div>
        )}
        {nothingToShow && (
          <div className="empty">
            <h2>No virtual machine is running</h2>
            <p>
              Choose Run VM on a downloaded system. The VM has no access to this computer’s
              physical drives, and its temporary changes are discarded when it stops — unless you
              save the session first.
            </p>
          </div>
        )}

        <div className="section-header">
          <h2 className="section-title">Saved sessions</h2>
        </div>
        <p className="section-copy">
          A saved session holds the whole machine — its memory, its open programs — on the PenLive
          drive. Resuming picks it up mid-sentence, even after this computer has been restarted.
          Resuming uses the session up; save again to keep it.
        </p>
        <VmSessions
          sessions={sessions}
          saving={saving}
          busyId={sessionBusy}
          onResume={resumeSession}
          onDelete={deleteSession}
        />
      </div>
    );
  }

  const savingThis = saving.find((entry) => entry.image_id === session.image.id);

  return (
    <div className="content vm-page">
      <div className="section-header">
        <div>
          <h2 className="section-title">{session.image.name}</h2>
          <p className="section-copy">
            Virtual machine · {session.kvm ? 'hardware acceleration' : 'software emulation'}
          </p>
        </div>
        <div className="button-row vm-actions">
          <VmStatus status={restoring ? 'restoring' : status} />
          <button className="btn" onClick={openDiskDialog} disabled={Boolean(session.physical_disk)}>
            {session.physical_disk ? 'Drive access enabled' : 'Enable drive access'}
          </button>
          <button className="btn" onClick={() => setFullscreen(true)}>
            Full screen
          </button>
          <button
            className="btn btn-primary"
            onClick={saveSession}
            disabled={
              Boolean(session.physical_disk) ||
              Boolean(savingThis) ||
              restoring ||
              sessionBusy !== null
            }
            title={
              session.physical_disk
                ? 'A session with direct drive access cannot be frozen: the drive can change while it sleeps.'
                : 'Write this machine to the PenLive drive and shut it down, to resume later'
            }
          >
            {sessionBusy === session.image.id ? <span className="spinner" /> : 'Save & suspend'}
          </button>
          <button className="btn btn-danger" onClick={stop}>Stop VM</button>
        </div>
      </div>
      {session.physical_disk ? (
        <div className="banner banner-error vm-safety-note">
          <span>
            <strong>Direct write access is active for {session.physical_disk}.</strong> The installer
            can erase or repartition that entire drive. Stop the VM before unplugging the drive or
            shutting the computer down. Firmware mode: {session.firmware === 'uefi' ? 'UEFI' : 'BIOS'}.
          </span>
        </div>
      ) : (
        <div className="banner banner-info vm-safety-note">
          <span>
            <strong>Your physical drives are protected.</strong> This VM receives only the ISO as a
            read-only CD-ROM—there is no local or persistent virtual disk to install onto. Changes
            made inside it disappear when the VM stops; <strong>Save &amp; suspend</strong> keeps
            them, and survives a restart of this computer.
          </span>
        </div>
      )}
      {restoring && (
        <div className="banner banner-info">
          <span>
            <span className="spinner" /> Reading the saved session back off the drive. The screen
            comes to life where you left it — this takes a moment for a large machine.
          </span>
        </div>
      )}
      {error && <div className="banner banner-error">{error}</div>}
      <div className={`vm-stage ${fullscreen ? 'vm-stage-fullscreen' : ''}`}>
        <div className="vm-screen" ref={screenRef} />
        {fullscreen && (
          <>
            <div className="vm-fullscreen-hotspot" aria-hidden="true" />
            <div className="vm-fullscreen-toolbar">
              <strong>{session.image.name}</strong>
              <VmStatus status={status} />
              <button className="btn btn-sm" onClick={() => setFullscreen(false)}>
                Exit full screen
              </button>
            </div>
            <div className="vm-fullscreen-hint">
              Ctrl+Alt+F to return · move the pointer to the top for controls
            </div>
          </>
        )}
      </div>
      {diskDialog && (
        <div className="modal-backdrop">
          <div className="modal modal-narrow">
            <div className="modal-head">
              <div>
                <h2 className="modal-title">Install onto a physical drive</h2>
                <p className="modal-sub">The VM will restart with direct access to one drive.</p>
              </div>
              <button className="btn btn-icon" onClick={() => setDiskDialog(false)} disabled={diskBusy}>
                ×
              </button>
            </div>
            <div className="modal-body vm-disk-dialog">
              <div className="banner banner-error">
                <span>
                  <strong>This can permanently erase the selected drive.</strong> PenLive’s USB drive
                  is excluded. Any mounted partitions on the target will be safely detached before
                  the VM restarts.
                </span>
              </div>
              <label className="form-label" htmlFor="vm-physical-disk">Physical drive</label>
              {diskLoading ? (
                <div className="form-help"><span className="spinner" /> Scanning drives…</div>
              ) : (
                <select
                  id="vm-physical-disk"
                  className="input"
                  value={selectedDisk}
                  onChange={(event) => {
                    setSelectedDisk(event.target.value);
                    setDiskConfirmation('');
                  }}
                  disabled={diskBusy}
                >
                  <option value="">Select a drive…</option>
                  {disks.map((disk) => (
                    <option key={disk.path} value={disk.path}>{describeDisk(disk)}</option>
                  ))}
                </select>
              )}
              {!diskLoading && disks.length === 0 && !diskError && (
                <div className="form-help">No eligible physical drive was found.</div>
              )}
              {selectedDisk && (
                <>
                  <label className="form-label" htmlFor="vm-disk-confirmation">
                    Type <strong>{selectedDisk}</strong> to confirm
                  </label>
                  <input
                    id="vm-disk-confirmation"
                    className="input"
                    value={diskConfirmation}
                    onChange={(event) => setDiskConfirmation(event.target.value)}
                    placeholder={selectedDisk}
                    autoComplete="off"
                    spellCheck="false"
                    disabled={diskBusy}
                  />
                  <p className="form-help">
                    Installation happens directly on this drive. UEFI boot entries created inside
                    the VM may not appear in the notebook firmware; after installation, use F12 and
                    select the installed drive.
                  </p>
                </>
              )}
              {diskError && <div className="banner banner-error">{diskError}</div>}
            </div>
            <div className="modal-foot end">
              <button className="btn" onClick={() => setDiskDialog(false)} disabled={diskBusy}>
                Cancel
              </button>
              <button
                className="btn btn-danger"
                onClick={attachDisk}
                disabled={!selectedDisk || diskConfirmation.trim() !== selectedDisk || diskBusy}
              >
                {diskBusy ? <><span className="spinner" /> Restarting VM…</> : 'Restart with drive access'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
