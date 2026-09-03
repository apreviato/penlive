import React from 'react';

export default function PendingBootBanner({ pending, onClear, onReboot }) {
  if (!pending) return null;

  const exhausted = pending.attempts >= pending.max_attempts;

  return (
    <div className={`banner ${exhausted ? 'banner-error' : 'banner-warning'}`}>
      <span>
        {exhausted ? (
          <>
            <strong>{pending.image_name}</strong> failed to start {pending.attempts} times and was
            disabled automatically. PenLive will keep starting normally.
          </>
        ) : (
          <>
            <strong>{pending.image_name}</strong>{' '}
            {pending.attempts > 0
              ? `did not take over the previous boot (attempt ${pending.attempts} of ${pending.max_attempts}). You can retry it.`
              : 'is scheduled for the next boot.'}{' '}
            If this computer requires F12, select the USB drive there; PenLive will then highlight
            this system in its boot menu.
          </>
        )}
      </span>
      <span className="button-row">
        {!exhausted && (
          <button className="btn btn-sm btn-primary" onClick={onReboot}>
            Restart now
          </button>
        )}
        <button className="btn btn-sm" onClick={onClear}>
          Cancel
        </button>
      </span>
    </div>
  );
}
