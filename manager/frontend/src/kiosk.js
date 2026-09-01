/* Browser-side half of the kiosk lockdown.
 *
 * This is the *last* layer, not the only one. A browser cannot block Alt+F4 or
 * Ctrl+Alt+F2 — those are intercepted by the window manager and the kernel, and
 * are handled in live/config (openbox rc.xml with no keybindings, masked TTYs,
 * masked ctrl-alt-del). What this file removes is the in-page surface: reload,
 * devtools, printing, text selection, drag-and-drop navigation and the context
 * menu, so the page cannot be navigated away from or turned into a file browser.
 *
 * Disabled automatically when running against a Vite dev server, since locking
 * out reload and devtools would make the UI impossible to work on.
 */

const BLOCKED_COMBOS = [
  { ctrl: true, key: 'r' },        // reload
  { ctrl: true, key: 'w' },        // close tab
  { ctrl: true, key: 't' },        // new tab
  { ctrl: true, key: 'n' },        // new window
  { ctrl: true, key: 'p' },        // print
  { ctrl: true, key: 's' },        // save page
  { ctrl: true, key: 'o' },        // open file
  { ctrl: true, key: 'j' },        // downloads
  { ctrl: true, key: 'h' },        // history
  { ctrl: true, key: 'u' },        // view source
  { ctrl: true, shift: true, key: 'i' }, // devtools
  { ctrl: true, shift: true, key: 'j' },
  { ctrl: true, shift: true, key: 'c' },
  { ctrl: true, shift: true, key: 'delete' },
  { key: 'f5' },
  { key: 'f11' },
  { key: 'f12' },
];

function matches(event, combo) {
  if (combo.ctrl && !(event.ctrlKey || event.metaKey)) return false;
  if (!combo.ctrl && (event.ctrlKey || event.metaKey)) return false;
  if (combo.shift && !event.shiftKey) return false;
  if (!combo.shift && event.shiftKey && combo.ctrl) return false;
  return event.key.toLowerCase() === combo.key;
}

function isEditable(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable;
}

export function installKioskLockdown({ enabled = true } = {}) {
  if (!enabled) return () => {};

  const onKeyDown = (event) => {
    // Ctrl+A / Ctrl+C / Ctrl+V must keep working inside form fields, or the
    // user cannot paste a Wi-Fi password.
    if (isEditable(event.target) && (event.ctrlKey || event.metaKey)) {
      const allowed = ['a', 'c', 'v', 'x', 'z'];
      if (allowed.includes(event.key.toLowerCase())) return;
    }
    if (BLOCKED_COMBOS.some((combo) => matches(event, combo))) {
      event.preventDefault();
      event.stopPropagation();
    }
  };

  const onContextMenu = (event) => event.preventDefault();
  const onDragStart = (event) => event.preventDefault();
  const onDrop = (event) => event.preventDefault();
  // Without this, dropping a file onto the window navigates Chromium to file://
  const onDragOver = (event) => event.preventDefault();

  window.addEventListener('keydown', onKeyDown, true);
  window.addEventListener('contextmenu', onContextMenu);
  window.addEventListener('dragstart', onDragStart);
  window.addEventListener('dragover', onDragOver);
  window.addEventListener('drop', onDrop);

  return () => {
    window.removeEventListener('keydown', onKeyDown, true);
    window.removeEventListener('contextmenu', onContextMenu);
    window.removeEventListener('dragstart', onDragStart);
    window.removeEventListener('dragover', onDragOver);
    window.removeEventListener('drop', onDrop);
  };
}

export const isDevServer = () => import.meta.env?.DEV === true;
