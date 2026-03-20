/**
 * static/js/service-worker.js
 *
 * WHAT IS A SERVICE WORKER?
 * A service worker is a JavaScript file that the browser runs in
 * the background — completely separate from your webpage.
 * It keeps running even when the GrowEasy tab is closed or minimised.
 *
 * This is how Gmail, WhatsApp Web, and Slack send you notifications
 * when you're not actively looking at the tab.
 *
 * HOW IT CONNECTS TO OUR SYSTEM:
 *   1. User visits GrowEasy → browser registers this service worker
 *   2. We open a WebSocket connection FROM the service worker
 *   3. When Celery fires a notification → WebSocket message arrives
 *   4. Service worker calls self.registration.showNotification()
 *   5. OS shows a native notification (even if tab is minimised)
 *
 * NOTE: Service workers require HTTPS in production.
 *       They work on http://localhost in development.
 */

const WS_RECONNECT_DELAY = 5000;  // 5 seconds between reconnect attempts
let socket = null;
let projectId = null;

// ─────────────────────────────────────────────
// INSTALL & ACTIVATE (required lifecycle events)
// ─────────────────────────────────────────────
self.addEventListener('install', event => {
    // Take control immediately without waiting for old SW to expire
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    // Take control of all open tabs immediately
    event.waitUntil(clients.claim());
});


// ─────────────────────────────────────────────
// MESSAGES FROM THE MAIN PAGE
// The main page sends us the project_id and tells
// us to start or stop the WebSocket connection
// ─────────────────────────────────────────────
self.addEventListener('message', event => {
    const data = event.data;

    if (data.type === 'START_NOTIFICATIONS') {
        projectId = data.project_id;
        connectWebSocket();
    }

    if (data.type === 'STOP_NOTIFICATIONS') {
        if (socket) {
            socket.close();
            socket = null;
        }
    }
});


// ─────────────────────────────────────────────
// WEBSOCKET CONNECTION
// Opens a connection to our Django Channels consumer
// ─────────────────────────────────────────────
function connectWebSocket() {
    if (!projectId) return;

    // Use wss:// in production (HTTPS), ws:// in development
    const protocol = self.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${self.location.host}/ws/notifications/${projectId}/`;

    try {
        socket = new WebSocket(wsUrl);
    } catch (e) {
        scheduleReconnect();
        return;
    }

    socket.onopen = () => {
        console.log('[SW] WebSocket connected for project', projectId);
        // Tell the main page we're connected
        broadcastToClients({ type: 'WS_CONNECTED' });
    };

    socket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleNotification(data);
        } catch (e) {
            console.error('[SW] Failed to parse notification', e);
        }
    };

    socket.onclose = () => {
        console.log('[SW] WebSocket closed, reconnecting...');
        broadcastToClients({ type: 'WS_DISCONNECTED' });
        scheduleReconnect();
    };

    socket.onerror = () => {
        socket.close();
    };
}

function scheduleReconnect() {
    setTimeout(() => {
        if (projectId) connectWebSocket();
    }, WS_RECONNECT_DELAY);
}


// ─────────────────────────────────────────────
// HANDLE INCOMING NOTIFICATION
// Decides whether to show a browser push notification
// or just relay to the open tab for a toast
// ─────────────────────────────────────────────
async function handleNotification(data) {
    // Always tell any open tabs about this notification
    // (open tabs show toast notifications instead of push)
    broadcastToClients({
        type: 'SHOW_TOAST',
        notification: data
    });

    // Check if any GrowEasy tab is currently focused
    const allClients = await clients.matchAll({
        type: 'window',
        includeUncontrolled: true
    });

    const isFocused = allClients.some(client =>
        client.visibilityState === 'visible'
    );

    // Only show OS push notification if NO tab is focused
    // (if a tab is open, the toast is enough)
    if (!isFocused) {
        await showPushNotification(data);
    }
}


// ─────────────────────────────────────────────
// SHOW OS-LEVEL PUSH NOTIFICATION
// This appears in the system notification tray
// even when the browser is minimised
// ─────────────────────────────────────────────
async function showPushNotification(data) {
    // Map severity to an icon
    const iconMap = {
        danger:  '/static/img/icon-danger.png',
        warning: '/static/img/icon-warning.png',
        info:    '/static/img/icon-info.png',
    };

    const icon = iconMap[data.severity] || iconMap.info;

    const options = {
        body: data.message,
        icon: icon,
        badge: '/static/img/badge.png',
        tag: `groweasy-${data.type}-${data.project_id}`,
        // tag means: if a notification with this tag already exists,
        // replace it rather than stacking duplicates
        renotify: true,
        data: {
            project_id: data.project_id,
            notification_type: data.type,
            url: data.type === 'risk_high'
                ? `/risk/${data.project_id}/`
                : `/dashboard/?project_id=${data.project_id}`
        },
        actions: [
            {
                action: 'view',
                title: 'View Project'
            },
            {
                action: 'dismiss',
                title: 'Dismiss'
            }
        ]
    };

    try {
        await self.registration.showNotification(data.title, options);
    } catch (e) {
        console.error('[SW] Failed to show notification', e);
    }
}


// ─────────────────────────────────────────────
// NOTIFICATION CLICK HANDLER
// When user clicks the OS notification, open GrowEasy
// ─────────────────────────────────────────────
self.addEventListener('notificationclick', event => {
    event.notification.close();

    if (event.action === 'dismiss') return;

    const targetUrl = event.notification.data?.url || '/dashboard/';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true })
            .then(clientList => {
                // If GrowEasy is already open, focus that tab
                for (const client of clientList) {
                    if (client.url.includes(self.location.host) && 'focus' in client) {
                        client.focus();
                        client.navigate(targetUrl);
                        return;
                    }
                }
                // Otherwise open a new tab
                if (clients.openWindow) {
                    return clients.openWindow(targetUrl);
                }
            })
    );
});


// ─────────────────────────────────────────────
// HELPER: Send message to all open GrowEasy tabs
// ─────────────────────────────────────────────
async function broadcastToClients(message) {
    const allClients = await clients.matchAll({
        type: 'window',
        includeUncontrolled: true
    });
    allClients.forEach(client => client.postMessage(message));
}