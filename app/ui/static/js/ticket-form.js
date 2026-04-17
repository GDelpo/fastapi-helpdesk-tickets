/**
 * ticket-form.js — Lógica del formulario de nuevo ticket (portal).
 *
 * Uso en el template:
 *   <script>
 *     const _attachments = initAttachments({...});
 *     initMentions('new-description', API_BASE);
 *     initTicketForm({ base: '{{ base }}', apiBase: API_BASE, attachments: _attachments });
 *   </script>
 */

window.initTicketForm = function ({ base, apiBase, attachments, preQueueId = null }) {

  // ── Cargar categorías (árbol padre → hijos) ───────────────────────────────
  async function loadQueues() {
    const token = localStorage.getItem('tk_token');
    if (!token) return;
    const resp = await fetch(`${apiBase}/queues/`, {
      headers: { 'Authorization': 'Bearer ' + token, 'Accept': 'application/json' },
    });
    if (!resp || !resp.ok) return;
    const all = await resp.json();
    const sel = document.getElementById('new-queue');
    if (!sel) return;

    const active = all.filter(q => q.isActive);
    const byId = new Map(active.map(q => [q.id, q]));
    const sort = (a, b) => ((a.sortOrder || 0) - (b.sortOrder || 0)) || a.name.localeCompare(b.name);
    const roots = active.filter(q => !q.parentId).sort(sort);
    const childrenOf = id => active.filter(q => q.parentId === id).sort(sort);

    roots.forEach(root => {
      const kids = childrenOf(root.id);
      if (kids.length) {
        // Padre con hijos: <optgroup> (no seleccionable) + <option> por cada hijo
        const grp = document.createElement('optgroup');
        grp.label = root.name;
        kids.forEach(child => {
          const opt = document.createElement('option');
          opt.value = child.id;
          opt.textContent = child.name;
          grp.appendChild(opt);
        });
        sel.appendChild(grp);
      } else {
        // Hoja sin hijos: <option> directa
        const opt = document.createElement('option');
        opt.value = root.id;
        opt.textContent = root.name;
        sel.appendChild(opt);
      }
    });

    // Huérfanos (parentId apunta a un padre que no existe en la lista)
    active.filter(q => q.parentId && !byId.has(q.parentId)).sort(sort).forEach(q => {
      const opt = document.createElement('option');
      opt.value = q.id;
      opt.textContent = q.name;
      sel.appendChild(opt);
    });

    // Pre-seleccionar categoría si viene por URL param
    if (preQueueId) sel.value = preQueueId;
  }

  // ── Autocomplete Responsable ──────────────────────────────────────────────
  let _assignedTimer = null;
  const assignedInput   = document.getElementById('new-assigned');
  const assignedHidden  = document.getElementById('new-assigned-id');
  const assignedSuggest = document.getElementById('assigned-suggestions');
  const assignedChip    = document.getElementById('assigned-chip');
  const assignedWrap    = document.getElementById('assigned-input-wrap');

  function closeAssigned() {
    if (assignedSuggest) assignedSuggest.classList.add('hidden');
  }

  function selectAssigned(username) {
    if (assignedHidden) assignedHidden.value = username;
    const err = document.getElementById('assigned-error');
    if (err) err.classList.add('hidden');
    closeAssigned();

    // Mostrar chip
    if (assignedChip && assignedWrap) {
      const avatar = document.getElementById('assigned-chip-avatar');
      const name   = document.getElementById('assigned-chip-name');
      if (avatar) avatar.textContent = username.slice(0, 2).toUpperCase();
      if (name)   name.textContent   = username;
      assignedChip.classList.remove('hidden');
      assignedChip.classList.add('flex');
      assignedWrap.classList.add('hidden');
      if (assignedInput) assignedInput.value = '';
    } else if (assignedInput) {
      assignedInput.value = username;
    }
  }

  function clearAssigned() {
    if (assignedHidden) assignedHidden.value = '';
    if (assignedInput)  assignedInput.value  = '';
    if (assignedChip) {
      assignedChip.classList.add('hidden');
      assignedChip.classList.remove('flex');
    }
    if (assignedWrap) assignedWrap.classList.remove('hidden');
    if (assignedInput) assignedInput.focus();
  }

  // Botón × del chip
  const chipClear = document.getElementById('assigned-chip-clear');
  if (chipClear) chipClear.addEventListener('click', clearAssigned);

  if (assignedInput) {
    assignedInput.addEventListener('input', function () {
      clearTimeout(_assignedTimer);
      assignedHidden.value = '';
      const q = assignedInput.value.trim();
      if (q.length < 1) { closeAssigned(); return; }
      _assignedTimer = setTimeout(() => searchAssigned(q), 250);
    });

    assignedInput.addEventListener('keydown', function (e) {
      if (assignedSuggest && assignedSuggest.classList.contains('hidden')) return;
      if (e.key === 'Escape') { e.preventDefault(); closeAssigned(); }
      if (e.key === 'Enter') {
        const first = assignedSuggest && assignedSuggest.querySelector('[data-username]');
        if (first) { e.preventDefault(); selectAssigned(first.dataset.username); }
      }
    });
  }

  async function searchAssigned(q) {
    const all = await window.loadUsersCache(apiBase);
    const users = window.filterUsers(all, q);
    if (!users || !users.length) { closeAssigned(); return; }
    const header = `<div class="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-gray-400 border-b border-gray-100 select-none">Responsable</div>`;
    assignedSuggest.innerHTML = header + users.map(u => `
      <div class="suggest-item px-3 py-2.5 flex items-center gap-2.5 cursor-pointer hover:bg-blue-100 transition-colors rounded-md"
           data-username="${esc(u.userName)}">
        <div class="w-7 h-7 rounded-full bg-blue-100 text-blue-700 flex items-center justify-center text-xs font-bold shrink-0">
          ${esc((u.userName || '').slice(0, 2).toUpperCase())}
        </div>
        <div class="min-w-0">
          <div class="text-sm font-medium text-gray-800 truncate">@${esc(u.userName)}</div>
          ${u.mail ? `<div class="text-xs text-gray-400 truncate">${esc(u.mail)}</div>` : ''}
        </div>
      </div>
    `).join('');
    assignedSuggest.classList.remove('hidden');

    assignedSuggest.querySelectorAll('[data-username]').forEach(el => {
      el.addEventListener('mousedown', (e) => {
        e.preventDefault();
        selectAssigned(el.dataset.username);
      });
    });
  }

  // Cerrar al hacer click fuera
  document.addEventListener('click', function (e) {
    if (assignedSuggest && !assignedSuggest.contains(e.target) && e.target !== assignedInput) {
      closeAssigned();
    }
  });

  // ── Autocomplete Ticket Padre ─────────────────────────────────────────────
  let _parentTimer = null;
  const parentInput   = document.getElementById('new-parent');
  const parentHidden  = document.getElementById('new-parent-id');
  const parentSuggest = document.getElementById('parent-suggestions');

  if (parentInput) {
    parentInput.addEventListener('input', function () {
      clearTimeout(_parentTimer);
      parentHidden.value = '';
      const q = parentInput.value.trim();
      if (q.length < 2) { parentSuggest.classList.add('hidden'); return; }
      _parentTimer = setTimeout(() => searchParent(q), 280);
    });
  }

  async function searchParent(q) {
    const token = localStorage.getItem('tk_token');
    if (!token) return;
    const resp = await fetch(`${apiBase}/my/tickets/?search=${encodeURIComponent(q)}&limit=8&include_closed=true`, {
      headers: { 'Authorization': 'Bearer ' + token, 'Accept': 'application/json' },
    });
    if (!resp || !resp.ok) return;
    const data = await resp.json();
    const tickets = (data.data || []);
    if (!tickets.length) { parentSuggest.classList.add('hidden'); return; }
    parentSuggest.innerHTML = tickets.map(t => `
      <div class="px-3 py-2 cursor-pointer hover:bg-blue-50 transition-colors"
           data-id="${t.id}" data-title="${esc(t.title)}">
        <span class="font-mono text-xs text-gray-400">${t.id.substring(0, 8)}</span>
        — <span class="text-xs text-gray-700">${esc(t.title)}</span>
      </div>
    `).join('');
    parentSuggest.classList.remove('hidden');

    parentSuggest.querySelectorAll('[data-id]').forEach(el => {
      el.addEventListener('mousedown', (e) => {
        e.preventDefault();
        if (parentInput)   parentInput.value  = `${el.dataset.id.substring(0, 8)} — ${el.dataset.title}`;
        if (parentHidden)  parentHidden.value = el.dataset.id;
        if (parentSuggest) parentSuggest.classList.add('hidden');
      });
    });
  }

  document.addEventListener('click', function (e) {
    if (parentSuggest && !parentSuggest.contains(e.target) && e.target !== parentInput) {
      parentSuggest.classList.add('hidden');
    }
  });

  // ── Submit ────────────────────────────────────────────────────────────────
  window.submitTicket = async function () {
    const errEl = document.getElementById('new-error');
    const btn   = document.getElementById('submit-btn');
    errEl.classList.add('hidden');

    const queueId     = document.getElementById('new-queue').value;
    const title       = document.getElementById('new-title').value.trim();
    const description = document.getElementById('new-description').value.trim();
    const priority    = parseInt(document.getElementById('new-priority').value);
    const parentId    = parentHidden ? (parentHidden.value || null) : null;
    const assignedTo  = assignedHidden ? (assignedHidden.value || null) : null;

    if (!queueId)     { errEl.textContent = 'Seleccioná una categoría.'; errEl.classList.remove('hidden'); return; }
    if (!title)       { errEl.textContent = 'El título no puede estar vacío.'; errEl.classList.remove('hidden'); return; }
    if (title.length > 200) { errEl.textContent = 'El título no puede superar 200 caracteres.'; errEl.classList.remove('hidden'); return; }
    if (description.length < 10) { errEl.textContent = 'La descripción debe tener al menos 10 caracteres.'; errEl.classList.remove('hidden'); return; }

    const hasMention = /@[\w.]+/.test(description);
    if (!assignedTo && !hasMention) {
      const assignedErr = document.getElementById('assigned-error');
      if (assignedErr) {
        assignedErr.textContent = 'Asigná un responsable o mencioná a alguien con @ en la descripción.';
        assignedErr.classList.remove('hidden');
      } else {
        errEl.textContent = 'Asigná un responsable o mencioná a alguien con @ en la descripción.';
        errEl.classList.remove('hidden');
      }
      return;
    }

    btn.disabled = true;
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Enviando...';

    const user = (typeof getUser === 'function') ? getUser() : null;

    // Paso 1: crear el ticket
    const token = localStorage.getItem('tk_token');
    let resp;
    try {
      resp = await fetch(`${apiBase}/my/tickets/`, {
        method: 'POST',
        headers: {
          'Authorization': 'Bearer ' + token,
          'Content-Type': 'application/json',
          'Accept': 'application/json',
        },
        body: JSON.stringify({
          queueId: parseInt(queueId),
          title,
          description,
          priority,
          parentId,
          assignedTo,
          submitterEmail: user?.mail || '',
          mentionedUserIds: [],  // @menciones en description son parseadas por el backend via regex
        }),
      });
    } catch (e) {
      errEl.textContent = 'Error de red al enviar.';
      errEl.classList.remove('hidden');
      btn.disabled = false;
      btn.innerHTML = '<i class="fas fa-paper-plane"></i> Enviar solicitud';
      return;
    }

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      errEl.textContent = err.message || 'Error al crear el ticket.';
      errEl.classList.remove('hidden');
      btn.disabled = false;
      btn.innerHTML = '<i class="fas fa-paper-plane"></i> Enviar solicitud';
      return;
    }

    const ticket = await resp.json();

    // Paso 2: subir adjuntos (si hay)
    if (attachments && attachments.getFiles().length) {
      await attachments.uploadTo(ticket.id);
    }

    // Paso 3: redirigir
    window.location.href = `${base}/portal/tickets/${ticket.id}`;
  };

  // ── Validaciones inline ───────────────────────────────────────────────────
  window.validateTitle = function () {
    const el  = document.getElementById('new-title');
    const err = document.getElementById('title-error');
    if (!el || !err) return;
    if (!el.value.trim()) {
      err.textContent = 'El título no puede estar vacío.';
      err.classList.remove('hidden'); el.classList.add('border-red-300');
    } else if (el.value.length > 200) {
      err.textContent = 'Máximo 200 caracteres.';
      err.classList.remove('hidden'); el.classList.add('border-red-300');
    } else {
      err.classList.add('hidden'); el.classList.remove('border-red-300');
    }
  };

  window.validateDescription = function () {
    const el  = document.getElementById('new-description');
    const err = document.getElementById('desc-error');
    if (!el || !err) return;
    if (!el.value.trim()) {
      err.textContent = 'La descripción no puede estar vacía.';
      err.classList.remove('hidden'); el.classList.add('border-red-300');
    } else if (el.value.trim().length < 10) {
      err.textContent = 'Debe tener al menos 10 caracteres.';
      err.classList.remove('hidden'); el.classList.add('border-red-300');
    } else {
      err.classList.add('hidden'); el.classList.remove('border-red-300');
    }
  };

  // ── Init ──────────────────────────────────────────────────────────────────
  loadQueues();
};
