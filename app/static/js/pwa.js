/**
 * ClassUp PWA bootstrap — service worker registration + install prompt UX.
 *
 * Loaded from base.html on every page. Lightweight; no dependencies.
 *
 * What it does:
 * 1. Registers /sw.js (skipped on http:// since browsers reject SW outside HTTPS)
 * 2. Detects standalone-mode launches (iOS sets navigator.standalone, Chrome
 *    matches display-mode: standalone) and tags <body> so CSS can react
 * 3. Captures the Android `beforeinstallprompt` event and exposes a global
 *    `ClassUp.installApp()` so any UI element can trigger it
 * 4. Shows a one-time iOS install hint (since iOS Safari has no install API —
 *    only a manual "Add to Home Screen" via the Share menu)
 * 5. Notifies when a new SW version is waiting and offers a one-tap reload
 */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Standalone detection — tag body so CSS can hide install prompts, etc.
  // ---------------------------------------------------------------------------
  const isStandalone =
    window.matchMedia('(display-mode: standalone)').matches ||
    window.navigator.standalone === true ||  // iOS
    document.referrer.startsWith('android-app://');

  if (isStandalone) {
    document.body.classList.add('is-standalone');
  }

  // ---------------------------------------------------------------------------
  // Service worker — register on load to avoid blocking first paint
  // ---------------------------------------------------------------------------
  if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')) {
    window.addEventListener('load', () => {
      navigator.serviceWorker
        .register('/sw.js', { scope: '/' })
        .then((reg) => {
          // Listen for updates
          reg.addEventListener('updatefound', () => {
            const newSW = reg.installing;
            if (!newSW) return;
            newSW.addEventListener('statechange', () => {
              if (newSW.state === 'installed' && navigator.serviceWorker.controller) {
                // A new SW is waiting. Offer reload toast (non-blocking).
                showUpdateToast(reg);
              }
            });
          });
        })
        .catch((err) => console.warn('[PWA] SW registration failed:', err));

      // When the active SW changes (after skipWaiting), reload to pick up
      // the new shell. One-shot — guarded with a flag so we don't loop.
      let refreshing = false;
      navigator.serviceWorker.addEventListener('controllerchange', () => {
        if (refreshing) return;
        refreshing = true;
        window.location.reload();
      });
    });
  }

  function showUpdateToast(reg) {
    // Don't pile up — only one update toast at a time
    if (document.getElementById('pwa-update-toast')) return;

    const toast = document.createElement('div');
    toast.id = 'pwa-update-toast';
    toast.className =
      'fixed bottom-20 md:bottom-4 left-1/2 -translate-x-1/2 z-[200] ' +
      'bg-neutral-800 text-white text-sm rounded-full shadow-lg px-4 py-2.5 ' +
      'flex items-center gap-3 animate-slide-up max-w-[calc(100vw-2rem)]';
    toast.innerHTML = `
      <svg class="w-4 h-4 text-accent-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M16 12V8a4 4 0 00-8 0v4M5 12h14l-1 8H6l-1-8z" />
      </svg>
      <span>New version available</span>
      <button id="pwa-update-btn" class="ml-2 text-accent-400 font-medium hover:text-accent-300">Reload</button>
      <button id="pwa-update-dismiss" class="text-neutral-400 hover:text-white" aria-label="Dismiss">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    `;
    document.body.appendChild(toast);

    document.getElementById('pwa-update-btn').onclick = () => {
      const waiting = reg.waiting;
      if (waiting) waiting.postMessage({ type: 'SKIP_WAITING' });
      else window.location.reload();
    };
    document.getElementById('pwa-update-dismiss').onclick = () => toast.remove();
  }

  // ---------------------------------------------------------------------------
  // Install prompt — Android / Chrome / Edge
  // ---------------------------------------------------------------------------
  let deferredInstallPrompt = null;

  window.addEventListener('beforeinstallprompt', (e) => {
    // Stop Chrome's mini-infobar so we control the timing
    e.preventDefault();
    deferredInstallPrompt = e;
    // Reveal any installer UI hiding behind .pwa-install-available
    document.body.classList.add('pwa-install-available');
  });

  window.addEventListener('appinstalled', () => {
    deferredInstallPrompt = null;
    document.body.classList.remove('pwa-install-available');
    document.body.classList.add('is-standalone');
    if (window.ClassUp && ClassUp.toast) {
      ClassUp.toast('ClassUp installed — find it on your home screen.', 'success');
    }
  });

  // Public API — hook this up to a button anywhere in the app
  window.ClassUp = window.ClassUp || {};

  /**
   * Trigger the native install prompt (Chrome/Edge/Android) or show the iOS
   * Add-to-Home-Screen sheet.
   *
   * @returns {Promise<'accepted'|'dismissed'|'unavailable'>}
   */
  window.ClassUp.installApp = async function () {
    if (deferredInstallPrompt) {
      deferredInstallPrompt.prompt();
      const result = await deferredInstallPrompt.userChoice;
      deferredInstallPrompt = null;
      document.body.classList.remove('pwa-install-available');
      return result.outcome;
    }
    // iOS — there's no install API, only a manual flow
    if (isIOS() && !isStandalone) {
      showIosInstallSheet();
      return 'unavailable';
    }
    if (window.ClassUp && ClassUp.toast) {
      ClassUp.toast('Already installed, or not supported by this browser.', 'info');
    }
    return 'unavailable';
  };

  window.ClassUp.canInstall = () => deferredInstallPrompt !== null || (isIOS() && !isStandalone);
  window.ClassUp.isStandalone = () => isStandalone;

  function isIOS() {
    return /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  }

  // ---------------------------------------------------------------------------
  // iOS install hint sheet — shown manually via ClassUp.installApp() OR
  // automatically once after 3 visits (so users discover the feature)
  // ---------------------------------------------------------------------------
  const IOS_HINT_DISMISSED_KEY = 'classup_ios_hint_dismissed';
  const IOS_HINT_VISITS_KEY = 'classup_ios_visits';

  function showIosInstallSheet() {
    if (document.getElementById('ios-install-sheet')) return;
    const sheet = document.createElement('div');
    sheet.id = 'ios-install-sheet';
    sheet.className =
      'fixed inset-0 z-[300] flex items-end md:items-center justify-center ' +
      'bg-black/40 px-4 pb-safe';
    sheet.innerHTML = `
      <div class="bg-white rounded-t-2xl md:rounded-2xl w-full max-w-sm p-6 shadow-2xl"
           role="dialog" aria-modal="true" aria-labelledby="ios-install-title">
        <div class="flex items-center justify-between mb-4">
          <h3 id="ios-install-title" class="text-base font-semibold text-neutral-800">Install ClassUp</h3>
          <button id="ios-install-close" class="text-neutral-400 hover:text-neutral-600 -m-2 p-2" aria-label="Close">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <p class="text-sm text-neutral-600 mb-5">
          Add ClassUp to your home screen for fullscreen access and an app-like experience.
        </p>
        <ol class="space-y-3 text-sm text-neutral-700">
          <li class="flex items-start gap-3">
            <span class="flex-shrink-0 w-6 h-6 rounded-full bg-primary-50 text-primary-600 flex items-center justify-center font-medium text-xs">1</span>
            <span>Tap the <strong>Share</strong> button
              <svg class="inline w-4 h-4 align-text-bottom mx-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.8">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9 8.25H7.5a2.25 2.25 0 00-2.25 2.25v9a2.25 2.25 0 002.25 2.25h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25H15M9 12l3 3m0 0l3-3m-3 3V2.25" />
              </svg>
              at the bottom of Safari.</span>
          </li>
          <li class="flex items-start gap-3">
            <span class="flex-shrink-0 w-6 h-6 rounded-full bg-primary-50 text-primary-600 flex items-center justify-center font-medium text-xs">2</span>
            <span>Scroll down and tap <strong>"Add to Home Screen"</strong>.</span>
          </li>
          <li class="flex items-start gap-3">
            <span class="flex-shrink-0 w-6 h-6 rounded-full bg-primary-50 text-primary-600 flex items-center justify-center font-medium text-xs">3</span>
            <span>Tap <strong>Add</strong> in the top-right corner.</span>
          </li>
        </ol>
        <button id="ios-install-dismiss"
                class="mt-5 w-full px-4 py-2.5 text-sm text-neutral-600 bg-neutral-100 rounded-lg hover:bg-neutral-200">
          Maybe later
        </button>
      </div>
    `;
    document.body.appendChild(sheet);
    const close = () => {
      sheet.remove();
      try { localStorage.setItem(IOS_HINT_DISMISSED_KEY, '1'); } catch (e) {}
    };
    sheet.querySelector('#ios-install-close').onclick = close;
    sheet.querySelector('#ios-install-dismiss').onclick = close;
    sheet.onclick = (e) => { if (e.target === sheet) close(); };
  }

  // Auto-prompt iOS users after their 3rd visit, if not already installed
  if (isIOS() && !isStandalone) {
    try {
      const dismissed = localStorage.getItem(IOS_HINT_DISMISSED_KEY) === '1';
      if (!dismissed) {
        const visits = parseInt(localStorage.getItem(IOS_HINT_VISITS_KEY) || '0', 10) + 1;
        localStorage.setItem(IOS_HINT_VISITS_KEY, String(visits));
        if (visits === 3) {
          // Show after a short delay — let the page settle first
          setTimeout(showIosInstallSheet, 1500);
        }
      }
    } catch (e) {
      // localStorage blocked (private browsing) — silently skip
    }
  }
})();
