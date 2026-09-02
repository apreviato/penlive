import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client.js';
import JobConsole from './JobConsole.jsx';
import ToolForm from './ToolForm.jsx';

const DANGER_LABEL = {
  safe: null,
  caution: 'Modifies disk',
  destructive: 'Erases data',
};

export default function Tools() {
  const [tools, setTools] = useState([]);
  const [categories, setCategories] = useState({});
  const [daemonAvailable, setDaemonAvailable] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTool, setActiveTool] = useState(null);
  const [activeJob, setActiveJob] = useState(null);
  const [recentJobs, setRecentJobs] = useState([]);

  const load = useCallback(async () => {
    try {
      const data = await api.listTools();
      setTools(data.tools);
      setCategories(data.categories);
      setDaemonAvailable(data.daemon_available);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
    try {
      const jobs = await api.listJobs();
      setRecentJobs((jobs.jobs || []).slice().reverse().slice(0, 5));
    } catch {
      setRecentJobs([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const grouped = tools.reduce((acc, tool) => {
    (acc[tool.category] ||= []).push(tool);
    return acc;
  }, {});

  return (
    <div className="content">
      {error && (
        <div className="banner banner-error">
          <span>{error}</span>
          <button className="btn btn-sm" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {!daemonAvailable && !loading && (
        <div className="banner banner-warning">
          The privileged helper is not running, so tools cannot be started. On a real
          PenLive stick it starts automatically; check{' '}
          <code>systemctl status penlive-daemon</code>.
        </div>
      )}

      {loading && (
        <div className="empty">
          <span className="spinner" /> Loading tools…
        </div>
      )}

      {recentJobs.length > 0 && (
        <>
          <h2 className="section-title">Recent jobs</h2>
          <div className="job-list">
            {recentJobs.map((job) => (
              <button key={job.id} className="job-row" onClick={() => setActiveJob(job.id)}>
                <span className={`badge badge-job-${job.state}`}>{job.state}</span>
                <span className="job-title">{job.title}</span>
                {job.progress !== null && job.progress !== undefined && job.state === 'running' && (
                  <span className="job-progress">{Math.round(job.progress)}%</span>
                )}
              </button>
            ))}
          </div>
        </>
      )}

      {Object.entries(grouped).map(([category, items]) => (
        <section key={category}>
          <h2 className="section-title">{categories[category] || category}</h2>
          <div className="tool-grid">
            {items.map((tool) => (
              <button
                key={tool.id}
                className={`tool-card ${tool.available ? '' : 'disabled'} danger-${tool.danger}`}
                onClick={() => tool.available && setActiveTool(tool)}
                disabled={!tool.available}
                title={tool.unavailable_reason || tool.description}
              >
                <div className="tool-head">
                  <span className="tool-icon">{tool.icon}</span>
                  <span className="tool-name">{tool.name}</span>
                </div>
                <div className="tool-desc">{tool.description}</div>
                <div className="tool-foot">
                  {DANGER_LABEL[tool.danger] && (
                    <span className={`tag tag-${tool.danger}`}>{DANGER_LABEL[tool.danger]}</span>
                  )}
                  {!tool.available && (
                    <span className="tag tag-unavailable">{tool.unavailable_reason}</span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </section>
      ))}

      {activeTool && (
        <ToolForm
          tool={activeTool}
          onClose={() => setActiveTool(null)}
          onStarted={(jobId) => {
            setActiveTool(null);
            setActiveJob(jobId);
          }}
        />
      )}

      {activeJob && (
        <JobConsole
          jobId={activeJob}
          onClose={() => {
            setActiveJob(null);
            load();
          }}
        />
      )}
    </div>
  );
}
