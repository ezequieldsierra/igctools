/* IGC Employee ID Photo 1.0.0 — installed as a Client Script, scoped to Employee. */
(() => {
  const VERSION = '1.0.0';
  const METHOD = 'igc_employee_id_photo';
  const W = 900, H = 1200, COLOR = '#16263F', TARGET = [22, 38, 63];
  const ACTIVE = ['En cola', 'Generando', 'Ajustando'];
  const esc = (s) => frappe.utils.escape_html(String(s || ''));
  const safeURL = (value) => {
    try {
      const u = new URL(value, location.origin);
      if (![location.hostname, 'igcaribe.com', 'www.igcaribe.com', 'igcaribe.erpnext.com'].includes(u.hostname)) return '';
      return u.pathname + u.search;
    } catch (_) { return ''; }
  };
  const api = async (employee, action, data = {}) => {
    const r = await frappe.call({method: METHOD, args: {employee, action, ...data}, freeze: false});
    return r.message;
  };
  const average = (points) => points.reduce((a, p) => ({x: a.x + p.x / points.length, y: a.y + p.y / points.length}), {x: 0, y: 0});
  const percentile = (histogram, count, q) => {
    let n = 0;
    for (let i = 0; i < 256; i++) { n += histogram[i]; if (n >= count * q) return i; }
    return 255;
  };

  // Deterministic color standardization. Only the connected navy garment beneath
  // the chin is recolored; skin, hair, white background and eyes are excluded.
  function standardizeShirt(imageData, chinY) {
    const {data, width, height} = imageData;
    const count = width * height;
    const candidates = new Uint8Array(count);
    const selected = new Uint8Array(count);
    const queue = new Int32Array(count);
    const minY = Math.max(Math.round(height * 0.48), Math.round(chinY - height * 0.12));
    for (let y = minY; y < height; y++) for (let x = 0; x < width; x++) {
      const p = y * width + x, i = p * 4;
      const r = data[i], g = data[i + 1], b = data[i + 2];
      const max = Math.max(r, g, b), min = Math.min(r, g, b);
      if (data[i + 3] > 240 && max >= 16 && max < 170 && b > r + 6 && b > g + 3 && (max - min) / max > 0.24) candidates[p] = 1;
    }
    let tail = 0;
    const add = (p) => { if (p >= 0 && p < count && candidates[p] && !selected[p]) { selected[p] = 1; queue[tail++] = p; } };
    // Seed the entire lower band, including shoulder panels separated by hair.
    for (let y = Math.floor(height * 0.88); y < height; y += 4) for (let x = 0; x < width; x += 4) add(y * width + x);
    for (let head = 0; head < tail; head++) {
      const p = queue[head], x = p % width;
      if (x) add(p - 1);
      if (x < width - 1) add(p + 1);
      add(p - width); add(p + width);
    }
    if (tail < count * 0.035) throw new Error('No se detectó suficiente polo azul marino. Genere otra versión para conservar el color corporativo.');
    const histogram = new Uint32Array(256);
    for (let p = 0; p < count; p++) if (selected[p]) {
      const i = p * 4;
      histogram[Math.round(0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2])]++;
    }
    const mid = percentile(histogram, tail, 0.5);
    const span = Math.max(12, percentile(histogram, tail, 0.9) - percentile(histogram, tail, 0.1));
    const channelHist = [new Uint32Array(256), new Uint32Array(256), new Uint32Array(256)];
    for (let p = 0; p < count; p++) if (selected[p]) {
      const i = p * 4;
      const luma = Math.round(0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2]);
      const shade = Math.max(0.76, Math.min(1.24, 1 + 0.24 * (luma - mid) / span));
      for (let c = 0; c < 3; c++) { data[i + c] = Math.round(TARGET[c] * shade); channelHist[c][data[i + c]]++; }
    }
    const median = channelHist.map((hist) => percentile(hist, tail, 0.5));
    return {garment_pixels: tail, garment_coverage: +(tail / count).toFixed(4), median_rgb: median, source_luminance: mid};
  }

  async function loadModels() {
    if (!window.__igcIDPhotoModels) window.__igcIDPhotoModels = (async () => {
      if (!window.faceapi) await new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = '/assets/igctools/face/face-api.min.js'; s.onload = resolve;
        s.onerror = () => reject(new Error('No se pudo cargar el ajuste automático de encuadre.'));
        document.head.appendChild(s);
      });
      const base = '/assets/igctools/face/weights';
      await Promise.all([faceapi.nets.tinyFaceDetector.loadFromUri(base), faceapi.nets.faceLandmark68TinyNet.loadFromUri(base)]);
    })().catch((error) => { window.__igcIDPhotoModels = null; throw error; });
    return window.__igcIDPhotoModels;
  }

  async function normalizePortrait(url) {
    await loadModels();
    const response = await fetch(safeURL(url), {credentials: 'same-origin'});
    if (!response.ok) throw new Error('No se pudo abrir la imagen privada para ajustar el encuadre.');
    const objectURL = URL.createObjectURL(await response.blob());
    try {
      const img = await new Promise((resolve, reject) => { const i = new Image(); i.onload = () => resolve(i); i.onerror = () => reject(new Error('La imagen generada no se puede leer.')); i.src = objectURL; });
      let faces = await faceapi.detectAllFaces(img, new faceapi.TinyFaceDetectorOptions({inputSize: 512, scoreThreshold: 0.35})).withFaceLandmarks(true);
      if (!faces.length) faces = await faceapi.detectAllFaces(img, new faceapi.TinyFaceDetectorOptions({inputSize: 608, scoreThreshold: 0.22})).withFaceLandmarks(true);
      if (faces.length !== 1) throw new Error('Se necesita un único rostro visible y de frente. Genere otra versión.');
      const landmarks = faces[0].landmarks;
      const left = average(landmarks.getLeftEye()), right = average(landmarks.getRightEye());
      const dx = right.x - left.x, dy = right.y - left.y;
      const distance = Math.hypot(dx, dy);
      if (distance < img.width * 0.08) throw new Error('El rostro quedó demasiado pequeño para el carnet. Genere otra versión.');
      const angle = Math.atan2(dy, dx), scale = (W * 0.24) / distance;
      const a = scale * Math.cos(angle), b = -scale * Math.sin(angle), c = -b, d = a;
      const center = {x: (left.x + right.x) / 2, y: (left.y + right.y) / 2};
      const tx = W / 2 - a * center.x - c * center.y;
      const ty = H * 0.38 - b * center.x - d * center.y;
      const chin = landmarks.getJawOutline()[8];
      const chinY = b * chin.x + d * chin.y + ty;
      if (chinY > H * 0.82 || chinY < H * 0.48) throw new Error('La pose no permite un encuadre uniforme. Genere otra versión de frente.');
      const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H;
      const ctx = canvas.getContext('2d', {willReadFrequently: true});
      ctx.fillStyle = '#FFFFFF'; ctx.fillRect(0, 0, W, H);
      ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
      ctx.setTransform(a, b, c, d, tx, ty); ctx.drawImage(img, 0, 0); ctx.resetTransform();
      const pixels = ctx.getImageData(0, 0, W, H);
      const garment = standardizeShirt(pixels, chinY);
      ctx.putImageData(pixels, 0, 0);
      return {image_data: canvas.toDataURL('image/png'), normalization: JSON.stringify({version: VERSION, width: W, height: H, color: COLOR, eye_line: H * 0.38, eye_distance: W * 0.24, chin_y: +chinY.toFixed(2), rotation: +angle.toFixed(4), ...garment})};
    } finally { URL.revokeObjectURL(objectURL); }
  }

  const CSS = `
  .igc-id-photo{--navy:#123651;--green:#26965b;font-family:inherit;color:#253b4b;padding:6px 0 20px}
  .igc-id-photo *{box-sizing:border-box}.igc-id-photo .id-head{background:var(--navy);color:white;border-radius:13px;padding:18px 22px;display:flex;align-items:center;justify-content:space-between;gap:14px}
  .igc-id-photo .id-title{font-size:19px;font-weight:700;margin:0;color:white}.igc-id-photo .id-subtitle{font-size:12px;opacity:.85;margin-top:4px}
  .igc-id-photo .id-status{border-radius:99px;background:#ffffff22;padding:6px 12px;font-size:12px;white-space:nowrap}
  .igc-id-photo .id-grid{display:grid;grid-template-columns:minmax(240px,330px) 1fr;gap:22px;margin-top:20px}
  .igc-id-photo .id-card{border:1px solid #e1e7eb;border-radius:13px;background:#fff;padding:18px}
  .igc-id-photo .id-frame{aspect-ratio:3/4;background:#f1f4f7;border-radius:8px;overflow:hidden;display:flex;align-items:center;justify-content:center;border:1px solid #e5eaed}
  .igc-id-photo .id-frame img{width:100%;height:100%;object-fit:contain;background:white}.igc-id-photo .id-empty{text-align:center;padding:22px;color:#6b7d89;font-size:13px}
  .igc-id-photo .id-caption{font-size:12px;color:#72808a;text-align:center;margin-top:10px}
  .igc-id-photo h4{font-size:14px;font-weight:700;margin:0 0 12px;color:var(--navy)}
  .igc-id-photo .id-tags{display:flex;gap:7px;flex-wrap:wrap;margin-bottom:18px}.igc-id-photo .id-tag{border-radius:99px;padding:5px 10px;background:#eef3f6;font-size:11px;color:#3b5263}
  .igc-id-photo .id-dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:#16263f;margin-right:5px;vertical-align:-1px}
  .igc-id-photo textarea{width:100%;resize:vertical;min-height:96px;border:1px solid #d3dee5;border-radius:9px;padding:11px 12px;font-size:13px;outline:none;background:white}
  .igc-id-photo textarea:focus{border-color:#4b87a7;box-shadow:0 0 0 2px #4b87a71a}.igc-id-photo .id-help{color:#6d7e8b;font-size:12px;line-height:1.5;margin:7px 0 14px}
  .igc-id-photo .id-actions{display:flex;gap:9px;flex-wrap:wrap}.igc-id-photo button{border:1px solid #d6e0e6;border-radius:8px;padding:9px 14px;font-size:12px;font-weight:600;cursor:pointer;background:white;color:var(--navy)}
  .igc-id-photo button.id-primary{background:var(--green);border-color:var(--green);color:white}.igc-id-photo button:disabled{opacity:.48;cursor:default}
  .igc-id-photo .id-references{display:flex;gap:8px;margin-top:9px;flex-wrap:wrap}.igc-id-photo .id-references img{width:65px;height:65px;object-fit:cover;border-radius:8px;border:1px solid #dce5eb}
  .igc-id-photo .id-divider{height:1px;background:#e8edf0;margin:20px 0}.igc-id-photo .id-note{border-radius:8px;background:#edf6fc;color:#235474;padding:11px 13px;font-size:12px;margin-top:14px}
  .igc-id-photo .id-note.error{background:#fff2ee;color:#973f2b}.igc-id-photo .id-history{display:flex;gap:10px;overflow:auto;padding:4px 0 12px;margin-top:10px}
  .igc-id-photo .id-version{flex:0 0 88px;border-radius:9px;border:1px solid #dae3e9;padding:5px;background:#fff;text-align:center}.igc-id-photo .id-version.current{border:2px solid var(--green);padding:4px}
  .igc-id-photo .id-version img{width:76px;height:101px;object-fit:contain;border-radius:5px;background:#f4f6f8}.igc-id-photo .id-version small{font-size:10px;display:block;padding:5px 0 2px;color:#657a88}
  .igc-id-photo .id-version button{padding:4px 6px;font-size:10px;width:100%}.igc-id-photo .id-busy{display:inline-block;width:11px;height:11px;border:2px solid #ffffff66;border-top-color:white;border-radius:50%;animation:igc-id-spin 1s linear infinite;margin-right:5px}
  @keyframes igc-id-spin{to{transform:rotate(360deg)}}@media(max-width:760px){.igc-id-photo .id-grid{grid-template-columns:1fr}.igc-id-photo .id-grid>.id-card:first-child{max-width:330px;width:100%;margin:auto}.igc-id-photo .id-head{padding:15px}}
  `;

  class PhotoPanel {
    constructor(frm) { this.frm = frm; this.name = frm.doc.name; this.prompt = ''; this.state = null; this.busy = false; this.normalizing = null; this.failedNormalization = new Set(); this.timer = null; }
    alive() { return window.cur_frm === this.frm && this.frm.doc.name === this.name; }
    wrapper() { return this.frm.fields_dict.custom_foto_id_panel?.$wrapper; }
    stop() { clearTimeout(this.timer); }
    async refresh() {
      this.stop();
      if (!this.alive() || !this.wrapper()) return;
      if (this.frm.is_new()) { this.wrapper().html('<div class="text-muted">Guarde el empleado para generar su Foto de ID Laboral.</div>'); return; }
      try {
        const state = await api(this.name, 'state');
        if (!this.alive()) return;
        this.state = state;
        this.frm.doc.custom_foto_id_laboral = state.current_image || '';
        this.frm.doc.custom_foto_id_version = state.current_version || '';
        this.render();
        const job = state.history.find((j) => j.status === 'Ajustando');
        if (job && state.can_generate && !this.normalizing && !this.failedNormalization.has(job.name)) await this.finish(job);
      } catch (error) { if (this.alive()) this.showError(error.message || 'No se pudo consultar la foto.'); }
      if (this.alive() && this.state?.history.some((j) => ACTIVE.includes(j.status))) this.timer = setTimeout(() => this.refresh(), 5000);
    }
    showError(message) { const w = this.wrapper(); if (w?.find('.id-feedback').length) w.find('.id-feedback').html(`<div class="id-note error">${esc(message)}</div>`); else w?.html(`<div class="text-muted">${esc(message)}</div>`); }
    render() {
      const s = this.state, pending = s.history.find((j) => ACTIVE.includes(j.status)), recent = s.history[0];
      const disabled = this.busy || !!pending || !s.can_generate;
      const status = pending ? (this.normalizing ? 'Unificando color y encuadre' : pending.status) : s.current_image ? 'Foto disponible' : 'Sin foto generada';
      const history = s.history.filter((j) => j.status === 'Lista' && j.final_image);
      this.wrapper().html(`<style>${CSS}</style><div class="igc-id-photo">
        <div class="id-head"><div><h3 class="id-title">Foto de ID Laboral</h3><div class="id-subtitle">${esc(this.frm.doc.employee_name || this.name)}</div></div><span class="id-status">${pending ? '<span class="id-busy"></span>' : ''}${esc(status)}</span></div>
        <div class="id-grid"><div class="id-card"><div class="id-frame">${s.current_image ? `<img src="${esc(safeURL(s.current_image))}" alt="Foto de ID Laboral">` : '<div class="id-empty">Su retrato corporativo<br>aparecerá aquí.</div>'}</div><div class="id-caption">Formato de carnet · 900 × 1200 px</div>${s.current_image ? '<div class="id-actions" style="margin-top:12px;justify-content:center"><button data-act="download">Descargar foto</button></div>' : ''}</div>
        <div class="id-card"><h4>Estilo corporativo</h4><div class="id-tags"><span class="id-tag"><span class="id-dot"></span>Azul marino ${COLOR}</span><span class="id-tag">Primer botón abierto</span><span class="id-tag">Fondo blanco</span><span class="id-tag">Retoque de ojeras</span><span class="id-tag">Encuadre uniforme</span></div>
        <h4>¿Qué desea ajustar?</h4><textarea class="id-prompt" maxlength="1200" placeholder="Ej.: soltar el pelo y darle un acabado más glamuroso." ${disabled ? 'disabled' : ''}>${esc(this.prompt)}</textarea><p class="id-help">Puede ajustar el peinado, la expresión o el acabado. Se mantienen el parecido, el polo, su color y el formato de la foto.</p>
        <div class="id-actions"><button class="id-primary" data-act="generate" ${disabled || !s.references.length ? 'disabled' : ''}>${s.current_image ? 'Generar nueva foto' : 'Generar foto'}</button><button data-act="edit" ${disabled || !s.current_image ? 'disabled' : ''}>Aplicar ajuste</button></div>
        <div class="id-feedback">${pending ? `<div class="id-note">${pending.status === 'Ajustando' ? 'La foto se generó. Se está preparando el color y el recorte final.' : 'La foto se está generando. Puede continuar trabajando; aparecerá al volver a esta pestaña.'}</div>` : recent?.status === 'Error' ? `<div class="id-note error">${esc(recent.error_message)}</div>` : ''}</div>
        ${pending?.status === 'Ajustando' && this.failedNormalization.has(pending.name) ? `<div class="id-note error">${esc(pending.error_message || 'No se pudo completar el encuadre automático.')}</div><div class="id-actions" style="margin-top:10px"><button data-act="retry" data-job="${esc(pending.name)}">Reintentar acabado</button><button data-act="discard" data-job="${esc(pending.name)}">Descartar esta prueba</button></div>` : ''}
        <div class="id-divider"></div><h4>Fotos de referencia del ponche</h4><div class="id-references">${s.references.map((r) => `<img src="${esc(safeURL(r.url))}" alt="Referencia del empleado">`).join('') || '<span class="text-muted">Registre primero las fotos de ponche.</span>'}</div>
        ${history.length ? `<div class="id-divider"></div><h4>Versiones anteriores</h4><div class="id-history">${history.map((j) => `<div class="id-version ${j.name === s.current_version ? 'current' : ''}"><img src="${esc(safeURL(j.final_image))}" alt="Versión anterior"><small>${esc(String(j.creation).slice(0, 10))}</small><button data-act="restore" data-job="${esc(j.name)}" ${disabled || j.name === s.current_version ? 'disabled' : ''}>${j.name === s.current_version ? 'Actual' : 'Usar versión'}</button></div>`).join('')}</div>` : ''}
        </div></div></div>`);
      this.wrapper().find('.id-prompt').on('input', (e) => { this.prompt = e.target.value; });
      this.wrapper().find('[data-act]').on('click', (e) => this.action(e.currentTarget.dataset.act, e.currentTarget.dataset.job));
    }
    async finish(job) {
      this.normalizing = job.name; this.render();
      try {
        const output = await normalizePortrait(job.raw_image);
        // Finish this employee's job even if the user navigated away during processing.
        await api(this.name, 'finalize', {photo_job: job.name, ...output});
        this.failedNormalization.delete(job.name);
        if (this.alive()) frappe.show_alert({message: 'Foto de ID Laboral guardada.', indicator: 'green'});
      } catch (error) {
        this.failedNormalization.add(job.name);
        const message = error.message || 'No se pudo preparar el acabado final. Reintente.';
        try { await api(this.name, 'normalization_error', {photo_job: job.name, message}); } catch (_) { /* Original result remains available for retry. */ }
      } finally { this.normalizing = null; }
      if (this.alive()) {
        this.state = await api(this.name, 'state');
        this.frm.doc.custom_foto_id_laboral = this.state.current_image || '';
        this.frm.doc.custom_foto_id_version = this.state.current_version || '';
        this.render();
      }
    }
    async action(action, job) {
      if (this.busy) return;
      if (action !== 'download' && this.frm.is_dirty()) { frappe.msgprint('Guarde los cambios del empleado antes de trabajar con su foto.'); return; }
      if (action === 'edit' && !this.prompt.trim()) { this.wrapper().find('.id-prompt').focus(); frappe.show_alert({message: 'Escriba el ajuste que desea realizar.', indicator: 'orange'}); return; }
      this.busy = true; this.render();
      try {
        if (action === 'download') {
          const response = await fetch(safeURL(this.state.current_image), {credentials: 'same-origin'});
          if (!response.ok) throw new Error('No se pudo descargar la foto.');
          const url = URL.createObjectURL(await response.blob());
          const a = document.createElement('a'); a.href = url; a.download = `ID-Laboral-${this.name.replace(/[^\p{L}\p{N} -]/gu, '')}.png`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
        } else if (action === 'generate' || action === 'edit') {
          await api(this.name, 'generate', {mode: action, adjustment: this.prompt.trim(), request_key: window.crypto.randomUUID()});
        } else if (action === 'restore') await api(this.name, 'restore', {photo_job: job});
        else if (action === 'discard') await api(this.name, 'discard', {photo_job: job});
        else if (action === 'retry') this.failedNormalization.delete(job);
      } catch (error) { this.showError(error.message || 'No se pudo completar la acción.'); }
      finally { this.busy = false; await this.refresh(); }
    }
  }

  // Export pure normalization functions for integration QA; no employee data or credentials.
  window.IGCEmployeeIDPhoto = {version: VERSION, standardizeShirt, normalizePortrait};
  frappe.ui.form.on('Employee', {refresh(frm) {
    if (frm.__igcIDPhoto && frm.__igcIDPhoto.name !== frm.doc.name) frm.__igcIDPhoto.stop();
    if (!frm.__igcIDPhoto || frm.__igcIDPhoto.name !== frm.doc.name) frm.__igcIDPhoto = new PhotoPanel(frm);
    frm.__igcIDPhoto.refresh();
  }});
})();
