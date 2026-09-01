import React, { useEffect, useState } from 'react';
import { api } from '../api/client.js';

/* Layout picker, shared by first-run setup and the status bar.
   The test field matters more than it looks: a user who picks the wrong layout
   has no other way to discover it before typing a Wi-Fi password that silently
   comes out wrong. */

export default function KeyboardPicker({ onChanged, compact = false }) {
  const [layouts, setLayouts] = useState([]);
  const [variants, setVariants] = useState([]);
  const [layout, setLayout] = useState('us');
  const [variant, setVariant] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [testText, setTestText] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.keyboardLayouts();
        if (cancelled) return;
        setLayouts(data.layouts);
        setLayout(data.current?.layout || 'us');
        setVariant(data.current?.variant || '');
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await api.keyboardVariants(layout);
        if (!cancelled) setVariants(data.variants || []);
      } catch {
        if (!cancelled) setVariants([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [layout]);

  const apply = async (nextLayout, nextVariant) => {
    setSaving(true);
    setError(null);
    try {
      const result = await api.setKeyboard(nextLayout, nextVariant || null);
      onChanged?.(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={compact ? '' : 'card'}>
      {error && <div className="banner banner-error">{error}</div>}

      <div className="form-row">
        <label className="form-label" htmlFor="kb-layout">
          Keyboard layout
        </label>
        <select
          id="kb-layout"
          className="input"
          value={layout}
          onChange={(e) => {
            setLayout(e.target.value);
            setVariant('');
            apply(e.target.value, '');
          }}
        >
          {layouts.map((l) => (
            <option key={l.code} value={l.code}>
              {l.name} ({l.code})
            </option>
          ))}
        </select>
      </div>

      {variants.length > 0 && (
        <div className="form-row">
          <label className="form-label" htmlFor="kb-variant">
            Variant
          </label>
          <select
            id="kb-variant"
            className="input"
            value={variant}
            onChange={(e) => {
              setVariant(e.target.value);
              apply(layout, e.target.value);
            }}
          >
            <option value="">Default</option>
            {variants.map((v) => (
              <option key={v.code} value={v.code}>
                {v.name}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="form-row">
        <label className="form-label" htmlFor="kb-test">
          Test your keyboard
        </label>
        <input
          id="kb-test"
          className="input"
          placeholder="Type here to check the layout is right"
          value={testText}
          onChange={(e) => setTestText(e.target.value)}
        />
        <div className="form-help">
          Try the characters you need for your Wi-Fi password, especially @ # / and accents.
        </div>
      </div>

      {saving && (
        <div className="form-help">
          <span className="spinner" /> Applying layout...
        </div>
      )}
    </div>
  );
}
