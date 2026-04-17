/**
 * mentions.js — Autocomplete inline de @menciones.
 *
 * Uso:
 *   initMentions('textarea-id', API_BASE)
 *
 * Al escribir '@' + al menos 1 char en el textarea, muestra un dropdown
 * con usuarios que coincidan. Al seleccionar, inserta @username en el texto.
 * El backend parsea @menciones del texto con regex — no requiere enviar IDs separados.
 */

window.initMentions = function (textareaId, apiBase) {
  const textarea = document.getElementById(textareaId);
  if (!textarea) return;

  // Crear dropdown
  const dropdown = document.createElement('div');
  dropdown.className = 'hidden absolute z-30 bg-white border border-gray-200 rounded-lg shadow-lg max-h-48 overflow-y-auto min-w-[220px]';
  dropdown.style.cssText = 'top:100%;left:0;';

  // Wrapper con position:relative
  const wrapper = document.createElement('div');
  wrapper.style.position = 'relative';
  textarea.parentNode.insertBefore(wrapper, textarea);
  wrapper.appendChild(textarea);
  wrapper.appendChild(dropdown);

  let _debounceTimer = null;
  let _currentQuery = '';

  function closeDrop() {
    dropdown.classList.add('hidden');
    dropdown.innerHTML = '';
    _currentQuery = '';
  }

  function insertMention(username) {
    const val = textarea.value;
    const pos = textarea.selectionStart;
    // Find the @ that triggered this
    const before = val.substring(0, pos);
    const atIdx = before.lastIndexOf('@');
    if (atIdx === -1) return;
    const after = val.substring(pos);
    textarea.value = before.substring(0, atIdx) + '@' + username + ' ' + after;
    textarea.selectionStart = textarea.selectionEnd = atIdx + username.length + 2;
    textarea.focus();
    closeDrop();
  }

  function renderResults(users) {
    if (!users.length) { closeDrop(); return; }
    const header = `<div class="px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-gray-400 border-b border-gray-100 select-none">Mencionar a...</div>`;
    dropdown.innerHTML = header + users.map(u => `
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
    dropdown.classList.remove('hidden');

    dropdown.querySelectorAll('[data-username]').forEach(el => {
      el.addEventListener('mousedown', (e) => {
        e.preventDefault(); // evitar blur en textarea
        insertMention(el.dataset.username);
      });
    });
  }

  async function fetchSuggestions(q) {
    const all = await window.loadUsersCache(apiBase);
    return window.filterUsers(all, q);
  }

  textarea.addEventListener('input', function () {
    clearTimeout(_debounceTimer);
    const pos = textarea.selectionStart;
    const before = textarea.value.substring(0, pos);
    // Detectar si estamos escribiendo @algo
    const match = before.match(/@([\w.]*)$/);
    if (!match) { closeDrop(); return; }
    const q = match[1];
    if (q === _currentQuery) return;
    _currentQuery = q;
    // Sin debounce si q está vacío (solo @ escrito): el cache ya está cargado
    const delay = q.length === 0 ? 0 : 250;
    _debounceTimer = setTimeout(async () => {
      const users = await fetchSuggestions(q);
      if (users && users.length) renderResults(users);
      else closeDrop();
    }, delay);
  });

  textarea.addEventListener('keydown', function (e) {
    if (dropdown.classList.contains('hidden')) return;
    if (e.key === 'Escape') { e.preventDefault(); closeDrop(); }
    if (e.key === 'Enter' && !dropdown.classList.contains('hidden')) {
      const first = dropdown.querySelector('[data-username]');
      if (first) { e.preventDefault(); insertMention(first.dataset.username); }
    }
  });

  document.addEventListener('click', function (e) {
    if (!wrapper.contains(e.target)) closeDrop();
  });

  // Pre-cargar el cache al inicializar para que el primer @ sea instantáneo
  window.loadUsersCache(apiBase);
};
