import React from 'react';

/* Distro marks, drawn inline as SVG.
 *
 * Inline rather than image files on purpose: the kiosk is expected to work with
 * no internet at all (that is the whole point of downloading ISOs to the
 * stick), so anything fetched over the network would render as a blank square
 * exactly when the user is offline and least able to fix it. Inlining also
 * keeps them crisp at any size and recolourable via currentColor.
 *
 * These are simplified geometric marks evoking each project's identity, not
 * reproductions of their official logos — trademarks belong to their projects,
 * and shipping exact logos would carry usage restrictions this project cannot
 * grant downstream. `family` comes from the catalog, so an unknown family
 * falls back to a neutral disc rather than breaking the row.
 */

const TITLES = {
  debian: 'Debian',
  ubuntu: 'Ubuntu',
  fedora: 'Fedora',
  arch: 'Arch Linux',
  proxmox: 'Proxmox VE',
  mint: 'Linux Mint',
  opensuse: 'openSUSE',
  alpine: 'Alpine Linux',
  rocky: 'Rocky Linux',
  kali: 'Kali Linux',
  tails: 'Tails',
  gparted: 'GParted',
  clonezilla: 'Clonezilla',
  memtest: 'Memtest86+',
  windows: 'Windows',
  generic: 'Disc image',
};

const COLORS = {
  debian: '#d70a53',
  ubuntu: '#e95420',
  fedora: '#51a2da',
  arch: '#1793d1',
  proxmox: '#e57000',
  mint: '#87cf3e',
  opensuse: '#73ba25',
  alpine: '#0d597f',
  rocky: '#10b981',
  kali: '#367bf0',
  tails: '#56347c',
  gparted: '#c9412e',
  clonezilla: '#f0a30a',
  memtest: '#8a8f98',
  windows: '#0078d4',
  generic: '#6b7280',
};

function Shape({ family }) {
  switch (family) {
    case 'debian': // spiral
      return (
        <path
          d="M32 14a18 18 0 1 0 12.7 30.7A15 15 0 1 1 30 18.5a12 12 0 0 0 2 25 12 12 0 0 1-2-24"
          fill="currentColor"
        />
      );
    case 'ubuntu': // circle of friends
      return (
        <>
          <circle cx="32" cy="32" r="17" fill="none" stroke="currentColor" strokeWidth="4" />
          <circle cx="32" cy="13" r="6" fill="currentColor" />
          <circle cx="15.5" cy="41.5" r="6" fill="currentColor" />
          <circle cx="48.5" cy="41.5" r="6" fill="currentColor" />
        </>
      );
    case 'fedora': // infinity-style 'f'
      return (
        <>
          <circle cx="32" cy="32" r="19" fill="currentColor" opacity="0.18" />
          <path
            d="M37 20h-5a8 8 0 0 0-8 8v4h-4v6h4v10h6V38h6v-6h-6v-3a2.5 2.5 0 0 1 2.5-2.5H37z"
            fill="currentColor"
          />
        </>
      );
    case 'arch': // peak
      return <path d="M32 10 51 50l-19-9-19 9z" fill="currentColor" />;
    case 'proxmox': // stacked hypervisor slabs
      return (
        <>
          <rect x="12" y="14" width="40" height="10" rx="2" fill="currentColor" />
          <rect x="12" y="27" width="40" height="10" rx="2" fill="currentColor" opacity="0.7" />
          <rect x="12" y="40" width="40" height="10" rx="2" fill="currentColor" opacity="0.4" />
        </>
      );
    case 'mint': // leaf
      return (
        <path
          d="M16 46V26c0-6 5-11 11-11 5 0 8 3 10 6 2-3 5-6 10-6v10c-3 0-5 2-5 5v16z"
          fill="currentColor"
        />
      );
    case 'opensuse': // chameleon-ish loop
      return (
        <>
          <circle cx="32" cy="32" r="18" fill="none" stroke="currentColor" strokeWidth="4" />
          <circle cx="39" cy="24" r="3.5" fill="currentColor" />
        </>
      );
    case 'alpine': // mountains
      return (
        <path d="M8 48 24 20l10 17 6-9 16 20z" fill="currentColor" />
      );
    case 'rocky': // mountain in circle
      return (
        <>
          <circle cx="32" cy="32" r="19" fill="none" stroke="currentColor" strokeWidth="3" />
          <path d="M20 42 32 22l12 20z" fill="currentColor" />
        </>
      );
    case 'kali': // dragon tail curve
      return (
        <path
          d="M18 14c14 2 24 12 26 26-4-6-9-9-15-10 4 4 6 9 6 15-6-4-10-9-12-15-2 6-1 11 2 16-8-6-11-19-7-32z"
          fill="currentColor"
        />
      );
    case 'tails': // usb key
      return (
        <>
          <rect x="26" y="12" width="12" height="26" rx="2" fill="currentColor" />
          <rect x="22" y="38" width="20" height="14" rx="3" fill="currentColor" opacity="0.6" />
        </>
      );
    case 'gparted': // partitioned bar
      return (
        <>
          <rect x="10" y="22" width="44" height="20" rx="3" fill="none" stroke="currentColor" strokeWidth="3" />
          <rect x="14" y="26" width="14" height="12" fill="currentColor" />
          <rect x="31" y="26" width="9" height="12" fill="currentColor" opacity="0.5" />
        </>
      );
    case 'clonezilla': // two discs
      return (
        <>
          <circle cx="25" cy="32" r="14" fill="none" stroke="currentColor" strokeWidth="3" />
          <circle cx="25" cy="32" r="3.5" fill="currentColor" />
          <path d="M39 18a14 14 0 0 1 0 28" fill="none" stroke="currentColor" strokeWidth="3" opacity="0.55" />
        </>
      );
    case 'memtest': // memory module
      return (
        <>
          <rect x="10" y="22" width="44" height="16" rx="2" fill="currentColor" />
          <rect x="16" y="38" width="4" height="6" fill="currentColor" />
          <rect x="30" y="38" width="4" height="6" fill="currentColor" />
          <rect x="44" y="38" width="4" height="6" fill="currentColor" />
        </>
      );
    case 'windows': // four panes
      return (
        <>
          <rect x="12" y="12" width="17" height="17" fill="currentColor" />
          <rect x="34" y="12" width="18" height="17" fill="currentColor" />
          <rect x="12" y="34" width="17" height="18" fill="currentColor" />
          <rect x="34" y="34" width="18" height="18" fill="currentColor" />
        </>
      );
    default: // optical disc
      return (
        <>
          <circle cx="32" cy="32" r="18" fill="none" stroke="currentColor" strokeWidth="3" />
          <circle cx="32" cy="32" r="5" fill="currentColor" />
        </>
      );
  }
}

export default function DistroLogo({ family, size = 40 }) {
  const key = (family || 'generic').toLowerCase();
  const color = COLORS[key] || COLORS.generic;
  const title = TITLES[key] || 'Disc image';

  return (
    <span className="distro-logo" style={{ width: size, height: size, color }}>
      <svg
        viewBox="0 0 64 64"
        width={size}
        height={size}
        role="img"
        aria-label={title}
        focusable="false"
      >
        <title>{title}</title>
        <Shape family={key} />
      </svg>
    </span>
  );
}
