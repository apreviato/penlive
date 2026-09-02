import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client.js';
import { formatBytes } from '../format.js';
import { FileIcon, FolderIcon, StorageIcon } from './Icons.jsx';

function formatDate(timestamp) {
  if (!timestamp) return '—';
  return new Date(timestamp * 1000).toLocaleString([], {
    year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

export default function FileManager({ onNotice, target }) {
  const [source, setSource] = useState(target?.source || 'pendata');
  const [sourceLabel, setSourceLabel] = useState(target?.label || 'PENDATA');
  const [sources, setSources] = useState({ disks: [], partitions: [], iso_mounts: [] });
  const [path, setPath] = useState('');
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [newFolder, setNewFolder] = useState(false);
  const [folderName, setFolderName] = useState('');
  const [renaming, setRenaming] = useState(null);
  const [renameName, setRenameName] = useState('');
  const [clipboard, setClipboard] = useState(null);

  const readOnly = source.startsWith('iso:');

  const loadSources = useCallback(async () => {
    try { setSources(await api.fileSources()); } catch { /* PENDATA remains usable */ }
  }, []);

  const load = useCallback(async (nextPath = path, nextSource = source) => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.listFiles(nextPath, nextSource);
      setSource(nextSource);
      setSourceLabel(result.label || nextSource);
      setPath(result.path);
      setEntries(result.entries);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [path, source]);

  useEffect(() => {
    load('', target?.source || 'pendata');
    loadSources();
  }, [target]); // eslint-disable-line react-hooks/exhaustive-deps

  const crumbs = useMemo(() => {
    const parts = path ? path.split('/') : [];
    return [{ name: sourceLabel, path: '' }, ...parts.map((name, index) => ({
      name, path: parts.slice(0, index + 1).join('/'),
    }))];
  }, [path, sourceLabel]);

  const action = async (work) => {
    setBusy(true);
    setError(null);
    try { await work(); } catch (err) { setError(err.message); } finally { setBusy(false); }
  };

  const createFolder = () => action(async () => {
    await api.createFolder(path, folderName, source);
    setFolderName(''); setNewFolder(false); await load(path, source);
  });

  const rename = () => action(async () => {
    await api.renameFile(renaming.path, renameName, source);
    setRenaming(null); setRenameName(''); await load(path, source);
  });

  const remove = (entry) => onNotice({
    kind: 'confirm', title: `Delete ${entry.name}?`,
    message: entry.kind === 'directory'
      ? 'The folder and everything inside it will be permanently removed.'
      : 'The file will be permanently removed.',
    confirmLabel: 'Delete', danger: true,
    onConfirm: () => action(async () => {
      await api.deleteFile(entry.path, entry.kind === 'directory', source);
      await load(path, source);
    }),
  });

  const paste = () => action(async () => {
    await api.transferFile(clipboard.source, clipboard.entry.path, source, path, clipboard.move);
    setClipboard(null); await load(path, source); await loadSources();
  });

  const mountPartition = (partition) => action(async () => {
    const result = await api.mountDevice(partition.path);
    await loadSources(); await load('', result.source);
  });

  const unmountPartition = (partition) => action(async () => {
    await api.unmountDevice(partition.path);
    if (source === partition.source) await load('', 'pendata');
    await loadSources();
  });

  const unmountIso = (item) => action(async () => {
    await api.unmountImage(item.image_id);
    if (source === item.source) await load('', 'pendata');
    await loadSources();
  });

  const rescan = () => action(async () => {
    const result = await api.rescanImages();
    onNotice({
      kind: 'info', title: 'ISO scan complete',
      message: `${result.found} ISO file(s) found; ${result.imported} new and ${result.invalid} invalid.`,
    });
    await load(path, source);
  });

  return (
    <div className="content">
      {error && <div className="banner banner-error"><span>{error}</span><button className="btn btn-sm" onClick={() => setError(null)}>Dismiss</button></div>}

      <div className="files-hero">
        <div><h2>Files and drives</h2><p>Browse PENDATA, mounted ISOs and storage connected to this machine.</p></div>
        <div className="button-row">
          {!readOnly && <button className="btn" onClick={() => setNewFolder(true)} disabled={busy}>New folder</button>}
          <button className="btn btn-primary scan-isos-button" onClick={rescan} disabled={busy}>{busy ? <span className="spinner" /> : 'Scan ISOs'}</button>
        </div>
      </div>

      <div className="drive-strip">
        <button className={`drive-card ${source === 'pendata' ? 'active' : ''}`} onClick={() => load('', 'pendata')}>
          <StorageIcon /><span><strong>PENDATA</strong><small>PenLive files</small></span>
        </button>
        {sources.iso_mounts.map((item) => (
          <div className="drive-card-shell" key={item.source}>
            <button className={`drive-card ${source === item.source ? 'active' : ''}`} onClick={() => load('', item.source)}>
              <StorageIcon /><span><strong>{item.label}</strong><small>Mounted ISO · read-only</small></span>
            </button>
            <button className="drive-eject" onClick={() => unmountIso(item)} title="Unmount ISO">Unmount</button>
          </div>
        ))}
        {sources.partitions.filter((item) => !item.penlive).map((item) => (
          <div className="drive-card-shell" key={item.path}>
            <button className={`drive-card ${source === item.source ? 'active' : ''}`} onClick={() => item.mounted ? load('', item.source) : mountPartition(item)}>
              <StorageIcon /><span><strong>{item.label || item.name}</strong><small>{formatBytes(item.size)} · {item.fstype} · {item.mounted ? 'Browse' : 'Mount'}</small></span>
            </button>
            {item.managed_mount && <button className="drive-eject" onClick={() => unmountPartition(item)} title="Unmount safely">Unmount</button>}
          </div>
        ))}
      </div>

      {sources.disks.length > 0 && <div className="disk-summary">{sources.disks.map((disk) => <span key={disk.path}>{disk.model || disk.name} · {formatBytes(disk.size)}{disk.removable ? ' · removable' : ''}</span>)}</div>}

      {clipboard && (
        <div className="banner banner-info clipboard-banner">
          <span>{clipboard.move ? 'Move' : 'Copy'} <strong>{clipboard.entry.name}</strong> to {sourceLabel}/{path}</span>
          <span className="button-row"><button className="btn btn-sm btn-primary" onClick={paste} disabled={busy || readOnly}>Paste here</button><button className="btn btn-sm" onClick={() => setClipboard(null)}>Cancel</button></span>
        </div>
      )}

      <nav className="breadcrumbs" aria-label="Current folder">
        {crumbs.map((crumb, index) => <React.Fragment key={crumb.path || 'root'}>{index > 0 && <span>/</span>}<button onClick={() => load(crumb.path, source)}>{crumb.name}</button></React.Fragment>)}
      </nav>

      {newFolder && <div className="file-inline-form"><FolderIcon /><input className="input" value={folderName} placeholder="Folder name" onChange={(event) => setFolderName(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && folderName.trim() && createFolder()} autoFocus /><button className="btn btn-primary" onClick={createFolder} disabled={!folderName.trim() || busy}>Create</button><button className="btn" onClick={() => setNewFolder(false)}>Cancel</button></div>}

      <div className="file-table">
        <div className="file-row file-head"><span>Name</span><span>Size</span><span>Modified</span><span>Actions</span></div>
        {loading && <div className="empty"><span className="spinner" /> Loading files…</div>}
        {!loading && entries.length === 0 && <div className="empty">This folder is empty.</div>}
        {!loading && entries.map((entry) => (
          <div className="file-row" key={entry.path}>
            <button className={`file-name ${entry.kind === 'directory' ? 'clickable' : ''}`} onClick={() => entry.kind === 'directory' && load(entry.path, source)} disabled={entry.kind !== 'directory'}>
              {entry.kind === 'directory' ? <FolderIcon /> : <FileIcon />}<span>{entry.name}</span>{entry.iso && <span className="tag">ISO</span>}{entry.protected && <span className="tag tag-unavailable">managed</span>}
            </button>
            <span className="file-meta">{entry.kind === 'file' ? formatBytes(entry.size) : '—'}</span>
            <span className="file-meta">{formatDate(entry.modified)}</span>
            <span className="file-actions">
              <button className="btn btn-sm" onClick={() => setClipboard({ source, entry, move: false })}>Copy</button>
              {!readOnly && !entry.protected && <button className="btn btn-sm" onClick={() => setClipboard({ source, entry, move: true })}>Move</button>}
              {!readOnly && !entry.protected && <button className="btn btn-sm" onClick={() => { setRenaming(entry); setRenameName(entry.name); }}>Rename</button>}
              {!readOnly && !entry.protected && <button className="btn btn-sm btn-danger" onClick={() => remove(entry)}>Delete</button>}
            </span>
          </div>
        ))}
      </div>

      {renaming && <div className="modal-backdrop"><div className="modal modal-narrow"><div className="modal-head"><h2 className="modal-title">Rename {renaming.name}</h2></div><input className="input" value={renameName} onChange={(event) => setRenameName(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && renameName.trim() && rename()} autoFocus /><div className="modal-foot end"><div className="button-row"><button className="btn" onClick={() => setRenaming(null)}>Cancel</button><button className="btn btn-primary" onClick={rename} disabled={!renameName.trim() || busy}>Rename</button></div></div></div></div>}
    </div>
  );
}
