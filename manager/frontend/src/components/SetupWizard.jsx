import React, { useState } from 'react';
import { api } from '../api/client.js';
import KeyboardPicker from './KeyboardPicker.jsx';
import WifiPanel from './WifiPanel.jsx';

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
    <div className="setup">
      <div className="setup-head">
        <h1 className="setup-title">
          {reason === 'offline' ? 'You are offline' : 'Welcome to PenLive'}
        </h1>
        <p className="setup-blurb">
          {reason === 'offline'
            ? 'Reconnect to download new systems. Systems already on the stick can be booted without a connection.'
            : 'Two quick steps and this stick is ready to install operating systems on any machine.'}
        </p>
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
        <h2 className="section-title">{current.blurb}</h2>

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
    </div>
  );
}
