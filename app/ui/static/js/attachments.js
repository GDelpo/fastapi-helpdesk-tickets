/**
 * attachments.js — Manejo de adjuntos: selección, drag&drop, preview, upload.
 *
 * Uso:
 *   const att = initAttachments({
 *     inputId: 'file-input',
 *     previewId: 'file-preview',
 *     dropZoneId: 'drop-zone',    // opcional
 *     maxMb: 10,
 *   });
 *   att.getFiles()                            → File[]
 *   await att.uploadTo(ticketId)              → AttachmentResponse[] | null
 *   await att.uploadTo(ticketId, followupId)  → AttachmentResponse[] | null
 */

function fileIcon(mime, name) {
  const m = (mime || '').toLowerCase();
  const n = (name || '').toLowerCase();
  if (m.startsWith('image/') || /\.(png|jpg|jpeg|gif|webp)$/.test(n)) return 'fa-image text-blue-400';
  if (m.includes('pdf') || n.endsWith('.pdf'))   return 'fa-file-pdf text-red-400';
  if (m.includes('excel') || m.includes('spreadsheet') || /\.(xlsx|xls|csv)$/.test(n)) return 'fa-file-excel text-green-500';
  if (m.includes('word') || /\.(docx|doc)$/.test(n)) return 'fa-file-word text-blue-500';
  return 'fa-paperclip text-gray-400';
}

function formatBytes(b) {
  if (!b) return '0 B';
  const units = ['B', 'KB', 'MB'];
  let v = b, u = 0;
  while (v >= 1024 && u < units.length - 1) { v /= 1024; u++; }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[u]}`;
}

window.initAttachments = function ({ inputId, previewId, dropZoneId, maxMb = 10 }) {
  const input     = document.getElementById(inputId);
  const previewEl = document.getElementById(previewId);
  const dropZone  = dropZoneId ? document.getElementById(dropZoneId) : null;
  const maxBytes  = maxMb * 1024 * 1024;

  // MIME types aceptados (alineado con backend settings.attachments_allowed_types)
  const ALLOWED_MIME = new Set([
    'application/pdf',
    'image/png', 'image/jpeg', 'image/gif', 'image/webp',
    'text/plain', 'text/csv',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  ]);

  let _files = [];

  function renderPreview() {
    if (!previewEl) return;
    if (!_files.length) { previewEl.innerHTML = ''; return; }
    previewEl.innerHTML = _files.map((f, idx) => `
      <div class="inline-flex items-center gap-2 border border-gray-200 rounded-lg px-2.5 py-2 bg-gray-50">
        <i class="fas ${fileIcon(f.type, f.name)} text-sm flex-shrink-0"></i>
        <div class="text-xs min-w-0">
          <div class="font-medium text-gray-700 max-w-[160px] truncate">${esc(f.name)}</div>
          <div class="text-gray-400">${formatBytes(f.size)}</div>
        </div>
        <button type="button" onclick="__att_remove_${inputId}(${idx})"
                class="text-gray-400 hover:text-red-500 transition-colors ml-1 flex-shrink-0">×</button>
      </div>
    `).join('');
  }

  // Expose remove function globally (named by inputId to avoid collisions)
  window[`__att_remove_${inputId}`] = function (idx) {
    _files.splice(idx, 1);
    renderPreview();
  };

  function addFiles(fileList) {
    const incoming = Array.from(fileList || []);
    for (const file of incoming) {
      // Dedup by name+size+lastModified
      if (_files.some(f => f.name === file.name && f.size === file.size && f.lastModified === file.lastModified)) continue;
      if (file.size > maxBytes) {
        if (typeof showToast === 'function') showToast(`"${esc(file.name)}" supera ${maxMb} MB — no se agregó.`, 'warning');
        continue;
      }
      if (!ALLOWED_MIME.has(file.type)) {
        if (typeof showToast === 'function') showToast(`"${esc(file.name)}" tipo no permitido (${file.type || 'desconocido'}).`, 'warning');
        continue;
      }
      _files.push(file);
    }
    renderPreview();
    if (input) input.value = '';
  }

  // Wire input
  if (input) {
    input.addEventListener('change', () => addFiles(input.files));
  }

  // Wire drop zone
  if (dropZone) {
    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('border-blue-400', 'bg-blue-50');
    });
    dropZone.addEventListener('dragleave', () => {
      dropZone.classList.remove('border-blue-400', 'bg-blue-50');
    });
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('border-blue-400', 'bg-blue-50');
      addFiles(e.dataTransfer.files);
    });
  }

  /**
   * Sube los archivos al servidor.
   * Continúa en caso de error parcial (reporta por toast, no lanza).
   * @returns {Promise<object[]>} adjuntos guardados exitosamente
   */
  async function uploadTo(ticketId, followupId = null) {
    if (!_files.length) return [];
    const token = localStorage.getItem('tk_token');
    if (!token) return [];
    const base = (typeof API_BASE !== 'undefined') ? API_BASE : (typeof BASE !== 'undefined' ? BASE + '/api/v1' : '');
    let url = `${base}/tickets/${ticketId}/attachments/`;
    if (followupId) url += `?followup_id=${followupId}`;

    const fd = new FormData();
    _files.forEach(f => fd.append('files', f, f.name));

    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token, 'Accept': 'application/json' },
        body: fd,
      });
      if (resp.ok) {
        const saved = await resp.json();
        _files = [];
        renderPreview();
        return saved;
      }
      // Intenta parsear el error del backend
      const err = await resp.json().catch(() => ({}));
      if (typeof showToast === 'function') {
        showToast(err.message || 'Error al subir adjuntos.', 'error');
      }
      return [];
    } catch (e) {
      if (typeof showToast === 'function') showToast('Error de red al subir adjuntos.', 'error');
      return [];
    }
  }

  function clear() {
    _files = [];
    if (input) input.value = '';
    renderPreview();
  }

  return {
    getFiles: () => _files,
    uploadTo,
    addFiles,
    clear,
  };
};

/**
 * Renderiza un adjunto guardado como link de descarga.
 * Requiere BASE definido en el template.
 */
window.renderAttachmentLink = function (a) {
  // Botón de descarga vía fetch con Authorization
  return `
    <button type="button" onclick="downloadAttachment('${a.ticketId}','${a.id}','${esc(a.filename)}')"
      class="inline-flex items-center gap-2 border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 hover:border-blue-300 hover:bg-blue-50 transition-colors no-underline">
      <i class="fas ${fileIcon(a.mimeType, a.filename)} text-sm flex-shrink-0"></i>
      <span class="text-xs text-gray-700 font-medium max-w-[180px] truncate">${esc(a.filename)}</span>
      <span class="text-xs text-gray-400">${formatBytes(a.size || 0)}</span>
      <i class="fas fa-download text-gray-300 text-[10px] ml-1"></i>
    </button>`;
};

// Descarga adjunto autenticado
window.downloadAttachment = async function(ticketId, attId, filename) {
  const token = localStorage.getItem('tk_token');
  if (!token) {
    if (typeof showToast === 'function') showToast('Sesión expirada. Volvé a iniciar sesión.', 'error');
    return;
  }
  const url = `${BASE}/api/v1/tickets/${ticketId}/attachments/${attId}/download`;
  try {
    const resp = await fetch(url, {
      headers: { 'Authorization': 'Bearer ' + token },
    });
    if (!resp.ok) {
      if (typeof showToast === 'function') showToast('Error al descargar el archivo.', 'error');
      return;
    }
    const blob = await resp.blob();
    const link = document.createElement('a');
    link.href = window.URL.createObjectURL(blob);
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    setTimeout(() => {
      window.URL.revokeObjectURL(link.href);
      link.remove();
    }, 100);
  } catch (e) {
    if (typeof showToast === 'function') showToast('Error de red al descargar el archivo.', 'error');
  }
};
