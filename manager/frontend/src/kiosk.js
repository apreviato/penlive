/* Browser-side half of the kiosk lockdown.
 *
 * This is the *last* layer, not the only one, and the weakest of the three.
 * Openbox grabs these keys before Chromium ever sees them (openbox-rc.xml), and
 * Chromium's managed policy leaves the ones that get through with nowhere to
 * navigate to (live/config/includes.chroot/etc/chromium/policies/managed). The
 * kernel and systemd close the VT and Ctrl+Alt+Del escapes separately. What
 * this file removes is the in-page surface: reload, devtools, printing, text
 * selection, drag-and-drop navigation and the context menu, so the page cannot
 * be navigated away from or turned into a file browser.
 *
 * Keep the three lists in step. A key grabbed here but not in openbox-rc.xml is
 * still handled by the browser; a key grabbed there but needed by the app (the
 * Terminal tab's Ctrl+C, Ctrl+D and Ctrl+L) never reaches the page at all.
 *
 * Disabled automatically when running against a Vite dev server, since locking
 * out reload and devtools would make the UI impossible to work on.
 */

const BLOCKED_COMBOS = [
  // new surfaces and windows
  { ctrl: true, key: 'n' },
  { ctrl: true, shift: true, key: 'n' },        // incognito
  { ctrl: true, key: 't' },
  { ctrl: true, shift: true, key: 't' },        // reopen closed tab
  { ctrl: true, key: 'w' },
  { ctrl: true, shift: true, key: 'w' },
  { ctrl: true, key: 'q' },
  { ctrl: true, shift: true, key: 'q' },
  // dialogs
  { ctrl: true, key: 'o' },                     // open file
  { ctrl: true, key: 'p' },                     // print
  { ctrl: true, shift: true, key: 'p' },
  { ctrl: true, key: 's' },                     // save page
  // reload: an SPA reload throws the session away and replays the boot wait
  { ctrl: true, key: 'r' },
  { ctrl: true, shift: true, key: 'r' },
  { key: 'f5' },
  { shift: true, key: 'f5' },
  { ctrl: true, key: 'f5' },
  // browser UI the kiosk has no business showing
  { ctrl: true, key: 'j' },                     // downloads
  { ctrl: true, key: 'h' },                     // history
  { ctrl: true, key: 'u' },                     // view source
  { ctrl: true, key: 'f' },                     // find overlay
  { ctrl: true, key: 'g' },                     // find next
  { ctrl: true, shift: true, key: 'g' },
  { key: 'f3' },
  { ctrl: true, key: 'l' },                     // address bar (see isEditable)
  { ctrl: true, shift: true, key: 'o' },        // bookmark manager
  { ctrl: true, shift: true, key: 'b' },        // bookmark bar
  { ctrl: true, shift: true, key: 'm' },        // profile switcher
  { ctrl: true, shift: true, key: 'a' },        // tab search
  { ctrl: true, shift: true, key: 'delete' },   // clear browsing data
  { shift: true, key: 'escape' },               // task manager
  // developer tools
  { ctrl: true, shift: true, key: 'i' },
  { ctrl: true, shift: true, key: 'j' },
  { ctrl: true, shift: true, key: 'c' },
  { key: 'f12' },
  // fullscreen, menus, help, caret browsing
  { key: 'f11' },
  { key: 'f1' },
  { key: 'f6' },
  { key: 'f7' },
  { key: 'f10' },
  { alt: true, key: 'e' },
  { alt: true, key: 'f' },
  // navigating away from the app, and tab switching
  { alt: true, key: 'arrowleft' },
  { alt: true, key: 'arrowright' },
  { alt: true, key: 'home' },
  { ctrl: true, key: 'tab' },
  { ctrl: true, shift: true, key: 'tab' },
  { ctrl: true, key: 'pageup' },
  { ctrl: true, key: 'pagedown' },
];

// Modifiers are matched exactly, so Ctrl+Shift+R needs its own entry rather
// than falling out of the Ctrl+R one. That is deliberate: a near-miss that
// silently swallows a key the app wanted is harder to spot than a missing row.
function matches(event, combo) {
  if (Boolean(combo.ctrl) !== (event.ctrlKey || event.metaKey)) return false;
  if (Boolean(combo.shift) !== event.shiftKey) return false;
  if (Boolean(combo.alt) !== event.altKey) return false;
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
      // 'l' is here for the Terminal tab, which binds Ctrl+L to clear and says
      // so in its own help text. Outside a text field it stays blocked; inside
      // one there is no address bar for it to reach anyway.
      const allowed = ['a', 'c', 'v', 'x', 'z', 'l'];
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
