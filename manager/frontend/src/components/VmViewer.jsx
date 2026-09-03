import React, { useCallback, useEffect, useRef, useState } from 'react';
import RFB from '@novnc/novnc';
import { api } from '../api/client.js';
import { formatBytes } from '../format.js';

// QEMU can drop the first connection while it is still setting the display up,
// so a single failure is not yet a reason to tell the user anything is wrong.
const MAX_ATTEMPTS = 5;
const RETRY_DELAY_MS = 800;

function VmStatus({ status }) {
  const connected = status === 'connected';
  const label = status.startsWith('reconnecting')
    ? status.replace('reconnecting', 'Reconnecting')
    : {
        connecting: 'Connecting…',
        connected: 'Connected',
        disconnected: 'Disconnected',
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

export default function VmViewer({ session, onSessionChanged, onClosed }) {
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

  useEffect(() => {
    if (!session || !screenRef.current) return undefined;

    let attempt = 0;
    let retryTimer = null;
    let rfb = null;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      attempt += 1;
      setStatus(attempt === 1 ? 'connecting' : `reconnecting (${attempt}/${MAX_ATTEMPTS})`);
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
        if (attempt < MAX_ATTEMPTS) {
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
    return (
      <div className="content">
        <div className="empty">
          <h2>No virtual machine is running</h2>
          <p>
            Choose Run VM on a downloaded system. The VM has no access to this computer’s
            physical drives, and its temporary changes are discarded when it stops.
          </p>
        </div>
      </div>
    );
  }

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
          <VmStatus status={status} />
          <button className="btn" onClick={openDiskDialog} disabled={Boolean(session.physical_disk)}>
            {session.physical_disk ? 'Drive access enabled' : 'Enable drive access'}
          </button>
          <button className="btn" onClick={() => setFullscreen(true)}>
            Full screen
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
            made inside it disappear when the VM stops.
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
