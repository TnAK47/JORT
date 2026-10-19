(() => {
  'use strict';
  const API = 'http://127.0.0.1:8765/api/collecte';
  const el = id => document.getElementById(({ 'files-year':'collection-year', 'pipeline-year':'index-year' })[id] || id);
  const yearSelect = el('collection-year');
  const indexYearSelect = el('index-year');
  const pipelineYearSelect = el('pipeline-year');
  const filesYearSelect = el('files-year');
  const uploadYearSelect = el('upload-year');
  const maintenanceYearSelect = el('maintenance-year');
  const ticketYearSelect = el('ticket-year');
  const logsYearSelect = el('logs-year');
  const backupYearSelect = el('backup-year');
  const yearSelects = [...new Set([yearSelect,indexYearSelect,uploadYearSelect,maintenanceYearSelect,backupYearSelect,ticketYearSelect,logsYearSelect])];
  const yearStorageKeys = new Map(yearSelects.map(select => [select.id, `jort:${select.id}`]));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
  const date = value => {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : new Intl.DateTimeFormat('fr-FR', { dateStyle:'short', timeStyle: String(value).includes('T') ? 'short' : undefined }).format(parsed);
  };
  const count = value => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? '—' : new Intl.NumberFormat('fr-FR').format(Number(value));
  const stateLabels = { present:'Présent', missing:'Manquant', drive_unavailable:'Drive inaccessible — vérifier G:', unknown:'À inventorier', conflict:'Conflit', invalid:'Invalide' };
  const processingLabels = { termine:'Indexé', a_verifier:'Aucune annonce · vérifier', en_attente:'En attente', en_cours:'En cours', a_traiter:'À traiter', erreur:'À reprendre', '—':'—' };
  const activeJob = job => Boolean(job && ['running', 'stopping'].includes(job.state));
  const laneNames = ['collection', 'indexation', 'maintenance'];
  const laneTitles = { collection:'Collecte', indexation:'Indexation', maintenance:'Maintenance' };
  function jobLanes(job) {
    if (job?.lanes && typeof job.lanes === 'object') return job.lanes;
    if (!job) return {};
    const lane = ['index'].includes(job.action) ? 'indexation' : String(job.action || '').startsWith('maintenance_') ? 'maintenance' : 'collection';
    return { [lane]:job };
  }
  function anyLaneActive(job) { return laneNames.some(lane => activeJob(jobLanes(job)[lane])); }
  function laneForAction(action) { return ['index','index_one'].includes(action) ? 'indexation' : action.startsWith('maintenance_') ? 'maintenance' : 'collection'; }
  let statusData = null;
  const statusByYear = new Map();
  let ticketOffset = 0;
  let ticketTotal = 0;
  let ticketRequestSequence = 0;
  const ticketDrafts = new Map();
  const reviewFeedbacks = new Map();
  let logPage = 0;
  let executionEvents = null;
  let pipelinePage = 0;
  let pollTimer = null;
  let pollInFlight = false;
  let pollErrors = 0;
  let pollingEnabled = false;
  let lastJobState = '';
  let uploadBusy = false;
  let lastSelectedYear = storageGet('jort:collection-year') || String(new Date().getFullYear());
  let audioContext = null;
  const pendingAudioKey = 'jort:pending-audio-job';
  const announcedAudioKey = 'jort:last-audio-job';
  let activePanel = 'overview';
  let pdfAction = storageGet('jort:pdf-action') || 'collecte';
  let overviewData = null;
  let overviewFetchedAt = 0;
  let overviewRequest = null;
  let logFilter = 'all';
  let pendingLogId = '';
  let maintenanceReport = null;
  let lastIdentityReport = '';
  const taskNames = { scan:'vérification des PDF déposés', inventory:'mise à jour de l’inventaire', download:'téléchargement des journaux', download_one:'relance d’un journal PDF', index:'extraction et indexation des annonces', index_one:'reprise de l’analyse d’un journal', maintenance_check:'contrôle de la base', maintenance_repair:'réparation de la base', maintenance_connections:'reconstruction de la restitution', maintenance_deduplicate_announcements:'nettoyage des annonces dupliquées', maintenance_merge_duplicates:'fusion des identités certaines', maintenance_merge_person:'fusion manuelle de Selma Elloumi Rekik', maintenance_optimize:'optimisation de la base', maintenance_backup:'sauvegarde de la base', maintenance_transfer_drive:'transfert des PDF vers Drive' };
  Object.assign(taskNames, { maintenance_reextract_entities:'correction des titres et des liens de toute la base', maintenance_clean_parasitic_extractions:'nettoyage des extractions parasitées', maintenance_merge_entities:'fusion des fiches sélectionnées', maintenance_undo_merge:'annulation de la fusion' });
  function storageGet(key) { try { return localStorage.getItem(key); } catch (_) { return null; } }
  function storageSet(key, value) { try { localStorage.setItem(key, value); } catch (_) { /* stockage local indisponible */ } }
  function rememberTaskAudio(job) {
    if (!job) return;
    const lanes = jobLanes(job);
    const primary = job.action === 'index' ? 'indexation' : String(job.action || '').startsWith('maintenance_') ? 'maintenance' : 'collection';
    for (const [name, lane] of Object.entries(lanes)) if (lane?.id && (name === primary || activeJob(lane))) storageSet(`${pendingAudioKey}:${name}`, String(lane.id));
  }
  function primeTaskAudio() {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!audioContext && Audio) { try { audioContext = new Audio(); } catch (_) { /* audio non pris en charge */ } }
    if (audioContext?.state === 'suspended') audioContext.resume().catch(() => {});
  }
  function playTaskBell() {
    if (!audioContext) return false;
    try {
      const start = audioContext.currentTime;
      [[880, 0], [1175, 0.2]].forEach(([frequency, delay]) => {
        const oscillator = audioContext.createOscillator();
        const volume = audioContext.createGain();
        oscillator.frequency.value = frequency;
        volume.gain.setValueAtTime(0.0001, start + delay);
        volume.gain.exponentialRampToValueAtTime(0.18, start + delay + 0.025);
        volume.gain.exponentialRampToValueAtTime(0.0001, start + delay + 0.16);
        oscillator.connect(volume); volume.connect(audioContext.destination);
        oscillator.start(start + delay); oscillator.stop(start + delay + 0.17);
      });
      return true;
    } catch (_) { return false; }
  }
  function speakTask(message) {
    if (!('speechSynthesis' in window) || !window.SpeechSynthesisUtterance) return false;
    try {
      const utterance = new SpeechSynthesisUtterance(message);
      utterance.lang = 'fr-FR'; utterance.rate = 0.92; utterance.volume = 1;
      const frenchVoice = speechSynthesis.getVoices().find(voice => /^fr([_-]|$)/i.test(voice.lang));
      if (frenchVoice) utterance.voice = frenchVoice;
      speechSynthesis.cancel(); speechSynthesis.speak(utterance);
      return true;
    } catch (_) { return false; }
  }
  function announceFinishedJob(job) {
    if (!job) return;
    const lanes = jobLanes(job);
    for (const [name, lane] of Object.entries(lanes)) {
      if (!lane?.id) continue;
      const pendingKey = `${pendingAudioKey}:${name}`;
      const announcedKey = `${announcedAudioKey}:${name}`;
      const id = String(lane.id);
      if (activeJob(lane)) { storageSet(pendingKey, id); continue; }
      if (!['done', 'paused', 'failed', 'cancelled'].includes(lane.state) || storageGet(pendingKey) !== id || storageGet(announcedKey) === id) continue;
      const task = taskNames[lane.action] || laneTitles[name].toLowerCase();
      const spokenYear = lane.current_year || (lane.years?.length === 1 ? lane.years[0] : lane.year) || job.year || '';
      const message = lane.state === 'done' ? `La tâche est finie : ${task}, ${spokenYear}.`
        : lane.state === 'paused' ? `La tâche est en pause : ${task}.`
        : lane.state === 'failed' ? `La tâche a échoué : ${task}. Consultez les logs.`
        : `La tâche a été arrêtée : ${task}.`;
      storageSet(announcedKey, id);
      const beeped = playTaskBell();
      const spoken = speakTask(message);
      el('audio-status').textContent = beeped && spoken ? `Annonce jouée : ${message}` : `Tâche terminée : ${message} Vérifiez le son avec le bouton de test.`;
    }
  }
  for (let year = 1956; year <= new Date().getFullYear(); year += 1) {
    yearSelects.forEach(select => { const option = document.createElement('option'); option.value = String(year); option.textContent = String(year); select.appendChild(option); });
  }
  yearSelects.forEach(select => {
    const saved = storageGet(yearStorageKeys.get(select.id)) || (select === logsYearSelect ? '0' : lastSelectedYear);
    select.value = [...select.options].some(option => option.value === String(saved)) ? String(saved) : lastSelectedYear;
  });
  function selectedYear(selectId) { return Number(el(selectId).value); }
  function statusFor(selectId) { return statusByYear.get(selectedYear(selectId)); }
  const panelYears = { overview:'collection-year', journaux:'collection-year', interventions:'ticket-year', maintenance:'maintenance-year', logs:'logs-year' };
  const panelCopy = {
    overview:['Pilotage','Lancez vos batchs et suivez les années, les tâches actives et leurs points de reprise.'],
    journaux:['Collecte & analyse','Choisissez une année, repérez les PDF manquants et analysez les fichiers reçus.'],
    interventions:['À vérifier','Les situations exceptionnelles qui nécessitent une décision humaine.'],
    maintenance:['Base & archives','Réparez les liens, fusionnez des fiches et gérez vos sauvegardes et archives Drive.'],
    logs:['Journal d’activité','Suivez chaque exécution : opérations unitaires, erreurs, reprises et résultats.']
  };
  function selectedStatusYears() {
    const years = [selectedYear(panelYears[activePanel]) || new Date().getFullYear()];
    if (activePanel === 'journaux' && pdfAction !== 'collecte') years.push(selectedYear(pdfAction === 'depot' ? 'upload-year' : 'index-year'));
    if (activePanel === 'maintenance') years.push(selectedYear('backup-year'));
    return [...new Set(years)];
  }
  function setActionFeedback(message, failed = false) {
    const feedback = el('action-feedback');
    feedback.textContent = message; feedback.hidden = !message; feedback.classList.toggle('error', failed);
  }
  function activatePanel(refresh = true) {
    const hash = location.hash.slice(1);
    const aliases = { collecte:'journaux', depot:'journaux', extraction:'journaux', suivi:'journaux', fichiers:'journaux', 'batch-nuit':'maintenance', 'identity-manager':'maintenance', batchs:'overview' };
    const target = aliases[hash] || hash;
    const saved = storageGet('jort:admin-panel');
    activePanel = panelYears[target] ? target : aliases[saved] || saved || 'overview';
    if (!panelYears[activePanel]) activePanel = 'overview';
    if (['collecte','depot','extraction','suivi'].includes(hash)) pdfAction = hash === 'suivi' ? 'extraction' : hash;
    document.querySelectorAll('[data-admin-panel]').forEach(panel => { panel.hidden = panel.dataset.adminPanel !== activePanel; });
    document.querySelectorAll('[data-admin-target]').forEach(link => {
      const active = link.dataset.adminTarget === activePanel;
      link.classList.toggle('is-active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    storageSet('jort:admin-panel', activePanel);
    el('admin-page-title').textContent = panelCopy[activePanel][0];
    el('admin-page-description').textContent = panelCopy[activePanel][1];
    setPdfAction(pdfAction, false);
    if (refresh && ['batchs','batch-nuit','identity-manager','collecte','depot','extraction','suivi'].includes(hash)) {
      requestAnimationFrame(() => document.getElementById(hash)?.scrollIntoView({ block:'start' }));
    }
    if (refresh) refreshAll();
  }
  function setPdfAction(action, refresh = true) {
    pdfAction = ['collecte','depot','extraction'].includes(action) ? action : 'collecte';
    storageSet('jort:pdf-action', pdfAction);
    document.querySelectorAll('[data-pdf-panel]').forEach(panel => { panel.hidden = panel.dataset.pdfPanel !== pdfAction; });
    document.querySelectorAll('[data-pdf-tab]').forEach(button => {
      const selected = button.dataset.pdfTab === pdfAction;
      button.classList.toggle('is-active', selected);
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    if (refresh) refreshAll();
  }
  function openAnnualDetails(year, filter = 'tous') {
    for (const select of [yearSelect, indexYearSelect, uploadYearSelect]) {
      select.value = String(year); storageSet(yearStorageKeys.get(select.id), select.value);
    }
    lastSelectedYear = String(year);
    el('file-filter').value = filter;
    pipelinePage = 0;
    setPdfAction(filter === 'todo' ? 'extraction' : 'collecte', false);
    updateExportLinks();
    if (location.hash === '#journaux') activatePanel(); else location.hash = 'journaux';
  }
  function renderOverview() {
    if (!overviewData) return;
    const years = overviewData.years || [];
    const known = years.filter(row => row.has_data);
    for (const [id, key] of [['recorded','recorded'],['analyzed','analyzed'],['missing','missing'],['errors','errors']]) {
      const value = known.reduce((sum, row) => sum + Number(row[key] || 0), 0);
      el(`overview-${id}`).textContent = count(value) + (key === 'analyzed' && known.some(row => row.analyzed == null) ? ' *' : '');
    }
    const filter = el('overview-filter').value;
    const query = el('overview-search').value.trim();
    const rows = years.filter(row => (!query || String(row.year).includes(query)) && (
      filter === 'all' || filter === 'missing' && row.missing > 0 || filter === 'todo' && (row.pending > 0 || row.missing > 0 || row.errors > 0 || row.detail_required) || filter === 'known' && row.has_data));
    el('overview-updated').textContent = `Mis à jour ${date(overviewData.updated_at)}${known.some(row => row.detail_required) ? ' · * certaines analyses restent à vérifier dans le détail annuel' : ''}`;
    const cell = (row, key, fileFilter) => row[key] == null ? '—' : `<button class="count-link ${key === 'missing' && row[key] ? 'is-missing' : ''}" type="button" data-open-year="${row.year}" data-file-filter="${fileFilter}" aria-label="Voir ${esc(row[key])} ${esc(key === 'missing' ? 'PDF manquants' : key === 'analyzed' ? 'journaux analysés' : key === 'errors' ? 'erreurs' : 'PDF')} de ${row.year}">${count(row[key])}</button>`;
    el('overview-body').innerHTML = rows.length ? rows.map(row => `<tr><td><strong>${row.year}</strong></td><td>${count(row.expected)}</td><td>${cell(row,'recorded','present')}<small class="storage-split">${count(row.local)} local · ${count(row.drive)} Drive</small></td><td>${cell(row,'analyzed','indexed')}</td><td>${cell(row,'pending','todo')}</td><td>${cell(row,'missing','missing')}</td><td>${cell(row,'errors','errors')}</td><td><span class="inventory-badge ${row.inventory_complete ? 'complete' : ''}">${row.inventory_complete ? 'Complet' : row.known ? 'Partiel' : 'À établir'}</span>${row.detail_required ? '<small class="storage-split">Analyse à vérifier</small>' : ''}</td><td><button class="button quiet" type="button" data-open-year="${row.year}">Voir les PDF</button></td></tr>`).join('') : '<tr><td colspan="9" class="table-empty">Aucune année pour ce filtre. Choisissez « Toutes les années » pour ouvrir une année sans inventaire.</td></tr>';
    el('overview-body').querySelectorAll('[data-open-year]').forEach(button => button.addEventListener('click', () => openAnnualDetails(Number(button.dataset.openYear), button.dataset.fileFilter || 'tous')));
  }
  async function loadOverview(force = false) {
    if (overviewRequest) return overviewRequest;
    if (!force && overviewData && Date.now() - overviewFetchedAt < 15000) return;
    overviewRequest = request('/overview').then(data => {
      overviewData = data; overviewFetchedAt = Date.now(); renderOverview();
    }).catch(error => { el('overview-updated').textContent = `Bilan non actualisé : ${error.message}`; throw error; }).finally(() => { overviewRequest = null; });
    return overviewRequest;
  }
  function renderMaintenanceReport(report) {
    if (!report || typeof report !== 'object' || !report.state) return;
    if (maintenanceReport && (Date.parse(maintenanceReport.finished_at || maintenanceReport.started_at) || 0) > (Date.parse(report.finished_at || report.started_at) || 0)) return;
    maintenanceReport = report;
    const modes = { check:'Contrôle', repair:'Réparation', connections:'Restitution globale', deduplicate_announcements:'Nettoyage des annonces dupliquées', merge_duplicates:'Fusion des identités certaines', merge_person:'Fusion manuelle de personne', optimize:'Optimisation', backup:'Sauvegarde', transfer_drive:'Transfert Drive' };
    Object.assign(modes, { reextract_entities:'Titres et liens complétés', clean_parasitic_extractions:'Extractions parasitées corrigées', merge_entities:'Fusion des fiches', undo_merge:'Annulation de fusion' });
    const states = { running:'En cours', done:'Terminé', failed:'Échec', error:'Échec', cancelled:'Arrêté' };
    const issues = report.issues || [];
    const sizes = report.sizes || [];
    const bytes = value => value == null ? '—' : `${count((Number(value) / 1048576).toFixed(1))} Mo`;
    const rows = sizes.map(item => `<li>${esc(item.year)} : ${item.before_bytes != null ? `${bytes(item.before_bytes)} → ${bytes(item.after_bytes)}` : `JSON ${bytes(item.json_bytes)} · SQLite ${bytes(item.sqlite_bytes)}`}</li>`).join('');
    const paths = [...new Set(report.backups || [])].map(path => `<li><code>${esc(path)}</code></li>`).join('');
    const errors = Object.values(report.severity_counts || {}).reduce((sum,value) => sum + Number(value || 0),0);
    const duplicateSummary = report.duplicate_cleanup ? ` ${count(report.duplicate_cleanup.exact_rows_removed || 0)} ligne(s) strictement identique(s) retirée(s) ; ${count(report.duplicate_cleanup.conflicting_ids || 0)} identifiant(s) contradictoire(s) gardé(s) pour vérification.` : '';
    const mergeSummary = report.mode === 'undo_merge' ? ' Fusion annulée ; annonces et preuves conservées.' : report.identity_merge ? ` ${count(report.identity_merge.groups || 0)} groupe(s) ${report.identity_merge.published ? 'réunis dans la restitution' : 'en attente de publication'} (${count(report.identity_merge.occurrences_grouped || 0)} occurrences). Les annonces sources sont conservées.` : '';
    el('maintenance-result').innerHTML = `<p><strong>${esc(modes[report.mode] || 'Maintenance')} · ${esc(states[report.state] || report.state || 'Rapport')}</strong> · ${esc((report.years || []).join(', ') || 'Toutes les années')} · ${esc(date(report.finished_at || report.started_at))}</p><p>${count((report.completed_years || []).length)} année(s) terminée(s) · ${count(errors)} signalement(s) · ${count((report.repairs || []).length)} correction(s).${duplicateSummary}${mergeSummary}${report.moved !== undefined ? ` Transfert : ${count(report.moved)} PDF archivés, ${count(report.skipped || 0)} ignorés, ${count(report.errors || 0)} erreurs.` : ''}</p>${report.remaining_years?.length ? `<p>Années restantes : ${esc(report.remaining_years.join(', '))}.</p>` : ''}${report.error ? `<p class="error">${esc(report.error)}</p>` : ''}${rows ? `<ul>${rows}</ul>` : ''}${paths ? `<details open><summary>Emplacements des sauvegardes locales</summary><ul>${paths}</ul></details>` : ''}${issues.length ? `<details><summary>Voir les signalements (${count(issues.length)})</summary><ul>${issues.slice(0,20).map(issue => `<li>${esc(issue.year || '')} · ${esc(issue.message || issue.code)}</li>`).join('')}</ul><p>Le rapport téléchargé contient le détail complet.</p></details>` : ''}`;
    el('download-report').disabled = false;
    if (report.extraction) {
      const summary = document.createElement('p');
      const parasiteSummary = Number(report.extraction.parasitic_occurrences_removed || 0) ? ` · ${count(report.extraction.parasitic_occurrences_removed)} occurrence(s) parasitée(s) supprimée(s)` : '';
      summary.textContent = `${count(report.extraction.announcements)} annonces réexaminées · ${count(report.extraction.reused)} déjà corrigées · ${count(report.extraction.corrected_titles)} titres corrigés · ${count(report.extraction.added_entities)} entités et ${count(report.extraction.added_relations)} liens ajoutés${parasiteSummary} · ${count(report.extraction.failures)} annonces à reprendre. Les annonces originales sont inchangées.`;
      el('maintenance-result').appendChild(summary);
    }
    const identityReport = `${report.id}:${report.finished_at || ''}:${report.published_revision || ''}`;
    if (['reextract_entities','merge_entities','undo_merge','merge_person','merge_duplicates'].includes(report.mode) && report.state === 'done' && lastIdentityReport !== identityReport) {
      lastIdentityReport = identityReport;
      window.dispatchEvent(new CustomEvent('jort:entities-updated'));
    }
  }
  async function loadMaintenanceReport() {
    const payload = await request('/maintenance-report');
    renderMaintenanceReport(payload.report);
  }
  function updateExportLinks() {
    document.querySelectorAll('.export-link').forEach(link => {
      const url = new URL(`${API}/export`);
      url.searchParams.set('type', link.dataset.exportType);
      const yearControl = link.dataset.yearSelector;
      const exportYear = yearControl ? selectedYear(yearControl) : selectedYear('logs-year');
      url.searchParams.set('year', String(exportYear));
      link.href = url.href;
      link.setAttribute('aria-disabled', String(!exportYear));
      link.tabIndex = exportYear ? 0 : -1;
    });
    el('clear-logs').disabled = !selectedYear('logs-year');
    el('clear-logs').title = selectedYear('logs-year') ? 'Effacer les événements de cette année' : 'Choisissez une année pour effacer son journal';
  }
  function renderEvents(logs) {
    logs = logs.filter(item => logFilter === 'all' || (logFilter === 'error' ? ['error','critical'].includes(String(item.level).toLowerCase()) : ['warning','error','critical'].includes(String(item.level).toLowerCase())));
    const taskLabels = { inventaire:'Inventaire', telechargement:'Téléchargement', indexation_pdf:'Indexation du journal', indexation_journal:'Indexation du journal', indexation_annonce:'Indexation de l’annonce', indexation:'Indexation' };
    const logPageSize = 40;
    const logPageCount = Math.max(1, Math.ceil(logs.length / logPageSize));
    logPage = Math.min(logPage, logPageCount - 1);
    const visibleLogs = logs.slice(-(logPage + 1) * logPageSize, logPage ? -logPage * logPageSize : undefined);
    el('events-log').textContent = logs.length ? visibleLogs.map(item => `${item.time || '—'}  [${String(item.level || 'info').toUpperCase()}]${item.task ? ` [${taskLabels[item.task] || item.task}]` : ''}${item.number ? `  n° ${item.number}` : ''}${item.current && item.total ? `  ${item.current}/${item.total}` : ''}  ${item.message || ''}`).join('\n') : logFilter === 'error' ? 'Aucune erreur dans les événements de cette vue.' : logFilter === 'warning' ? 'Aucune alerte ni erreur dans les événements de cette vue.' : 'Aucun événement pour cette vue.';
    el('events-page').textContent = `Page ${logPage + 1} sur ${logPageCount} · ${count(logs.length)} événements`;
    el('events-prev').disabled = logPage >= logPageCount - 1;
    el('events-next').disabled = logPage === 0;
  }
  async function loadExecutionLog(id, resetPage = true) {
    const selectedLogYear = selectedYear('logs-year');
    const events = id ? (await request(`/run-log?id=${encodeURIComponent(id)}`)).events || [] : null;
    if (el('execution-select').value !== id || selectedYear('logs-year') !== selectedLogYear) return;
    executionEvents = events;
    if (resetPage) logPage = 0;
    renderEvents(executionEvents || statusFor('logs-year')?.logs || []);
  }
  async function refreshRuns() {
    const logYear = selectedYear('logs-year');
    const payload = await request(`/runs${logYear ? `?year=${logYear}` : ''}`);
    if (selectedYear('logs-year') !== logYear) return;
    const select = el('execution-select');
    const selected = select.value;
    const runs = (payload.items || []).filter(run => !logYear || (run.years || []).map(Number).includes(logYear));
    select.innerHTML = `<option value="">${logYear ? 'Journal de l’année choisie' : 'Choisir une exécution'}</option>` + runs.map(run => {
      const action = taskNames[run.action] || run.action || laneTitles[run.lane] || 'Opération';
      const years = run.years || [];
      const scope = years.length > 4 ? `${years[0]} → ${years[years.length-1]} (${years.length} années)` : years.join(', ');
      return `<option value="${esc(run.id)}">${esc(date(run.started_at))} · ${esc(action)} · ${esc(scope)}</option>`;
    }).join('');
    const wanted = pendingLogId || selected || (!logYear ? runs[0]?.id : '');
    if ([...select.options].some(option => option.value === wanted)) select.value = wanted;
    pendingLogId = '';
    await loadExecutionLog(select.value, selected !== select.value);
  }

  function openTaskLog(runId, year) {
    logsYearSelect.value = '0'; storageSet(yearStorageKeys.get(logsYearSelect.id), '0');
    pendingLogId = String(runId || ''); executionEvents = null; logPage = 0;
    if (location.hash === '#logs') activatePanel(); else location.hash = 'logs';
  }

  async function request(path, options = {}) {
    const response = await fetch(`${API}${path}`, options);
    if (!response.ok) {
      let detail = '';
      try { detail = (await response.json()).error || ''; } catch (_) { /* empty */ }
      throw Object.assign(new Error(detail || `Réponse du serveur : ${response.status}`), { httpStatus:response.status });
    }
    return response.json();
  }
  function showError(error) {
    if (error?.httpStatus && error.httpStatus < 500) { setActionFeedback(error.message, true); return; }
    el('api-error').hidden = false;
    el('api-error').querySelector('strong').textContent = error?.httpStatus ? 'Erreur du service local.' : 'API locale indisponible.';
    el('api-error').querySelector('span').textContent = error?.httpStatus ? error.message : `${error?.message || 'Connexion impossible'} Démarrez le serveur local avec demarrer.ps1, puis réessayez.`;
  }
  function clearError() { el('api-error').hidden = true; }
  function uploadSelectionError(files) {
    if (!files.length) return 'Sélectionnez au moins un PDF.';
    if (files.some(file => !file.name.toLowerCase().endsWith('.pdf'))) return 'Seuls les fichiers PDF sont acceptés.';
    const totalSize = files.reduce((sum, file) => sum + file.size, 0);
    if (totalSize > 100 * 1024 * 1024) return 'La taille totale dépasse la limite de 100 Mo.';
    return '';
  }
  function updateUploadControls() {
    const files = [...el('manual-pdfs').files];
    const totalSize = files.reduce((sum, file) => sum + file.size, 0);
    el('upload-selection').textContent = files.length
      ? `${files.length} PDF sélectionné${files.length === 1 ? '' : 's'} · ${(totalSize / (1024 * 1024)).toFixed(1)} Mo sur 100 Mo`
      : 'Aucun fichier sélectionné · 100 Mo maximum au total';
    const error = uploadSelectionError(files);
    const busy = uploadBusy || activeJob(jobLanes(statusData?.job).collection);
    el('manual-pdfs').disabled = busy;
    el('upload-button').disabled = Boolean(error) || busy;
    if (error && files.length) {
      el('upload-feedback').textContent = error;
      el('upload-feedback').className = 'upload-feedback error';
    }
  }
  function localDocumentHref(path) {
    if (!path) return '';
    const value = String(path).replaceAll('\\', '/');
    if (/^https?:\/\//i.test(value)) return value;
    if (value.startsWith('/documents/') || value.startsWith('/sources/') || value.startsWith('/downloads/') || value.startsWith('/drive/')) return value;
    if (/^(documents|sources|downloads|drive)\//i.test(value)) return `/${value}`;
    return '';
  }
  function renderStatus(data) {
    statusData = data;
    statusByYear.set(Number(data.year), data);
    renderJob(data.job);
    const dataYear = Number(data.year);
    const coverage = data.coverage || {};
    if (dataYear === selectedYear('collection-year')) {
      el('coverage-title').textContent = `COUVERTURE ${dataYear}`;
      el('coverage-count').textContent = coverage.expected === null || coverage.expected === undefined
        ? `${count(coverage.available)} disponibilité${coverage.available === 1 ? '' : 's'} déclarée${coverage.available === 1 ? '' : 's'} · à vérifier`
        : `${count(coverage.available)} / ${count(coverage.expected)} journaux déclarés disponibles`;
      el('known-count').textContent = count(coverage.known);
      el('available-count').textContent = count(coverage.available);
      el('indexed-count').textContent = count(coverage.indexed);
      el('inventory-state').textContent = coverage.expected === null || coverage.expected === undefined
        ? 'Inventaire non effectué ou non communiqué'
        : coverage.inventory_complete ? 'Inventaire marqué complet · disponibilité IORT à vérifier' : 'Inventaire à compléter ou confirmer';
    }
    if (dataYear === selectedYear('index-year')) {
      const stats = data.stats || {};
      el('stat-announcements').textContent = count(stats.announcements);
      el('stat-organizations').textContent = count(stats.organizations);
      el('stat-persons').textContent = count(stats.persons);
      el('stat-relations').textContent = count(stats.relations);
      el('stat-pages').textContent = count(stats.pages);
      el('stat-unclassified').textContent = count(stats.unclassified);
    }
    if (dataYear === selectedYear('upload-year')) {
      const folders = data.paths || {};
      const folderDefs = [['inbox', 'Réception des PDF à analyser'], ['archive', 'Originaux vérifiés'], ['quarantine', 'PDF reçus à vérifier']];
      el('folder-cards').innerHTML = folderDefs.map(([key, label]) => {
        const path = folders[key] || 'Chemin non communiqué';
        return `<article class="folder-card"><span class="folder-symbol">▱</span><div><span class="folder-label">${label}</span><code>${esc(path)}</code><p>${key === 'inbox' ? 'Copiez vos PDF ici, puis cliquez sur « Vérifier le dépôt » pour les prendre en charge.' : key === 'archive' ? 'Originaux vérifiés, classés par année.' : 'PDF conservés si le numéro réel ne correspond pas au numéro attendu.'}</p>${key === 'inbox' ? '<button type="button" class="button quiet" id="open-deposit-folder">Ouvrir le dossier</button><button type="button" class="button quiet" id="scan-deposit">Vérifier le dépôt</button>' : ''}</div><button type="button" class="copy-path" data-copy="${esc(path)}" aria-label="Copier le chemin">Copier le chemin</button></article>`;
      }).join('');
      el('open-deposit-folder').addEventListener('click', async () => {
        try { await request('/open-inbox', {method:'POST', headers:{'Content-Type':'application/json','X-JORT-Local':'1'}, body:JSON.stringify({year:selectedYear('upload-year')})}); }
        catch (error) { showError(error); }
      });
      el('scan-deposit').addEventListener('click', () => startJob('scan', null, selectedYear('upload-year')));
      el('folder-cards').querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(button.dataset.copy); button.textContent = 'Copié'; setTimeout(() => { button.textContent = 'Copier'; }, 1200); }
        catch (_) { button.textContent = 'Copie indisponible'; }
      }));
    }
    if (dataYear === selectedYear('files-year')) {
      el('files-title').textContent = `Journaux ${dataYear}`;
      renderFiles(data.files || [], dataYear, coverage);
    }
    if (dataYear === selectedYear('pipeline-year')) renderPipeline(data.files || [], data.job, dataYear);
    if (dataYear === selectedYear('logs-year') && !executionEvents) renderEvents(Array.isArray(data.logs) ? data.logs : []);
  }
  function renderPipeline(files, job, yearValue) {
    const present = files.filter(file => file.status === 'present');
    const indexed = files.filter(file => file.indexed);
    const errors = present.filter(file => !file.indexed && (file.processing_state === 'erreur' || file.processing_error));
    const pending = present.filter(file => !file.indexed && ['a_traiter','en_attente','en_cours'].includes(file.processing_state));
    el('pipeline-downloaded').textContent = count(present.length);
    el('pipeline-indexed').textContent = count(indexed.length);
    el('pipeline-pending').textContent = count(pending.length);
    el('pipeline-errors').textContent = count(errors.length);
    const activity = Object.entries(jobLanes(job)).filter(([name,lane]) => name !== 'maintenance' && Number(lane.current_year || lane.year) === yearValue && activeJob(lane)).map(([name,lane]) => `${laneTitles[name]} : ${lane.message || 'En cours'}`);
    el('pipeline-activity').textContent = activity.length ? activity.join(' · ') : `PDF présents et état de l’analyse pour ${yearValue}.`;
    const analysisFilter = el('pipeline-filter').value;
    const recent = present.filter(file => {
      if (file.indexed) return false;
      if (analysisFilter === 'pending') return ['a_traiter','en_attente','en_cours'].includes(file.processing_state);
      if (analysisFilter === 'error') return file.processing_state === 'erreur' || Boolean(file.processing_error || file.error);
      if (analysisFilter === 'verify') return file.processing_state === 'a_verifier';
      return true;
    }).sort((a,b) => (Date.parse(b.processing_updated_at || '') || 0) - (Date.parse(a.processing_updated_at || '') || 0) || Number(b.number || 0) - Number(a.number || 0));
    if (!recent.length) {
      el('pipeline-body').innerHTML = '<tr><td colspan="6" class="table-empty">Aucun PDF à afficher pour ce filtre.</td></tr>';
      pipelinePage = 0;
      el('pipeline-page').textContent = 'Page 1 sur 1 · 0 journaux';
      el('pipeline-prev').disabled = true;
      el('pipeline-next').disabled = true;
      return;
    }
    const pageCount = Math.max(1, Math.ceil(recent.length / 25));
    pipelinePage = Math.min(pipelinePage, pageCount - 1);
    el('pipeline-page').textContent = `Page ${pipelinePage + 1} sur ${pageCount} · ${count(recent.length)} journaux`;
    el('pipeline-prev').disabled = pipelinePage === 0;
    el('pipeline-next').disabled = pipelinePage >= pageCount - 1;
    el('pipeline-body').innerHTML = recent.slice(pipelinePage * 25, (pipelinePage + 1) * 25).map(file => {
      const href = localDocumentHref(file.path);
      const filename = href ? `<a class="text-link" href="${esc(href)}" target="_blank" rel="noopener">${esc(file.filename || 'Ouvrir le PDF')}</a>` : esc(file.filename || file.path || '—');
      const detail = file.processing_error || file.error || (file.processing_state === 'a_verifier' ? 'Extraction terminée sans annonce détectée ; vérifier le document.' : (file.processing_updated_at ? `Mis à jour ${date(file.processing_updated_at)}` : 'En attente de l’analyse'));
      const stateClass = file.processing_state === 'erreur' ? 'state-invalid' : 'state-unknown';
      const canReanalyze = Number(file.number) > 0;
      const reanalyze = canReanalyze ? `<button type="button" class="button quiet reanalyze-pdf" data-year="${esc(file.year || yearValue)}" data-number="${esc(file.number)}" ${activeJob(jobLanes(job).indexation) ? 'disabled' : ''}>Relancer l’analyse</button>` : '—';
      return `<tr><td>n° ${esc(file.number ?? '—')}</td><td>${esc(date(file.date))}</td><td>${filename}</td><td><span class="file-state ${stateClass}">${esc(processingLabels[file.processing_state] || 'À traiter')}</span></td><td>${esc(detail)}</td><td>${reanalyze}</td></tr>`;
    }).join('');
    el('pipeline-body').querySelectorAll('.reanalyze-pdf').forEach(button => button.addEventListener('click', () => startJob('index_one', Number(button.dataset.number), Number(button.dataset.year))));
  }
  function renderJob(job) {
    el('monitor-updated').textContent='Dernier relevé : '+date(new Date().toISOString());
    const status = el('job-status');
    const lanes = jobLanes(job);
    renderInitialBatch(job, lanes);
    if (lanes.maintenance?.report) renderMaintenanceReport({ ...lanes.maintenance.report, mode:lanes.maintenance.report.mode || String(lanes.maintenance.action || '').replace('maintenance_', ''), years:lanes.maintenance.report.years || lanes.maintenance.years, started_at:lanes.maintenance.report.started_at || lanes.maintenance.started_at, finished_at:lanes.maintenance.report.finished_at || lanes.maintenance.finished_at });
    const active = laneNames.filter(name => activeJob(lanes[name]));
    const busy = active.length > 0;
    const publishing = ['pending', 'publishing'].includes(job?.publication?.state);
    const description = [busy ? active.map(name => `${laneTitles[name]} en cours`).join(' · ') : '', publishing ? `Restitution en cours de mise à jour${job.publication.error ? ' · reprise automatique prévue' : ''}` : ''].filter(Boolean).join(' · ') || 'Aucun traitement actif.';
    status.innerHTML = `<span class="status-dot ${busy || publishing ? 'busy' : job?.state === 'failed' ? 'failed' : ''}"></span><span>${esc(description)}</span>`;
    const stateLabels = { running:'En cours', stopping:'Arrêt demandé', paused:'En pause', done:'Terminé', failed:'Échec', cancelled:'Arrêté' };
    const cards = {};
    laneNames.forEach(name => {
      const lane = lanes[name] || {};
      const laneBusy = activeJob(lane);
      let current = Number(lane.current || lane.completed || 0);
      let total = Number(lane.total || lane.download_total || 0);
      let progress = total ? `${count(current)} / ${count(total)}${name === 'maintenance' ? ' années' : ' journaux'}` : '';
      let percent = total ? Math.min(100, Math.round(current * 100 / total)) : 0;
      if (name === 'indexation' && Number(lane.total_journals) > 0) {
        total = Number(lane.total_journals);
        const completedFiles = Number(lane.completed_journals || 0);
        const activeFraction = laneBusy && lane.current_number && Number(lane.total) > 0
          ? Math.min(0.99, Math.max(0, Number(lane.current || 0) / Number(lane.total))) : 0;
        current = Math.min(total, completedFiles + activeFraction);
        progress = `${count(completedFiles)} / ${count(total)} PDF indexés${activeFraction ? ` · journal n° ${esc(lane.current_number)} : ${count(lane.current || 0)}/${count(lane.total)} annonces` : ''}`;
        percent = Math.min(100, Math.round(current * 100 / total));
      }
      const elapsed = Date.now() - (Date.parse(lane.started_at || '') || Date.now());
      // L’indexation mesure la file complète. Son estimation se base sur les
      // PDF terminés depuis le démarrage de la file, plus la fraction du PDF actif.
      const eta = laneBusy && current > 0 && total > current && elapsed > 1000
        ? ` · env. ${Math.ceil((elapsed / current * (total - current)) / 60000)} min restantes` : '';
      // Pendant l’exécution, l’année du PDF actif prévaut sur les années
      // demandées lors d’une ancienne indexation ou d’un batch plus large.
      const year = laneBusy
        ? (lane.current_year || lane.year || (lane.years?.length === 1 ? lane.years[0] : ''))
        : (lane.current_year || lane.year || (lane.years?.length ? lane.years[0] : ''));
      const detail = [stateLabels[lane.state] || 'En attente', year, progress, lane.current_number ? `JORT n° ${lane.current_number}` : '', lane.message, lane.error].filter(Boolean).join(' · ');
      cards[name] = `<article class="lane-card" data-lane="${name}" data-state="${esc(lane.state||'idle')}"><div><strong>${laneTitles[name]}${year ? ` · ${esc(year)}` : ''}</strong><span>${esc(detail)}</span>${laneBusy ? `<div class="progress-meta"><b>${total?percent+' %':'Préparation du total…'}</b><small>${esc(eta.trim().replace(/^·\s*/,''))||'Estimation après les premiers fichiers'}</small></div><progress max="100" ${total?`value="${percent}"`:''} aria-label="Avancement ${laneTitles[name]}">${percent}%</progress>` : ''}</div><div class="lane-actions"><button class="button quiet" type="button" data-task-log="${esc(lane.id || '')}">Voir les logs</button>${laneBusy ? `<button class="button danger lane-stop" type="button" data-stop-lane="${name}" ${lane.state !== 'stopping' ? '' : 'disabled'}>Arrêter proprement</button>` : ''}</div></article>`;
    });
    el('lane-monitor').innerHTML = active.map(name => cards[name]).join('');
    el('lane-monitor').hidden = !active.length;
    const recent = laneNames.filter(name => !activeJob(lanes[name]) && lanes[name]?.id);
    el('recent-lanes').innerHTML = recent.map(name => cards[name]).join('');
    el('last-tasks').hidden = !recent.length;
    document.querySelectorAll('[data-task-log]').forEach(button => button.addEventListener('click', () => openTaskLog(button.dataset.taskLog)));
    el('lane-monitor').querySelectorAll('[data-stop-lane]').forEach(button => button.addEventListener('click', () => stopJob(button.dataset.stopLane)));
    document.querySelectorAll('.job-action').forEach(button => {
      const laneName = laneForAction(button.dataset.action);
      button.disabled = activeJob(lanes[laneName]) || (uploadBusy && laneName === 'collection');
    });
    el('job-limit').disabled = activeJob(lanes.collection) || uploadBusy;
    el('index-limit').disabled = activeJob(lanes.indexation);
    el('maintenance-scope').disabled = activeJob(lanes.maintenance);
    el('backup-scope').disabled = activeJob(lanes.maintenance);
    maintenanceYearSelect.disabled = activeJob(lanes.maintenance) || el('maintenance-scope').value === 'all';
    backupYearSelect.disabled = activeJob(lanes.maintenance) || el('backup-scope').value === 'all';
    el('stop-job').disabled = !busy;
    yearSelect.disabled = false;
    indexYearSelect.disabled = false;
    uploadYearSelect.disabled = uploadBusy;
    updateUploadControls();
    announceFinishedJob(job);
    const stateKey = laneNames.map(name => `${name}:${lanes[name]?.id || ''}:${lanes[name]?.state || ''}`).join('|') + `:${job?.action || ''}:${job?.state || ''}`;
    if (stateKey !== lastJobState) {
      lastJobState = stateKey;
      if (!pollingEnabled) startPolling();
    }
  }
  function renderFiles(files, yearValue, coverage = {}) {
    const filter = el('file-filter').value;
    const present = files.filter(file => file.status === 'present');
    const missing = files.filter(file => file.status === 'missing');
    const drivePresent = present.filter(file => String(file.path || '').replaceAll('\\', '/').startsWith('drive/'));
    const localPresent = present.length - drivePresent.length;
    const indexed = files.filter(file => file.indexed);
    const toAnalyze = present.filter(file => !file.indexed && ['a_traiter','en_attente','en_cours'].includes(file.processing_state));
    const errors = files.filter(file => file.processing_state === 'erreur' || file.processing_error || file.error);
    const unverified = files.filter(file => ['unknown','drive_unavailable','conflict','invalid'].includes(file.status) || file.processing_state === 'a_verifier');
    const attention = [...new Set([...errors, ...unverified])];
    el('file-count-expected').textContent = count(coverage.expected);
    el('file-count-present').textContent = count(present.length);
    el('file-count-local').textContent = count(localPresent);
    el('file-count-drive').textContent = count(drivePresent.length);
    el('file-count-missing').textContent = count(missing.length);
    el('file-count-pending').textContent = count(toAnalyze.length);
    el('file-count-indexed').textContent = count(indexed.length);
    el('file-count-errors').textContent = count(attention.length);
    const filtered = filter === 'tous' ? files
      : filter === 'todo' ? present.filter(file => !file.indexed)
      : filter === 'indexed' ? indexed
      : filter === 'errors' ? attention
      : files.filter(file => file.status === filter);
    const isComplete = Boolean(coverage.inventory_complete);
    el('file-summary').textContent = `${count(filtered.length)} ligne(s) affichée(s) pour ${yearValue}. ${count(missing.length)} PDF absent(s) parmi les numéros inventoriés${isComplete ? '' : ' ; inventaire annuel incomplet, le total des absents peut augmenter'}.`;
    if (!filtered.length) {
      const message = files.length ? 'Aucun journal pour ce filtre.' : 'Aucun numéro encore inventorié pour cette année.';
      el('files-body').innerHTML = `<tr><td colspan="6" class="table-empty">${message}</td></tr>`; return;
    }
    el('files-body').innerHTML = filtered.map(file => {
      const href = localDocumentHref(file.path);
      const source = file.source_url ? `<a class="text-link" href="${esc(file.source_url)}" target="_blank" rel="noopener">Source IORT ↗</a>` : '';
      const onDrive = String(file.path || '').replaceAll('\\','/').startsWith('drive/');
      const local = href && file.status === 'present' ? `<a class="text-link" href="${esc(href)}" target="_blank" rel="noopener">Ouvrir le PDF${onDrive ? ' · Drive' : ''} ↗</a>` : '';
      const retryDownload = file.status === 'missing' && file.date ? `<button type="button" class="button quiet retry-download" data-year="${esc(file.year || yearValue)}" data-number="${esc(file.number)}" ${activeJob(jobLanes(statusData?.job).collection) ? 'disabled' : ''}>Télécharger</button>` : '';
      const retryIndex = file.status === 'present' && !file.indexed ? `<button type="button" class="button quiet retry-index" data-year="${esc(file.year || yearValue)}" data-number="${esc(file.number)}" ${activeJob(jobLanes(statusData?.job).indexation) ? 'disabled' : ''}>Analyser</button>` : '';
      const analysisLabel = file.indexed ? 'Indexé' : file.indexation_current ? 'Analysé · aucune annonce' : file.status !== 'present' ? '—' : (processingLabels[file.processing_state] || 'À analyser');
      return `<tr><td>${esc(file.year || yearValue)} · ${esc(file.number ?? '—')}</td><td>${esc(date(file.date))}</td><td>${esc(file.filename || 'Nom non communiqué')}</td><td><span class="file-state state-${esc(file.status)}">${esc(stateLabels[file.status] || 'À vérifier')}</span>${file.status === 'present' ? `<small class="storage-split">${onDrive ? 'Google Drive' : 'Sur cet ordinateur'}</small>` : ''}</td><td><span class="file-analysis ${file.indexed ? 'is-done' : file.processing_state === 'erreur' ? 'is-error' : ''}">${esc(analysisLabel)}</span>${file.processing_error || file.error ? `<small class="storage-split">${esc(file.processing_error || file.error)}</small>` : ''}</td><td class="document-links">${local}${source}${retryDownload}${retryIndex}</td></tr>`;
    }).join('');
    el('files-body').querySelectorAll('.retry-download').forEach(button => button.addEventListener('click', () => startJob('download_one', Number(button.dataset.number), Number(button.dataset.year))));
    el('files-body').querySelectorAll('.retry-index').forEach(button => button.addEventListener('click', () => startJob('index_one', Number(button.dataset.number), Number(button.dataset.year), button)));
  }
  function renderTickets(payload) {
    el('ticket-list').querySelectorAll('.ticket').forEach(ticket => {
      const id = ticket.dataset.ticket;
      if (!ticket.contains(document.activeElement)) ticketDrafts.set(id, { state:ticket.querySelector('.review-state')?.value || '', note:ticket.querySelector('.review-note')?.value || '' });
    });
    const items = payload.items || [];
    ticketTotal = Number(payload.total || 0);
    const pageNumber = Math.floor(ticketOffset / 30) + 1;
    const pages = Math.max(1, Math.ceil(ticketTotal / 30));
    el('ticket-summary').textContent = `${count(ticketTotal)} intervention${ticketTotal === 1 ? '' : 's'} exceptionnelle${ticketTotal === 1 ? '' : 's'} · page ${pageNumber} sur ${pages}`;
    el('ticket-page').textContent = `Page ${pageNumber} sur ${pages}`;
    el('ticket-prev').disabled = ticketOffset === 0;
    el('ticket-next').disabled = ticketOffset + 30 >= ticketTotal;
    if (!items.length) { el('ticket-list').innerHTML = '<div class="table-empty">Aucun ticket pour ces filtres.</div>'; return; }
    el('ticket-list').innerHTML = items.map(ticket => {
      const pdfHref = localDocumentHref(ticket.source_pdf);
      const sourceHref = ticket.source_url ? `<a class="text-link" href="${esc(ticket.source_url)}" target="_blank" rel="noopener">Source IORT ↗</a>` : '';
      const pdfLink = pdfHref ? `<a class="text-link" href="${esc(pdfHref)}" target="_blank" rel="noopener">PDF local ↗</a>` : (ticket.source_pdf ? `<span class="muted-path" title="${esc(ticket.source_pdf)}">${esc(ticket.source_pdf)}</span>` : '');
      const pageText = Array.isArray(ticket.pages) && ticket.pages.length ? `Pages ${ticket.pages.join(', ')}` : '';
      const identity = ticket.kind === 'identite';
      const candidates = identity ? `<div class="identity-candidates"><strong>CIN se terminant par ${esc(ticket.candidate_cin_suffix || '??')}</strong>${(ticket.candidates || []).map(candidate => {
        const evidenceRows = candidate.occurrences?.length ? candidate.occurrences : [candidate];
        const evidence = evidenceRows.map(row => {
          const candidatePdf = localDocumentHref(row.source_pdf);
          const candidateSource = row.source_url ? `<a class="text-link" href="${esc(row.source_url)}" target="_blank" rel="noopener">Source IORT ↗</a>` : '';
          return `<div class="identity-evidence"><span class="ticket-meta">${esc(row.reference || `Journal n° ${row.number ?? '—'}`)} · pages ${esc((row.pages || []).join(', ') || 'non précisées')}</span>${row.quote ? `<blockquote>« ${esc(row.quote)} »</blockquote>` : '<span class="ticket-meta">Aucune citation de rôle disponible</span>'}<span class="ticket-links">${candidatePdf ? `<a class="text-link" href="${esc(candidatePdf)}" target="_blank" rel="noopener">PDF local ↗</a>` : ''}${candidateSource}</span></div>`;
        }).join('');
        const evidenceContent = evidenceRows.length > 1 ? `<details><summary>${count(evidenceRows.length)} annonces sources conservées</summary>${evidence}</details>` : evidence;
        return `<div class="identity-candidate"><b>${esc(candidate.name || 'Personne sans nom relevé')}</b>${evidenceContent}</div>`;
      }).join('')}</div>` : '';
      const options = identity ? '<option value="valide">Confirmer après vérification des sources</option><option value="a_corriger">Écarter le rapprochement</option>' : '<option value="valide">Valider ce cas majeur</option><option value="a_corriger">À corriger</option><option value="resolu">Résolu</option>';
      const draft = ticketDrafts.get(String(ticket.id));
      const feedback = reviewFeedbacks.get(String(ticket.id));
      return `<article class="ticket" data-ticket="${esc(ticket.id)}"><div class="ticket-head"><span class="ticket-kind kind-${esc(ticket.kind)}">${esc(ticket.kind)}</span><span class="severity severity-${esc(ticket.severity)}">${esc(ticket.severity || 'normal')}</span><span class="ticket-state">${esc(ticket.state)}${ticket.application_state === 'pending' ? ' · application en attente' : ''}</span><time>${esc(date(ticket.updated_at))}</time></div><div class="ticket-body"><div><strong>${esc(ticket.reference || `Journal n° ${ticket.number ?? '—'}`)}</strong><span class="ticket-meta">JORT n° ${esc(ticket.number ?? '—')} · ${esc(pageText || 'Page non précisée')}</span><p>${esc(ticket.message || '')}</p><div class="ticket-links">${sourceHref}${pdfLink}</div>${candidates}</div><div class="review-form"><label>Intervention<select class="review-state"><option value="" disabled>Choisir une action</option>${options}</select></label><label>Note<textarea class="review-note" rows="2" placeholder="Note obligatoire ; précisez les preuves consultées">${esc(ticket.note || '')}</textarea></label><button class="button primary review-save" type="button" data-id="${esc(ticket.id)}">Enregistrer l’intervention</button><span class="review-feedback" aria-live="polite">${esc(feedback?.text || '')}</span></div></div></article>`;
    }).join('');
    el('ticket-list').querySelectorAll('.ticket').forEach(ticket => {
      const id = ticket.dataset.ticket;
      const draft = ticketDrafts.get(id);
      if (draft) { ticket.querySelector('.review-state').value = draft.state; ticket.querySelector('.review-note').value = draft.note; }
      const feedback = ticket.querySelector('.review-feedback');
      if (reviewFeedbacks.has(id)) feedback.className = `review-feedback ${reviewFeedbacks.get(id).className || ''}`;
    });
    el('ticket-list').querySelectorAll('.review-save').forEach(button => button.addEventListener('click', () => saveReview(button)));
  }
  async function saveReview(button) {
    const ticket = button.closest('.ticket');
    const state = ticket.querySelector('.review-state').value;
    const note = ticket.querySelector('.review-note').value.trim();
    const feedback = ticket.querySelector('.review-feedback');
    if (!state) { feedback.textContent = 'Choisissez une intervention.'; feedback.className = 'review-feedback error'; return; }
    if (state === 'valide' && !note) { feedback.textContent = 'Une note est requise pour valider ce cas exceptionnel.'; feedback.className = 'review-feedback error'; return; }
    button.disabled = true; feedback.textContent = 'Enregistrement…';
    try {
      const result = await request('/review', { method:'POST', headers:{ 'Content-Type':'application/json', 'X-JORT-Local':'1' }, body:JSON.stringify({ id:button.dataset.id, state, note, year:selectedYear('ticket-year') }) });
      const message = result.application === 'pending' ? 'Intervention enregistrée · application en attente du traitement actif.' : 'Intervention enregistrée et appliquée.';
      reviewFeedbacks.set(String(button.dataset.id), { text:message, className:'success' });
      ticketDrafts.set(String(button.dataset.id), { state:'', note:'' });
      ticket.querySelector('.review-state').value = '';
      ticket.querySelector('.review-note').value = '';
      feedback.textContent = message; feedback.className = 'review-feedback success';
      await loadTickets();
    } catch (error) { feedback.textContent = error.message; feedback.className = 'review-feedback error'; button.disabled = false; }
  }
  async function loadStatus(requestYear = null) {
    if (requestYear === null && activePanel === 'overview') {
      const response = await fetch('http://127.0.0.1:8765/api/health');
      if (!response.ok) throw new Error(`État du serveur indisponible : ${response.status}`);
      const health = await response.json();
      const liveJob = { id:health.job_id, lanes:health.lanes || {}, queue:health.queue || {}, publication:health.publication || {} };
      statusData = { ...(statusData || {}), job:liveJob };
      renderJob(liveJob); clearError();
      await loadOverview();
      return;
    }
    const years = [...new Set(requestYear === null ? selectedStatusYears() : [Number(requestYear)])];
    const payloads = await Promise.all(years.map(value => request(`/status?year=${value}`)));
    clearError(); payloads.forEach(renderStatus);
  }
  async function loadTickets() {
    const requestSequence = ++ticketRequestSequence;
    const params = new URLSearchParams({ year:String(selectedYear('ticket-year')), kind:el('ticket-kind').value, state:el('ticket-state').value, offset:String(ticketOffset), limit:'30' });
    const payload = await request(`/tickets?${params}`);
    if (requestSequence !== ticketRequestSequence) return;
    clearError();
    if (!el('ticket-list').contains(document.activeElement)) renderTickets(payload);
  }
  async function refreshAll() {
    el('refresh').disabled = true;
    try { await Promise.all([loadStatus(), ...(activePanel === 'interventions' ? [loadTickets()] : []), ...(activePanel === 'logs' ? [refreshRuns()] : []), ...(activePanel === 'maintenance' ? [loadMaintenanceReport()] : [])]); }
    catch (error) { showError(error); }
    finally { el('refresh').disabled = false; }
  }
  function startPolling() {
    stopPolling();
    pollingEnabled = true;
    const poll = async () => {
      if (!pollingEnabled) return;
      if (pollInFlight) return;
      pollInFlight = true;
      try {
        await loadStatus();
        if (activePanel === 'logs') await refreshRuns();
        pollErrors = 0;
        if (activePanel === 'interventions') await loadTickets();
      } catch (error) { pollErrors += 1; showError(error); }
      finally {
        pollInFlight = false;
        const active = anyLaneActive(statusData?.job);
        const normalDelay = active ? 3000 : 15000;
        const delay = document.hidden ? 30000 : Math.min(normalDelay * (2 ** Math.min(pollErrors, 4)), 60000);
        if (pollingEnabled) pollTimer = setTimeout(poll, delay);
      }
    };
    pollTimer = setTimeout(poll, 0);
  }
  function stopPolling() { pollingEnabled = false; if (pollTimer) clearTimeout(pollTimer); pollTimer = null; }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && pollingEnabled) {
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = setTimeout(() => {
        if (!pollingEnabled) return;
        refreshAll().catch(showError).finally(() => { if (pollingEnabled) startPolling(); });
      }, 0);
    }
  });
  function initialBatchYears() {
    const currentYear = new Date().getFullYear();
    return Array.from({ length: currentYear - 1956 + 1 }, (_, index) => currentYear - index);
  }
  function renderInitialBatch(job, lanes = jobLanes(job)) {
    const collection = lanes.collection || {};
    const indexation = lanes.indexation || {};
    const years = initialBatchYears();
    const samePlan = collection.action === 'download' && collection.plan_id
      && Array.isArray(collection.years)
      && collection.years.map(Number).join(',') === years.join(',');
    const resumable = samePlan && ['cancelled', 'paused', 'failed', 'stopping'].includes(collection.state);
    const busy = activeJob(collection) || activeJob(indexation);
    const start = el('initial-batch-start');
    const stop = el('initial-batch-stop');
    if (!start || !stop) return;
    start.textContent = resumable ? 'Reprendre au point sauvegardé' : 'Lancer le rattrapage initial';
    start.disabled = activeJob(collection) || (samePlan && collection.state === 'done' && activeJob(indexation)) || uploadBusy;
    stop.disabled = !busy;
    const queue = job?.queue || {};
    const cursor = collection.current_year
      ? `Collecte : ${collection.current_year}${collection.current_number ? ` · JORT n° ${collection.current_number}` : ''}`
      : 'Collecte : en attente';
    const analysis = indexation.current_year
      ? `Analyse : ${indexation.current_year}${indexation.current_number ? ` · JORT n° ${indexation.current_number}` : ''}`
      : 'Analyse : en attente';
    const queueText = `${count(queue.pending || 0)} en attente · ${count(queue.active || 0)} actif(s) · ${count(queue.completed || 0)} terminé(s) · ${count(queue.error || 0)} erreur(s)`;
    const stateText = resumable ? 'Batch arrêté : reprise possible.'
      : samePlan && collection.state === 'done' && activeJob(indexation) ? 'Téléchargements terminés ; analyse de la file en cours.'
      : samePlan && collection.state === 'done' ? 'Rattrapage terminé.'
      : samePlan && busy ? 'Batch en cours.'
      : samePlan ? `Dernier état : ${collection.state || 'arrêté'}.`
      : 'Aucun rattrapage initial en cours.';
    el('initial-batch-checkpoint').textContent = `${stateText} ${cursor} · ${analysis} · ${queueText}. Le serveur sauvegarde l’état après chaque journal.`;
  }
  async function runInitialBatch() {
    primeTaskAudio();
    const button = el('initial-batch-start');
    button.disabled = true;
    try {
      const years = initialBatchYears();
      const collection = jobLanes(statusData?.job).collection || {};
      const samePlan = collection.action === 'download' && collection.plan_id
        && Array.isArray(collection.years)
        && collection.years.map(Number).join(',') === years.join(',')
        && ['cancelled', 'paused', 'failed', 'stopping'].includes(collection.state);
      const planId = samePlan ? collection.plan_id : `initial-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const response = await request('/job', {
        method:'POST', headers:{ 'Content-Type':'application/json', 'X-JORT-Local':'1' },
        body:JSON.stringify({ action:'download', years, limit:0, plan_id:planId })
      });
      rememberTaskAudio(response.job);
      setActionFeedback(samePlan ? 'Reprise du rattrapage au point sauvegardé.' : `Rattrapage initial lancé : ${years[0]} → 1956.`);
      await loadStatus(selectedYear('collection-year'));
    } catch (error) { showError(error); }
    finally { button.disabled = false; }
  }
  async function stopInitialBatch() {
    const button = el('initial-batch-stop');
    button.disabled = true;
    try {
      for (const lane of ['collection', 'indexation']) {
        await request('/stop', { method:'POST', headers:{ 'Content-Type':'application/json', 'X-JORT-Local':'1' }, body:JSON.stringify({ lane }) });
      }
      setActionFeedback('Arrêt demandé. Les numéros en attente et le point de reprise sont conservés.');
      await loadStatus(selectedYear('collection-year'));
    } catch (error) { showError(error); }
    finally { button.disabled = false; }
  }

  async function startJob(action, number = null, selectedYearValue = null, sourceButton = null, identityOptions = null) {
    primeTaskAudio();
    const actionYear = selectedYearValue || (sourceButton?.dataset.yearSelector ? selectedYear(sourceButton.dataset.yearSelector) : null) || (['index','index_one'].includes(action) ? selectedYear('index-year') : action.startsWith('maintenance_') ? selectedYear('maintenance-year') : action === 'download_one' ? selectedYear('files-year') : selectedYear('collection-year'));
    const limit = action === 'download' ? Number(el('job-limit').value) : action === 'index' ? Number(el('index-limit').value) : 0;
    const button = sourceButton || (action === 'download_one' ? el('files-body').querySelector(`[data-number="${number}"]`) : action === 'index_one' ? el('pipeline-body').querySelector(`[data-number="${number}"]`) : [...document.querySelectorAll('.job-action')].find(item => item.dataset.action === action));
    if (button) button.disabled = true;
    let maintenanceYears = undefined;
    let maintenanceScopeLabel = '';
    let maintenanceScope = null;
    let identityEntityIds = null;
    let identityName = null;
    if (action.startsWith('maintenance_')) {
      const scope = sourceButton?.dataset.maintenanceScope || el(sourceButton?.dataset.scopeSelector || (action === 'maintenance_backup' || action === 'maintenance_transfer_drive' ? 'backup-scope' : 'maintenance-scope')).value;
      maintenanceScope = scope;
      maintenanceYears = scope === 'year' ? [actionYear] : undefined;
      maintenanceScopeLabel = maintenanceYears ? `Année ${actionYear}` : 'toute la base';
      if (action === 'maintenance_merge_person') {
        identityEntityIds = String(sourceButton?.dataset.entityIds || '').split('|').filter(Boolean);
        identityName = sourceButton?.dataset.personName || '';
      }
    }
    try {
      const payload = await request('/job', { method:'POST', headers:{ 'Content-Type':'application/json', 'X-JORT-Local':'1' }, body:JSON.stringify({ action, limit, year:actionYear, ...(number !== null ? { number } : {}), ...(maintenanceYears ? { years:maintenanceYears } : {}), ...(maintenanceScope ? { maintenance_scope:maintenanceScope } : {}), ...(identityOptions ? { identity_options:identityOptions } : {}), ...(identityEntityIds ? { entity_ids:identityEntityIds, person_name:identityName } : {}) }) });
      rememberTaskAudio(payload.job);
      setActionFeedback(`Action prise en charge : ${button?.textContent.trim() || action}${maintenanceScopeLabel ? ` · ${maintenanceScopeLabel}` : ` · ${actionYear}`}. Suivez sa progression ci-dessus.`);
      await loadStatus(actionYear);
      return true;
    } catch (error) { showError(error); if (button) button.disabled = false; return false; }
  }
  async function stopJob(lane = 'all') {
    const button = lane === 'all' ? el('stop-job') : el('lane-monitor').querySelector(`[data-stop-lane="${lane}"]`);
    if (button) button.disabled = true;
    try { await request('/stop', { method:'POST', headers:{ 'Content-Type':'application/json', 'X-JORT-Local':'1' }, body:JSON.stringify({ lane }) }); await loadStatus(); }
    catch (error) { showError(error); if (button) button.disabled = false; }
  }
  async function uploadPdfs() {
    const input = el('manual-pdfs');
    const files = [...input.files];
    const validationError = uploadSelectionError(files);
    if (validationError) { el('upload-feedback').textContent = validationError; el('upload-feedback').className = 'upload-feedback error'; return; }
    primeTaskAudio();
    uploadBusy = true;
    renderJob(statusData?.job);
    el('upload-feedback').textContent = 'Envoi des PDF au dossier de dépôt…';
    el('upload-feedback').className = 'upload-feedback';
    let payload;
    try {
      const form = new FormData();
      files.forEach(file => form.append('files', file, file.name));
      payload = await request(`/upload?year=${selectedYear('upload-year')}`, { method:'POST', headers:{ 'X-JORT-Local':'1' }, body:form });
    } catch (error) {
      uploadBusy = false;
      renderJob(statusData?.job);
      el('upload-feedback').textContent = `Dépôt impossible : ${error.message}`;
      el('upload-feedback').className = 'upload-feedback error';
      return;
    }
    const received = Array.isArray(payload.files) ? payload.files.length : files.length;
    rememberTaskAudio(payload.job);
    el('upload-feedback').textContent = `${received} PDF déposé${received === 1 ? '' : 's'}. Vérification et analyse en cours.`;
    el('upload-feedback').className = 'upload-feedback success';
    input.value = '';
    uploadBusy = false;
    renderJob(payload.job || statusData?.job);
    try { await Promise.all([loadStatus(), ...(activePanel === 'interventions' ? [loadTickets()] : []), ...(activePanel === 'logs' ? [refreshRuns()] : []), ...(activePanel === 'maintenance' ? [loadMaintenanceReport()] : [])]); }
    catch (error) { showError(error); el('upload-feedback').textContent = `${received} PDF déposé${received === 1 ? '' : 's'} ; l’actualisation du suivi a échoué.`; }
  }

  el('refresh').addEventListener('click', refreshAll);
  el('refresh-overview').addEventListener('click', async () => {
    const button = el('refresh-overview'); button.disabled = true;
    try { await Promise.all([loadStatus(), loadOverview(true)]); }
    catch (error) { showError(error); }
    finally { button.disabled = false; }
  });
  el('overview-filter').addEventListener('change', renderOverview);
  el('overview-search').addEventListener('input', renderOverview);
  document.querySelectorAll('[data-pdf-tab]').forEach(button => {
    button.addEventListener('click', () => setPdfAction(button.dataset.pdfTab));
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const tabs = [...document.querySelectorAll('[data-pdf-tab]')];
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : (tabs.indexOf(button)+(event.key === 'ArrowRight' ? 1 : -1)+tabs.length)%tabs.length;
      setPdfAction(tabs[index].dataset.pdfTab); tabs[index].focus();
    });
  });
  document.querySelectorAll('[data-log-filter]').forEach(button => button.addEventListener('click', () => {
    logFilter = button.dataset.logFilter; logPage = 0;
    document.querySelectorAll('[data-log-filter]').forEach(item => { const selected = item === button; item.classList.toggle('is-active', selected); item.setAttribute('aria-pressed', String(selected)); });
    renderEvents(executionEvents || statusFor('logs-year')?.logs || []);
  }));
  el('download-run-log').addEventListener('click', () => {
    const events = executionEvents || statusFor('logs-year')?.logs || [];
    const blob = new Blob([events.map(item => `${item.time || ''} [${String(item.level || 'info').toUpperCase()}] ${item.year || ''} ${item.number ? `n° ${item.number}` : ''} ${item.message || ''}`).join('\n')], { type:'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob); const link = document.createElement('a');
    link.href = url; link.download = `journal-jort-${el('execution-select').value || selectedYear('logs-year') || 'recent'}.txt`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  el('refresh-files').addEventListener('click', async () => { try { await loadStatus(selectedYear('files-year')); } catch (error) { showError(error); } });
  el('refresh-pipeline').addEventListener('click', async () => { try { await loadStatus(selectedYear('pipeline-year')); } catch (error) { showError(error); } });
  el('refresh-events').addEventListener('click', async () => { try { await loadStatus(selectedYear('logs-year')); if (el('execution-select').value) await loadExecutionLog(el('execution-select').value); } catch (error) { showError(error); } });
  el('refresh-runs').addEventListener('click', () => refreshRuns().catch(showError));
  el('execution-select').addEventListener('change', () => loadExecutionLog(el('execution-select').value).catch(showError));
  el('clear-logs').addEventListener('click', async () => {
    if (!window.confirm(`Effacer les journaux de traitement de ${selectedYear('logs-year')} ? Les PDF et interventions seront conservés.`)) return;
    const button = el('clear-logs');
    const feedback = el('clear-logs-feedback');
    button.disabled = true; feedback.textContent = 'Effacement…'; feedback.className = 'upload-feedback';
    try {
      const result = await request('/clear-logs', { method:'POST', headers:{ 'Content-Type':'application/json', 'X-JORT-Local':'1' }, body:JSON.stringify({ year:selectedYear('logs-year') }) });
      feedback.textContent = `${count(result.events_removed)} événement(s) effacé(s). ${result.processing_cleared ? 'État du dernier traitement effacé.' : 'Aucun état de traitement de cette année à effacer.'}`;
      feedback.className = 'upload-feedback success';
      await loadStatus(selectedYear('logs-year'));
    } catch (error) { feedback.textContent = error.message; feedback.className = 'upload-feedback error'; }
    finally { button.disabled = false; }
  });
  el('retry-api').addEventListener('click', refreshAll);
  el('audio-test').addEventListener('click', async () => {
    primeTaskAudio();
    if (audioContext?.state === 'suspended') { try { await audioContext.resume(); } catch (_) { /* navigateur bloque le son */ } }
    const beeped = playTaskBell();
    const spoken = speakTask('Test des annonces sonores. Le suivi annoncera la fin de chaque tâche en français.');
    el('audio-status').textContent = beeped && spoken ? 'Test en cours : bip puis annonce en français. Le volume se règle dans Windows.' : 'Audio indisponible dans ce navigateur. Vérifiez ses réglages sonores et les voix françaises installées dans Windows.';
  });
  el('manual-pdfs').addEventListener('change', updateUploadControls);
  el('upload-button').addEventListener('click', uploadPdfs);
  document.querySelectorAll('.job-action').forEach(button => button.addEventListener('click', () => startJob(button.dataset.action, null, null, button)));
  el('stop-job').addEventListener('click', () => stopJob());
  el('initial-batch-start').addEventListener('click', runInitialBatch);
  el('initial-batch-stop').addEventListener('click', stopInitialBatch);
  el('file-filter').addEventListener('change', () => { const data=statusFor('files-year'); if (data) renderFiles(data.files || [],selectedYear('files-year'),data.coverage || {}); });
  el('pipeline-filter').addEventListener('change', () => { pipelinePage=0; const data=statusFor('pipeline-year'); if (data) renderPipeline(data.files || [],data.job,selectedYear('pipeline-year')); });
  [el('ticket-kind'),el('ticket-state')].forEach(select => select.addEventListener('change', () => { ticketOffset=0; loadTickets().catch(showError); }));
  el('ticket-prev').addEventListener('click', () => { ticketOffset=Math.max(0,ticketOffset-30); loadTickets().catch(showError); });
  el('ticket-next').addEventListener('click', () => { ticketOffset+=30; loadTickets().catch(showError); });
  el('pipeline-prev').addEventListener('click', () => { pipelinePage=Math.max(0,pipelinePage-1); const data=statusFor('pipeline-year'); if(data) renderPipeline(data.files || [],data.job,selectedYear('pipeline-year')); });
  el('pipeline-next').addEventListener('click', () => { pipelinePage+=1; const data=statusFor('pipeline-year'); if(data) renderPipeline(data.files || [],data.job,selectedYear('pipeline-year')); });
  el('events-prev').addEventListener('click', () => { logPage+=1; renderEvents(executionEvents || statusFor('logs-year')?.logs || []); });
  el('events-next').addEventListener('click', () => { logPage=Math.max(0,logPage-1); renderEvents(executionEvents || statusFor('logs-year')?.logs || []); });
  yearSelects.forEach(select => select.addEventListener('change', () => {
    storageSet(yearStorageKeys.get(select.id),select.value);
    if (select === yearSelect) lastSelectedYear=select.value;
    if (select === indexYearSelect) pipelinePage=0;
    updateExportLinks();
    if (select === ticketYearSelect) { ticketOffset=0; loadTickets().catch(showError); }
    else if (select === logsYearSelect) { executionEvents=null; el('execution-select').value=''; logPage=0; Promise.all([loadStatus(selectedYear('logs-year')), refreshRuns()]).catch(showError); }
    else loadStatus(selectedYear(select.id)).catch(showError);
  }));
  updateExportLinks();
  el('download-report').addEventListener('click', () => {
    if (!maintenanceReport) return;
    const blob = new Blob([JSON.stringify(maintenanceReport, null, 2)], { type:'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = `rapport-jort-${maintenanceReport.mode || 'maintenance'}-${maintenanceReport.id || 'dernier'}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  });
  window.addEventListener('hashchange', () => activatePanel());
  window.JORT_ADMIN = { runMaintenance:startJob };
  activatePanel(false);
  refreshAll();
})();
