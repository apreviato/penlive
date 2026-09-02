import React, { useCallback, useEffect, useRef, useState } from 'react';

// Strips ANSI escapes from the shell's output. The second alternative is
// OSC (ESC ]), which ends at either BEL or the String Terminator, ESC followed
// by a backslash. That escaped backslash is load-bearing: this is a
// module-scope regex literal, so a malformed one throws while the bundle is
// being evaluated, React never mounts, and the whole kiosk is a black page
// with a working cursor.
const ANSI = /\x1B(?:[@-_][0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))/g;

export default function Terminal() {
  const [output, setOutput] = useState('');
  const [command, setCommand] = useState('');
  const [connected, setConnected] = useState(false);
  const socketRef = useRef(null);
  const outputRef = useRef(null);
  const inputRef = useRef(null);

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

  // A terminal takes keystrokes wherever you click, and never loses the caret.
  const focusInput = useCallback(() => inputRef.current?.focus(), []);
  useEffect(() => {
    focusInput();
  }, [focusInput]);

  const write = (text) => {
    if (socketRef.current?.readyState !== WebSocket.OPEN) return;
    socketRef.current.send(text);
  };

  const onKeyDown = (event) => {
    // The Run / Ctrl+C / Clear buttons are gone; these are the bindings a real
    // terminal already uses for the same three things.
    if (event.key === 'Enter') {
      event.preventDefault();
      if (!command) return;
      write(`${command}\n`);
      setCommand('');
      return;
    }
    if (event.ctrlKey && (event.key === 'c' || event.key === 'C')) {
      // Never swallow a genuine copy: only interrupt when nothing is selected.
      if (!window.getSelection()?.toString()) {
        event.preventDefault();
        write('\x03');
        setCommand('');
      }
      return;
    }
    if (event.ctrlKey && (event.key === 'l' || event.key === 'L')) {
      event.preventDefault();
      setOutput('');
      return;
    }
    if (event.ctrlKey && (event.key === 'd' || event.key === 'D') && !command) {
      event.preventDefault();
      write('\x04');
    }
  };

  return (
    <div className="content terminal-page" onMouseUp={focusInput}>
      <div className="section-header">
        <div><h2 className="section-title">Terminal</h2><p className="section-copy">Shell session running as the unprivileged penlive user. Mounted drives are available from the paths shown in Files. Ctrl+C interrupts, Ctrl+L clears.</p></div>
        <span className={`tag ${connected ? 'tag-success' : 'tag-caution'}`}>{connected ? 'connected' : 'disconnected'}</span>
      </div>
      <pre className="terminal-output" ref={outputRef}>{output || 'Opening terminal…\n'}</pre>
      <div className="terminal-input-row">
        <span className="terminal-prompt">$</span>
        <input
          ref={inputRef}
          className="terminal-input"
          value={command}
          onChange={(event) => setCommand(event.target.value)}
          onKeyDown={onKeyDown}
          onBlur={focusInput}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          autoFocus
        />
      </div>
    </div>
  );
}
