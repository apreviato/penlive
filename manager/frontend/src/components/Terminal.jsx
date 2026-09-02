import React, { useEffect, useRef, useState } from 'react';

const ANSI = /\x1B(?:[@-_][0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))/g;

export default function Terminal() {
  const [output, setOutput] = useState('');
  const [command, setCommand] = useState('');
  const [connected, setConnected] = useState(false);
  const socketRef = useRef(null);
  const outputRef = useRef(null);

  useEffect(() => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(`${proto}://${window.location.host}/api/terminal`);
    socketRef.current = socket;
    socket.onopen = () => setConnected(true);
    socket.onclose = () => setConnected(false);
    socket.onmessage = (event) => {
      const clean = String(event.data).replace(ANSI, '').replace(/\r(?!\n)/g, '');
      setOutput((current) => (current + clean).slice(-120000));
    };
    return () => socket.close();
  }, []);

  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [output]);

  const send = () => {
    if (!command || socketRef.current?.readyState !== WebSocket.OPEN) return;
    socketRef.current.send(`${command}\n`);
    setCommand('');
  };

  return (
    <div className="content terminal-page">
      <div className="section-header">
        <div><h2 className="section-title">Terminal</h2><p className="section-copy">Shell session running as the unprivileged penlive user. Mounted drives are available from the paths shown in Files.</p></div>
        <span className={`tag ${connected ? 'tag-success' : 'tag-caution'}`}>{connected ? 'connected' : 'disconnected'}</span>
      </div>
      <pre className="terminal-output" ref={outputRef}>{output || 'Opening terminal…\n'}</pre>
      <div className="terminal-input-row">
        <span className="terminal-prompt">$</span>
        <input className="terminal-input" value={command} onChange={(event) => setCommand(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && send()} placeholder="Enter a command" spellCheck={false} autoComplete="off" autoCapitalize="none" />
        <button className="btn btn-primary" onClick={send} disabled={!connected || !command}>Run</button>
        <button className="btn" onClick={() => socketRef.current?.send('\x03')} disabled={!connected}>Ctrl+C</button>
        <button className="btn" onClick={() => setOutput('')}>Clear</button>
      </div>
    </div>
  );
}
