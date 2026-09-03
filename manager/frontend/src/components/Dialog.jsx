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
          {notice.detail && <p className="dialog-text dialog-detail">{notice.detail}</p>}
          {/* Screens the user has to walk through after this app is gone,
              and a code to type into one of them. Neither survives being
              folded into a sentence: the steps get skimmed and the digits get
              read straight past. */}
          {notice.steps?.length > 0 && (
            <ol className="enrol-steps">
              {notice.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          )}
          {notice.code && (
            <div className="enrol-code">
              <span className="enrol-code-label">{notice.codeLabel || 'Code'}</span>
              <span className="enrol-code-value">{notice.code}</span>
            </div>
          )}
        </div>
        <div className="modal-foot end">
          <div className="button-row">
            {isConfirm && (
              <button
                className="btn"
                onClick={() => {
                  // Some choices are between two actions rather than between an
                  // action and nothing, so the second button gets to do
                  // something. Without onCancel this stays a plain dismissal.
                  notice.onCancel?.();
                  onClose();
                }}
              >
                {notice.cancelLabel || 'Cancel'}
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
