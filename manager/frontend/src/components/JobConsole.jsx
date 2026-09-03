import React, { useEffect, useRef, useState } from 'react';
import { api, jobStreamSocket } from '../api/client.js';
import { formatDuration } from '../format.js';

const STATE_LABEL = {
  running: 'Running',
  success: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export default function JobConsole({ jobId, onClose }) {
  const [job, setJob] = useState(null);
  const [lines, setLines] = useState([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const logRef = useRef(null);

  useEffect(() => {
    if (!jobId) return undefined;
    setLines([]);
    const ws = jobStreamSocket(jobId, (snapshot) => {
      if (snapshot.error && !snapshot.id) {
        setLines((prev) => [...prev, `ERROR: ${snapshot.error}`]);
        return;
      }
      setJob(snapshot);
      if (snapshot.log?.length) {
        setLines((prev) => [...prev, ...snapshot.log]);
      }
    });
    return () => ws.close();
  }, [jobId]);

  // Follow the tail, but stop fighting the user if they scroll up to read.
  useEffect(() => {
    if (autoScroll && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  const onScroll = () => {
    const el = logRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setAutoScroll(atBottom);
  };

  const cancel = async () => {
    try {
      await api.cancelJob(jobId);
    } catch {
      // The job may have finished between render and click; the stream will say so.
    }
  };

  const running = job?.state === 'running';
  const progress = job?.progress;

  return (
    <div className="modal-backdrop">
      <div className="modal modal-wide">
        <div className="modal-head">
          <div>
            <h2 className="modal-title">{job?.title || 'Job'}</h2>
            <div className="modal-sub">
              <span className={`badge badge-job-${job?.state || 'running'}`}>
                {STATE_LABEL[job?.state] || 'Starting'}
              </span>
              {job?.started_at && (
                <span> {formatDuration(job.started_at, job.finished_at)}</span>
              )}
              {job?.exit_code !== null && job?.exit_code !== undefined && !running && (
                <span> · exit {job.exit_code}</span>
              )}
              {job?.log_file && <span> · saved to {job.log_file}</span>}
            </div>
          </div>
          <button className="btn btn-sm" onClick={onClose} disabled={running}>
            {running ? 'Running…' : 'Close'}
          </button>
        </div>

        {running && progress !== null && progress !== undefined && (
          <div className="progress">
            <div className="progress-bar" style={{ width: `${progress}%` }} />
          </div>
        )}

        {job?.error && <div className="banner banner-error">{job.error}</div>}
        {job?.log_error && <div className="banner banner-warning">{job.log_error}</div>}

        <pre className="console" ref={logRef} onScroll={onScroll}>
          {lines.length === 0 ? 'Waiting for output…' : lines.join('\n')}
        </pre>

        <div className="modal-foot">
          {!autoScroll && (
            <button
              className="btn btn-sm"
              onClick={() => {
                setAutoScroll(true);
                if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
              }}
            >
              Jump to end
            </button>
          )}
          <div className="button-row">
            {running && (
              <button className="btn btn-danger" onClick={cancel}>
                Cancel job
              </button>
            )}
            <button className="btn" onClick={onClose} disabled={running}>
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
