import React from 'react';
import { formatBytes } from '../format.js';

/* Frozen VM sessions, one per system.

   A session is the whole guest — memory, running programs, open terminals —
   written to the stick. It survives a restart of this computer, which is the
   only reason it exists: everything inside a VM otherwise dies with the
   session, and setting a live environment up again costs more than the disk
   space does. */

function when(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString();
}

export default function VmSessions({
  sessions, saving, busyId, onResume, onDelete, compact = false,
}) {
  const hasAny = sessions.length > 0 || saving.length > 0;
  if (!hasAny) {
    return compact ? null : (
      <div className="form-help">
        No saved sessions. Use <strong>Save &amp; suspend</strong> while a VM is running to keep it
        for later.
      </div>
    );
  }

  return (
    <div className="vm-session-list">
      {saving.map((entry) => (
        <div className="card vm-session" key={`saving-${entry.image_id}`}>
          <div className="vm-session-main">
            <div className="image-name">{entry.image_id}</div>
            {entry.status === 'failed' ? (
              <div className="progress-meta danger-text">
                Could not save this session — {entry.error || 'the write did not finish'}.
              </div>
            ) : (
              <>
                <div className="progress">
                  <div className="progress-bar" style={{ width: `${entry.percent}%` }} />
                </div>
                <div className="progress-meta">
                  <span>
                    Freezing the machine · {formatBytes(entry.transferred_bytes)} of{' '}
                    {formatBytes(entry.total_bytes)} ({entry.percent.toFixed(0)}%)
                  </span>
                  <span>Keep this computer on until it finishes</span>
                </div>
              </>
            )}
          </div>
          {entry.status === 'failed' && (
            <div className="image-actions">
              <button
                className="btn"
                onClick={() => onDelete(entry)}
                disabled={busyId === entry.image_id}
              >
                Dismiss
              </button>
            </div>
          )}
        </div>
      ))}

      {sessions.map((session) => (
        <div className="card vm-session" key={session.image_id}>
          <div className="vm-session-main">
            <div className="image-name">{session.image_name || session.image_id}</div>
            <div className="image-sub">
              <span className="badge badge-ready">saved session</span>
              <span>{formatBytes(session.size_bytes)}</span>
              <span>{session.memory_mib} MiB RAM</span>
              {session.cpus && <span>{session.cpus} CPU</span>}
              {when(session.saved_at) && <span>{when(session.saved_at)}</span>}
            </div>
            {!session.usable && (
              <div className="progress-meta danger-text">
                Cannot be resumed — {session.unusable_reason}.
              </div>
            )}
          </div>
          <div className="image-actions">
            <button
              className="btn btn-primary"
              onClick={() => onResume(session)}
              disabled={!session.usable || busyId === session.image_id}
              title={session.usable ? 'Start this system where it left off' : session.unusable_reason}
            >
              {busyId === session.image_id ? <span className="spinner" /> : 'Resume'}
            </button>
            <button
              className="btn btn-danger"
              onClick={() => onDelete(session)}
              disabled={busyId === session.image_id}
            >
              Delete
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
