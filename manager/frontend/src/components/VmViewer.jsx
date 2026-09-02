import React, { useCallback, useEffect, useRef, useState } from 'react';
import RFB from '@novnc/novnc';
import { api } from '../api/client.js';

// QEMU can drop the first connection while it is still setting the display up,
// so a single failure is not yet a reason to tell the user anything is wrong.
const MAX_ATTEMPTS = 5;
const RETRY_DELAY_MS = 800;

export default function VmViewer({ session, onClosed }) {
  const screenRef = useRef(null);
  const [status, setStatus] = useState('connecting');
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!session || !screenRef.current) return undefined;

    let attempt = 0;
    let retryTimer = null;
    let rfb = null;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      attempt += 1;
      setStatus(attempt === 1 ? 'connecting' : `reconnecting (${attempt}/${MAX_ATTEMPTS})`);
      const host = window.location.hostname || '127.0.0.1';
      rfb = new RFB(screenRef.current, `ws://${host}:${session.websocket_port}`);
      rfb.scaleViewport = true;
      rfb.resizeSession = true;
      rfb.background = '#050607';
      rfb.addEventListener('connect', () => {
        if (cancelled) return;
        attempt = 0;
        setStatus('connected');
        setError(null);
      });
      rfb.addEventListener('disconnect', (event) => {
        if (cancelled) return;
        if (event.detail?.clean) {
          setStatus('disconnected');
          return;
        }
        if (attempt < MAX_ATTEMPTS) {
          retryTimer = setTimeout(connect, RETRY_DELAY_MS);
          return;
        }
        setStatus('disconnected');
        // Say which of the two things went wrong: the machine died, or we
        // could not reach its display.
        api
          .vmStatus(session.image.id)
          .then(({ running }) =>
            setError(
              running
                ? 'Could not reach the virtual machine display. It is still running — try Stop VM and start it again.'
                : 'The virtual machine stopped. Start it again from Systems.'
            )
          )
          .catch(() => setError('The virtual-machine display disconnected and could not be restored.'));
      });
      rfb.addEventListener('securityfailure', (event) =>
        setError(event.detail?.reason || 'VNC security negotiation failed.')
      );
    };

    connect();
    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
      rfb?.disconnect();
    };
  }, [session]);

  const stop = useCallback(async () => {
    try {
      await api.stopVm(session.image.id);
    } catch (err) {
      setError(err.message);
      return;
    }
    onClosed();
  }, [session, onClosed]);

  if (!session) {
    return <div className="content"><div className="empty"><h2>No virtual machine is running</h2><p>Choose Run VM on a downloaded system. Its display will stay inside PenLive.</p></div></div>;
  }

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
