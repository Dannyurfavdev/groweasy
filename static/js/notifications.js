/**
 * static/js/notifications.js
 *
 * This file handles BOTH:
 *   1. Toast notifications (tab is open)
 *   2. Browser push notifications (tab minimised/closed)
 *
 * Include this on any page that should receive notifications.
 * It needs one global variable set before the script loads:
 *
 *   <script>
 *     const GROWEASY_PROJECT_ID = {{ project.id }};
 *   </script>
 *   <script src="{% static 'js/notifications.js' %}"></script>
 *
 * For the risk_overview page where there's no single project,
 * set GROWEASY_PROJECT_ID = null and we'll skip WebSocket.
 * Push notifications will still work via the service worker
 * which connects per-project.
 */

(function() {
    'use strict';
    
    // ─────────────────────────────────────────────────────────────
    // 1. SERVICE WORKER REGISTRATION
    //    Register the service worker that handles background pushes
    // ─────────────────────────────────────────────────────────────
    async function registerServiceWorker() {
        if (!('serviceWorker' in navigator)) {
            console.log('[GrowEasy] Service workers not supported in this browser');
            return null;
        }
    
        try {
            const registration = await navigator.serviceWorker.register(
                '/static/js/service-worker.js',
                { scope: '/' }
            );
            console.log('[GrowEasy] Service worker registered');
            return registration;
        } catch (err) {
            console.error('[GrowEasy] Service worker registration failed:', err);
            return null;
        }
    }
    
    
    // ─────────────────────────────────────────────────────────────
    // 2. REQUEST NOTIFICATION PERMISSION
    //    Shows the browser's "Allow notifications?" prompt
    //    We show this after a small delay so it doesn't interrupt
    //    the user immediately on page load
    // ─────────────────────────────────────────────────────────────
    async function requestNotificationPermission() {
        if (!('Notification' in window)) {
            return false;
        }
    
        if (Notification.permission === 'granted') {
            return true;
        }
    
        if (Notification.permission === 'denied') {
            // User previously blocked notifications
            // Show a gentle reminder in the UI
            showPermissionBanner();
            return false;
        }
    
        // Ask for permission
        const permission = await Notification.requestPermission();
        return permission === 'granted';
    }
    
    function showPermissionBanner() {
        // Only show once per session
        if (sessionStorage.getItem('notif_banner_shown')) return;
        sessionStorage.setItem('notif_banner_shown', '1');
    
        const banner = document.createElement('div');
        banner.style.cssText = `
            position: fixed; bottom: 1rem; left: 50%; transform: translateX(-50%);
            background: #1e293b; color: #e2e8f0; padding: 0.75rem 1.25rem;
            border-radius: 8px; font-size: 0.82rem; z-index: 9999;
            display: flex; align-items: center; gap: 0.75rem;
            box-shadow: 0 4px 16px rgba(0,0,0,0.3);
        `;
        banner.innerHTML = `
            <i class="bi bi-bell-slash" style="color:#f59e0b;"></i>
            <span>Enable notifications to get alerts when you're away from this tab</span>
            <button onclick="GrowEasyNotifications.requestPermission()" 
                    style="background:#2563eb; color:white; border:none; padding:0.3rem 0.75rem;
                           border-radius:5px; cursor:pointer; font-size:0.78rem;">
                Enable
            </button>
            <button onclick="this.closest('div').remove()"
                    style="background:none; border:none; color:#94a3b8; cursor:pointer; font-size:1rem;">
                ✕
            </button>
        `;
        document.body.appendChild(banner);
    
        // Auto-hide after 8 seconds
        setTimeout(() => banner.remove(), 8000);
    }
    
    
    // ─────────────────────────────────────────────────────────────
    // 3. TELL SERVICE WORKER WHICH PROJECT TO WATCH
    // ─────────────────────────────────────────────────────────────
    function startServiceWorkerConnection(projectId) {
        if (!navigator.serviceWorker.controller) return;
        navigator.serviceWorker.controller.postMessage({
            type: 'START_NOTIFICATIONS',
            project_id: projectId
        });
    }
    
    // Listen for messages FROM the service worker
    // (the SW broadcasts to all tabs when a notification arrives)
    navigator.serviceWorker.addEventListener('message', event => {
        const data = event.data;
        if (data.type === 'SHOW_TOAST') {
            showToast(data.notification);
        }
        if (data.type === 'WS_CONNECTED') {
            setConnectionStatus('connected');
        }
        if (data.type === 'WS_DISCONNECTED') {
            setConnectionStatus('disconnected');
        }
    });
    
    
    // ─────────────────────────────────────────────────────────────
    // 4. TOAST NOTIFICATION UI
    //    Shows an in-page notification when the tab is active
    // ─────────────────────────────────────────────────────────────
    function ensureToastContainer() {
        let container = document.getElementById('ge-toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'ge-toast-container';
            container.style.cssText = `
                position: fixed; top: 1.25rem; right: 1.25rem;
                z-index: 9999; display: flex; flex-direction: column;
                gap: 0.5rem; width: 340px;
                max-width: calc(100vw - 2.5rem);
            `;
            document.body.appendChild(container);
        }
        return container;
    }
    
    function showToast(data) {
        const container = ensureToastContainer();
    
        const colors = {
            danger:  { bg: '#fef2f2', border: '#fecaca', accent: '#dc2626', text: '#991b1b' },
            warning: { bg: '#fffbeb', border: '#fde68a', accent: '#d97706', text: '#92400e' },
            info:    { bg: '#eff6ff', border: '#bfdbfe', accent: '#2563eb', text: '#1e40af' },
        };
        const c = colors[data.severity] || colors.info;
    
        const now = new Date();
        const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    
        const toast = document.createElement('div');
        toast.style.cssText = `
            background: ${c.bg}; border: 1px solid ${c.border};
            border-left: 4px solid ${c.accent};
            border-radius: 10px; padding: 0; overflow: hidden;
            box-shadow: 0 4px 16px rgba(0,0,0,0.12);
            opacity: 0; transform: translateX(20px);
            transition: opacity 0.25s ease, transform 0.25s ease;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        `;
    
        toast.innerHTML = `
            <div style="display:flex; align-items:center; justify-content:space-between;
                        padding:0.6rem 0.9rem; border-bottom:1px solid ${c.border};">
                <span style="font-weight:600; font-size:0.82rem; color:${c.text};">
                    ${escapeHtml(data.title)}
                </span>
                <div style="display:flex; align-items:center; gap:0.5rem;">
                    <span style="font-size:0.7rem; color:#94a3b8;">${timeStr}</span>
                    <button onclick="this.closest('[data-toast]').remove()"
                            style="background:none; border:none; color:#94a3b8;
                                   cursor:pointer; font-size:0.9rem; line-height:1; padding:0;">✕</button>
                </div>
            </div>
            <div style="padding:0.65rem 0.9rem; font-size:0.82rem; color:#475569; line-height:1.5;">
                ${escapeHtml(data.message)}
                ${data.type === 'risk_high' ? `
                    <div style="margin-top:0.4rem;">
                        <a href="/risk/${data.project_id}/"
                           style="color:#2563eb; font-size:0.78rem; text-decoration:none; font-weight:500;">
                            View Risk Detail →
                        </a>
                    </div>` : ''}
            </div>
        `;
        toast.setAttribute('data-toast', '1');
    
        container.insertBefore(toast, container.firstChild);
    
        // Animate in
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                toast.style.opacity = '1';
                toast.style.transform = 'translateX(0)';
            });
        });
    
        // Auto-dismiss: 8s for danger, 5s for others
        const delay = data.severity === 'danger' ? 8000 : 5000;
        setTimeout(() => dismissToast(toast), delay);
    
        // Play a subtle sound for danger alerts
        if (data.severity === 'danger') playAlertSound();
    
        // Bump any alerts badge in the navbar
        bumpAlertsBadge();
    }
    
    function dismissToast(toast) {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(20px)';
        toast.addEventListener('transitionend', () => toast.remove(), { once: true });
    }
    
    function bumpAlertsBadge() {
        const badge = document.querySelector('.alerts-count-badge, #alertsBadge');
        if (badge) {
            const n = (parseInt(badge.textContent) || 0) + 1;
            badge.textContent = n;
            badge.style.display = 'inline';
        }
    }
    
    function playAlertSound() {
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.frequency.setValueAtTime(880, ctx.currentTime);
            osc.frequency.setValueAtTime(660, ctx.currentTime + 0.12);
            gain.gain.setValueAtTime(0.07, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
            osc.start(ctx.currentTime);
            osc.stop(ctx.currentTime + 0.4);
        } catch (e) { /* silent fail if audio blocked */ }
    }
    
    function escapeHtml(text) {
        const d = document.createElement('div');
        d.appendChild(document.createTextNode(text || ''));
        return d.innerHTML;
    }
    
    
    // ─────────────────────────────────────────────────────────────
    // 5. CONNECTION STATUS INDICATOR
    //    Small dot in the bottom-right shows WS status
    // ─────────────────────────────────────────────────────────────
    function createStatusIndicator() {
        const el = document.createElement('div');
        el.id = 'ge-ws-status';
        el.style.cssText = `
            position: fixed; bottom: 1rem; right: 1rem;
            display: flex; align-items: center; gap: 0.35rem;
            font-size: 0.7rem; color: #94a3b8; z-index: 9998;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        `;
        el.innerHTML = `
            <span id="ge-ws-dot" style="width:6px; height:6px; border-radius:50%;
                  background:#94a3b8; display:inline-block; transition:background 0.3s;"></span>
            <span id="ge-ws-text">Connecting...</span>
        `;
        document.body.appendChild(el);
    }
    
    function setConnectionStatus(state) {
        const dot = document.getElementById('ge-ws-dot');
        const text = document.getElementById('ge-ws-text');
        if (!dot || !text) return;
    
        const map = {
            connected:    { color: '#16a34a', label: 'Live' },
            disconnected: { color: '#dc2626', label: 'Reconnecting...' },
            denied:       { color: '#f59e0b', label: 'Notifications off' },
        };
        const s = map[state] || map.disconnected;
        dot.style.background = s.color;
        text.textContent = s.label;
    }
    
    
    // ─────────────────────────────────────────────────────────────
    // 6. INIT — runs on page load
    // ─────────────────────────────────────────────────────────────
    async function init() {
        // Only run if GROWEASY_PROJECT_ID is set on this page
        if (typeof GROWEASY_PROJECT_ID === 'undefined' || !GROWEASY_PROJECT_ID) {
            return;
        }
    
        createStatusIndicator();
    
        // Register service worker
        const swRegistration = await registerServiceWorker();
    
        // Request push permission after 3 seconds
        // (gives user time to see the page before the prompt)
        setTimeout(async () => {
            const granted = await requestNotificationPermission();
            if (!granted) {
                setConnectionStatus('denied');
            }
        }, 3000);
    
        // Wait for service worker to be ready, then tell it which project to watch
        navigator.serviceWorker.ready.then(() => {
            startServiceWorkerConnection(GROWEASY_PROJECT_ID);
        });
    }
    
    // Expose requestPermission publicly so the banner button can call it
    window.GrowEasyNotifications = {
        requestPermission: async function() {
            const granted = await requestNotificationPermission();
            if (granted) {
                navigator.serviceWorker.ready.then(() => {
                    startServiceWorkerConnection(GROWEASY_PROJECT_ID);
                });
            }
        }
    };
    
    // Start
    init();
    
    })(); // end IIFE