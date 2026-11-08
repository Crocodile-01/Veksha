/**
 * VEKSHA Health Telemetry Dashboard
 * Application Logic & Canvas Charting Engine
 */

(function () {
  'use strict';

  // Config & State
  const DEFAULT_API_BASE = (window.location.protocol.startsWith('http'))
    ? window.location.origin
    : 'http://localhost:8000';
  let apiBaseUrl = localStorage.getItem('veksha_api_url') || DEFAULT_API_BASE;
  const POLL_INTERVAL_MS = 1500;
  const MAX_CHART_POINTS = 30;

  let pollTimer = null;
  let isBackendOnline = false;
  let historyData = []; // Array of { time: Date, hr: number, spo2: number }
  let lastTimestamp = null;
  let totalReadingsReceived = 0;

  // DOM Elements
  const backendStatusPill = document.getElementById('backendStatusPill');
  const backendStatusDot = document.getElementById('backendStatusDot');
  const backendStatusText = document.getElementById('backendStatusText');

  const firebaseStatusPill = document.getElementById('firebaseStatusPill');
  const firebaseStatusDot = document.getElementById('firebaseStatusDot');
  const firebaseStatusText = document.getElementById('firebaseStatusText');

  const sensorStatusPill = document.getElementById('sensorStatusPill');
  const sensorStatusDot = document.getElementById('sensorStatusDot');
  const sensorStatusText = document.getElementById('sensorStatusText');

  const noticeBanner = document.getElementById('noticeBanner');
  const noticeIcon = document.getElementById('noticeIcon');
  const noticeMessage = document.getElementById('noticeMessage');

  const heartRateValue = document.getElementById('heartRateValue');
  const heartRateStatus = document.getElementById('heartRateStatus');
  const hrRangeFill = document.getElementById('hrRangeFill');
  const beatPulseDot = document.getElementById('beatPulseDot');

  const spo2Value = document.getElementById('spo2Value');
  const spo2Status = document.getElementById('spo2Status');
  const spo2GradeBadge = document.getElementById('spo2GradeBadge');
  const spo2RangeFill = document.getElementById('spo2RangeFill');

  const deviceIdText = document.getElementById('deviceIdText');
  const lastUpdatedText = document.getElementById('lastUpdatedText');
  const telemetryStateChip = document.getElementById('telemetryStateChip');
  const totalReadingsCount = document.getElementById('totalReadingsCount');

  const chartCanvas = document.getElementById('vitalsChart');
  const chartEmptyState = document.getElementById('chartEmptyState');
  const clearChartBtn = document.getElementById('clearChartBtn');
  const sendTestSampleBtn = document.getElementById('sendTestSampleBtn');

  // Modal Elements
  const configBtn = document.getElementById('configBtn');
  const configModal = document.getElementById('configModal');
  const closeModalBtn = document.getElementById('closeModalBtn');
  const cancelConfigBtn = document.getElementById('cancelConfigBtn');
  const saveConfigBtn = document.getElementById('saveConfigBtn');
  const apiUrlInput = document.getElementById('apiUrlInput');
  const activeEndpointUrl = document.getElementById('activeEndpointUrl');

  // SOS DOM Elements
  const sosTriggerBtn = document.getElementById('sosTriggerBtn');
  const sosConfirmModal = document.getElementById('sosConfirmModal');
  const sosCancelBtn = document.getElementById('sosCancelBtn');
  const sosConfirmBtn = document.getElementById('sosConfirmBtn');
  const sosConfirmHr = document.getElementById('sosConfirmHr');
  const sosConfirmSpo2 = document.getElementById('sosConfirmSpo2');
  const sosLatestStatus = document.getElementById('sosLatestStatus');
  const sosLastTriggered = document.getElementById('sosLastTriggered');
  const sosCountBadge = document.getElementById('sosCountBadge');
  const sosHistoryList = document.getElementById('sosHistoryList');
  const sosHistoryEmpty = document.getElementById('sosHistoryEmpty');

  // SOS State
  let latestHrSnapshot = null;
  let latestSpo2Snapshot = null;
  let latestSensorStatusSnapshot = null;
  let latestDeviceIdSnapshot = 'esp32-veksha';
  let sosIsSending = false;

  /**
   * Initialize Dashboard
   */
  function init() {
    apiUrlInput.value = apiBaseUrl;
    updateEndpointDisplay();
    setupEventListeners();
    setupCanvasDPI();

    // Start background telemetry polling
    startPolling();

    // Immediate initial telemetry & health check
    fetchTelemetry();
    fetchFirebaseStatus();
    setInterval(fetchFirebaseStatus, 5000);
    fetchGatewayStatus();
    setInterval(fetchGatewayStatus, 15000);

    // Pre-populate chart with recent historical readings from Firebase
    loadHistoricalReadings();

    // Load existing SOS history on startup
    fetchSosHistory();
  }

  function updateEndpointDisplay() {
    activeEndpointUrl.textContent = `${apiBaseUrl}/api/v1/readings/latest`;
  }

  function setupEventListeners() {
    // Modal controls
    configBtn.addEventListener('click', () => {
      apiUrlInput.value = apiBaseUrl;
      configModal.classList.remove('hidden');
    });

    closeModalBtn.addEventListener('click', () => configModal.classList.add('hidden'));
    cancelConfigBtn.addEventListener('click', () => configModal.classList.add('hidden'));

    saveConfigBtn.addEventListener('click', () => {
      const newUrl = apiUrlInput.value.trim().replace(/\/+$/, '');
      if (newUrl) {
        apiBaseUrl = newUrl;
        localStorage.setItem('veksha_api_url', apiBaseUrl);
        updateEndpointDisplay();
        configModal.classList.add('hidden');
        historyData = [];
        totalReadingsReceived = 0;
        updateChart();
        fetchTelemetry();
      }
    });

    clearChartBtn.addEventListener('click', () => {
      historyData = [];
      updateChart();
    });

    sendTestSampleBtn.addEventListener('click', sendSampleReading);

    // SOS Button: open confirmation modal
    sosTriggerBtn.addEventListener('click', () => {
      // Populate vitals snapshot in confirmation dialog
      sosConfirmHr.textContent = latestHrSnapshot !== null ? `${Math.round(latestHrSnapshot)} BPM` : '-- BPM';
      sosConfirmSpo2.textContent = latestSpo2Snapshot !== null ? `${latestSpo2Snapshot.toFixed(1)} %` : '-- %';
      sosConfirmModal.classList.remove('hidden');
    });

    sosCancelBtn.addEventListener('click', () => sosConfirmModal.classList.add('hidden'));

    sosConfirmBtn.addEventListener('click', () => {
      sosConfirmModal.classList.add('hidden');
      sendSos();
    });

    // Responsive Canvas Resizing
    window.addEventListener('resize', () => {
      setupCanvasDPI();
      drawChart();
    });
  }

  /**
   * Send a test reading to verify backend POST validation and live dashboard update
   */
  async function sendSampleReading() {
    sendTestSampleBtn.disabled = true;
    sendTestSampleBtn.textContent = 'Transmitting sample...';

    // Random realistic vitals for testing
    const sampleHr = Math.floor(68 + Math.random() * 16);
    const sampleSpo2 = Number((97.0 + Math.random() * 2.5).toFixed(1));

    const payload = {
      heart_rate: sampleHr,
      spo2: sampleSpo2,
      sensor_status: 'VALID_DATA',
      device_id: 'esp32-veksha-test'
    };

    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/readings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        throw new Error(`HTTP error ${response.status}`);
      }

      await fetchTelemetry();
    } catch (err) {
      alert(`Failed to send test reading: ${err.message}. Is backend running at ${apiBaseUrl}?`);
    } finally {
      sendTestSampleBtn.disabled = false;
      sendTestSampleBtn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polygon points="5 3 19 12 5 21 5 3"></polygon>
        </svg>
        Send Test Sample Reading
      `;
    }
  }

  /**
   * Main telemetry polling cycle
   */
  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(fetchTelemetry, POLL_INTERVAL_MS);
  }

  async function fetchTelemetry() {
    try {
      // 1. Fetch latest reading
      const readingRes = await fetch(`${apiBaseUrl}/api/v1/readings/latest`, { cache: 'no-store' });
      
      if (!readingRes.ok) {
        throw new Error(`Server responded with ${readingRes.status}`);
      }

      const result = await readingRes.json();
      setBackendOnlineState(true);

      if (result.status === 'no_data' || !result.data) {
        renderNoDataState();
      } else {
        renderTelemetryData(result.data);
      }
    } catch (error) {
      setBackendOnlineState(false);
      renderOfflineState(error.message);
    }
  }

  /**
   * Check Firebase / Firestore cloud synchronization status via /health
   */
  async function fetchFirebaseStatus() {
    try {
      const res = await fetch(`${apiBaseUrl}/health`, { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (data.firebase && data.firebase.firebase_connected) {
        if (firebaseStatusDot) firebaseStatusDot.className = 'status-dot pulse-green';
        if (firebaseStatusText) firebaseStatusText.textContent = 'Firebase: Synced';
        if (firebaseStatusPill) {
          firebaseStatusPill.style.borderColor = 'rgba(0, 230, 118, 0.3)';
          firebaseStatusPill.title = `Firestore Project: ${data.firebase.firebase_project}`;
        }
      } else {
        if (firebaseStatusDot) firebaseStatusDot.className = 'status-dot pulse-gray';
        if (firebaseStatusText) firebaseStatusText.textContent = 'Firebase: Offline';
        if (firebaseStatusPill) {
          firebaseStatusPill.style.borderColor = 'rgba(255, 255, 255, 0.1)';
          firebaseStatusPill.title = data.firebase ? (data.firebase.firebase_error || 'Not connected') : 'Offline';
        }
      }
    } catch (_) {
      if (firebaseStatusDot) firebaseStatusDot.className = 'status-dot pulse-gray';
      if (firebaseStatusText) firebaseStatusText.textContent = 'Firebase: Offline';
    }
  }

  /**
   * Fetch Android SMS Gateway status and update the gateway card.
   */
  async function fetchGatewayStatus() {
    const dot = document.getElementById('gatewayStatusDot');
    const text = document.getElementById('gatewayStatusText');
    const badge = document.getElementById('gatewayBadge');
    const deviceCount = document.getElementById('gatewayDeviceCount');
    const lastSms = document.getElementById('gatewayLastSms');
    const smsLog = document.getElementById('gatewaySmsLog');
    try {
      const res = await fetch(`${apiBaseUrl}/api/v1/gateway/status`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const count = data.gateway_count || 0;
      if (dot) dot.className = count > 0 ? 'status-dot pulse-green' : 'status-dot pulse-gray';
      if (text) text.textContent = count > 0 ? `Gateway: ${count} Device${count>1?'s':''}` : 'Gateway: No Device';
      if (badge) badge.textContent = count > 0 ? '✅ Online' : '⚫ Offline';

      if (deviceCount) deviceCount.textContent = count;

      // Recent SMS statuses
      const statuses = data.recent_sms_statuses || [];
      if (smsLog) {
        if (statuses.length === 0) {
          smsLog.textContent = 'No SMS reported yet';
        } else {
          smsLog.textContent = statuses.map(s => {
            const t = s.reported_at ? new Date(s.reported_at * 1000).toLocaleTimeString() : '—';
            return `[${t}] evt:${s.event_id} → ${s.sms_status}`;
          }).join('\n');
        }
      }
      if (lastSms) {
        const latest = statuses[0];
        lastSms.textContent = latest ?
          `${latest.sms_status} @ ${new Date(latest.reported_at * 1000).toLocaleTimeString()}` : '—';
      }
    } catch (e) {
      if (dot) dot.className = 'status-dot pulse-gray';
      if (text) text.textContent = 'Gateway: —';
      if (badge) badge.textContent = '⚫ Offline';
    }
  }

  /**
   * Preload recent historical readings from Firebase into the canvas chart
   */
  async function loadHistoricalReadings() {
    try {
      const res = await fetch(`${apiBaseUrl}/api/v1/readings/history?limit=30`, { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (data.readings && data.readings.length > 0) {
        historyData = [];
        data.readings.forEach(r => {
          if (r.sensor_status === 'VALID_DATA' && r.heart_rate !== null && r.spo2 !== null) {
            historyData.push({
              time: new Date(r.timestamp),
              hr: Number(r.heart_rate),
              spo2: Number(r.spo2),
            });
          }
        });
        if (historyData.length > 0) {
          updateChart();
        }
      }
    } catch (e) {
      console.warn('Could not load historical readings:', e);
    }
  }

  /**
   * State Renders
   */
  function setBackendOnlineState(online) {
    isBackendOnline = online;
    if (online) {
      backendStatusDot.className = 'status-dot pulse-green';
      backendStatusText.textContent = 'Backend Online';
      backendStatusPill.style.borderColor = 'rgba(0, 230, 118, 0.3)';
    } else {
      backendStatusDot.className = 'status-dot pulse-red';
      backendStatusText.textContent = 'Backend Offline';
      backendStatusPill.style.borderColor = 'rgba(255, 45, 85, 0.4)';
    }
  }

  function renderOfflineState(errMsg) {
    showNotice(
      'notice-offline',
      '🚨',
      `Cannot connect to VEKSHA backend at ${apiBaseUrl}. Ensure FastAPI is running via 'uvicorn main:app --reload'.`
    );

    sensorStatusDot.className = 'status-dot pulse-gray';
    sensorStatusText.textContent = 'Node Offline';

    telemetryStateChip.className = 'status-chip chip-invalid';
    telemetryStateChip.textContent = 'Disconnected';

    heartRateValue.textContent = '--';
    heartRateStatus.textContent = 'Backend unreachable';
    hrRangeFill.style.width = '0%';
    beatPulseDot.classList.remove('beating');

    spo2Value.textContent = '--';
    spo2Status.textContent = 'Backend unreachable';
    spo2GradeBadge.textContent = 'Offline';
    spo2GradeBadge.className = 'badge';
    spo2RangeFill.style.width = '0%';
  }

  function renderNoDataState() {
    showNotice(
      'notice-no-finger',
      '⏳',
      'Backend is online. Awaiting first telemetry frame from ESP32 MAX30102 node...'
    );

    sensorStatusDot.className = 'status-dot pulse-amber';
    sensorStatusText.textContent = 'Awaiting Data';

    telemetryStateChip.className = 'status-chip chip-neutral';
    telemetryStateChip.textContent = 'Standby';

    heartRateValue.textContent = '--';
    heartRateStatus.textContent = 'No readings recorded yet';
    hrRangeFill.style.width = '0%';
    beatPulseDot.classList.remove('beating');

    spo2Value.textContent = '--';
    spo2Status.textContent = 'No readings recorded yet';
    spo2GradeBadge.textContent = 'Standby';
    spo2GradeBadge.className = 'badge';
    spo2RangeFill.style.width = '0%';
  }

  function renderTelemetryData(data) {
    const status = data.sensor_status;
    const hr = data.heart_rate;
    const spo2 = data.spo2;
    const deviceId = data.device_id || 'esp32-veksha';
    const timestampStr = data.timestamp;

    deviceIdText.textContent = deviceId;
    if (timestampStr) {
      lastTimestamp = new Date(timestampStr);
      updateRelativeTime();
    }

    // 1. NO_FINGER state
    if (status === 'NO_FINGER') {
      showNotice(
        'notice-no-finger',
        '👆',
        'Sensor reports: Finger removed. Please place index finger firmly on MAX30102 sensor.'
      );

      sensorStatusDot.className = 'status-dot pulse-amber';
      sensorStatusText.textContent = 'Finger Removed';

      telemetryStateChip.className = 'status-chip chip-no-finger';
      telemetryStateChip.textContent = 'No Finger';

      heartRateValue.textContent = '--';
      heartRateStatus.textContent = 'Sensor idle - place finger';
      hrRangeFill.style.width = '0%';
      beatPulseDot.classList.remove('beating');

      spo2Value.textContent = '--';
      spo2Status.textContent = 'Sensor idle - place finger';
      spo2GradeBadge.textContent = 'No Finger';
      spo2GradeBadge.className = 'badge badge-warning';
      spo2RangeFill.style.width = '0%';
      return;
    }

    // 2. CALIBRATING state (Warmup stabilization)
    if (status === 'CALIBRATING') {
      showNotice(
        'notice-calibrating',
        '🔬',
        'Sensor stabilizing optical baseline & pulse equilibrium. Please hold finger steady...'
      );

      sensorStatusDot.className = 'status-dot pulse-amber';
      sensorStatusText.textContent = 'Calibrating';

      telemetryStateChip.className = 'status-chip chip-amber';
      telemetryStateChip.textContent = 'Warmup';

      heartRateValue.textContent = '--';
      heartRateStatus.textContent = 'Calibrating baseline...';
      hrRangeFill.style.width = '0%';
      beatPulseDot.classList.remove('beating');

      spo2Value.textContent = '--';
      spo2Status.textContent = 'Calibrating baseline...';
      spo2GradeBadge.textContent = 'Warmup';
      spo2GradeBadge.className = 'badge';
      spo2RangeFill.style.width = '0%';
      return;
    }

    // 3. INVALID_DATA state
    if (status === 'INVALID_DATA') {
      showNotice(
        'notice-invalid',
        '⚠️',
        'Sensor reports: Invalid or noisy signal. Hold finger steady to allow optical calibration.'
      );

      sensorStatusDot.className = 'status-dot pulse-red';
      sensorStatusText.textContent = 'Signal Unstable';

      telemetryStateChip.className = 'status-chip chip-invalid';
      telemetryStateChip.textContent = 'Signal Noise';

      heartRateValue.textContent = '--';
      heartRateStatus.textContent = 'Stabilizing signal...';
      hrRangeFill.style.width = '0%';
      beatPulseDot.classList.remove('beating');

      spo2Value.textContent = '--';
      spo2Status.textContent = 'Stabilizing signal...';
      spo2GradeBadge.textContent = 'Invalid';
      spo2GradeBadge.className = 'badge badge-critical';
      spo2RangeFill.style.width = '0%';
      return;
    }

    // 4. VALID_DATA state
    const ageSeconds = timestampStr ? Math.floor((Date.now() - new Date(timestampStr).getTime()) / 1000) : 0;
    if (ageSeconds > 15) {
      telemetryStateChip.className = 'status-chip chip-amber';
      telemetryStateChip.textContent = `Stale (${ageSeconds}s ago)`;
      showNotice(
        'notice-stale',
        '⏳',
        `Warning: Telemetry was recorded ${ageSeconds}s ago. Awaiting fresh data packet from ESP32 node...`
      );
    } else {
      hideNotice();
      telemetryStateChip.className = 'status-chip chip-valid';
      telemetryStateChip.textContent = 'Live Telemetry';
    }

    sensorStatusDot.className = 'status-dot pulse-green';
    sensorStatusText.textContent = 'Sensor Active';

    telemetryStateChip.className = 'status-chip chip-valid';
    telemetryStateChip.textContent = 'Transmitting';

    // Render Heart Rate
    if (typeof hr === 'number' && hr > 0) {
      heartRateValue.textContent = Math.round(hr);
      
      // Calculate HR gauge fill (40 - 180 scale)
      const hrPercent = Math.min(Math.max(((hr - 40) / (180 - 40)) * 100, 5), 100);
      hrRangeFill.style.width = `${hrPercent}%`;

      if (hr >= 60 && hr <= 100) {
        heartRateStatus.textContent = 'Normal Resting Pulse';
      } else if (hr > 100) {
        heartRateStatus.textContent = 'Elevated Pulse (Tachycardia range)';
      } else {
        heartRateStatus.textContent = 'Low Pulse (Bradycardia range)';
      }

      // Trigger beat visual pulse
      beatPulseDot.classList.add('beating');
      setTimeout(() => beatPulseDot.classList.remove('beating'), 200);
    } else {
      heartRateValue.textContent = '--';
      heartRateStatus.textContent = 'Calculating pulse...';
      hrRangeFill.style.width = '0%';
    }

    // Render SpO2
    if (typeof spo2 === 'number' && spo2 > 0) {
      spo2Value.textContent = spo2.toFixed(1);

      // Calculate SpO2 gauge fill (70 - 100 scale)
      const spo2Percent = Math.min(Math.max(((spo2 - 70) / (100 - 70)) * 100, 5), 100);
      spo2RangeFill.style.width = `${spo2Percent}%`;

      if (spo2 >= 95.0) {
        spo2GradeBadge.textContent = 'Optimal';
        spo2GradeBadge.className = 'badge badge-optimal';
        spo2Status.textContent = 'Healthy Oxygen Saturation';
      } else if (spo2 >= 90.0) {
        spo2GradeBadge.textContent = 'Warning';
        spo2GradeBadge.className = 'badge badge-warning';
        spo2Status.textContent = 'Mild Hypoxia Range (Monitor closely)';
      } else {
        spo2GradeBadge.textContent = 'Critical';
        spo2GradeBadge.className = 'badge badge-critical';
        spo2Status.textContent = 'Hypoxemia threshold reached';
      }
    } else {
      spo2Value.textContent = '--';
      spo2Status.textContent = 'Calculating oxygenation...';
      spo2RangeFill.style.width = '0%';
    }

    // Append to real historical chart data if valid
    if (typeof hr === 'number' && hr > 0 && typeof spo2 === 'number' && spo2 > 0) {
      const now = new Date();
      // Check if duplicate of last reading timestamp
      const isNewTimestamp = !historyData.length || (historyData[historyData.length - 1].rawTimestamp !== timestampStr);
      
      if (isNewTimestamp) {
        totalReadingsReceived++;
        totalReadingsCount.textContent = totalReadingsReceived;

        historyData.push({
          time: now,
          rawTimestamp: timestampStr,
          hr: hr,
          spo2: spo2
        });

        if (historyData.length > MAX_CHART_POINTS) {
          historyData.shift();
        }

        updateChart();
      }
    }

    // Persist current vitals as SOS snapshot
    latestHrSnapshot = hr;
    latestSpo2Snapshot = spo2;
    latestSensorStatusSnapshot = status;
    latestDeviceIdSnapshot = deviceId;
  }

  function showNotice(className, icon, text) {
    noticeBanner.className = `notice-banner ${className}`;
    noticeIcon.textContent = icon;
    noticeMessage.textContent = text;
  }

  function hideNotice() {
    noticeBanner.className = 'notice-banner hidden';
  }

  function updateRelativeTime() {
    if (!lastTimestamp) {
      lastUpdatedText.textContent = 'Never';
      return;
    }
    const elapsedSec = Math.floor((Date.now() - lastTimestamp.getTime()) / 1000);
    if (elapsedSec < 2) {
      lastUpdatedText.textContent = 'Just now';
    } else if (elapsedSec < 60) {
      lastUpdatedText.textContent = `${elapsedSec}s ago`;
    } else {
      const min = Math.floor(elapsedSec / 60);
      lastUpdatedText.textContent = `${min}m ago`;
    }
  }
  // Periodically refresh relative time display
  setInterval(updateRelativeTime, 1000);

  // -------------------------------------------------------------------------
  // SOS Module Functions
  // -------------------------------------------------------------------------

  let sosCooldownSeconds = 0;
  let sosTimerId = null;

  /**
   * Start a visual countdown on the SOS button to prevent spamming
   */
  function startSosCooldown(seconds) {
    sosCooldownSeconds = seconds;
    sosTriggerBtn.disabled = true;
    sosTriggerBtn.style.opacity = '0.65';
    if (sosTimerId) clearInterval(sosTimerId);

    const updateBtnText = () => {
      const label = sosTriggerBtn.querySelector('.sos-btn-inner');
      if (sosCooldownSeconds > 0) {
        if (label) label.lastChild.textContent = `WAIT ${sosCooldownSeconds}s`;
        sosCooldownSeconds--;
      } else {
        clearInterval(sosTimerId);
        sosTimerId = null;
        sosTriggerBtn.disabled = false;
        sosTriggerBtn.style.opacity = '1';
        if (label) label.lastChild.textContent = 'SOS';
      }
    };
    updateBtnText();
    sosTimerId = setInterval(updateBtnText, 1000);
  }

  /**
   * Send SOS event to backend with current vitals snapshot.
   */
  async function sendSos() {
    if (sosIsSending || sosCooldownSeconds > 0) return;
    sosIsSending = true;
    sosTriggerBtn.disabled = true;

    const payload = {
      device_id: latestDeviceIdSnapshot || 'esp32-veksha',
      triggered_by: 'manual_button',
      heart_rate: latestHrSnapshot,
      spo2: latestSpo2Snapshot,
      sensor_status: latestSensorStatusSnapshot,
      gps_location: 'Not Available',
    };

    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/sos`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (response.status === 429) {
        const errJson = await response.json();
        alert(errJson.detail || 'SOS rate limit active. Please wait before triggering another alert.');
        startSosCooldown(10);
        return;
      }

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(`Server ${response.status}: ${errText}`);
      }

      const result = await response.json();
      renderSosStatus(result.data);
      await fetchSosHistory();

      // Notification banner feedback
      const smsFeedback = result.data.sms_sent ? 'SMS dispatched to family!' : (result.data.sms_status || '');
      showNotice('notice-sos', '🚨', `Emergency SOS recorded! ${smsFeedback}`);

      // Start 15-second cooldown countdown on button to prevent accidental spam
      startSosCooldown(15);
    } catch (err) {
      alert(`SOS failed: ${err.message}\nIs the backend running at ${apiBaseUrl}?`);
    } finally {
      sosIsSending = false;
      if (sosCooldownSeconds <= 0) {
        sosTriggerBtn.disabled = false;
      }
    }
  }

  /**
   * Update the SOS panel status fields after a trigger.
   */
  function renderSosStatus(event) {
    if (!event) return;

    sosLatestStatus.textContent = event.alert_status || 'TRIGGERED';
    sosLatestStatus.className = 'sos-status-chip chip-triggered';

    const ts = new Date(event.timestamp);
    sosLastTriggered.textContent = ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  /**
   * Fetch SOS history from the backend and render the list.
   */
  async function fetchSosHistory() {
    try {
      const res = await fetch(`${apiBaseUrl}/api/v1/sos/history`, { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      renderSosHistory(data.events || [], data.total || 0);
    } catch (_) {
      // Silently ignore – backend might not be up yet
    }
  }

  /**
   * Render the SOS alert history list.
   * @param {Array} events - SOS events (newest first from backend)
   * @param {number} total - Total count
   */
  function renderSosHistory(events, total) {
    sosCountBadge.textContent = `${total} alert${total !== 1 ? 's' : ''}`;

    if (total === 0) {
      sosHistoryEmpty.style.display = 'flex';
      // Remove any old rows
      Array.from(sosHistoryList.querySelectorAll('.sos-history-row')).forEach(el => el.remove());
      return;
    }

    sosHistoryEmpty.style.display = 'none';

    // Rebuild list from scratch to ensure correct order
    Array.from(sosHistoryList.querySelectorAll('.sos-history-row')).forEach(el => el.remove());

    events.forEach(ev => {
      const row = document.createElement('div');
      row.className = 'sos-history-row';
      row.setAttribute('data-sos-id', ev.event_id);

      const ts = new Date(ev.timestamp);
      const timeStr = ts.toLocaleString([], {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit'
      });

      let vitalsStr = 'No vitals snapshot';
      if (ev.heart_rate !== null && ev.spo2 !== null) {
        vitalsStr = `HR: ${Math.round(ev.heart_rate)} BPM  ·  SpO₂: ${ev.spo2.toFixed(1)}%`;
      } else if (ev.sensor_status) {
        vitalsStr = `Sensor: ${ev.sensor_status}`;
      }

      const smsBadge = ev.sms_sent
        ? `<span class="badge badge-optimal" style="margin-left:6px; font-size:10px;">SMS Sent</span>`
        : (ev.sms_status ? `<span class="badge" style="margin-left:6px; font-size:10px;">${ev.sms_status.substring(0, 18)}</span>` : '');

      row.innerHTML = `
        <span class="sos-row-id">#${ev.event_id}</span>
        <div class="sos-row-info">
          <span class="sos-row-time">${timeStr}</span>
          <span class="sos-row-vitals">${vitalsStr}</span>
        </div>
        <span class="sos-row-gps">${ev.gps_location || 'Not Available'}</span>
        <span class="sos-row-status">${ev.alert_status} ${smsBadge}</span>
      `;

      sosHistoryList.appendChild(row);
    });

    // Update status panel from most recent event
    if (events.length > 0) {
      renderSosStatus(events[0]);
    }
  }

  /**
   * HTML5 Canvas Hardware-Accelerated Chart Engine
   */
  function setupCanvasDPI() {
    const dpr = window.devicePixelRatio || 1;
    const rect = chartCanvas.parentElement.getBoundingClientRect();
    chartCanvas.width = rect.width * dpr;
    chartCanvas.height = rect.height * dpr;
    const ctx = chartCanvas.getContext('2d');
    ctx.scale(dpr, dpr);
  }

  function updateChart() {
    if (historyData.length > 0) {
      chartEmptyState.classList.add('hidden');
    } else {
      chartEmptyState.classList.remove('hidden');
    }
    drawChart();
  }

  function drawChart() {
    const ctx = chartCanvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const w = chartCanvas.width / dpr;
    const h = chartCanvas.height / dpr;

    // Clear canvas
    ctx.clearRect(0, 0, w, h);

    if (historyData.length === 0) return;

    const padLeft = 45;
    const padRight = 20;
    const padTop = 25;
    const padBottom = 35;
    const plotWidth = w - padLeft - padRight;
    const plotHeight = h - padTop - padBottom;

    // Draw horizontal grid lines (e.g. 5 steps)
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
    ctx.lineWidth = 1;
    ctx.font = '10px JetBrains Mono, monospace';
    ctx.fillStyle = '#546279';
    ctx.textAlign = 'right';

    for (let i = 0; i <= 4; i++) {
      const yVal = 40 + i * 35; // 40, 75, 110, 145, 180
      const yPos = padTop + plotHeight - (i / 4) * plotHeight;
      
      ctx.beginPath();
      ctx.moveTo(padLeft, yPos);
      ctx.lineTo(w - padRight, yPos);
      ctx.stroke();

      ctx.fillText(yVal.toString(), padLeft - 8, yPos + 3);
    }

    if (historyData.length < 2) {
      // Single point indicator
      const pt = historyData[0];
      const cx = padLeft + plotWidth / 2;
      const hrY = padTop + plotHeight - ((pt.hr - 40) / (180 - 40)) * plotHeight;
      
      ctx.fillStyle = '#ff2d55';
      ctx.beginPath();
      ctx.arc(cx, hrY, 5, 0, Math.PI * 2);
      ctx.fill();
      return;
    }

    // Helper to calculate coordinates
    const getX = (index) => padLeft + (index / (historyData.length - 1)) * plotWidth;
    const getHrY = (val) => padTop + plotHeight - ((Math.min(Math.max(val, 40), 180) - 40) / 140) * plotHeight;
    const getSpo2Y = (val) => padTop + plotHeight - ((Math.min(Math.max(val, 70), 100) - 70) / 30) * plotHeight;

    // 1. Draw Heart Rate curve (Red)
    ctx.save();
    ctx.strokeStyle = '#ff2d55';
    ctx.lineWidth = 2.5;
    ctx.shadowColor = 'rgba(255, 45, 85, 0.4)';
    ctx.shadowBlur = 10;
    ctx.beginPath();

    historyData.forEach((pt, i) => {
      const x = getX(i);
      const y = getHrY(pt.hr);
      if (i === 0) ctx.moveTo(x, y);
      else {
        // Smooth bezier interpolation
        const prevX = getX(i - 1);
        const prevY = getHrY(historyData[i - 1].hr);
        const cpX = (prevX + x) / 2;
        ctx.bezierCurveTo(cpX, prevY, cpX, y, x, y);
      }
    });
    ctx.stroke();
    ctx.restore();

    // 2. Draw SpO2 curve (Cyan)
    ctx.save();
    ctx.strokeStyle = '#00e5ff';
    ctx.lineWidth = 2.5;
    ctx.shadowColor = 'rgba(0, 229, 255, 0.4)';
    ctx.shadowBlur = 10;
    ctx.beginPath();

    historyData.forEach((pt, i) => {
      const x = getX(i);
      const y = getSpo2Y(pt.spo2);
      if (i === 0) ctx.moveTo(x, y);
      else {
        const prevX = getX(i - 1);
        const prevY = getSpo2Y(historyData[i - 1].spo2);
        const cpX = (prevX + x) / 2;
        ctx.bezierCurveTo(cpX, prevY, cpX, y, x, y);
      }
    });
    ctx.stroke();
    ctx.restore();

    // 3. Draw latest value indicator circles
    const lastIdx = historyData.length - 1;
    const lastPt = historyData[lastIdx];
    const lastX = getX(lastIdx);

    // HR circle
    ctx.fillStyle = '#ff2d55';
    ctx.beginPath();
    ctx.arc(lastX, getHrY(lastPt.hr), 4.5, 0, Math.PI * 2);
    ctx.fill();

    // SpO2 circle
    ctx.fillStyle = '#00e5ff';
    ctx.beginPath();
    ctx.arc(lastX, getSpo2Y(lastPt.spo2), 4.5, 0, Math.PI * 2);
    ctx.fill();
  }

  // Boot on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
