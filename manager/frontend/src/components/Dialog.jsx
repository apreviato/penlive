import React from 'react';

/* In-page dialog replacing window.confirm/alert.

   Native dialogs are technically available in the kiosk, but they render with
   browser chrome that breaks the "this is an appliance, not a web page"
   illusion, and they block the event loop while a download is streaming
   progress. */

export default function Dialog({ notice, onClose }) {
  if (!notice) return null;

  const isConfirm = notice.kind === 'confirm';

  return (
    <div className="modal-backdrop">
      <div className="modal modal-narrow">
        <div className="modal-head">
          <h2 className="modal-title">{notice.title}</h2>
        </div>
        <div className="modal-body">
          <p className="dialog-text">{notice.message}</p>
        </div>
        <div className="modal-foot end">
          <div className="button-row">
            {isConfirm && (
              <button className="btn" onClick={onClose}>
                Cancel
              </button>
            )}
            <button
              className={`btn ${notice.danger ? 'btn-danger-solid' : 'btn-primary'}`}
              onClick={() => {
                notice.onConfirm?.();
                onClose();
              }}
              autoFocus
            >
              {notice.confirmLabel || 'OK'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
