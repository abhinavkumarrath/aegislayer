/**
 * AegisLayer — Frontend Application Controller
 * =============================================
 * Vanilla JS / Fetch API implementation of the full dashboard.
 * No dependencies. No frameworks. Pure performance.
 *
 * Responsibilities:
 *   - UUID generation & session management
 *   - POST /api/process orchestration with full UI state machine
 *   - GET /health polling for GPU status indicator
 *   - Token chip rendering for the interception panel
 *   - Compliance ledger (audit table) with row animations
 *   - Toast notification system
 *   - Metrics display
 */

'use strict';

// ============================================================================
// Configuration
// ============================================================================

const CONFIG = {
  API_BASE:           window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
                        ? 'http://localhost:8000'
                        : '',          // same-origin when served by FastAPI
  HEALTH_POLL_MS:     5_000,          // poll /health every 5s
  TOAST_DURATION_MS:  4_500,
  MAX_AUDIT_ROWS:     200,
};

// ============================================================================
// Utility: UUID v4
// ============================================================================

function uuidv4() {
  if (crypto && crypto.randomUUID) return crypto.randomUUID();
  // Polyfill
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// ============================================================================
// Utility: Token → chip class
// ============================================================================

const TOKEN_PATTERN = /\[(PERSON|ORG|LOC|EMAIL|APIKEY|IP|PHONE|CARD|ENTITY)_[A-Z0-9]+\]/g;

const TYPE_TO_CLASS = {
  PERSON: 'chip-person',
  ORG:    'chip-org',
  LOC:    'chip-loc',
  EMAIL:  'chip-email',
  APIKEY: 'chip-apikey',
  IP:     'chip-ip',
  PHONE:  'chip-phone',
  CARD:   'chip-card',
  ENTITY: 'chip-unknown',
};

/**
 * Escape text for safe innerHTML insertion.
 */
function escapeHtml(str) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(str));
  return d.innerHTML;
}

/**
 * Render a sanitised prompt string into HTML with token chips highlighted.
 * @param {string} text - The sanitised text containing [TYPE_N] tokens.
 * @returns {string} HTML string safe to set as innerHTML.
 */
function renderTokenisedText(text) {
  // Split on tokens, preserving them
  const parts = text.split(TOKEN_PATTERN);
  // Re-split strategy: use a capture group approach
  const segments = [];
  let lastIndex = 0;

  // Reset pattern
  TOKEN_PATTERN.lastIndex = 0;
  let match;
  while ((match = TOKEN_PATTERN.exec(text)) !== null) {
    // Text before the token
    if (match.index > lastIndex) {
      segments.push({ type: 'text', value: text.slice(lastIndex, match.index) });
    }
    // The token itself
    const tokenStr  = match[0];
    const tokenType = match[1]; // capture group: PERSON | ORG | LOC etc.
    segments.push({ type: 'token', value: tokenStr, tokenType });
    lastIndex = TOKEN_PATTERN.lastIndex;
  }
  // Remaining text after last token
  if (lastIndex < text.length) {
    segments.push({ type: 'text', value: text.slice(lastIndex) });
  }

  return segments.map((seg) => {
    if (seg.type === 'text') {
      return `<span>${escapeHtml(seg.value)}</span>`;
    }
    const cls = TYPE_TO_CLASS[seg.tokenType] || 'chip-unknown';
    return `<span class="token-chip ${cls}" title="${seg.tokenType} entity" aria-label="${seg.tokenType} token">${escapeHtml(seg.value)}</span>`;
  }).join('');
}

// ============================================================================
// Toast System
// ============================================================================

class ToastManager {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
  }

  _show(title, message, type = 'info', icon = 'ℹ️') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.setAttribute('role', 'alert');
    toast.innerHTML = `
      <span class="toast-icon" aria-hidden="true" style="font-family:var(--mono); font-size:0.75rem; font-weight:700; flex-shrink:0; padding-top:1px; color:var(--t2)">${icon}</span>
      <div class="toast-body">
        <div class="toast-title">${escapeHtml(title)}</div>
        <div class="toast-msg">${escapeHtml(message)}</div>
      </div>
    `;
    this.container.appendChild(toast);

    const timer = setTimeout(() => this._dismiss(toast), CONFIG.TOAST_DURATION_MS);
    toast.addEventListener('click', () => {
      clearTimeout(timer);
      this._dismiss(toast);
    });
  }

  _dismiss(toast) {
    toast.classList.add('exiting');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
    // Fallback remove
    setTimeout(() => toast.remove(), 400);
  }

  error(title, message)   { this._show(title, message, 't-error',   '!'); }
  success(title, message) { this._show(title, message, 't-success', '✓'); }
  info(title, message)    { this._show(title, message, 't-info',    'i'); }
  warn(title, message)    { this._show(title, message, 't-info',    '⚠'); }
}

// ============================================================================
// GPU / Health Poller
// ============================================================================

class HealthPoller {
  constructor({ pulseEl, labelEl, metricDeviceEl, onStatus }) {
    this.pulseEl        = pulseEl;
    this.labelEl        = labelEl;
    this.metricDeviceEl = metricDeviceEl;
    this.onStatus       = onStatus;
    this._timerId       = null;
  }

  async poll() {
    try {
      const res  = await fetch(`${CONFIG.API_BASE}/health`, { signal: AbortSignal.timeout(4000) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this._applyStatus(data);
      this.onStatus(data);
    } catch (err) {
      this._applyError(err);
    }
  }

  _applyStatus(data) {
    const isGpu   = data.ner_device && !data.ner_device.toLowerCase().includes('cpu');
    const isReady = data.ner_ready;

    // Pulse dot
    this.pulseEl.className = isReady ? 'active' : 'loading';

    // Label
    const rawDevice = data.ner_device || 'cpu';
    const shortLabel = isReady
      ? (isGpu ? `ROCm · ${rawDevice.split('·')[1]?.trim() || 'GPU'}` : 'CPU Mode')
      : 'Loading…';
    this.labelEl.textContent = shortLabel;
    this.labelEl.className   = isReady && isGpu ? 'active' : '';

    if (this.metricDeviceEl) {
      this.metricDeviceEl.textContent = rawDevice.slice(0, 28);
    }
  }

  _applyError(err) {
    this.pulseEl.className   = '';
    this.labelEl.textContent = 'API Offline';
    this.labelEl.className   = '';
    if (this.metricDeviceEl) {
      this.metricDeviceEl.textContent = 'N/A';
    }
  }

  start() {
    this.poll();
    this._timerId = setInterval(() => this.poll(), CONFIG.HEALTH_POLL_MS);
  }

  stop() {
    if (this._timerId) clearInterval(this._timerId);
  }
}

// ============================================================================
// Audit Ledger
// ============================================================================

class AuditLedger {
  constructor({ drawerId, tbodyId, emptyId, tableId, countId, floatCountId, toggleBtnId, closeBtnId }) {
    this.drawer    = document.getElementById(drawerId);
    this.tbody     = document.getElementById(tbodyId);
    this.emptyEl   = document.getElementById(emptyId);
    this.tableEl   = document.getElementById(tableId);
    this.countEl   = document.getElementById(countId);
    this.floatCountEl  = document.getElementById(floatCountId);
    this.toggleBtn = document.getElementById(toggleBtnId);
    this.closeBtn  = document.getElementById(closeBtnId);

    this._totalEntries = 0;
    this._isOpen       = false;

    this.closeBtn.addEventListener('click', () => this.close());
    this.toggleBtn.addEventListener('click', () => this.toggle());

    // Also update the header ledger button
    const headerLedgerBtn = document.getElementById('btn-ledger-toggle');
    if (headerLedgerBtn) {
      headerLedgerBtn.addEventListener('click', () => this.toggle());
    }
  }

  open() {
    this._isOpen = true;
    this.drawer.classList.add('open');
    this.drawer.setAttribute('aria-hidden', 'false');
  }

  close() {
    this._isOpen = false;
    this.drawer.classList.remove('open');
    this.drawer.setAttribute('aria-hidden', 'true');
  }

  toggle() {
    this._isOpen ? this.close() : this.open();
  }

  /**
   * Populate the ledger with a fresh set of audit entries.
   * @param {Array} entries - Array of AuditEntry objects from the API.
   */
  populate(entries) {
    // Clear old entries
    this.tbody.innerHTML = '';
    this._totalEntries = entries.length;

    if (entries.length === 0) {
      this.emptyEl.style.display = 'flex';
      this.tableEl.style.display = 'none';
    } else {
      this.emptyEl.style.display = 'none';
      this.tableEl.style.display = 'table';

      entries.slice(0, CONFIG.MAX_AUDIT_ROWS).forEach((entry, idx) => {
        const row = document.createElement('tr');
        row.className = 'audit-row';
        row.style.animationDelay = `${idx * 40}ms`;

        const actionClass = entry.action === 'REDACTED' ? 'act-redacted' : 'act-restored';
        const typeChipCls = this._typeToChipClass(entry.type);

        // Truncate original value for display
        const original = entry.original
          ? (entry.original.length > 20 ? entry.original.slice(0, 18) + '…' : entry.original)
          : '—';

        row.innerHTML = `
          <td>
            <span class="action-badge ${actionClass}">${escapeHtml(entry.action)}</span>
          </td>
          <td>
            <span class="token-chip ${typeChipCls}" style="font-size:0.67rem">${escapeHtml(entry.type)}</span>
          </td>
          <td class="td-token">${escapeHtml(entry.token)}</td>
          <td class="td-orig" title="${entry.original ? escapeHtml(entry.original) : ''}">${escapeHtml(original)}</td>
        `;
        this.tbody.appendChild(row);
      });
    }

    this._updateCount();
  }

  _typeToChipClass(type) {
    const map = {
      PERSON: 'chip-person', ORG: 'chip-org', LOCATION: 'chip-loc',
      EMAIL: 'chip-email', API_KEY: 'chip-apikey', IPV4: 'chip-ip',
      PHONE: 'chip-phone', CREDIT_CARD: 'chip-card',
    };
    return map[type] || 'chip-unknown';
  }

  _updateCount() {
    const n = this._totalEntries;
    [this.countEl, this.floatCountEl].forEach((el) => {
      if (!el) return;
      el.textContent = n;
      el.className = n > 0
        ? (el.classList.contains('ledger-count') ? 'ledger-count' : 'ledger-toggle-count')
        : (el.classList.contains('ledger-count') ? 'ledger-count' : 'ledger-toggle-count zero');
    });

    // Header badge
    const headerCount = document.getElementById('ledger-toggle-count');
    if (headerCount) {
      headerCount.textContent = n;
      headerCount.className = n > 0 ? 'ledger-toggle-count' : 'ledger-toggle-count zero';
    }

    // Auto-open drawer if there are new entries
    if (n > 0 && !this._isOpen) {
      this.open();
    }
  }

  get entryCount() {
    return this._totalEntries;
  }
}

// ============================================================================
// Latency Bar Renderer
// ============================================================================

function updateLatencyBar(ms) {
  const fill    = document.getElementById('latency-bar-fill');
  const display = document.getElementById('latency-display');

  if (!ms) {
    fill.style.width = '0%';
    display.textContent = '— ms';
    return;
  }

  display.textContent = `${ms.toFixed(0)} ms`;

  // Color and fill width: <200ms = fast (green), <800ms = medium (yellow), else slow (red)
  let cls   = 'fast';
  let width = Math.min((ms / 1000) * 100, 100); // cap at 1s = 100%
  if (ms > 800) { cls = 'slow'; width = Math.min((ms / 3000) * 100, 100); }
  else if (ms > 200) { cls = 'medium'; }

  fill.className = `latency-bar-fill ${cls}`;
  // Animate: first reset width, then apply
  fill.style.transition = 'none';
  fill.style.width      = '0%';
  requestAnimationFrame(() => {
    fill.style.transition = 'width 1s ease-out';
    fill.style.width      = `${width}%`;
  });

  // Metric card
  const metricVal = document.getElementById('metric-latency-val');
  if (metricVal) {
    metricVal.textContent = `${ms.toFixed(0)} ms`;
  }
}

// ============================================================================
// Main Application Class
// ============================================================================

class AegisApp {
  constructor() {
    // State
    this._isProcessing  = false;
    this._pipelinesRun  = 0;

    // DOM refs (cached for performance)
    this.els = {
      sessionInput      : document.getElementById('session-id-input'),
      btnRegen          : document.getElementById('btn-regen-session'),
      llmModelSelect    : document.getElementById('llm-model-select'),
      promptTextarea    : document.getElementById('prompt-textarea'),
      charCount         : document.getElementById('char-count'),
      btnProcess        : document.getElementById('btn-process'),
      btnProcessIcon    : document.getElementById('btn-process-icon'),
      btnProcessText    : document.getElementById('btn-process-text'),
      interceptContent  : document.getElementById('intercept-content'),
      interceptCharCount: document.getElementById('intercept-char-count'),
      tokenCountLabel   : document.getElementById('token-count-label'),
      outputContent     : document.getElementById('output-content'),
      outputCharCount   : document.getElementById('output-char-count'),
      btnCopyOutput     : document.getElementById('btn-copy-output'),
      ribbonStatus      : document.getElementById('ribbon-status-text'),
      metricRedacted    : document.getElementById('metric-redacted-val'),
      metricSession     : document.getElementById('metric-session-val'),
      metricDevice      : document.getElementById('metric-device-val'),
      panelInput        : document.getElementById('panel-input'),
      panelIntercept    : document.getElementById('panel-intercept'),
      panelOutput       : document.getElementById('panel-output'),
    };

    // Sub-systems
    this.toast  = new ToastManager('toast-container');
    this.ledger = new AuditLedger({
      drawerId     : 'ledger-drawer',
      tbodyId      : 'audit-tbody',
      emptyId      : 'ledger-empty',
      tableId      : 'audit-table',
      countId      : 'ledger-count',
      floatCountId : 'ledger-float-count',
      toggleBtnId  : 'btn-ledger-float',
      closeBtnId   : 'btn-close-ledger',
    });

    this.healthPoller = new HealthPoller({
      pulseEl       : document.getElementById('gpu-pulse'),
      labelEl       : document.getElementById('gpu-label'),
      metricDeviceEl: this.els.metricDevice,
      onStatus      : (data) => this._onHealthData(data),
    });
  }

  // --------------------------------------------------------------------------
  // Initialisation
  // --------------------------------------------------------------------------

  init() {
    this._generateSession();
    this._bindEvents();
    this.healthPoller.start();
    this.toast.info('AegisLayer Ready', 'System initialised. Enter a prompt to begin.');
  }

  _bindEvents() {
    // Session management
    this.els.btnRegen.addEventListener('click', () => this._generateSession());

    // Character counter
    this.els.promptTextarea.addEventListener('input', () => {
      const len = this.els.promptTextarea.value.length;
      this.els.charCount.textContent = `${len.toLocaleString()} chars`;
    });

    // Process button
    this.els.btnProcess.addEventListener('click', () => this._runPipeline());

    // Keyboard shortcut: Ctrl/Cmd+Enter to process
    this.els.promptTextarea.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        this._runPipeline();
      }
    });

    // Copy output
    this.els.btnCopyOutput.addEventListener('click', () => this._copyOutput());
  }

  _generateSession() {
    this.els.sessionInput.value = uuidv4();
    this.els.sessionInput.style.animation = 'none';
    requestAnimationFrame(() => {
      this.els.sessionInput.style.animation = '';
    });
  }

  _onHealthData(data) {
    this._nerReady = data.ner_ready;
    if (this.els.metricDevice) {
      this.els.metricDevice.textContent = data.ner_device || '—';
    }
    
    // Visually disable the process button if not ready
    if (!this._isProcessing) {
      this.els.btnProcess.disabled = !this._nerReady;
      if (!this._nerReady) {
        this.els.btnProcessText.textContent = 'Loading Model...';
      } else {
        this.els.btnProcessText.textContent = 'Process Pipeline';
      }
    }
  }

  // --------------------------------------------------------------------------
  // Pipeline Execution
  // --------------------------------------------------------------------------

  async _runPipeline() {
    if (this._isProcessing) return;

    const prompt    = this.els.promptTextarea.value.trim();
    const sessionId = this.els.sessionInput.value.trim();
    const llmModel  = this.els.llmModelSelect.value || undefined;

    // Validation
    if (!prompt) {
      this.toast.warn('Empty Prompt', 'Please enter a prompt before processing.');
      this.els.promptTextarea.focus();
      return;
    }
    if (!sessionId) {
      this.toast.warn('No Session ID', 'Session ID is required. Click "↻ New" to generate one.');
      return;
    }
    if (!this._nerReady) {
      this.toast.warn('Engine Initialising', 'The privacy engine model is still downloading or loading into memory. Please wait...');
      return;
    }

    this._setProcessingState(true);
    this._clearPanels();
    this._setRibbonStatus('⚡ Processing…');

    const payload = {
      session_id: sessionId,
      prompt,
      ...(llmModel ? { llm_model: llmModel } : {}),
    };

    try {
      const t0  = performance.now();
      const res = await fetch(`${CONFIG.API_BASE}/api/process`, {
        method  : 'POST',
        headers : { 'Content-Type': 'application/json' },
        body    : JSON.stringify(payload),
        signal  : AbortSignal.timeout(60_000),
      });

      if (!res.ok) {
        const errBody = await res.text();
        throw new Error(`HTTP ${res.status}: ${errBody.slice(0, 200)}`);
      }

      const data = await res.json();
      const elapsed = performance.now() - t0;

      this._renderResults(data, elapsed);
      this._pipelinesRun++;
      this.els.metricSession.textContent = this._pipelinesRun;

      this.toast.success(
        'Pipeline Complete',
        `${data.audit_logs.length} entities redacted in ${(data.latency_ms || elapsed).toFixed(0)} ms.`
      );

    } catch (err) {
      console.error('[AegisLayer] Pipeline error:', err);
      this._renderError(err);
      this.toast.error('Pipeline Failed', err.message.slice(0, 120));
    } finally {
      this._setProcessingState(false);
      this._setRibbonStatus('Ready');
    }
  }

  // --------------------------------------------------------------------------
  // UI State Transitions
  // --------------------------------------------------------------------------

  _setProcessingState(processing) {
    this._isProcessing = processing;
    this.els.btnProcess.disabled = processing;

    if (processing) {
      this.els.btnProcessIcon.textContent = '';
      this.els.btnProcessText.textContent = 'Processing…';

      // Show loading skeleton in intercept + output panels
      this._showSkeleton(this.els.interceptContent);
      this._showSkeleton(this.els.outputContent);

      this.els.panelInput.classList.add('panel-active');
      this.els.panelIntercept.classList.remove('panel-active');
      this.els.panelOutput.classList.remove('panel-active');
    } else {
      this.els.btnProcessIcon.textContent = '⚡';
      this.els.btnProcessText.textContent = 'Process Pipeline';
    }
  }

  _showSkeleton(container) {
    container.innerHTML = `
      <div class="proc-wrap">
        <div class="proc-spinner" aria-hidden="true"></div>
        <div class="proc-label">Running AegisLayer Pipeline…</div>
        <div class="proc-step" id="pipeline-step">▶ CPU Regex Engine…</div>
      </div>
    `;
    const steps = [
      '▶ CPU Regex Engine…',
      '▶ AMD GPU NER Inference…',
      '▶ Forwarding to LLM…',
      '▶ De-tokenising response…',
    ];
    let i = 0;
    this._stepInterval = setInterval(() => {
      const el = document.getElementById('pipeline-step');
      if (el && i < steps.length - 1) { i++; el.textContent = steps[i]; }
    }, 650);
  }

  _clearPanels() {
    clearInterval(this._stepInterval);
    this.els.interceptContent.innerHTML = '<span class="panel-hint">Processing…</span>';
    this.els.outputContent.innerHTML    = '<span class="panel-hint">Awaiting LLM response…</span>';
    this.els.interceptCharCount.textContent = '0 chars';
    this.els.tokenCountLabel.textContent    = '0 entities redacted';
    this.els.outputCharCount.textContent    = '0 chars';
    this.els.btnCopyOutput.style.display    = 'none';
    updateLatencyBar(null);
  }

  // --------------------------------------------------------------------------
  // Results Rendering
  // --------------------------------------------------------------------------

  _renderResults(data, clientElapsed) {
    clearInterval(this._stepInterval);

    const latency = data.latency_ms || clientElapsed;

    // ── Interception Panel ────────────────────────────────────────────────
    const sanitised = data.sanitized_prompt || '';
    this.els.interceptContent.innerHTML = sanitised
      ? renderTokenisedText(sanitised)
      : '<span class="panel-hint">No text after sanitisation.</span>';

    this.els.interceptCharCount.textContent = `${sanitised.length.toLocaleString()} chars`;
    this.els.tokenCountLabel.textContent    = `${data.audit_logs.length} entit${data.audit_logs.length === 1 ? 'y' : 'ies'} redacted`;

    // ── Output Panel ──────────────────────────────────────────────────────
    const restored = data.final_de_sanitized_response || '';
    this.els.outputContent.innerHTML = restored
      ? `<span>${escapeHtml(restored)}</span>`
      : '<span class="panel-hint">No response received.</span>';

    this.els.outputCharCount.textContent = `${restored.length.toLocaleString()} chars`;

    if (restored) {
      this.els.btnCopyOutput.style.display = 'inline-flex';
    }

    // ── Panel active states ───────────────────────────────────────────────
    this.els.panelInput.classList.remove('panel-active');
    this.els.panelIntercept.classList.add('panel-active');
    this.els.panelOutput.classList.add('panel-active');

    // ── Metrics ───────────────────────────────────────────────────────────
    updateLatencyBar(latency);
    this.els.metricRedacted.textContent = data.audit_logs.length;
    if (data.ner_device) {
      this.els.metricDevice.textContent = data.ner_device.slice(0, 30);
    }

    // ── Audit Ledger ──────────────────────────────────────────────────────
    this.ledger.populate(data.audit_logs || []);
  }

  _renderError(err) {
    clearInterval(this._stepInterval);

    const errHtml = `
      <div style="color:var(--red-hi); font-family:var(--mono); font-size:0.76rem; line-height:1.7; padding:14px;">
        <div style="font-weight:700; margin-bottom:8px; letter-spacing:0.02em;">Pipeline Error</div>
        <div style="color:var(--t2); margin-bottom:10px;">${escapeHtml(err.message)}</div>
        <div style="color:var(--t3); font-size:0.68rem; border-top:1px solid var(--b1); padding-top:8px; margin-top:4px;">
          Ensure the AegisLayer backend is running:<br/>
          <span style="color:var(--t2)">cd backend &amp;&amp; uvicorn main:app --reload</span>
        </div>
      </div>`;

    this.els.interceptContent.innerHTML = errHtml;
    this.els.outputContent.innerHTML    = errHtml;

    this.els.panelInput.classList.remove('panel-active');
    this.els.panelIntercept.classList.remove('panel-active');
    this.els.panelOutput.classList.remove('panel-active');
  }

  _setRibbonStatus(text) {
    if (this.els.ribbonStatus) {
      this.els.ribbonStatus.textContent = text;
    }
  }

  // --------------------------------------------------------------------------
  // Copy Output
  // --------------------------------------------------------------------------

  async _copyOutput() {
    const text = this.els.outputContent.textContent || '';
    try {
      await navigator.clipboard.writeText(text);
      this.toast.success('Copied!', 'Output text copied to clipboard.');
      const btn = this.els.btnCopyOutput;
      const orig = btn.textContent;
      btn.textContent = '✅ Copied';
      setTimeout(() => { btn.textContent = orig; }, 1500);
    } catch {
      this.toast.error('Copy Failed', 'Could not access clipboard. Please copy manually.');
    }
  }
}

// ============================================================================
// Theme Toggle
// ============================================================================

function initTheme() {
  const toggleBtns = document.querySelectorAll('.theme-toggle');
  
  // Check local storage or system preference
  const savedTheme = localStorage.getItem('theme');
  const prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
  
  if (savedTheme === 'light' || (!savedTheme && prefersLight)) {
    document.documentElement.setAttribute('data-theme', 'light');
  } else {
    document.documentElement.removeAttribute('data-theme');
  }

  toggleBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      if (document.documentElement.getAttribute('data-theme') === 'light') {
        document.documentElement.removeAttribute('data-theme');
        localStorage.setItem('theme', 'dark');
      } else {
        document.documentElement.setAttribute('data-theme', 'light');
        localStorage.setItem('theme', 'light');
      }
    });
  });
}

// ============================================================================
// Bootstrap
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  
  // Initialize AegisApp if we are on the dashboard
  if (document.getElementById('app')) {
    const app = new AegisApp();
    app.init();
    // Expose to console for debugging
    window.__aegis = app;
  }
});
