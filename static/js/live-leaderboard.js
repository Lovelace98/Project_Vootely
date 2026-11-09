function connectLiveView(options) {
    var opts = {
        wsUrl: options.wsUrl,
        containerId: options.containerId,
        reconnect: options.reconnect !== false,
        reconnectBaseDelay: 1000,
        reconnectMaxDelay: 30000,
        onMessage: options.onMessage || null,
    };

    var container = document.getElementById(opts.containerId);
    if (!container) {
        console.warn('[LiveView] Container #' + opts.containerId + ' not found');
        return;
    }

    var ws = null;
    var reconnectAttempt = 0;
    var reconnectTimer = null;
    var closed = false;
    var fallbackTimer = null;
    var fallbackUrl = container.dataset.fallbackUrl || options.fallbackUrl || null;

    function startFallbackPoll() {
        if (!fallbackUrl || fallbackTimer || closed) return;
        var jitter = Math.floor(Math.random() * 15000);
        var interval = 30000 + jitter;
        fallbackTimer = setTimeout(function () {
            fallbackTimer = null;
            if (closed || (ws && ws.readyState === WebSocket.OPEN)) return;
            fetch(fallbackUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (res) { return res.text(); })
                .then(function (html) {
                    if (html && (!ws || ws.readyState !== WebSocket.OPEN)) {
                        animateSwap(container, html);
                    }
                    startFallbackPoll();
                })
                .catch(function () {
                    startFallbackPoll();
                });
        }, interval);
    }

    function stopFallbackPoll() {
        if (fallbackTimer) {
            clearTimeout(fallbackTimer);
            fallbackTimer = null;
        }
    }

    function connect() {
        if (closed) return;

        try {
            ws = new WebSocket(opts.wsUrl);
        } catch (e) {
            console.warn('[LiveView] WebSocket connection failed:', e);
            startFallbackPoll();
            scheduleReconnect();
            return;
        }

        ws.onopen = function () {
            console.log('[LiveView] Connected');
            reconnectAttempt = 0;
            stopFallbackPoll();
            container.classList.remove('live-view-disconnected');
            container.classList.add('live-view-connected');
        };

        ws.onmessage = function (event) {
            try {
                var data = JSON.parse(event.data);
                if (data.html) {
                    animateSwap(container, data.html);
                }
                if (opts.onMessage) {
                    opts.onMessage(data);
                }
            } catch (e) {
                console.warn('[LiveView] Failed to parse message:', e);
            }
        };

        ws.onclose = function () {
            container.classList.remove('live-view-connected');
            container.classList.add('live-view-disconnected');
            console.log('[LiveView] Disconnected');
            startFallbackPoll();
            scheduleReconnect();
        };

        ws.onerror = function () {
            console.warn('[LiveView] Error');
            startFallbackPoll();
        };
    }

    function scheduleReconnect() {
        if (!opts.reconnect || closed) return;
        if (reconnectTimer) return;

        var delay = Math.min(
            opts.reconnectBaseDelay * Math.pow(2, reconnectAttempt),
            opts.reconnectMaxDelay
        );
        reconnectAttempt++;
        console.log('[LiveView] Reconnecting in ' + delay + 'ms (attempt ' + reconnectAttempt + ')');
        reconnectTimer = setTimeout(function () {
            reconnectTimer = null;
            connect();
        }, delay);
    }

    function animateSwap(el, newHtml) {
        var oldHeight = el.offsetHeight;
        el.style.transition = 'opacity 0.3s ease-out';
        el.style.opacity = '0';

        setTimeout(function () {
            el.innerHTML = newHtml;
            el.style.opacity = '1';
        }, 200);
    }

    function disconnect() {
        closed = true;
        stopFallbackPoll();
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        if (ws) {
            ws.onclose = null;
            ws.close();
            ws = null;
        }
    }

    connect();

    return {
        disconnect: disconnect,
        reconnect: function () {
            closed = false;
            if (ws) ws.close();
            connect();
        },
    };
}
