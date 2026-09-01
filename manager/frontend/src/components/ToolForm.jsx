import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client.js';
import { formatBytes } from '../format.js';

/* Renders a plugin's parameter schema as a form.

   Device pickers are the important part: they mark BootStack's own partitions
   explicitly, because the single worst outcome here is a user imaging over the
   stick they are currently running from. */

function describeDevice(d) {
  const bits = [d.path];
  if (d.size) bits.push(formatBytes(d.size));
  if (d.fstype) bits.push(d.fstype);
  if (d.label) bits.push(`"${d.label}"`);
  if (d.model) bits.push(d.model);
  return bits.join(' · ');
}

function DeviceSelect({ param, value, onChange, devices, loading }) {
  const list = param.type === 'device' ? devices.disks : devices.partitions;
  const filtered = useMemo(() => {
    if (!param.fstypes?.length) return list;
    return list.filter((d) => param.fstypes.includes(d.fstype));
  }, [list, param.fstypes]);

  if (loading) {
    return (
      <div className="form-help">
        <span className="spinner" /> Scanning devices…
      </div>
    );
  }

  if (!devices.available) {
    return (
      <div className="banner banner-warning">
        Device list unavailable — the privileged helper is not running.
      </div>
    );
  }

  if (filtered.length === 0) {
    return <div className="form-help">No matching devices found on this machine.</div>;
  }

  return (
    <>
      <select
        className="input"
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Select…</option>
        {filtered.map((d) => (
          <option key={d.path} value={d.path}>
            {describeDevice(d)}
            {d.bootstack ? '  ⚠ BootStack' : ''}
          </option>
        ))}
      </select>
      {value && filtered.find((d) => d.path === value)?.bootstack && (
        <div className="banner banner-error" style={{ marginTop: 10 }}>
          That is part of the BootStack stick you are running from. Choosing it can
          destroy this system while it is in use.
        </div>
      )}
      {value && filtered.find((d) => d.path === value)?.mountpoint && (
        <div className="banner banner-warning" style={{ marginTop: 10 }}>
          Currently mounted at {filtered.find((d) => d.path === value).mountpoint}. Unmount it
          first, or results may be inconsistent.
        </div>
      )}
    </>
  );
}

export default function ToolForm({ tool, onClose, onStarted }) {
  const [values, setValues] = useState(() => {
    const initial = {};
    for (const p of tool.params) if (p.default !== null && p.default !== undefined) initial[p.name] = p.default;
    return initial;
  });
  const [devices, setDevices] = useState({ disks: [], partitions: [], available: false });
  const [backups, setBackups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [confirmed, setConfirmed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [dev, bk] = await Promise.all([
          api.toolDevices(),
          api.listBackups().catch(() => ({ backups: [] })),
        ]);
        if (cancelled) return;
        setDevices(dev);
        setBackups(bk.backups || []);
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const set = (name, value) => setValues((v) => ({ ...v, [name]: value }));

  const missingRequired = tool.params
    .filter((p) => p.required && !values[p.name] && p.type !== 'checkbox')
    .map((p) => p.label);

  const needsConfirm = tool.danger === 'destructive';
  const canSubmit =
    missingRequired.length === 0 && (!needsConfirm || confirmed) && !submitting;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const job = await api.runTool(tool.id, values);
      onStarted(job.id);
    } catch (err) {
      setError(err.message);
      setSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <div className="modal-head">
          <div>
            <h2 className="modal-title">
              <span className="tool-icon">{tool.icon}</span> {tool.name}
            </h2>
            <div className="modal-sub">{tool.description}</div>
          </div>
        </div>

        {error && <div className="banner banner-error">{error}</div>}

        {tool.danger === 'destructive' && (
          <div className="banner banner-error">
            <strong>This permanently erases data on the target.</strong> There is no undo.
          </div>
        )}
        {tool.danger === 'caution' && (
          <div className="banner banner-warning">
            This modifies the selected disk. Back up anything important first.
          </div>
        )}

        <div className="modal-body">
          {tool.params.map((param) => (
            <div className="form-row" key={param.name}>
              <label className="form-label" htmlFor={`p-${param.name}`}>
                {param.label}
                {!param.required && <span className="optional"> (optional)</span>}
              </label>

              {(param.type === 'device' || param.type === 'partition') && (
                <DeviceSelect
                  param={param}
                  value={values[param.name]}
                  onChange={(v) => set(param.name, v)}
                  devices={devices}
                  loading={loading}
                />
              )}

              {param.type === 'select' && (
                <select
                  id={`p-${param.name}`}
                  className="input"
                  value={values[param.name] ?? ''}
                  onChange={(e) => set(param.name, e.target.value)}
                >
                  <option value="">Select…</option>
                  {param.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              )}

              {param.type === 'backup_image' && (
                <select
                  id={`p-${param.name}`}
                  className="input"
                  value={values[param.name] ?? ''}
                  onChange={(e) => set(param.name, e.target.value)}
                >
                  <option value="">Select…</option>
                  {backups.map((b) => (
                    <option key={b.name} value={b.name}>
                      {b.name} · {formatBytes(b.size)} · {b.kind}
                    </option>
                  ))}
                </select>
              )}

              {param.type === 'text' && (
                <input
                  id={`p-${param.name}`}
                  className="input"
                  value={values[param.name] ?? ''}
                  onChange={(e) => set(param.name, e.target.value)}
                />
              )}

              {param.type === 'checkbox' && (
                <label className="checkbox">
                  <input
                    id={`p-${param.name}`}
                    type="checkbox"
                    checked={Boolean(values[param.name])}
                    onChange={(e) => set(param.name, e.target.checked)}
                  />
                  <span>{param.help || 'Enable'}</span>
                </label>
              )}

              {param.help && param.type !== 'checkbox' && (
                <div className="form-help">{param.help}</div>
              )}
            </div>
          ))}

          {backups.length === 0 && tool.params.some((p) => p.type === 'backup_image') && (
            <div className="banner banner-info">
              No backups on this stick yet. Use the Backup tool first.
            </div>
          )}

          {needsConfirm && (
            <label className="checkbox confirm">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              <span>I understand this will permanently erase data on the selected target.</span>
            </label>
          )}
        </div>

        <div className="modal-foot">
          <span className="form-help">
            {missingRequired.length > 0 && `Required: ${missingRequired.join(', ')}`}
          </span>
          <div className="button-row">
            <button className="btn" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button
              className={`btn ${tool.danger === 'destructive' ? 'btn-danger-solid' : 'btn-primary'}`}
              onClick={submit}
              disabled={!canSubmit}
            >
              {submitting ? <span className="spinner" /> : 'Run'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
