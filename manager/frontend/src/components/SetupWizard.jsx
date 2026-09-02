import React, { useState } from 'react';
import { api } from '../api/client.js';
import KeyboardPicker from './KeyboardPicker.jsx';
import WifiPanel from './WifiPanel.jsx';
import { PenLiveMark } from './Icons.jsx';

/* Shown on first boot, and afterwards only when the machine is offline.
   Keyboard comes first on purpose: the next screen asks for a Wi-Fi password,
   and typing one with the wrong layout is a confusing way to fail. */

const STEPS = [
  { id: 'keyboard', title: 'Keyboard', blurb: 'Pick the layout that matches your physical keyboard.' },
  { id: 'network', title: 'Network', blurb: 'Connect to the internet to download systems.' },
];

export default function SetupWizard({ networkStatus, onNetworkChanged, onFinish, reason }) {
  const [step, setStep] = useState(0);
  const [finishing, setFinishing] = useState(false);
  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  const finish = async () => {
    setFinishing(true);
    try {
      await api.completeSetup();
    } catch {
      // Not being able to persist the flag shouldn't trap the user here;
      // worst case they see this screen again next boot.
    } finally {
      setFinishing(false);
      onFinish();
    }
  };

  return (
    <div className="setup-shell">
      <aside className="setup-intro">
        <div className="setup-brand"><PenLiveMark size={34} /><span>PenLive</span></div>
        <div>
          <p className="setup-eyebrow">Portable system manager</p>
          <h1 className="setup-title">
            {reason === 'offline' ? 'Let’s get you back online.' : 'Your live systems, ready anywhere.'}
          </h1>
          <p className="setup-blurb">
            {reason === 'offline'
              ? 'Reconnect to download new systems. Images already on the stick remain available offline.'
              : 'Choose your keyboard, connect to Wi-Fi, then download and boot verified operating systems directly from this stick.'}
          </p>
        </div>
        <div className="setup-trust">
          <span>✓ Verified downloads</span>
          <span>✓ Persistent storage</span>
          <span>✓ Secure Boot ready</span>
        </div>
      </aside>

      <main className="setup-panel">
        <div className="setup-head">
          <p className="setup-eyebrow">Initial setup</p>
          <h2>{current.title}</h2>
          <p>{current.blurb}</p>
        </div>

        <ol className="steps">
          {STEPS.map((s, i) => (
            <li key={s.id} className={`step ${i === step ? 'active' : ''} ${i < step ? 'done' : ''}`}>
              <span className="step-num">{i < step ? '✓' : i + 1}</span>
              <span className="step-name">{s.title}</span>
            </li>
          ))}
        </ol>

        <div className="setup-body">
          {current.id === 'keyboard' && <KeyboardPicker compact />}
          {current.id === 'network' && (
            <WifiPanel status={networkStatus} onConnected={onNetworkChanged} />
          )}
        </div>

        <div className="setup-foot">
          <button className="btn" onClick={() => setStep((s) => s - 1)} disabled={step === 0}>
            Back
          </button>

          <div className="button-row">
            {isLast ? (
              <>
                <button className="btn" onClick={finish} disabled={finishing}>
                  Skip for now
                </button>
                <button className="btn btn-primary" onClick={finish} disabled={finishing}>
                  {finishing ? <span className="spinner" /> : 'Finish'}
                </button>
              </>
            ) : (
              <button className="btn btn-primary" onClick={() => setStep((s) => s + 1)}>
                Continue
              </button>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
