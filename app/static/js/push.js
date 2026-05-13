/**
 * ClassUp Push Notifications — state machine + subscription flow.
 *
 * Exposes a global `ClassUpPush` object:
 *   ClassUpPush.detectState()      → 'unsupported' | 'ios-need-install' |
 *                                    'server-not-configured' | 'denied' |
 *                                    'ready' | 'subscribed'
 *   ClassUpPush.subscribe()        → enable on this device
 *   ClassUpPush.unsubscribe()      → disable on this device
 *   ClassUpPush.sendTest()         → fire a test push at all this user's devices
 *   ClassUpPush.listSubscriptions()→ array of {id, endpoint_preview, ...}
 *
 * State machine — exactly one of these is true at any moment per device:
 *   - unsupported          → browser lacks ServiceWorker/PushManager/Notification
 *   - ios-need-install     → iOS Safari, not yet installed to home screen
 *   - server-not-configured→ VAPID keypair not generated yet
 *   - denied               → user clicked "Don't allow"; only fixable in OS settings
 *   - ready                → all preconditions OK, just no subscription yet
 *   - subscribed           → this device is enrolled
 */

(function () {
  'use strict';

  // -------------------------------------------------------------------------
  // Platform detection — iPadOS spoofs MacIntel; only Safari can install
  // -------------------------------------------------------------------------
  function isIOS() {
    const ua = navigator.userAgent || '';
    if (/iPad|iPhone|iPod/.test(ua) && !window.MSStream) return true;
    if (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1) return true;
    return false;
  }
  function isIOSSafari() {
    if (!isIOS()) return false;
    return !/CriOS|FxiOS|EdgiOS|OPiOS|YaBrowser|UCBrowser/i.test(navigator.userAgent || '');
  }
  function isStandalone() {
    return window.matchMedia('(display-mode: standalone)').matches
      || window.navigator.standalone === true;
  }

  // -------------------------------------------------------------------------
  // VAPID key cache + base64url → Uint8Array
  // -------------------------------------------------------------------------
  let _vapidKey = null;
  let _vapidConfigured = null;

  async function getServerKey(force) {
    if (!force && _vapidKey !== null && _vapidConfigured !== null) {
      return { key: _vapidKey, configured: _vapidConfigured };
    }
    try {
      const res = await ClassUp.fetch('/api/v1/push/public-key');
      _vapidKey = (res && res.data && res.data.key) || '';
      _vapidConfigured = !!(res && res.data && res.data.configured);
      return { key: _vapidKey, configured: _vapidConfigured };
    } catch (err) {
      console.error('[push] could not fetch VAPID key:', err);
      return { key: '', configured: false };
    }
  }

  /**
   * URL-safe base64 → Uint8Array. Browsers expect the
   * applicationServerKey to be raw bytes, not a string.
   */
  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const b64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(b64);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  // -------------------------------------------------------------------------
  // State machine
  // -------------------------------------------------------------------------

  async function detectState() {
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)
        || !('PushManager' in window) || !('Notification' in window)) {
      return 'unsupported';
    }

    // iOS 16.4+ allows push BUT only in installed PWAs. Before the user
    // does Add to Home Screen, pushManager.subscribe() rejects with
    // NotAllowedError. Short-circuit and tell the user to install first.
    if (isIOS() && !isStandalone()) {
      return 'ios-need-install';
    }

    const server = await getServerKey();
    if (!server.configured) return 'server-not-configured';

    if (Notification.permission === 'denied') return 'denied';

    // Permission default or granted — check whether we have an active sub
    let reg;
    try {
      reg = await navigator.serviceWorker.ready;
    } catch (e) {
      return 'unsupported';
    }
    const existing = await reg.pushManager.getSubscription();
    return existing ? 'subscribed' : 'ready';
  }

  // -------------------------------------------------------------------------
  // Subscribe — only call when state is 'ready'
  // -------------------------------------------------------------------------

  async function subscribe() {
    const state = await detectState();
    if (state === 'subscribed') return { ok: true, alreadyEnabled: true };
    if (state !== 'ready') {
      return { ok: false, reason: state };
    }

    // Permission prompt — may auto-resolve to 'granted' if it's already set
    if (Notification.permission === 'default') {
      const result = await Notification.requestPermission();
      if (result !== 'granted') return { ok: false, reason: 'denied' };
    }

    const { key } = await getServerKey();
    if (!key) return { ok: false, reason: 'server-not-configured' };

    let reg;
    try {
      reg = await navigator.serviceWorker.ready;
    } catch (e) {
      return { ok: false, reason: 'unsupported' };
    }

    let sub;
    try {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(key),
      });
    } catch (err) {
      console.error('[push] subscribe failed:', err);
      // iOS often surfaces as NotAllowedError if the PWA isn't installed
      const reason = (err && err.name === 'NotAllowedError') ? 'denied' : 'failed';
      return { ok: false, reason, error: err && err.message };
    }

    try {
      await ClassUp.fetch('/api/v1/push/subscribe', {
        method: 'POST',
        body: JSON.stringify(sub.toJSON()),
      });
    } catch (err) {
      console.error('[push] failed to register sub on server:', err);
      // Roll back the browser-side subscription so we don't end up with a
      // sub the server doesn't know about
      try { await sub.unsubscribe(); } catch (_) {}
      return { ok: false, reason: 'server-error', error: err && err.message };
    }

    return { ok: true };
  }

  // -------------------------------------------------------------------------
  // Unsubscribe — drop the sub on the browser AND the server
  // -------------------------------------------------------------------------

  async function unsubscribe() {
    if (!('serviceWorker' in navigator)) return { ok: false, reason: 'unsupported' };
    let reg;
    try {
      reg = await navigator.serviceWorker.ready;
    } catch (e) {
      return { ok: false, reason: 'unsupported' };
    }
    const sub = await reg.pushManager.getSubscription();
    if (!sub) return { ok: true, alreadyDisabled: true };

    const endpoint = sub.endpoint;
    // Best-effort tell the server first (so we don't lose the endpoint if
    // the browser-side unsubscribe succeeds but the network call fails)
    try {
      await ClassUp.fetch('/api/v1/push/unsubscribe', {
        method: 'POST',
        body: JSON.stringify({ endpoint }),
      });
    } catch (err) {
      console.warn('[push] server unsubscribe failed (continuing):', err);
    }

    try {
      await sub.unsubscribe();
    } catch (err) {
      console.error('[push] browser unsubscribe failed:', err);
      return { ok: false, reason: 'failed', error: err && err.message };
    }

    return { ok: true };
  }

  // -------------------------------------------------------------------------
  // Test push — verify end-to-end delivery
  // -------------------------------------------------------------------------

  async function sendTest() {
    try {
      const res = await ClassUp.fetch('/api/v1/push/test', { method: 'POST' });
      return { ok: true, data: res && res.data };
    } catch (err) {
      return { ok: false, error: err && err.message };
    }
  }

  // -------------------------------------------------------------------------
  // Subscriptions list — for the Notifications settings page
  // -------------------------------------------------------------------------

  async function listSubscriptions() {
    try {
      const res = await ClassUp.fetch('/api/v1/push/subscriptions');
      return (res && res.data) || [];
    } catch (err) {
      console.error('[push] could not list subscriptions:', err);
      return [];
    }
  }

  // -------------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------------
  window.ClassUpPush = {
    detectState,
    subscribe,
    unsubscribe,
    sendTest,
    listSubscriptions,
    // Exposed for the settings UI labels
    isIOS,
    isIOSSafari,
    isStandalone,
  };
})();
