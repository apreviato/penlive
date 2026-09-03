import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client.js';

/* Secure Boot status, and the one-time key enrolment that makes downloaded
 * systems bootable while it stays on.
 *
 * PenLive itself always boots under Secure Boot (signed shim + signed GRUB).
 * What needs a key is booting someone *else's* kernel: an Ubuntu or Fedora
 * kernel is signed by Canonical or Red Hat, which the firmware does not trust
 * through Debian's shim. Enrolling a machine owner key lets PenLive counter-sign
 * those kernels.
 *
 * Nothing here is a prerequisite for booting a downloaded system: pressing Boot
 * creates and queues the key on its own. This panel is the status readout, and
 * the place to come back to for the code MokManager asks for - that prompt
 * appears before PenLive is running, so there is nowhere else to read it.
 *
 * Enrolment finishes at the MokManager screen on the next boot, which cannot be
 * automated - that physical-presence step is the point of the mechanism - so
 * the job here is to make the instructions impossible to misread.
 */
export default function SecureBootPanel() {
  const [state, setState] = useState(null);
  const [enrolment, setEnrolment] = useState(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setState(await api.secureBootState());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const enrol = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.enrolSecureBootKey();
      if (result.already_enrolled) {
        await refresh();
      } else {
        setEnrolment(result);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  if (!state) {
    return (
      <div className="card">
        {loading ? (
          <><span className="spinner" /> Checking Secure Boot…</>
        ) : (
          <div className="sb-row">
            <span className="badge badge-sb-warn">Check failed</span>
            <span>{error || 'Secure Boot status could not be read.'}</span>
            <button className="btn btn-sm" onClick={refresh}>Retry</button>
          </div>
        )}
      </div>
    );
  }

  if (!state.secure_boot_enabled) {
    return (
      <div className="card">
        <div className="sb-row">
          <span className="badge badge-sb-ok">Off</span>
          <span>
            Secure Boot is disabled in this machine&apos;s firmware. Every system boots
            without restriction; nothing to configure here.
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="sb-row">
        <span className="badge badge-sb-ok">On</span>
        <span>PenLive itself boots normally — it ships a signed bootloader.</span>
      </div>

      {state.key_enrolled && (
        <div className="sb-row">
          <span className="badge badge-sb-ok">Key enrolled</span>
          <span>Downloaded systems are signed with this machine&apos;s key and boot too.</span>
        </div>
      )}

      {!state.key_enrolled && state.key_pending && (
        <>
          <div className="sb-row">
            <span className="badge badge-sb-warn">Awaiting reboot</span>
            <span>
              A key is queued. Reboot and complete enrolment at the blue MokManager
              screen to finish.
            </span>
          </div>
          {state.enrolment_password && !enrolment && (
            <div className="sb-enrolment">
              <p>Finish it at the blue screen on the next restart:</p>
              <ol className="sb-steps">
                {(state.enrolment_steps || []).map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
              <div className="sb-password">{state.enrolment_password}</div>
            </div>
          )}
        </>
      )}

      {!state.key_enrolled && !state.key_pending && (
        <>
          <div className="sb-row">
            <span className="badge badge-sb-warn">Limited</span>
            <span>
              Downloaded systems whose kernel is signed by another vendor (Ubuntu,
              Fedora, and most others) cannot boot natively while Secure Boot is on.
            </span>
          </div>
          <p className="muted">
            You do not have to do anything here: the first time you press Boot on a
            downloaded system, PenLive creates a key for this machine, signs that
            system&apos;s kernel with it and tells you the code to type at the firmware
            screen. Enrol it now if you would rather get that step out of the way. The
            alternative is turning Secure Boot off in your firmware setup.
          </p>
          <button className="btn" onClick={enrol} disabled={busy || !state.tools_available}>
            {busy ? 'Preparing…' : 'Enrol a key now'}
          </button>
          {!state.tools_available && (
            <p className="muted">Signing tools are unavailable on this system.</p>
          )}
        </>
      )}

      {enrolment && (
        <div className="sb-enrolment">
          <p>
            <strong>Reboot to finish.</strong> Its password prompt runs before any
            keyboard layout is loaded, which is why this code is digits only.
          </p>
          <ol className="sb-steps">
            {(enrolment.steps || []).map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
          <div className="sb-password">{enrolment.password}</div>
          <p className="muted">
            The prompt appears before PenLive is running, so write the code down — it
            stays on this panel until the key is enrolled if you need it again.
          </p>
        </div>
      )}

      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
