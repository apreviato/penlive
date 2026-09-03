import React from 'react';

/* One scheduled system, one action.

   The selection is already one-shot on both sides - GRUB consumes it before
   booting, and the manager deletes it the moment it starts - so there is
   nothing here to cancel or retry. Restarting is the only thing left to do
   with it, and picking a different system simply replaces it.

   The Secure Boot line is the exception worth the space. Whatever the firmware
   still wants is enforced after PenLive is gone: the user meets it as "bad
   shim signature" on a black screen, with nothing to work backwards from and
   no way to look the answer up. It belongs on screen before they restart, not
   in a dialog they dismissed. */
export default function PendingBootBanner({ pending, onReboot }) {
  if (!pending) return null;

  const secureBoot = pending.secure_boot;
  const blocked = secureBoot?.action === 'unsupported';

  if (blocked) {
    return (
      <div className="banner banner-error">
        <span>
          <strong>{pending.image_name}</strong> is scheduled, but will not start as things
          stand. {secureBoot.message}
        </span>
        <span className="button-row">
          <button className="btn btn-sm btn-primary" onClick={onReboot}>
            Restart now
          </button>
        </span>
      </div>
    );
  }

  const enrolling = secureBoot?.action === 'enrol';

  return (
    <div className={`banner banner-warning ${enrolling ? 'banner-stacked' : ''}`}>
      <span>
        <strong>{pending.image_name}</strong> starts on the next restart, this once. If this
        computer needs F12 to boot from USB, choose the PenLive drive there.
        {enrolling && <> {secureBoot.message}</>}
      </span>
      {enrolling && secureBoot.steps?.length > 0 && (
        <ol className="enrol-steps">
          {secureBoot.steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      )}
      {enrolling && secureBoot.password && (
        <div className="enrol-code">
          <span className="enrol-code-label">Code to type</span>
          <span className="enrol-code-value">{secureBoot.password}</span>
        </div>
      )}
      <span className="button-row">
        <button className="btn btn-sm btn-primary" onClick={onReboot}>
          Restart now
        </button>
      </span>
    </div>
  );
}
