window.ticketCheckInPage = function ticketCheckInPage(config) {
    return {
        eventId: String(config.eventId),
        eventTitle: config.eventTitle,
        scanUrl: config.scanUrl,
        provisionalSyncUrl: config.provisionalSyncUrl || '',
        provisionalAllowed: !!config.provisionalAllowed,
        csrfToken: config.csrfToken,
        autoLock: !!config.autoLock,
        tickets: [],
        provisionalQueue: [],
        provisionalCandidate: null,
        provisionalSyncing: false,
        deviceId: '',
        lockInput: '',
        locked: false,
        online: navigator.onLine,
        code: '',
        query: '',
        resultMessage: '',
        resultDetail: '',
        resultOk: false,
        scanning: false,
        scannerState: 'Lock the event, then start the camera or use manual lookup.',
        stream: null,
        detector: null,
        scanTimer: null,
        html5Scanner: null,
        usingNativeScanner: false,
        lastScan: { code: '', at: 0 },

        init() {
            const dataEl = document.getElementById('vc-doorlist-data');
            const embedded = dataEl ? JSON.parse(dataEl.textContent || '[]') : [];
            const cached = this.readCachedDoorlist();
            this.deviceId = this.ensureDeviceId();
            this.provisionalQueue = this.readProvisionalQueue();
            this.tickets = embedded.length ? embedded : cached;
            this.applyLocalProvisionalState();
            this.scannerState = 'Camera ready. Start camera scanning or use manual lookup.';
            if (embedded.length) this.cacheDoorlist();
            this.locked = this.autoLock || localStorage.getItem(this.lockKey()) === this.eventId;
            this.lockInput = this.locked ? this.eventId : '';
            window.addEventListener('online', () => {
                this.online = true;
                this.syncProvisionalQueue();
            });
            window.addEventListener('offline', () => { this.online = false; });
            if (this.online) this.syncProvisionalQueue();
        },

        get checkedInCount() {
            return this.tickets.filter((ticket) => ticket.status === 'used').length;
        },

        get activeCount() {
            return this.tickets.filter((ticket) => ticket.status === 'active').length;
        },

        get provisionalPendingCount() {
            return this.provisionalQueue.filter((attempt) => attempt.status === 'pending').length;
        },

        get provisionalConfirmedCount() {
            return this.provisionalQueue.filter((attempt) => attempt.status === 'confirmed').length;
        },

        get provisionalRejectedCount() {
            return this.provisionalQueue.filter((attempt) => attempt.status === 'rejected').length;
        },

        get filteredTickets() {
            const term = this.query.trim().toLowerCase();
            const source = term ? this.tickets.filter((ticket) => ticket.searchable.includes(term)) : this.tickets;
            return source.slice(0, 80);
        },

        lockKey() {
            return `vootely.checkin.locked.${this.eventId}`;
        },

        cacheKey() {
            return `vootely.checkin.doorlist.${this.eventId}`;
        },

        provisionalQueueKey() {
            return `vootely.checkin.provisional.${this.eventId}`;
        },

        deviceKey() {
            return 'vootely.checkin.device_id';
        },

        randomId() {
            const browserCrypto = window.crypto || window.msCrypto;
            if (browserCrypto && browserCrypto.randomUUID) return browserCrypto.randomUUID();
            return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
        },

        ensureDeviceId() {
            let deviceId = localStorage.getItem(this.deviceKey());
            if (!deviceId) {
                deviceId = this.randomId();
                localStorage.setItem(this.deviceKey(), deviceId);
            }
            return deviceId;
        },

        normalizeEventCode(value) {
            return String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
        },

        lockEvent() {
            if (this.normalizeEventCode(this.lockInput) !== this.normalizeEventCode(this.eventId)) {
                this.showResult(false, `Event code must be ${this.eventId}.`, '');
                return;
            }
            localStorage.setItem(this.lockKey(), this.eventId);
            this.locked = true;
            this.cacheDoorlist();
            this.showResult(true, 'Event locked for check-in.', `${this.tickets.length} paid tickets cached on this device.`);
        },

        cacheDoorlist() {
            localStorage.setItem(this.cacheKey(), JSON.stringify({ saved_at: new Date().toISOString(), tickets: this.tickets }));
        },

        readCachedDoorlist() {
            try {
                const cached = JSON.parse(localStorage.getItem(this.cacheKey()) || '{}');
                return Array.isArray(cached.tickets) ? cached.tickets : [];
            } catch (error) {
                return [];
            }
        },

        readProvisionalQueue() {
            try {
                const cached = JSON.parse(localStorage.getItem(this.provisionalQueueKey()) || '[]');
                return Array.isArray(cached) ? cached : [];
            } catch (error) {
                return [];
            }
        },

        writeProvisionalQueue() {
            localStorage.setItem(this.provisionalQueueKey(), JSON.stringify(this.provisionalQueue));
        },

        applyLocalProvisionalState() {
            const latestByCode = {};
            this.provisionalQueue.forEach((attempt) => {
                latestByCode[String(attempt.ticket_code || '').toUpperCase()] = attempt;
            });
            this.tickets = this.tickets.map((ticket) => {
                const attempt = latestByCode[String(ticket.code || '').toUpperCase()];
                if (!attempt) return ticket;
                if (attempt.status === 'pending') {
                    return { ...ticket, status: 'provisional_pending', provisional_result: 'pending', provisional_message: 'Pending sync' };
                }
                if (attempt.status === 'rejected') {
                    return { ...ticket, status: 'provisional_rejected', provisional_result: attempt.result, provisional_message: attempt.message };
                }
                return ticket;
            });
        },

        showResult(ok, message, detail) {
            this.resultOk = !!ok;
            this.resultMessage = message || '';
            this.resultDetail = detail || '';
        },

        checkedInLabel(ticket) {
            return ticket.checked_in_by ? `Checked in by ${ticket.checked_in_by}` : 'Checked in';
        },

        async checkIn(rawCode) {
            const ticketCode = String(rawCode || '').trim().toUpperCase();
            if (!this.locked) {
                this.showResult(false, 'Lock the event before checking in tickets.', '');
                return;
            }
            if (!ticketCode) {
                this.showResult(false, 'Enter or scan a ticket ID.', '');
                return;
            }
            const now = Date.now();
            if (this.lastScan.code === ticketCode && now - this.lastScan.at < 1800) return;
            this.lastScan = { code: ticketCode, at: now };

            try {
                const response = await fetch(this.scanUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
                    body: JSON.stringify({ code: ticketCode }),
                });
                const data = await response.json();
                this.applyServerResult(data);
                this.showResult(!!data.ok, data.message || 'Scan complete', this.resultSummary(data));
                if (data.ok) this.code = '';
            } catch (error) {
                const cached = this.tickets.find((ticket) => ticket.code.toUpperCase() === ticketCode);
                if (cached) {
                    this.handleOfflineTicket(cached);
                } else {
                    this.showResult(false, 'Network unavailable and this ticket is not in the cached doorlist.', '');
                }
            }
        },

        handleOfflineTicket(ticket) {
            const label = `${ticket.buyer_name || ticket.buyer_email || ticket.buyer_phone || ticket.code} · ${ticket.ticket_type}`;
            if (ticket.status === 'used') {
                this.showResult(false, 'Network unavailable. This cached ticket is already checked in.', label);
                return;
            }
            if (ticket.status === 'provisional_pending') {
                this.showResult(false, 'This ticket is already pending provisional sync on this device.', label);
                return;
            }
            if (!this.provisionalAllowed || !this.provisionalSyncUrl) {
                this.showResult(false, 'Network unavailable. Ticket found, but emergency provisional entry is not enabled for this scanner.', label);
                return;
            }
            this.provisionalCandidate = ticket;
            this.showResult(false, 'Network unavailable. Ticket found in cached doorlist.', `${label}. Confirm before admitting provisionally.`);
        },

        confirmProvisionalEntry() {
            const ticket = this.provisionalCandidate;
            if (!ticket) return;
            const code = String(ticket.code || '').toUpperCase();
            if (this.provisionalQueue.some((attempt) => attempt.ticket_code === code && attempt.status === 'pending')) {
                this.provisionalCandidate = null;
                this.showResult(false, 'This ticket is already pending provisional sync on this device.', code);
                return;
            }
            const attempt = {
                client_attempt_id: this.randomId(),
                ticket_code: code,
                offline_at: new Date().toISOString(),
                device_id: this.deviceId,
                status: 'pending',
                result: 'pending',
                message: 'Pending sync',
                ticket_snapshot: {
                    buyer_name: ticket.buyer_name,
                    buyer_email: ticket.buyer_email,
                    buyer_phone: ticket.buyer_phone,
                    ticket_type: ticket.ticket_type,
                    purchase_reference: ticket.purchase_reference,
                },
            };
            this.provisionalQueue.push(attempt);
            this.writeProvisionalQueue();
            this.updateLocalTicket(code, {
                status: 'provisional_pending',
                provisional_result: 'pending',
                provisional_message: 'Pending sync',
            });
            this.provisionalCandidate = null;
            this.showResult(false, 'Provisional entry recorded on this device.', 'Admit only if your gate procedure allows it. It will sync when network returns.');
            if (this.online) this.syncProvisionalQueue();
        },

        cancelProvisionalEntry() {
            this.provisionalCandidate = null;
        },

        resultSummary(data) {
            return [data.ticket_code, data.ticket_type, data.buyer_name || data.buyer_email || data.buyer_phone].filter(Boolean).join(' · ');
        },

        applyServerResult(data) {
            if (!data.ticket_code) return;
            const code = String(data.ticket_code).toUpperCase();
            this.updateLocalTicket(code, {
                status: data.ticket_status || undefined,
                buyer_name: data.buyer_name || undefined,
                buyer_email: data.buyer_email || undefined,
                buyer_phone: data.buyer_phone || undefined,
                ticket_type: data.ticket_type || undefined,
                purchase_reference: data.purchase_reference || undefined,
                used_at: data.used_at || undefined,
                checked_in_by: data.checked_in_by || undefined,
                provisional_result: '',
                provisional_message: '',
            });
            this.cacheDoorlist();
        },

        updateLocalTicket(code, changes) {
            const index = this.tickets.findIndex((ticket) => ticket.code.toUpperCase() === code);
            if (index === -1) return;
            const cleaned = {};
            Object.keys(changes).forEach((key) => {
                if (changes[key] !== undefined) cleaned[key] = changes[key];
            });
            this.tickets[index] = {
                ...this.tickets[index],
                ...cleaned,
            };
            this.cacheDoorlist();
        },

        async syncProvisionalQueue() {
            if (!this.provisionalSyncUrl || this.provisionalSyncing || !navigator.onLine) return;
            const pending = this.provisionalQueue.filter((attempt) => attempt.status === 'pending');
            if (!pending.length) return;
            this.provisionalSyncing = true;
            try {
                const response = await fetch(this.provisionalSyncUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
                    body: JSON.stringify({ attempts: pending }),
                });
                const data = await response.json();
                if (!response.ok || !data.ok) throw new Error(data.message || 'Unable to sync provisional entries.');
                (data.results || []).forEach((result) => {
                    const index = this.provisionalQueue.findIndex((attempt) => attempt.client_attempt_id === result.client_attempt_id);
                    if (index === -1) return;
                    this.provisionalQueue[index] = {
                        ...this.provisionalQueue[index],
                        status: result.status,
                        result: result.result,
                        message: result.message,
                        synced_at: result.synced_at,
                    };
                    const status = result.status === 'confirmed'
                        ? 'used'
                        : (result.ticket_status === 'used' ? 'used' : 'provisional_rejected');
                    this.updateLocalTicket(String(result.ticket_code || '').toUpperCase(), {
                        status,
                        provisional_result: result.result,
                        provisional_message: result.message,
                        checked_in_by: result.status === 'confirmed' ? 'Provisional sync' : '',
                    });
                });
                this.writeProvisionalQueue();
                this.showResult(true, 'Provisional entries synced.', `${this.provisionalConfirmedCount} confirmed · ${this.provisionalRejectedCount} rejected · ${this.provisionalPendingCount} pending`);
            } catch (error) {
                this.showResult(false, 'Provisional sync failed.', error.message || 'Retry when network is stable.');
            } finally {
                this.provisionalSyncing = false;
            }
        },

        async startScanner() {
            if (!this.locked) return;
            this.scannerState = 'Starting camera...';
            if ('BarcodeDetector' in window && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
                await this.startNativeScanner();
                return;
            }
            await this.startHtml5Scanner();
        },

        async startNativeScanner() {
            try {
                this.usingNativeScanner = true;
                this.detector = new BarcodeDetector({ formats: ['qr_code'] });
                this.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
                const video = document.getElementById('vc-checkin-video');
                video.srcObject = this.stream;
                video.classList.remove('hidden');
                await video.play();
                this.scanning = true;
                this.scannerState = 'Camera active. Point it at a ticket QR code.';
                this.scanTimer = window.setInterval(async () => {
                    if (!this.scanning) return;
                    try {
                        const codes = await this.detector.detect(video);
                        if (codes.length) this.checkIn(codes[0].rawValue);
                    } catch (error) {}
                }, 500);
            } catch (error) {
                this.stopScanner();
                this.scannerState = 'Camera could not start. Use manual lookup or allow camera access.';
            }
        },

        async startHtml5Scanner() {
            if (!window.Html5Qrcode) {
                this.scannerState = 'Native QR scanning is not available. Manual lookup is ready.';
                return;
            }
            try {
                this.usingNativeScanner = false;
                this.html5Scanner = new Html5Qrcode('qr-scanner');
                await this.html5Scanner.start(
                    { facingMode: 'environment' },
                    { fps: 10, qrbox: 220 },
                    (decodedText) => this.checkIn(decodedText)
                );
                this.scanning = true;
                this.scannerState = 'Camera active. Point it at a ticket QR code.';
            } catch (error) {
                this.scannerState = 'Camera could not start. Use manual lookup or allow camera access.';
            }
        },

        stopScanner() {
            this.scanning = false;
            if (this.scanTimer) window.clearInterval(this.scanTimer);
            this.scanTimer = null;
            if (this.stream) {
                this.stream.getTracks().forEach((track) => track.stop());
                this.stream = null;
            }
            const video = document.getElementById('vc-checkin-video');
            if (video) {
                video.pause();
                video.srcObject = null;
                video.classList.add('hidden');
            }
            if (this.html5Scanner) {
                this.html5Scanner.stop().catch(() => {});
                this.html5Scanner = null;
            }
            this.scannerState = 'Camera stopped. Start it again or use manual lookup.';
        },
    };
};
