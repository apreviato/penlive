import React from 'react';

function Icon({ children, size = 18, className = '', viewBox = '0 0 24 24' }) {
  return (
    <svg
      className={`ui-icon ${className}`}
      viewBox={viewBox}
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function PenLiveMark({ size = 28 }) {
  return (
    <Icon size={size} viewBox="0 0 28 28" className="penlive-mark">
      <path d="M10 3.5h8v5.2l3.2 3.2v8.6a4 4 0 0 1-4 4h-6.4a4 4 0 0 1-4-4v-8.6L10 8.7z" />
      <path d="M11.5 3.5v4.3M16.5 3.5v4.3M10.2 16.2h7.6" />
      <circle cx="14" cy="20" r="1" fill="currentColor" stroke="none" />
    </Icon>
  );
}

export function KeyboardIcon({ size }) {
  return (
    <Icon size={size}>
      <rect x="3" y="6" width="18" height="12" rx="2" />
      <path d="M6 10h.01M9 10h.01M12 10h.01M15 10h.01M18 10h.01M7 14h.01M10 14h.01M13 14h4" />
    </Icon>
  );
}

export function StorageIcon({ size }) {
  return (
    <Icon size={size}>
      <ellipse cx="12" cy="6" rx="8" ry="3" />
      <path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
    </Icon>
  );
}

export function PowerIcon({ size }) {
  return (
    <Icon size={size}>
      <path d="M12 3v9" />
      <path d="M7.1 5.9a8 8 0 1 0 9.8 0" />
    </Icon>
  );
}

export function LockIcon({ size = 14 }) {
  return (
    <Icon size={size}>
      <rect x="5" y="10" width="14" height="10" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </Icon>
  );
}

export function WifiIcon({ signal = 0, size = 19 }) {
  const level = signal >= 70 ? 3 : signal >= 40 ? 2 : signal > 0 ? 1 : 0;
  return (
    <svg
      className="ui-icon wifi-icon"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M3.5 8.5a13 13 0 0 1 17 0" opacity={level >= 3 ? 1 : 0.22} />
      <path d="M6.7 12a8.3 8.3 0 0 1 10.6 0" opacity={level >= 2 ? 1 : 0.22} />
      <path d="M9.8 15.5a3.5 3.5 0 0 1 4.4 0" opacity={level >= 1 ? 1 : 0.22} />
      <circle cx="12" cy="19" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function FolderIcon({ size = 20 }) {
  return (
    <Icon size={size}>
      <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H10l2 2h6.5A2.5 2.5 0 0 1 21 9.5v7A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z" />
    </Icon>
  );
}

export function FileIcon({ size = 20 }) {
  return (
    <Icon size={size}>
      <path d="M6 3h8l4 4v14H6z" />
      <path d="M14 3v5h5" />
    </Icon>
  );
}

export function RefreshIcon({ size = 17 }) {
  return (
    <Icon size={size}>
      <path d="M20 7v5h-5" />
      <path d="M18.5 16a8 8 0 1 1 .8-8.5L20 12" />
    </Icon>
  );
}
