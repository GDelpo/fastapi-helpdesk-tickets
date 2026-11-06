/**
 * attachments.js — attachment handling: selection, drag&drop, preview, upload.
 *
 * Usage:
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

  // Accepted MIME types (kept in sync with backend settings.attachments_allowed_types)
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
        <button type="button" data-att-remove="${idx}"
                class="text-gray-400 hover:text-red-500 transition-colors ml-1 flex-shrink-0">×</button>
      </div>
    `).join('');
  }

  // Delegated remove: values travel in data-* attributes, never inside inline handlers.
  if (previewEl) {
    previewEl.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-att-remove]');
      if (!btn) return;
      _files.splice(Number(btn.dataset.attRemove), 1);
      renderPreview();
    });
  }

  function addFiles(fileList) {
    const incoming = Array.from(fileList || []);
    for (const file of incoming) {
      // Dedup by name+size+lastModified
      if (_files.some(f => f.name === file.name && f.size === file.size && f.lastModified === file.lastModified)) continue;
      if (file.size > maxBytes) {
        if (typeof showToast === 'function') showToast(`"${file.name}" exceeds ${maxMb} MB — not added.`, 'warning');
        continue;
      }
      if (!ALLOWED_MIME.has(file.type)) {
        if (typeof showToast === 'function') showToast(`"${file.name}" file type not allowed (${file.type || 'unknown'}).`, 'warning');
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
   * Uploads the files to the server.
   * Keeps going on partial errors (reports via toast, does not throw).
   * @returns {Promise<object[]>} attachments saved successfully
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
      // Try to parse the backend error
      const err = await resp.json().catch(() => ({}));
      if (typeof showToast === 'function') {
        showToast(err.message || 'Error uploading attachments.', 'error');
      }
      return [];
    } catch (e) {
      if (typeof showToast === 'function') showToast('Network error while uploading attachments.', 'error');
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
 * Renders a saved attachment as a download button.
 * Requires BASE defined in the template. The filename is user-controlled: it is only
 * written escaped into text and data-* attributes; clicks are handled by delegation below.
 */
window.renderAttachmentLink = function (a) {
  // Download button via fetch with the Authorization header
  return `
    <button type="button" data-att-download data-ticket-id="${esc(a.ticketId)}"
      data-attachment-id="${esc(a.id)}" data-filename="${esc(a.filename)}"
      class="inline-flex items-center gap-2 border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 hover:border-blue-300 hover:bg-blue-50 transition-colors no-underline">
      <i class="fas ${fileIcon(a.mimeType, a.filename)} text-sm flex-shrink-0"></i>
      <span class="text-xs text-gray-700 font-medium max-w-[180px] truncate">${esc(a.filename)}</span>
      <span class="text-xs text-gray-400">${formatBytes(a.size || 0)}</span>
      <i class="fas fa-download text-gray-300 text-[10px] ml-1"></i>
    </button>`;
};

document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-att-download]');
  if (!btn) return;
  window.downloadAttachment(btn.dataset.ticketId, btn.dataset.attachmentId, btn.dataset.filename);
});

// Authenticated attachment download
window.downloadAttachment = async function(ticketId, attId, filename) {
  const token = localStorage.getItem('tk_token');
  if (!token) {
    if (typeof showToast === 'function') showToast('Session expired. Please sign in again.', 'error');
    return;
  }
  const url = `${BASE}/api/v1/tickets/${ticketId}/attachments/${attId}/download`;
  try {
    const resp = await fetch(url, {
      headers: { 'Authorization': 'Bearer ' + token },
    });
    if (!resp.ok) {
      if (typeof showToast === 'function') showToast('Error downloading file.', 'error');
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
    if (typeof showToast === 'function') showToast('Network error while downloading file.', 'error');
  }
};
