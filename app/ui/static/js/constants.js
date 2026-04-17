/**
 * constants.js — helpers que consumen APP_META inyectado por Jinja2.
 * APP_META = { statuses: [{value, label, order},...], priorities: [{value, label},...] }
 * Cargar DESPUÉS de que APP_META esté definido en el template base.
 */

(function () {
  // Índices para acceso O(1)
  const _statusByValue = {};
  const _priorityByValue = {};

  (APP_META.statuses || []).forEach(s => { _statusByValue[s.value] = s; });
  (APP_META.priorities || []).forEach(p => { _priorityByValue[p.value] = p; });

  const STATUS_BADGE_CLASS = {
    open:        'badge-open',
    in_progress: 'badge-in_progress',
    pending:     'badge-pending',
    reopened:    'badge-reopened',
    resolved:    'badge-resolved',
    closed:      'badge-closed',
  };

  const PRIORITY_BADGE_CLASS = {
    '1': 'badge-critical',
    '2': 'badge-high',
    '3': 'badge-normal',
    '4': 'badge-low',
    '5': 'badge-verylow',
  };

  window.statusBadge = function (s) {
    const meta = _statusByValue[s];
    const cls = STATUS_BADGE_CLASS[s] || 'badge-closed';
    const label = meta ? meta.label : (s || 'Desconocido');
    return `<span class="badge ${cls}">${label}</span>`;
  };

  window.priorityBadge = function (p) {
    const key = String(p);
    const meta = _priorityByValue[key];
    const cls = PRIORITY_BADGE_CLASS[key] || 'badge-normal';
    const label = meta ? meta.label : key;
    return `<span class="badge ${cls}">${label}</span>`;
  };

  /**
   * Genera options HTML para un <select> de estados.
   * @param {string} placeholder - Texto de la opción vacía
   * @param {string[]} [exclude] - Values a excluir (ej: ['closed'])
   */
  window.buildStatusOptions = function (placeholder = 'Todos los estados', exclude = []) {
    const excludeSet = new Set(exclude);
    const sorted = [...APP_META.statuses]
      .filter(s => !excludeSet.has(s.value))
      .sort((a, b) => a.order - b.order);
    return `<option value="">${placeholder}</option>` +
      sorted.map(s => `<option value="${s.value}">${s.label}</option>`).join('');
  };

  window.buildPriorityOptions = function (placeholder = 'Todas las prioridades') {
    const emptyOpt = placeholder ? `<option value="">${placeholder}</option>` : '';
    return emptyOpt + APP_META.priorities.map(p => `<option value="${p.value}">${p.label}</option>`).join('');
  };

  /** Retorna el label legible de un status value */
  window.statusLabel = function (s) {
    return (_statusByValue[s] || {}).label || s;
  };

  /** Retorna el label legible de una priority value */
  window.priorityLabel = function (p) {
    return (_priorityByValue[String(p)] || {}).label || String(p);
  };

  /** Constantes de agrupación para portals */
  window.ACTIVE_STATUSES   = ['open', 'in_progress', 'pending', 'reopened'];
  window.ARCHIVED_STATUSES = ['resolved', 'closed'];

  /** Retorna el order numérico de un status (para sort) */
  window.statusOrder = function (s) {
    return (_statusByValue[s] || {}).order || 99;
  };
})();

// ── Users cache (compartido por mentions.js y ticket-form.js) ─────────────
// Carga todos los empleados activos una vez y filtra localmente.
// Se refresca automáticamente cada 5 minutos.
(function () {
  let _cache = null;
  let _cacheTs = 0;
  const _TTL = 5 * 60 * 1000; // 5 min

  window.loadUsersCache = async function (apiBase) {
    const now = Date.now();
    if (_cache && (now - _cacheTs < _TTL)) return _cache;
    try {
      const token = localStorage.getItem('tk_token');
      if (!token) return _cache || [];
      const r = await fetch(`${apiBase}/users/search`, {
        headers: { 'Authorization': 'Bearer ' + token, 'Accept': 'application/json' },
      });
      if (!r.ok) return _cache || [];
      _cache = await r.json();
      _cacheTs = now;
      return _cache;
    } catch { return _cache || []; }
  };

  window.filterUsers = function (users, q) {
    if (!users || !users.length) return [];
    const lq = q.toLowerCase();
    return users.filter(u =>
      u.userName.toLowerCase().includes(lq) ||
      (u.mail && u.mail.toLowerCase().includes(lq))
    ).slice(0, 10);
  };
})();
