import React, { useEffect, useRef, useState } from 'react';
import RFB from '@novnc/novnc';
import { api } from '../api/client.js';

export default function VmViewer({ session, onClosed }) {
  const screenRef = useRef(null);
  const [status, setStatus] = useState('waiting');
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!session || !screenRef.current) return undefined;
    const host = window.location.hostname || '127.0.0.1';
    const rfb = new RFB(screenRef.current, `ws://${host}:${session.websocket_port}`);
    rfb.scaleViewport = true;
    rfb.resizeSession = true;
    rfb.background = '#050607';
    const connected = () => setStatus('connected');
    const disconnected = (event) => {
      setStatus('disconnected');
      if (!event.detail?.clean) setError('The virtual-machine display disconnected unexpectedly.');
    };
    const securityFailure = (event) => setError(event.detail?.reason || 'VNC security negotiation failed.');
    rfb.addEventListener('connect', connected);
    rfb.addEventListener('disconnect', disconnected);
    rfb.addEventListener('securityfailure', securityFailure);
    return () => rfb.disconnect();
  }, [session]);

  if (!session) {
    return <div className="content"><div className="empty"><h2>No virtual machine is running</h2><p>Choose Run VM on a downloaded system. Its display will stay inside PenLive.</p></div></div>;
  }

  const stop = async () => {
    try { await api.stopVm(session.image.id); } catch (err) { setError(err.message); return; }
    onClosed();
  };

  return (
    <div className="content vm-page">
      <div className="section-header">
        <div><h2 className="section-title">{session.image.name}</h2><p className="section-copy">Virtual machine · {session.kvm ? 'hardware acceleration' : 'software emulation'}</p></div>
        <div className="button-row"><span className={`tag ${status === 'connected' ? 'tag-success' : 'tag-caution'}`}>{status}</span><button className="btn btn-danger" onClick={stop}>Stop VM</button></div>
      </div>
      {error && <div className="banner banner-error">{error}</div>}
      <div className="vm-screen" ref={screenRef} />
    </div>
  );
}
