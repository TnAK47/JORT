(async () => {
  'use strict';
  async function api(path, params = {}, signal) {
    const started=performance.now();
    const response = await fetch(path + '?' + new URLSearchParams(params), { signal, cache:'no-store' });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Service de consultation indisponible');
    Object.defineProperty(payload,'_jortTiming',{value:Math.round(performance.now()-started),enumerable:false});
    return payload;
  }
  const activeOperations=new Map();
  const operationStatus=document.getElementById('operation-status');
  const operationLabel=document.getElementById('operation-status-label');
  function beginOperation(key,label,control) {
    activeOperations.set(key,{label,control,started:performance.now()});
    operationLabel.textContent=label;operationStatus.hidden=false;
    if(control){control.setAttribute('aria-busy','true');control.disabled=true;}
  }
  function endOperation(key,control) {
    activeOperations.delete(key);
    if(control&&![...activeOperations.values()].some(item=>item.control===control)){control.setAttribute('aria-busy','false');control.disabled=false;}
    const pending=[...activeOperations.values()].at(-1);
    operationStatus.hidden=!pending;if(pending)operationLabel.textContent=pending.label;
  }
  function refreshOperationStatus() {
    const pending=[...activeOperations.values()].at(-1);
    if(!pending) return;
    const elapsed=(performance.now()-pending.started)/1000;
    const phase=elapsed<1 ? '' : elapsed<3 ? ' · lecture des index locaux' : elapsed<8 ? ' · préparation des résultats' : ' · la recherche continue en local';
    operationLabel.textContent=pending.label+' ('+Math.floor(elapsed)+' s)'+phase;
  }
  window.setInterval(refreshOperationStatus,500);
  function loadingMarkup(label) {
    return '<div class="surface-progress" role="status"><span class="operation-spinner" aria-hidden="true"></span><span><strong>'+esc(label)+'</strong><small>La demande reste locale. Les résultats apparaîtront dès qu’ils seront prêts.</small></span></div>';
  }
  function markSurfaceBusy(surface,label) {
    if(!surface) return;
    surface.classList.add('surface-loading');surface.setAttribute('aria-busy','true');
    let indicator=surface.querySelector(':scope > .surface-progress');
    if(!indicator){surface.insertAdjacentHTML('afterbegin',loadingMarkup(label));indicator=surface.querySelector(':scope > .surface-progress');}
    if(indicator){const strong=indicator.querySelector('strong');if(strong)strong.textContent=label;}
  }
  function clearSurfaceBusy(surface) {
    if(!surface) return;
    surface.classList.remove('surface-loading');surface.removeAttribute('aria-busy');surface.querySelector(':scope > .surface-progress')?.remove();
  }
  let data;
  try { data = await api('/api/restitution/meta'); }
  catch (error) {
    document.getElementById('result-summary').textContent = error.message;
    document.getElementById('empty-results').hidden = false;
    return;
  }
  const sources = Array.isArray(data.sources) ? data.sources : [];
  const announcements = Array.isArray(data.announcements) ? data.announcements : [];
  // Legacy extractions may include a complete act heading before the legal name.
  // Clean display names only; occurrence IDs, identifiers and source data stay intact.
  const office = "(?:commissaire\\s+aux\\s+comptes|pr[ée]sident(?:e)?\\s+directeur(?:trice)?\\s+g[ée]n[ée]ral(?:e)?|directeur(?:trice)?\\s+g[ée]n[ée]ral(?:e)?\\s+adjoint(?:e)?|vice\\s*[-–]?\\s*pr[ée]sident(?:e)?|pr[ée]sident(?:e)?(?:\\s+du\\s+conseil(?:\\s+d['’]administration)?)?|directeur(?:trice)?(?:\\s+g[ée]n[ée]ral(?:e)?)?|administrateurs?(?:trice)?|g[ée]rant(?:e)?)";
  const article = "(?:du|des|de\\s+(?:la|l['’]|un|une)|d['’]un(?:e)?)\\s*";
  const actPrefix = new RegExp("^\\s*(?:(?:[ée]lection|nomination|d[ée]signation|r[ée]vocation|d[ée]mission)\\s+" + article + office + "|renouvellement\\s+(?:de|des)\\s+mandats?\\s+d['’]administrateurs?|reconduction\\s+des\\s+fonctions\\s+d['’]un(?:e)?\\s+" + office + ")(?:\\s+et\\s+(?:nomination|d[ée]signation|reconduction\\s+des\\s+fonctions)\\s+" + article + office + ")*\\b\\s*", 'i');
  const entities = (Array.isArray(data.entities) ? data.entities : []).map(entity => {
    if (!['societe', 'association'].includes(entity.type)) return entity;
    const original = String(entity.name || '');
    const cleaned = original.replace(actPrefix, '').trim();
    return cleaned && cleaned !== original ? { ...entity, name:cleaned, name_as_extracted:original } : entity;
  });
  const relations = Array.isArray(data.relations) ? data.relations : [];
  const coverage = data.coverage && typeof data.coverage === 'object' ? data.coverage : {};
  const stats = data.stats && typeof data.stats === 'object' ? data.stats : {};
  const byId = (items, id) => items.find(item => String(item.id) === String(id));
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
  const norm = value => String(value ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('fr');
  const normalizeSearchField = window.JORT_SEARCH.normalize;
  const escapeRegex = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  function buildSearchMatcher(value, exact) {
    const rank = window.JORT_SEARCH.prepare(value, exact, true);
    return fields => rank(fields).score > 0;
  }
  const sourcesById = new Map(sources.map(item => [String(item.id), item]));
  const announcementsById = new Map(announcements.map(item => [String(item.id), item]));
  const entitiesById = new Map(entities.map(item => [String(item.id), item]));
  const entitiesByAnnouncement = new Map();
  entities.forEach(entity => {
    const key = String(entity.announcement_id);
    if (!entitiesByAnnouncement.has(key)) entitiesByAnnouncement.set(key, []);
    entitiesByAnnouncement.get(key).push(entity);
  });
  const sourceFor = announcement => announcement ? sourcesById.get(String(announcement.source_id)) : undefined;
  const publicationTimestamp = item => {
    const source = sourceFor(item);
    const value = source?.date || item.source_date || '';
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const announcementsNewestFirst = [...announcements].sort((a, b) =>
    publicationTimestamp(b) - publicationTimestamp(a) || String(b.id).localeCompare(String(a.id), 'fr', { numeric:true })
  );
  const sourceHref = (source, pages) => {
    // `pdf` is the old extraction/work path. Prefer the managed archival path,
    // which may point to Google Drive and is served through the local /drive route.
    const reference = source?.canonical_pdf || source?.pdf;
    if (!reference) return '';
    const page = Array.isArray(pages) && pages.length ? Number(pages[0]) : NaN;
    let value = String(reference).replaceAll('\\', '/');
    // Older records still contain the former local archive path. The archive is
    // now on Drive, so route that legacy shape through the configured Drive root.
    const legacyArchive = value.match(/^\/?documents\/PDF\/(\d{4})\/originaux\/([^/]+\.pdf)$/i);
    if (legacyArchive) value = `drive/${legacyArchive[1]}/${legacyArchive[2]}`;
    const href = /^https?:\/\//i.test(value) || value.startsWith('/') ? value : `/${value}`;
    return `${href}${Number.isFinite(page) && page > 0 ? `#page=${page}` : ''}`;
  };
  const categoryName = value => ({ societe:'Société', association:'Association', organisation:'Organisation', a_classer:'À classer', fonds_commerce:'Fonds de commerce' }[value] || 'À classer');
  const formatDate = value => {
    if (!value) return '';
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
    return match ? `${match[3]}/${match[2]}/${match[1]}` : String(value);
  };
  const entitiesFor = announcement => entitiesByAnnouncement.get(String(announcement.id)) || [];
  const organizationFor = announcement => entitiesFor(announcement).find(entity => entity.type === 'societe' || entity.type === 'association');
  const announcementTitle = announcement => {
    if (announcement.title) return announcement.title;
    const candidates = entitiesFor(announcement).filter(entity =>
      ['societe', 'association', 'organisation', 'fonds_commerce'].includes(entity.type) &&
      entity.name && !/^\d{4}[A-Z0-9-]+$/i.test(String(entity.name).trim())
    );
    const namedEntity = candidates.find(entity => entity.type === 'fonds_commerce') || candidates.find(entity => entity.is_primary) || candidates[0];
    return namedEntity?.name || 'Annonce — désignation à préciser';
  };
  const search = document.getElementById('search');
  const categoryFilter = document.getElementById('category-filter');
  const sourceFilter = document.getElementById('source-filter');
  const entityFilter = document.getElementById('matching-entity-filter');
  const entityFilterKey = entity => String(entity.canonical_id || `${entity.type}:${normalizeSearchField(entity.name)}`);
  let fixedEntity = null;
  let rankedQuery = '', rankedResults = announcementsNewestFirst, resultMatches = new Map();
  const list = document.getElementById('announcement-list');
  const details = document.getElementById('detail-panel');
  const emptyResults = document.getElementById('empty-results');
  const exactSearchControls = [...document.querySelectorAll('[data-exact-match]')];
  let exactSearchEnabled = exactSearchControls.some(control => control.checked);
  let selectedAnnouncement = null;
  const PAGE_SIZE = 40;
  let pageIndex = 0;
  const searchIndex = new Map(announcements.map(item => {
    const entityFields = entitiesFor(item).flatMap(entity => [entity.name, entity.cin].filter(Boolean));
    return [String(item.id), [item.reference, item.title, item.excerpt, item.category, ...entityFields].filter(Boolean).map(normalizeSearchField)];
  }));
  const sourceLabel = source => `JORT n° ${source.number ?? source.id}${source.date ? ` · ${formatDate(source.date)}` : ''}`;
  sources.forEach(source => {
    const option = document.createElement('option'); option.value = String(source.id); option.textContent = sourceLabel(source); sourceFilter.appendChild(option);
  });
  let listSequence = 0, listController;
  async function renderList() {
    const sequence = ++listSequence;
    const started=performance.now();
    listController?.abort(); listController = new AbortController();
    const operation='annonces:'+sequence,control=document.getElementById('search-submit');
    beginOperation(operation,'Recherche des annonces dans toute la base…',control);
    list.setAttribute('aria-busy','true');
    markSurfaceBusy(list,'Recherche des annonces dans toute la base');
    document.getElementById('result-summary').textContent = 'Recherche dans toute la base…';
    try {
      const payload = await api('/api/restitution/announcements', { q:search.value, exact:exactSearchEnabled ? 1:0,
        page:pageIndex, category:categoryFilter.value, source:sourceFilter.value, entity:entityFilter.value }, listController.signal);
      if (sequence !== listSequence) return;
      pageIndex = payload.page;
      const pageCount = Math.max(1, Math.ceil(payload.total / PAGE_SIZE));
      const start = payload.total ? pageIndex * PAGE_SIZE + 1 : 0;
      const end = Math.min((pageIndex + 1) * PAGE_SIZE, payload.total);
      document.getElementById('result-summary').textContent = `${start}–${end} sur ${payload.total.toLocaleString('fr-FR')} annonces (${stats.announcements.toLocaleString('fr-FR')} au total) · ${((performance.now()-started)/1000).toFixed(1)} s`;
      document.getElementById('page-label').textContent = `Page ${pageIndex + 1} sur ${pageCount}`;
      document.getElementById('page-prev').disabled = pageIndex === 0;
      document.getElementById('page-next').disabled = pageIndex >= pageCount - 1;
      emptyResults.hidden = payload.total > 0; list.hidden = payload.total === 0;
      // Seule la page courante est conservée ; le texte complet vient à l'ouverture.
      announcementsById.clear(); resultMatches.clear();
      payload.items.forEach(item => { announcementsById.set(String(item.id), item); resultMatches.set(String(item.id), item.match); });
      list.innerHTML = payload.items.map(item => {
        const source=sourceFor(item), date=formatDate(source?.date);
        const pages=Array.isArray(item.pages)&&item.pages.length ? `Page${item.pages.length>1?'s':''} ${item.pages.join(', ')}` : 'Page non précisée';
        return `<button class="announcement-card ${String(selectedAnnouncement)===String(item.id)?'selected':''}" type="button" data-announcement="${esc(item.id)}"><span class="card-top"><span class="card-ref">${esc(item.reference||item.id)} · ${esc(date)}</span><span class="category-pill ${esc(item.category)}">${esc(categoryName(item.category))}</span></span>${item.match?.total?`<span class="search-match ${esc(item.match.kind)}">${esc(item.match.label)}</span>`:''}<span class="card-title">${esc(announcementTitle(item))}</span>${item.title_review_required?'<span class="title-uncertain">Désignation à préciser dans la source</span>':''}<span class="card-excerpt">${esc(item.excerpt)}</span><span class="card-bottom"><span>JORT n° ${esc(source?.number||'—')} · ${esc(pages)}</span><span class="open-link">Voir le détail ↗</span></span></button>`;
      }).join('');
      list.querySelectorAll('[data-announcement]').forEach(button => button.addEventListener('click',()=>selectAnnouncement(button.dataset.announcement)));
      const key=JSON.stringify([search.value,exactSearchEnabled]);
      if(key!==rankedQuery) {
        rankedQuery=key;
        const choices=search.value.trim()?await api('/api/restitution/entities',{q:search.value,exact:exactSearchEnabled?1:0}):{items:[]};
        if(sequence!==listSequence)return;
        const selected=entityFilter.value;
        if(fixedEntity&&!choices.items.some(e=>e.id===fixedEntity.id))choices.items.unshift({...fixedEntity,match:{matched:0,total:0}});
        entityFilter.innerHTML='<option value="">Toutes les entités trouvées</option>'+choices.items.slice(0,60).map(e=>`<option value="${esc(e.id)}">${esc(e.name)}${e.match.total?` · ${e.match.matched}/${e.match.total} mots`:''}</option>`).join('');
        if([...entityFilter.options].some(o=>o.value===selected))entityFilter.value=selected;
        entityFilter.hidden=!choices.items.length;
      }
    } catch(error) {
      if(error.name!=='AbortError'&&sequence===listSequence)document.getElementById('result-summary').textContent=error.message;
    } finally {endOperation(operation,control);if(sequence===listSequence){list.setAttribute('aria-busy','false');clearSurfaceBusy(list);}}
  }
  let detailSequence=0;
  async function selectAnnouncement(id) {
    const sequence=++detailSequence;
    const started=performance.now();
    const operation='annonce:'+sequence;
    beginOperation(operation,'Chargement du texte et des références de l’annonce…');
    details.setAttribute('aria-busy','true');
    details.innerHTML='<div class="empty-detail">'+loadingMarkup('Chargement du texte et des preuves de l’annonce')+'</div>';
    let payload;
    try { payload=await api('/api/annonce',{id}); }
    catch(error) { if(sequence===detailSequence)details.textContent=error.message; endOperation(operation);details.setAttribute('aria-busy','false');return; }
    if(sequence!==detailSequence){endOperation(operation);return;}
    const item=payload.announcement, full=item;
    announcementsById.set(String(id),item);
    if(payload.source)sourcesById.set(String(payload.source.id),payload.source);
    entitiesByAnnouncement.set(String(id),payload.entities);
    payload.entities.forEach(e=>entitiesById.set(String(e.id),e));
    selectedAnnouncement = item.id;
    const source = sourceFor(item);
    const href = sourceHref(source, item.pages);
    const pages = Array.isArray(item.pages) && item.pages.length ? `Page${item.pages.length > 1 ? 's' : ''} ${item.pages.join(', ')}` : 'Page non précisée';
    const linkedEntities = entitiesFor(item);
    const relatedCount = payload.relation_count;
    details.innerHTML = `<div class="detail-top"><span class="detail-ref">${esc(item.reference || `Annonce ${item.id}`)}</span><span class="detail-date">${esc(formatDate(source?.date))} · chargée en ${((performance.now()-started)/1000).toFixed(1)} s</span></div><h3>${esc(announcementTitle(item))}</h3><span class="detail-status"><i></i>${esc(item.status === 'a_verifier' ? 'Relevé à vérifier' : item.status || 'À vérifier')}</span><div class="detail-text">${esc(full.text || 'Aucun texte transcrit.')}</div>${linkedEntities.length ? `<div class="entity-tags">${linkedEntities.map(entity => `<span class="entity-tag ${entity.type === 'personne' ? 'person' : ''}">${esc(entity.name)} · ${esc(entity.type === 'personne' ? 'Personne' : categoryName(entity.type))}</span>`).join('')}</div>` : ''}<div class="detail-links">${href ? `<a class="source-link" href="${esc(href)}" target="_blank" rel="noopener">↗ Ouvrir le JORT · ${esc(pages)}</a>` : '<span class="entity-tag">Document source non lié</span>'}${source?.number ? `<span class="source-link secondary">JORT n° ${esc(source.number)}${source.date ? ` · ${esc(formatDate(source.date))}` : ''}</span>` : ''}</div>${relatedCount ? `<p class="detail-date" style="margin-top:14px">${relatedCount} relation${relatedCount > 1 ? 's' : ''} explicitement relevée${relatedCount > 1 ? 's' : ''} dans cette annonce.</p>` : ''}`;
    details.querySelectorAll('.entity-tag').forEach((tag, index) => {
      const entity = linkedEntities[index];
      if (!entity) return;
      const button = document.createElement('button');
      button.type = 'button'; button.className = tag.className + ' entity-explore';
      button.textContent = tag.textContent + ' ↗'; button.title = 'Explorer les liens de ' + entity.name;
      button.addEventListener('click', () => openNetworkFromEntity(graphNodeKey(entity)));
      tag.replaceWith(button);
    });
    list.querySelectorAll('[data-announcement]').forEach(button => button.classList.toggle('selected', button.dataset.announcement===String(id)));
    endOperation(operation);details.setAttribute('aria-busy','false');
  }

  const tabs = [...document.querySelectorAll('.tab')];
  const HISTORY_MARKER = 'jort-navigation-v1';
  let currentView = '';
  let restoringHistory = false;
  function navigationSnapshot(view = currentView || 'entites') {
    return {
      [HISTORY_MARKER]: true,
      view,
      entity: { query: entitySearch?.value || '', type: entityType || '', page: entityPage || 0 },
      announcements: { query: search.value || '', category: categoryFilter.value || 'toutes', source: sourceFilter.value || 'tous', page: pageIndex || 0, selected: selectedAnnouncement || null, fixedEntity: fixedEntity ? { id: fixedEntity.id, name: fixedEntity.name } : null },
      network: { query: networkSearch?.value || '', root: rootOverride || currentRoot || organizationSelect?.value || '', depth: graphDepth || 2, event: eventFilter?.value || 'all', page: pivotPage || 0 },
      exact: exactSearchEnabled
    };
  }
  function navigationUrl(state) {
    const url = new URL(window.location.href);
    url.hash = state.view === 'reseau' ? 'liens' : state.view;
    // Les liens directs existants restent utilisables, mais ils suivent le contexte affiché.
    if (state.view === 'reseau' && state.network.root) url.searchParams.set('entite', state.network.root);
    else url.searchParams.delete('entite');
    if (state.view === 'annonces' && state.announcements.selected) url.searchParams.set('annonce', state.announcements.selected);
    else url.searchParams.delete('annonce');
    return url.pathname + url.search + url.hash;
  }
  function writeNavigationState(mode = 'replace', view = currentView) {
    if (restoringHistory) return;
    const state = navigationSnapshot(view);
    history[mode === 'push' ? 'pushState' : 'replaceState'](state, '', navigationUrl(state));
  }
  function syncEntityTypeButtons() {
    document.querySelectorAll('[data-entity-type]').forEach(button => {
      const active = button.dataset.entityType === entityType;
      button.classList.toggle('selected', active);
      button.setAttribute('aria-pressed', String(active));
    });
  }
  function restoreNavigationState(state) {
    if (!state || !state[HISTORY_MARKER]) {
      showView(location.hash === '#liens' ? 'reseau' : location.hash === '#annonces' ? 'annonces' : 'entites', false);
      return;
    }
    restoringHistory = true;
    try {
      const entityState = state.entity || {};
      const announcementState = state.announcements || {};
      const networkState = state.network || {};
      entitySearch.value = entityState.query || '';
      entityType = entityState.type || '';
      entityPage = Math.max(0, Number(entityState.page) || 0);
      syncEntityTypeButtons();
      search.value = announcementState.query || '';
      categoryFilter.value = announcementState.category || 'toutes';
      if ([...sourceFilter.options].some(option => option.value === announcementState.source)) sourceFilter.value = announcementState.source;
      pageIndex = Math.max(0, Number(announcementState.page) || 0);
      selectedAnnouncement = announcementState.selected || null;
      fixedEntity = announcementState.fixedEntity || null;
      if (fixedEntity) {
        entityFilter.innerHTML = `<option value="">Toutes les entités trouvées</option><option value="${esc(fixedEntity.id)}">${esc(fixedEntity.name)}</option>`;
        entityFilter.value = fixedEntity.id;
        entityFilter.hidden = false;
      } else {
        entityFilter.value = '';
      }
      exactSearchEnabled = Boolean(state.exact);
      exactSearchControls.forEach(control => { control.checked = exactSearchEnabled; });
      networkSearch.value = networkState.query || '';
      pivotPage = Math.max(0, Number(networkState.page) || 0);
      graphDepth = Math.max(1, Math.min(4, Number(networkState.depth) || 2));
      if ([...eventFilter.options].some(option => option.value === networkState.event)) eventFilter.value = networkState.event;
      rootOverride = networkState.root || '';
      showView(state.view || 'entites', false);
    } finally {
      restoringHistory = false;
    }
  }
  function showView(view, updateHistory = true, historyMode = 'replace') {
    if(!['entites','annonces','reseau'].includes(view))view='entites';
    const isNetwork = view === 'reseau';
    const wasHidden = document.getElementById('reseau-view').hidden;
    tabs.forEach(other => { const active = other.dataset.view === view; other.classList.toggle('active', active); other.setAttribute('aria-selected', String(active)); other.tabIndex = active || (isNetwork && other.dataset.view==='entites') ? 0 : -1; });
    document.getElementById('annonces-view').hidden = view!=='annonces';
    document.getElementById('entites-view').hidden = view!=='entites';
    document.getElementById('reseau-view').hidden = !isNetwork;
    document.body.classList.toggle('explorer-mode', isNetwork);
    document.body.classList.toggle('entity-mode',view==='entites');
    currentView = view;
    if (updateHistory) writeNavigationState(historyMode, view);
    if(view==='entites') renderEntities();
    if(view==='annonces') renderList();
    if (isNetwork) renderGraph(!wasHidden);
  }
  tabs.forEach(tab => {
    tab.addEventListener('click', () => showView(tab.dataset.view));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const other = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[tabs.length - 1] : tabs[(tabs.indexOf(tab) + (event.key==='ArrowLeft'?tabs.length-1:1)) % tabs.length];
      showView(other.dataset.view); other.focus();
    });
  });
  document.querySelector('.brand')?.addEventListener('click', () => showView('entites'));
  document.querySelector('.hero-links a')?.addEventListener('click',()=>showView('entites'));
  window.addEventListener('hashchange',()=>showView(location.hash==='#liens'&&(currentRoot||rootOverride)?'reseau':location.hash==='#annonces'?'annonces':'entites',false));
  window.addEventListener('popstate', event => restoreNavigationState(event.state));
  [categoryFilter, sourceFilter, entityFilter].forEach(control => control.addEventListener('change', () => { pageIndex = 0; renderList(); }));
  exactSearchControls.forEach(control => control.addEventListener('change', () => {
    exactSearchEnabled = control.checked;
    exactSearchControls.forEach(other => { other.checked = exactSearchEnabled; });
    pageIndex = 0;
    if(currentView==='annonces')renderList();
    if(currentView==='reseau')searchGraph();
    if(currentView==='entites')renderEntities();
  }));
  document.getElementById('page-prev').addEventListener('click', () => { pageIndex = Math.max(0, pageIndex - 1); renderList(); });
  document.getElementById('page-next').addEventListener('click', () => { pageIndex += 1; renderList(); });
  let listSearchTimer;
  search.addEventListener('input', () => { clearTimeout(listSearchTimer); listSearchTimer = setTimeout(() => { pageIndex = 0; renderList(); }, 140); });
  document.getElementById('search-submit').addEventListener('click', () => { pageIndex = 0; renderList(); search.focus(); });
  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); (currentView==='entites'?document.getElementById('entity-search'):currentView==='reseau'?networkSearch:search).focus(); }
    if (event.key === 'Escape' && document.activeElement === search) { search.value = ''; renderList(); search.blur(); }
    if (event.key === 'Escape') { document.getElementById('reseau-view').classList.remove('immersive-fallback'); updateFullscreenButton(); }
  });

  let entityPage=0, entityType='', entitySequence=0, entityController;
  const entitySearch=document.getElementById('entity-search');
  const entityGrid=document.getElementById('entity-grid');
  async function renderEntities() {
    const sequence=++entitySequence;
    const started=performance.now();
    entityController?.abort();entityController=new AbortController();
    const operation='entites:'+sequence,control=document.querySelector('#entity-search-form .search-submit');
    beginOperation(operation,'Recherche et regroupement des entités…',control);
    document.getElementById('entity-summary').textContent='Recherche dans toute la base…';
    entityGrid.setAttribute('aria-busy','true');
    markSurfaceBusy(entityGrid,'Recherche et regroupement des entités');
    try {
      const result=await api('/api/restitution/entities',{q:entitySearch.value,exact:exactSearchEnabled?1:0,type:entityType,page:entityPage,limit:24,featured:1},entityController.signal);
      if(sequence!==entitySequence)return;
      entityPage=result.page;
      if (currentView === 'entites' && !restoringHistory) writeNavigationState('replace', 'entites');
      document.getElementById('entity-summary').textContent=`${result.total.toLocaleString('fr-FR')} entités · ${entitySearch.value.trim()?'résultats par pertinence':'les plus connectées en premier'} · ${((performance.now()-started)/1000).toFixed(1)} s`;
      const pageCount=Math.max(1,Math.ceil(result.total/result.limit));
      document.getElementById('entity-page').textContent=`Page ${entityPage+1} sur ${pageCount}`;
      document.getElementById('entity-prev').disabled=entityPage===0;
      document.getElementById('entity-next').disabled=entityPage>=pageCount-1;
      entityGrid.replaceChildren();
      for(const node of result.items) {
        const card=document.createElement('article');card.className='discovery-card '+(node.type==='personne'?'discovery-person':'discovery-org');
        const presumed=node.resolutionMethods.some(m=>m==='same_name_presumed'||m==='organization_name_presumed');
        card.innerHTML=`<div class="discovery-card-top"><span class="entity-avatar">${node.type==='personne'?'P':node.type==='fonds_commerce'?'F':'O'}</span><span>${esc(node.type==='personne'?'Personne':categoryName(node.type))}</span>${presumed?'<span class="presumed-dot" title="Rapprochement présumé">Présumé</span>':''}</div><h3>${esc(node.name)}</h3><span class="entity-public-id">${esc(node.public_entity_id||'')}</span>${node.match?.total?`<span class="search-match ${esc(node.match.kind)}">${esc(node.match.label)}</span>`:''}<div class="discovery-numbers"><span><b>${node.occurrence_count.toLocaleString('fr-FR')}</b> occurrences</span><span><b>${node.relation_count.toLocaleString('fr-FR')}</b> relations</span></div><div class="discovery-actions"><button type="button" class="explore-entity">Ouvrir la fiche et le réseau ↗</button><button type="button" class="entity-notices">Voir ses annonces →</button></div>`;
        card.querySelector('.explore-entity').addEventListener('click',()=>openNetworkFromEntity(node.id));
        card.querySelector('.entity-notices').addEventListener('click',()=>{
          fixedEntity=node;search.value='';rankedQuery='';pageIndex=0;categoryFilter.value='toutes';sourceFilter.value='tous';
          entityFilter.innerHTML=`<option value="">Toutes les entités trouvées</option><option value="${esc(node.id)}">${esc(node.name)}</option>`;
          entityFilter.value=node.id;entityFilter.hidden=false;
          showView('annonces', true, 'push');
        });
        entityGrid.appendChild(card);
      }
      if(!result.items.length)entityGrid.innerHTML='<div class="empty-detail"><h3>Aucune entité trouvée</h3><p>Essayez un nom plus court, une autre catégorie ou la recherche dans les annonces.</p></div>';
    } catch(error) { if(error.name!=='AbortError'&&sequence===entitySequence)document.getElementById('entity-summary').textContent=error.message; }
    finally {endOperation(operation,control);if(sequence===entitySequence){entityGrid.setAttribute('aria-busy','false');clearSurfaceBusy(entityGrid);}}
  }
  document.getElementById('entity-search-form').addEventListener('submit',event=>{event.preventDefault();entityPage=0;renderEntities();});
  let entityTimer;
  entitySearch.addEventListener('input',()=>{clearTimeout(entityTimer);entityTimer=setTimeout(()=>{entityPage=0;renderEntities();},300);});
  document.querySelectorAll('[data-entity-type]').forEach(button=>button.addEventListener('click',()=>{
    entityType=button.dataset.entityType;entityPage=0;
    syncEntityTypeButtons();renderEntities();
  }));
  document.getElementById('entity-prev').addEventListener('click',()=>{entityPage=Math.max(0,entityPage-1);renderEntities();});
  document.getElementById('entity-next').addEventListener('click',()=>{entityPage+=1;renderEntities();});
  entityFilter.addEventListener('change',()=>{if(!entityFilter.value)fixedEntity=null;});
  document.getElementById('network-back-to-entities')?.addEventListener('click',()=>showView('entites'));

  const graph = document.getElementById('network-graph');
  const graphEmpty = document.getElementById('graph-empty');
  const graphLimitNote = document.getElementById('graph-limit-note');
  const networkDetail = document.getElementById('network-detail');
  const networkSearch = document.getElementById('network-search');
  let activeNode = null;
  let pivotPage = 0;
  let graphDepth = 2;
  const PIVOT_PAGE_SIZE = 100;
  const GRAPH_NODE_LIMIT = 200;
  const graphNodeKey = entity => {
    if (entity?.canonical_id) return String(entity.canonical_id);
    if (entity?.identity_group_key) return String(entity.identity_group_key);
    return String(entity?.id || '');
  };
  const graphNodes = new Map();
  entities.forEach(entity => {
    const key = graphNodeKey(entity);
    if (!graphNodes.has(key)) graphNodes.set(key, { ...entity, id: key, name:entity.canonical_name || entity.name, occurrenceIds: [], nameVariants: [], resolutionMethods:[] });
    const node = graphNodes.get(key);
    node.occurrenceIds.push(String(entity.id));
    if (!node.nameVariants.includes(entity.name)) node.nameVariants.push(entity.name);
    if (!node.resolutionMethods.includes(entity.identity_resolution)) node.resolutionMethods.push(entity.identity_resolution);
    if (!node.identifier && entity.identifier) node.identifier = entity.identifier;
  });
  let graphRelations = [];
  const adjacency = new Map();
  graphRelations.forEach(relation => {
    const from = graphNodeKey(entitiesById.get(String(relation.from)));
    const to = graphNodeKey(entitiesById.get(String(relation.to)));
    if (!adjacency.has(from)) adjacency.set(from, []);
    if (!adjacency.has(to)) adjacency.set(to, []);
    adjacency.get(from).push({ relation, other: to });
    adjacency.get(to).push({ relation, other: from });
  });
  let pivotResults={items:[],total:0,page:0};
  const organizationSelect = document.getElementById('organization-select');
  function pivotLabel(pivot) {
    const count=pivot.occurrence_count || pivot.occurrenceIds.length;
    return `${pivot.name} · ${pivot.type==='personne' ? count+' occurrences regroupées' : categoryName(pivot.type)}`;
  }
  const graphContainer = document.getElementById('graph-container');
  const graphWorkspace = document.getElementById('network-workspace');
  const eventFilter = document.getElementById('graph-event-filter');
  const svgNS = 'http://www.w3.org/2000/svg';
  const announcementMap = new Map(announcements.map(item => [String(item.id), item]));
  const svgElement = (name, attributes, text) => {
    const element = document.createElementNS(svgNS, name);
    Object.entries(attributes || {}).forEach(([key, value]) => element.setAttribute(key, String(value)));
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const relationKind = relation => {
    if (relation.type === 'vente_fonds_commerce' || relation.event_type === 'vente') return 'vente';
    if (relation.type === 'souscription_capital' || relation.event_type === 'augmentation_capital') return 'capital';
    if (['resignation', 'revocation', 'cessation', 'demission'].includes(relation.event_type)) return 'cessation';
    if (['appointment', 'nomination', 'renewal', 'renouvellement'].includes(relation.event_type)) return 'nomination';
    return 'role';
  };
  const kindName = kind => ({ vente:'Vente de fonds de commerce', capital:'Souscription réservée au capital', cessation:'Cessation / révocation', nomination:'Nomination / renouvellement', role:'Rôle publié' }[kind] || 'Relation');
  const roleFamily = relation => {
    if (relation.role_family) return relation.role_family;
    const role = normalizeSearchField(relation.role || '');
    return /commissaire|auditeur/.test(role) ? 'controle' : /directeur|gerant/.test(role) ? 'direction' : /administrateur|president|conseil/.test(role) ? 'administration' : 'autre';
  };
  let visibleBundles = [];
  let nodePositions = new Map();
  let rootOverride = '';
  let currentRoot = '';
  const pivotHistory = [];
  let camera = { x: -400, y: -250, width: 800, height: 500 };
  let sceneBounds = { x: -200, y: -150, width: 400, height: 300 };
  let cameraReady = false;
  let suppressClickUntil = 0;
  let dragging = null;
  const pointers = new Map();
  let pinchDistance = 0;
  const filteredRelations = () => {
    const kind = eventFilter?.value || 'all';
    return kind === 'all' ? graphRelations : graphRelations.filter(relation => relationKind(relation) === kind);
  };
  function viewportSize() {
    const box = graphContainer.getBoundingClientRect();
    return { width: Math.max(320, box.width || 900), height: Math.max(300, box.height || 600) };
  }
  function updateCamera() {
    graph.setAttribute('viewBox', [camera.x, camera.y, camera.width, camera.height].join(' '));
    const scale = viewportSize().width / camera.width;
    const label = document.getElementById('graph-zoom-label');
    if (label) label.textContent = Math.round(scale * 100) + ' %';
    graphContainer.classList.toggle('zoomed-out', scale < 0.65);
  }
  function fitGraph() {
    const size = viewportSize();
    const scale = Math.min(size.width / (sceneBounds.width + 100), size.height / (sceneBounds.height + 100), 1.35);
    camera.width = size.width / scale;
    camera.height = size.height / scale;
    camera.x = sceneBounds.x + sceneBounds.width / 2 - camera.width / 2;
    camera.y = sceneBounds.y + sceneBounds.height / 2 - camera.height / 2;
    cameraReady = true;
    updateCamera();
  }
  function zoomGraph(factor, origin) {
    const size = viewportSize();
    const scale = size.width / camera.width;
    const nextScale = Math.max(0.04, Math.min(3, scale * factor));
    const ratio = scale / nextScale;
    const anchor = origin || { x: camera.x + camera.width / 2, y: camera.y + camera.height / 2 };
    camera.x = anchor.x - (anchor.x - camera.x) * ratio;
    camera.y = anchor.y - (anchor.y - camera.y) * ratio;
    camera.width *= ratio;
    camera.height *= ratio;
    updateCamera();
  }
  function pointerPosition(event) {
    const box = graph.getBoundingClientRect();
    return { x: camera.x + (event.clientX - box.left) / Math.max(1, box.width) * camera.width,
      y: camera.y + (event.clientY - box.top) / Math.max(1, box.height) * camera.height };
  }
  function setDetailsVisible(visible) {
    graphWorkspace?.classList.toggle('details-hidden', !visible);
    const button = document.getElementById('graph-details-toggle');
    if (button) {
      button.setAttribute('aria-expanded', String(visible));
      button.textContent = visible ? 'Masquer les détails' : 'Afficher les détails';
    }
  }
  function highlightSelection(bundle) {
    const neighbors = new Set([String(activeNode)]);
    if (activeNode) (adjacency.get(String(activeNode)) || []).forEach(edge => neighbors.add(edge.other));
    graph.querySelectorAll('.node').forEach(element => {
      element.classList.toggle('active', element.dataset.node === String(activeNode));
      element.classList.toggle('dimmed', Boolean(activeNode) && !neighbors.has(element.dataset.node));
    });
    graph.querySelectorAll('.edge-bundle').forEach((element, index) => {
      const item = visibleBundles[index];
      const related = item && (item.from === String(activeNode) || item.to === String(activeNode));
      element.classList.toggle('related', Boolean(activeNode) && related);
      element.classList.toggle('dimmed', Boolean(activeNode) && !related);
      element.classList.toggle('active', item === bundle);
    });
    const focusButton = document.getElementById('graph-focus');
    if (focusButton) focusButton.disabled = !activeNode || !nodePositions.has(String(activeNode));
  }
  let graphSequence=0, graphController;
  async function renderGraph(preserveCamera = false) {
    if(document.getElementById('reseau-view').hidden)return;
    const sequence=++graphSequence;
    const started=performance.now();
    graphController?.abort(); graphController=new AbortController();
    const operation='connexions:'+sequence,control=document.getElementById('graph-search-submit');
    beginOperation(operation,'Recherche des connexions et de leurs preuves…',control);
    graphContainer.setAttribute('aria-busy','true');
    markSurfaceBusy(graphContainer,'Recherche des entités puis construction du réseau');
    const explicitRoot=rootOverride || organizationSelect.value;
    rootOverride='';
    document.getElementById('graph-summary').textContent='Chargement des connexions…';
    let network;
    try {
      pivotResults=await api('/api/restitution/entities',{q:networkSearch.value,exact:exactSearchEnabled?1:0,page:pivotPage},graphController.signal);
      if(sequence!==graphSequence){endOperation(operation,control);return;}
      pivotPage=pivotResults.page;
      const options=[...pivotResults.items];
      if(explicitRoot && graphNodes.has(explicitRoot) && !options.some(n=>n.id===explicitRoot))options.unshift(graphNodes.get(explicitRoot));
      const previous=explicitRoot || options[0]?.id || '';
      network=await api('/api/restitution/graph',{root:previous,depth:graphDepth,event:eventFilter?.value||'all'},graphController.signal);
      if(sequence!==graphSequence){endOperation(operation,control);return;}
      const rootOption=network.nodes.find(n=>n.id===network.root);
      if(rootOption&&!options.some(n=>n.id===network.root))options.unshift(rootOption);
      graphNodes.clear(); entitiesById.clear(); announcementMap.clear(); adjacency.clear();
      network.nodes.forEach(n=>graphNodes.set(n.id,n));
      options.forEach(n=>{if(!graphNodes.has(n.id))graphNodes.set(n.id,n);});
      network.entities.forEach(e=>entitiesById.set(String(e.id),e));
      network.announcements.forEach(a=>announcementMap.set(String(a.id),a));
      graphRelations=network.relations;
      graphRelations.forEach(relation=>{
        const from=graphNodeKey(entitiesById.get(String(relation.from))),to=graphNodeKey(entitiesById.get(String(relation.to)));
        if(!adjacency.has(from))adjacency.set(from,[]); if(!adjacency.has(to))adjacency.set(to,[]);
        adjacency.get(from).push({relation,other:to}); adjacency.get(to).push({relation,other:from});
      });
      const pages=Math.max(1,Math.ceil(pivotResults.total/PIVOT_PAGE_SIZE));
      document.getElementById('pivot-page').textContent=`${pivotPage+1} / ${pages} · ${pivotResults.total.toLocaleString('fr-FR')} entités`;
      document.getElementById('pivot-prev').disabled=pivotPage===0;
      document.getElementById('pivot-next').disabled=pivotPage>=pages-1;
      organizationSelect.innerHTML=options.map(n=>`<option value="${esc(n.id)}">${esc(pivotLabel(n))}</option>`).join('');
      organizationSelect.value=network.root;
    } catch(error) {
      if(error.name!=='AbortError'&&sequence===graphSequence){graph.replaceChildren(); graphEmpty.hidden=false; graphEmpty.textContent=error.message;document.getElementById('graph-summary').textContent='Connexions indisponibles';}
      endOperation(operation,control);graphContainer.setAttribute('aria-busy','false');clearSurfaceBusy(graphContainer);
      return;
    }
    const rootId=network.root,root=graphNodes.get(rootId);
    currentRoot=rootId;
    if (!restoringHistory) writeNavigationState('replace', 'reseau');
    const allowedRelations = filteredRelations();
    const allowedAdjacency = new Map();
    allowedRelations.forEach(relation => {
      const from = graphNodeKey(entitiesById.get(String(relation.from)));
      const to = graphNodeKey(entitiesById.get(String(relation.to)));
      if (!allowedAdjacency.has(from)) allowedAdjacency.set(from, []);
      if (!allowedAdjacency.has(to)) allowedAdjacency.set(to, []);
      allowedAdjacency.get(from).push(to);
      allowedAdjacency.get(to).push(from);
    });
    const depths = new Map();
    let graphWasCapped = network.capped || network.proofs_capped;
    if (root) {
      const queue = [rootId]; depths.set(rootId, 0);
      for (let cursor = 0; cursor < queue.length; cursor++) {
        const current = queue[cursor];
        if (depths.get(current) >= graphDepth) continue;
        for (const other of allowedAdjacency.get(current) || []) {
          if (depths.has(other)) continue;
          if (depths.size >= GRAPH_NODE_LIMIT) { graphWasCapped = true; continue; }
          depths.set(other, depths.get(current) + 1); queue.push(other);
        }
      }
    }
    graphLimitNote.hidden = !graphWasCapped;
    graphLimitNote.textContent = graphWasCapped ? `Vue bornée à ${GRAPH_NODE_LIMIT} entités et 3 000 preuves. ${network.total_relations} relations dans ce voisinage. Cliquez une entité pour poursuivre ou réduisez la profondeur ; toutes les preuves restent dans la base.` : '';
    const expand = document.getElementById('graph-expand');
    expand.disabled = !root || !allowedAdjacency.has(rootId) || graphDepth >= 4 || graphWasCapped;
    expand.textContent = 'Élargir · ' + graphDepth + ' niveau' + (graphDepth > 1 ? 'x' : '');
    const contract = document.getElementById('graph-contract');
    if (contract) contract.disabled = !root || graphDepth <= 1;
    const visibleRelations = allowedRelations.filter(relation => depths.has(graphNodeKey(entitiesById.get(String(relation.from)))) && depths.has(graphNodeKey(entitiesById.get(String(relation.to)))));
    graphEmpty.hidden = visibleRelations.length > 0;
    if (!visibleRelations.length) graphEmpty.textContent = root
      ? 'Aucun lien documenté pour ce point de départ avec le filtre choisi. Essayez une autre entité ou « Tous les liens ».'
      : 'Aucune entité trouvée. Essayez un autre nom.';
    const summary = document.getElementById('graph-summary');
    if (summary) summary.textContent = depths.size + ' entité' + (depths.size > 1 ? 's' : '') + ' · ' + visibleRelations.length + ' relation' + (visibleRelations.length > 1 ? 's' : '') + ' · ' + graphDepth + ' niveau' + (graphDepth > 1 ? 'x' : '') + ' · ' + ((performance.now()-started)/1000).toFixed(1) + ' s';
    graph.replaceChildren();
    graph.style.width = '100%'; graph.style.height = '100%';
    graph.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    graph.setAttribute('tabindex', '0');
    graph.setAttribute('aria-label', 'Graphe interactif. Flèches pour déplacer, plus ou moins pour zoomer, touche Origine pour tout voir. Chaque nœud et lien est accessible au clavier.');
    nodePositions = new Map();
    visibleBundles = [];
    if (!root || !visibleRelations.length) { sceneBounds = { x:-200, y:-150, width:400, height:300 }; fitGraph(); highlightSelection(); if(root)selectNode(root); endOperation(operation,control);if(sequence===graphSequence){graphContainer.setAttribute('aria-busy','false');clearSurfaceBusy(graphContainer);}return; }
    const layers = new Map();
    for (const [key, depth] of depths) {
      if (!layers.has(depth)) layers.set(depth, []);
      layers.get(depth).push(graphNodes.get(key));
    }
    nodePositions.set(rootId, { x:0, y:0 });
    let radius = 0;
    for (const [depth, nodes] of layers) {
      if (depth === 0) continue;
      nodes.sort((a, b) => String(a.name).localeCompare(String(b.name), 'fr'));
      radius = Math.max(radius + 270, nodes.length * 195 / (2 * Math.PI));
      nodes.forEach((node, index) => {
        const angle = -Math.PI / 2 + index / nodes.length * 2 * Math.PI;
        nodePositions.set(node.id, { x:Math.cos(angle) * radius, y:Math.sin(angle) * radius });
      });
    }
    const points = [...nodePositions.values()];
    const minX = Math.min(...points.map(point => point.x)) - 100;
    const minY = Math.min(...points.map(point => point.y)) - 55;
    sceneBounds = { x:minX, y:minY, width:Math.max(...points.map(point => point.x)) + 100 - minX,
      height:Math.max(...points.map(point => point.y)) + 55 - minY };
    const defs = svgElement('defs');
    const marker = svgElement('marker', { id:'sale-arrow', viewBox:'0 0 10 10', refX:9, refY:5, markerWidth:6, markerHeight:6, orient:'auto-start-reverse' });
    marker.appendChild(svgElement('path', { d:'M0 0 L10 5 L0 10Z', fill:'#cf7628' }));
    defs.appendChild(marker); graph.appendChild(defs);
    const bundled = new Map();
    visibleRelations.forEach(relation => {
      const from = graphNodeKey(entitiesById.get(String(relation.from)));
      const to = graphNodeKey(entitiesById.get(String(relation.to)));
      const kind = relationKind(relation);
      const key = JSON.stringify([from, to, kind, norm(relation.role)]);
      if (!bundled.has(key)) bundled.set(key, { from, to, kind, family:roleFamily(relation), role:relation.role || 'Lien publié', relations:[] });
      bundled.get(key).relations.push(relation);
    });
    visibleBundles = [...bundled.values()];
    visibleBundles.forEach((bundle, index) => {
      const a = nodePositions.get(bundle.from), b = nodePositions.get(bundle.to);
      const dx = b.x - a.x, dy = b.y - a.y;
      const offset = Math.min(100, Math.hypot(dx, dy) * 0.14) * (index % 2 ? 1 : -1);
      const startFactor = Math.min(88 / Math.max(1, Math.abs(dx)), 34 / Math.max(1, Math.abs(dy)));
      const endFactor = Math.min(88 / Math.max(1, Math.abs(dx)), 34 / Math.max(1, Math.abs(dy)));
      const start = { x:a.x + dx * startFactor, y:a.y + dy * startFactor };
      const end = { x:b.x - dx * endFactor, y:b.y - dy * endFactor };
      const length = Math.max(1, Math.hypot(dx, dy));
      const control = { x:(a.x + b.x) / 2 - dy / length * offset, y:(a.y + b.y) / 2 + dx / length * offset };
      const path = 'M' + start.x + ',' + start.y + ' Q' + control.x + ',' + control.y + ' ' + end.x + ',' + end.y;
      const group = svgElement('g', { class:'edge-bundle' });
      const kindClass = ({ vente:'edge-fonds-commerce', capital:'edge-capital', nomination:'edge-appointment', cessation:'edge-cessation', role:'edge-role' })[bundle.kind];
      group.appendChild(svgElement('path', { d:path, class:'edge ' + kindClass + (['role','nomination'].includes(bundle.kind) ? ' edge-family-' + bundle.family : ''), fill:'none', ...(bundle.kind === 'vente' ? { 'marker-end':'url(#sale-arrow)' } : {}) }));
      const hit = svgElement('path', { d:path, class:'edge-hit', fill:'none', stroke:'transparent', 'stroke-width':18, tabindex:0, role:'button',
        'aria-label':bundle.role + ' entre ' + graphNodes.get(bundle.from).name + ' et ' + graphNodes.get(bundle.to).name + ', ' + bundle.relations.length + ' preuve(s)' });
      hit.appendChild(svgElement('title', {}, bundle.role + ' · ' + bundle.relations.length + ' preuve(s). Cliquer pour consulter les sources.'));
      hit.addEventListener('click', () => { if (Date.now() >= suppressClickUntil) selectEdge(bundle); });
      hit.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectEdge(bundle); } });
      group.appendChild(hit);
      const label = svgElement('text', { x:(start.x + 2 * control.x + end.x) / 4, y:(start.y + 2 * control.y + end.y) / 4 - 8, class:'edge-label', 'text-anchor':'middle' },
        bundle.role + (bundle.relations.length > 1 ? ' · ' + bundle.relations.length : ''));
      group.appendChild(label); graph.appendChild(group);
    });
    for (const [key, pos] of nodePositions) {
      const node = graphNodes.get(key);
      const nodeClass = node.type === 'personne' ? 'node-person' : node.type === 'fonds_commerce' ? 'node-sale' : 'node-org';
      const group = svgElement('g', { transform:'translate(' + pos.x + ' ' + pos.y + ')', class:'node ' + nodeClass + (key === rootId ? ' is-root' : ''),
        tabindex:0, role:'button', 'aria-label':node.name + ', ' + (node.type === 'personne' ? 'Personne' : categoryName(node.type)) + '. Cliquer pour recentrer le graphe et explorer ses liens.', 'data-node':key });
      group.appendChild(svgElement('title', {}, node.name + ' · ' + (adjacency.get(key)?.length || 0) + ' relation(s) documentée(s)'));
      group.appendChild(svgElement('rect', { x:-88, y:-34, width:176, height:68, rx:14, class:'node-surface' }));
      group.appendChild(svgElement('circle', { cx:-64, cy:-11, r:12, class:'node-icon' }));
      group.appendChild(svgElement('text', { x:-64, y:-7, 'text-anchor':'middle', class:'node-icon-text' }, ({ personne:'P', association:'A', fonds_commerce:'F' })[node.type] || 'S'));
      const words = String(node.name || 'Entité').split(/\s+/);
      const lines = []; let line = '';
      for (const word of words) {
        const next = line ? line + ' ' + word : word;
        if (next.length > 19 && line) { lines.push(line); line = word; } else line = next;
      }
      if (line) lines.push(line);
      const label = svgElement('text', { x:-44, y:-13, class:'node-label' });
      lines.slice(0, 2).forEach((part, row) => label.appendChild(svgElement('tspan', { x:-44, dy:row ? 14 : 0 }, part.length > 19 ? part.slice(0, 18) + '…' : part)));
      group.appendChild(label);
      const caption = key === rootId ? 'Point de départ' : (adjacency.get(key)?.length || 0) + ' relation(s)';
      group.appendChild(svgElement('text', { x:-72, y:23, class:'node-caption' }, caption));
      group.addEventListener('click', () => { if (Date.now() >= suppressClickUntil) focusRoot(key); });
      group.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); focusRoot(key); }
        if (event.key.toLowerCase() === 'e') { event.preventDefault(); focusRoot(key); }
      });
      graph.appendChild(group);
    }
    if (!preserveCamera || !cameraReady) {
      fitGraph();
      // Ouvrir à une échelle lisible autour du point de départ. « Tout voir »
      // reste disponible pour les réseaux qui dépassent la fenêtre.
      const scale = viewportSize().width / camera.width;
      if (scale < 0.72) {
        zoomGraph(0.82 / scale);
        camera.x = -camera.width / 2; camera.y = -camera.height / 2; updateCamera();
      }
    } else updateCamera();
    highlightSelection();
    if(root)selectNode(root);
    endOperation(operation,control);graphContainer.setAttribute('aria-busy','false');clearSurfaceBusy(graphContainer);
  }
  function relationMarkup(relation, selfId) {
    const from = entitiesById.get(String(relation.from));
    const to = entitiesById.get(String(relation.to));
    const other = selfId && graphNodeKey(from) === String(selfId) ? to : from;
    const announcement = announcementMap.get(String(relation.announcement_id));
    const source = sourceFor(announcement);
    const href = sourceHref(source, announcement?.pages);
    const kind = relationKind(relation);
    const date = formatDate(source?.date);
    const effect = formatDate(relation.event_date);
    const otherKey = graphNodeKey(other);
    return '<article class="relation-item relation-' + kind + '"><div class="relation-role">' + esc(relation.role || 'Lien relevé') + '</div>'
      + '<button type="button" class="relation-neighbor" data-node="' + esc(otherKey) + '">' + esc(graphNodes.get(otherKey)?.name || other?.name || 'Entité inconnue') + ' <span aria-hidden="true">↗</span></button>'
      + '<span class="proof-meta">' + esc(kindName(kind)) + ' · ' + esc(announcement?.reference || 'Annonce source') + (date ? ' · publié le ' + esc(date) : '') + (effect ? ' · effet le ' + esc(effect) : '') + '</span>'
      + (relation.quote ? '<blockquote class="proof-quote">« ' + esc(relation.quote) + ' »</blockquote>' : '<p class="relation-other">Citation justificative non fournie.</p>')
      + '<div class="proof-actions">' + (announcement ? '<button class="proof-link proof-view" type="button" data-announcement="' + esc(announcement.id) + '">Lire l’annonce</button>' : '')
      + (href ? '<a class="proof-link" href="' + esc(href) + '" target="_blank" rel="noopener">PDF source' + (announcement?.pages?.length ? ' · p. ' + esc(announcement.pages.join(', ')) : '') + ' ↗</a>' : '')
      + '</div></article>';
  }
  function bindDetailActions() {
    networkDetail.querySelectorAll('.proof-view').forEach(button => button.addEventListener('click', async () => {
      if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
      showView('annonces'); selectAnnouncement(button.dataset.announcement);
      details.scrollIntoView({ behavior:'smooth', block:'start' });
    }));
    networkDetail.querySelectorAll('[data-node]').forEach(button => button.addEventListener('click', () => focusRoot(button.dataset.node)));
  }
  function selectNode(node) {
    activeNode = node.id;
    const incident = filteredRelations().filter(relation => graphNodeKey(entitiesById.get(String(relation.from))) === node.id || graphNodeKey(entitiesById.get(String(relation.to))) === node.id);
    const occurrenceCount = node.occurrence_count || node.occurrenceIds.length;
    const groupedIdentity = occurrenceCount > 1;
    const methods = node.resolutionMethods || [];
    const groupingReason = methods.includes('identity_conflict') || methods.includes('review_after_split') ? 'Rattachement à vérifier après un conflit ou une séparation.' : methods.includes('same_name_presumed') ? 'Même nom complet : rapprochement présumé, à vérifier ultérieurement.' : methods.includes('organization_name_presumed') ? 'Rapprochement de société présumé à vérifier.' : methods.every(method => method === 'human_confirmed') ? 'Fusion confirmée par l’administrateur.' : methods.includes('cin_and_exact_name') ? 'Rapprochement par CIN et nom concordants, à confirmer.' : methods.includes('official_identifier') ? 'Rapprochement par identifiant officiel publié.' : 'Occurrences regroupées dans leurs annonces sources.';
    const kind = node.type === 'personne' ? (groupedIdentity ? 'Personne · occurrences regroupées' : 'Personne citée') : categoryName(node.type);
    networkDetail.innerHTML = '<span class="node-kind">' + esc(kind) + '</span><h3 class="node-detail-name">' + esc(node.name) + '</h3>'
      + (node.public_entity_id ? '<p class="entity-public-id">Identifiant JORT · ' + esc(node.public_entity_id) + '</p>' : '')
      + (groupedIdentity ? '<p class="identity-notice">' + occurrenceCount + ' occurrences réunies. ' + esc(groupingReason) + ' Les annonces sources restent consultables.</p>' : '')
      + (node.canonical_id ? '<details class="name-variants"><summary>Identifiant permanent</summary><code>' + esc(node.canonical_id) + '</code></details>' : '')
      + (node.identifier ? '<p class="relation-other">Identifiant publié : ' + esc(node.identifier) + '</p>' : '')
      + (node.nameVariants?.length > 1 ? '<details class="name-variants"><summary>Noms publiés (' + node.nameVariants.length + ')</summary><p>' + node.nameVariants.map(esc).join(' · ') + '</p></details>' : '')
      + (node.historicalNames?.length ? '<details class="name-variants"><summary>Historique des dénominations</summary>' + node.historicalNames.map(item => '<p><strong>' + esc(item.name) + '</strong>' + (item.name_status==='former' ? ' · ancien nom documenté' : ' · nom publié') + (item.observed_on ? ' · publié le ' + esc(formatDate(item.observed_on)) : '') + (item.valid_from ? ' · effet le ' + esc(formatDate(item.valid_from)) : '') + (item.quote ? '<br>« '+esc(item.quote)+' »' : '') + (item.announcement_id ? '<br><button class="proof-link proof-view" type="button" data-announcement="'+esc(item.announcement_id)+'">Lire l’annonce source</button>' : '') + '</p>').join('') + '</details>' : '')
      + '<div class="detail-actions"><button type="button" class="node-explore" data-node="' + esc(node.id) + '">Explorer depuis cette entité ↗</button></div>'
      + '<div class="node-connections"><h4>' + incident.length + ' relation' + (incident.length > 1 ? 's' : '') + ' documentée' + (incident.length > 1 ? 's' : '') + (eventFilter?.value !== 'all' ? ' · filtre actif' : '') + '</h4>'
      + (incident.length ? incident.map(relation => relationMarkup(relation, node.id)).join('') : '<p class="relation-other">Aucun lien pour le filtre choisi.</p>') + '</div>';
    bindDetailActions(); setDetailsVisible(true); highlightSelection();
  }
  function selectEdge(bundle) {
    activeNode = null;
    const from = graphNodes.get(bundle.from), to = graphNodes.get(bundle.to);
    networkDetail.innerHTML = '<span class="node-kind">' + esc(kindName(bundle.kind)) + '</span><h3 class="node-detail-name">' + esc(bundle.role) + '</h3>'
      + '<p class="relation-other">' + esc(from.name) + ' ↔ ' + esc(to.name) + '</p>'
      + '<div class="detail-actions"><button class="node-explore" data-node="' + esc(from.id) + '">Explorer ' + esc(from.name) + ' ↗</button></div>'
      + '<div class="node-connections"><h4>' + bundle.relations.length + ' preuve' + (bundle.relations.length > 1 ? 's' : '') + ' publiée' + (bundle.relations.length > 1 ? 's' : '') + '</h4>'
      + bundle.relations.map(relation => relationMarkup(relation, from.id)).join('') + '</div>';
    bindDetailActions(); setDetailsVisible(true); highlightSelection(bundle);
  }
  function openNetworkFromEntity(id) {
    if (!id) return;
    rootOverride = String(id);
    activeNode = String(id);
    showView('reseau', true, 'push');
  }
  function focusRoot(id, remember = true, pushHistory = true) {
    if (!id) return;
    if (remember && currentRoot && currentRoot !== String(id)) {
      pivotHistory.push(currentRoot);
      if (pivotHistory.length > 50) pivotHistory.shift();
    }
    networkSearch.value = '';
    if (eventFilter) eventFilter.value = 'all';
    rootOverride = String(id);
    activeNode = String(id);
    if (pushHistory && currentView === 'reseau' && !restoringHistory) writeNavigationState('push', 'reseau');
    renderGraph();
    const back = document.getElementById('graph-back');
    if (back) back.disabled = !pivotHistory.length;
  }
  document.getElementById('graph-back')?.addEventListener('click', () => {
    const previous = pivotHistory.pop();
    if (previous) focusRoot(previous, false, false);
  });
  let graphSearchTimer;
  function searchGraph() {
    window.clearTimeout(graphSearchTimer);
    pivotPage = 0; activeNode = null; rootOverride = ''; organizationSelect.value='';
    renderGraph();
    networkDetail.innerHTML = '<div class="empty-detail"><h3>Aucune entité trouvée</h3><p>Essayez un nom plus court ou une autre orthographe.</p></div>';
  }
  networkSearch.addEventListener('input', () => {
    window.clearTimeout(graphSearchTimer); graphSearchTimer = window.setTimeout(searchGraph, 160);
  });
  networkSearch.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); searchGraph(); } });
  document.getElementById('graph-search-submit')?.addEventListener('click', searchGraph);
  document.getElementById('pivot-prev').addEventListener('click', () => { pivotPage = Math.max(0, pivotPage - 1); activeNode = null; organizationSelect.value=''; renderGraph(); });
  document.getElementById('pivot-next').addEventListener('click', () => { pivotPage += 1; activeNode = null; organizationSelect.value=''; renderGraph(); });
  document.getElementById('graph-expand').addEventListener('click', () => { graphDepth = Math.min(4, graphDepth + 1); renderGraph(); });
  document.getElementById('graph-contract')?.addEventListener('click', () => { graphDepth = Math.max(1, graphDepth - 1); renderGraph(); });
  organizationSelect.addEventListener('change', () => { activeNode = null; renderGraph(); const node = graphNodes.get(currentRoot); if (node) selectNode(node); });
  eventFilter?.addEventListener('change', () => { renderGraph(); if (activeNode && graphNodes.has(activeNode)) selectNode(graphNodes.get(activeNode)); });
  document.getElementById('graph-reset').addEventListener('click', () => {
    pivotHistory.length = 0;
    if (document.getElementById('graph-back')) document.getElementById('graph-back').disabled = true;
    networkSearch.value = ''; eventFilter.value = 'all'; activeNode = null; pivotPage = 0; graphDepth = 2; organizationSelect.value = '';
    networkDetail.innerHTML = '<div class="empty-detail"><span class="empty-icon">◎</span><h3>Choisissez une entité ou un lien</h3><p>Le panneau affichera ses relations et les annonces qui les documentent.</p></div>';
    renderGraph();
  });
  document.getElementById('graph-zoom-in')?.addEventListener('click', () => zoomGraph(1.3));
  document.getElementById('graph-zoom-out')?.addEventListener('click', () => zoomGraph(1 / 1.3));
  document.getElementById('graph-fit')?.addEventListener('click', fitGraph);
  document.getElementById('graph-focus')?.addEventListener('click', () => {
    const point = nodePositions.get(String(activeNode));
    if (point) { camera.x = point.x - camera.width / 2; camera.y = point.y - camera.height / 2; updateCamera(); }
  });
  document.getElementById('graph-details-toggle')?.addEventListener('click', () => setDetailsVisible(graphWorkspace.classList.contains('details-hidden')));
  document.getElementById('graph-detail-close')?.addEventListener('click', () => { setDetailsVisible(false); document.getElementById('graph-details-toggle')?.focus(); });
  const networkView = document.getElementById('reseau-view');
  const fullscreenButton = document.getElementById('graph-fullscreen');
  function updateFullscreenButton() {
    if (!fullscreenButton) return;
    const expanded = Boolean(document.fullscreenElement || networkView.classList.contains('immersive-fallback'));
    fullscreenButton.setAttribute('aria-label', expanded ? 'Quitter le plein écran' : 'Passer le graphe en plein écran');
    fullscreenButton.title = expanded ? 'Quitter le plein écran' : 'Plein écran';
    fullscreenButton.setAttribute('aria-pressed', String(expanded));
  }
  fullscreenButton?.addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (networkView.requestFullscreen) await networkView.requestFullscreen();
      else networkView.classList.toggle('immersive-fallback');
    } catch (_) { networkView.classList.toggle('immersive-fallback'); }
    updateFullscreenButton();
  });
  document.addEventListener('fullscreenchange', updateFullscreenButton);
  graph.addEventListener('wheel', event => {
    event.preventDefault(); zoomGraph(Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * 0.004), pointerPosition(event));
  }, { passive:false });
  graph.addEventListener('pointerdown', event => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    pointers.set(event.pointerId, { x:event.clientX, y:event.clientY });
    if (!event.target.closest('.node, .edge-hit')) graph.setPointerCapture(event.pointerId);
    if (pointers.size === 1) dragging = { id:event.pointerId, x:event.clientX, y:event.clientY, startX:event.clientX, startY:event.clientY, moved:false };
    if (pointers.size === 2) {
      const pair = [...pointers.values()]; pinchDistance = Math.hypot(pair[0].x - pair[1].x, pair[0].y - pair[1].y);
      if (dragging) dragging.moved = true;
    }
  });
  graph.addEventListener('pointermove', event => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, { x:event.clientX, y:event.clientY });
    if (pointers.size === 2) {
      const pair = [...pointers.values()];
      const distance = Math.hypot(pair[0].x - pair[1].x, pair[0].y - pair[1].y);
      if (pinchDistance > 0 && distance > 0) zoomGraph(distance / pinchDistance);
      pinchDistance = distance; suppressClickUntil = Date.now() + 250; return;
    }
    if (!dragging || dragging.id !== event.pointerId) return;
    const size = viewportSize();
    if (Math.hypot(event.clientX - dragging.startX, event.clientY - dragging.startY) > 5) dragging.moved = true;
    if (dragging.moved) {
      if (!graph.hasPointerCapture(event.pointerId)) graph.setPointerCapture(event.pointerId);
      camera.x -= (event.clientX - dragging.x) / size.width * camera.width;
      camera.y -= (event.clientY - dragging.y) / size.height * camera.height;
      graphContainer.classList.add('dragging'); updateCamera();
    }
    dragging.x = event.clientX; dragging.y = event.clientY;
  });
  function endPointer(event) {
    const wasDrag = dragging?.moved;
    pointers.delete(event.pointerId);
    if (graph.hasPointerCapture(event.pointerId)) graph.releasePointerCapture(event.pointerId);
    if (wasDrag) suppressClickUntil = Date.now() + 250;
    dragging = null; pinchDistance = 0; graphContainer.classList.remove('dragging');
    if (pointers.size === 1) {
      const [id, point] = [...pointers.entries()][0];
      dragging = { id, x:point.x, y:point.y, startX:point.x, startY:point.y, moved:true };
    }
  }
  graph.addEventListener('pointerup', endPointer);
  graph.addEventListener('pointercancel', endPointer);
  graph.addEventListener('click', event => { if (Date.now() < suppressClickUntil) { event.preventDefault(); event.stopImmediatePropagation(); } }, true);
  graph.addEventListener('keydown', event => {
    if (event.target !== graph) return;
    const step = camera.width * 0.08;
    if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','Home'].includes(event.key)) event.preventDefault();
    if (event.key === 'ArrowLeft') camera.x -= step;
    if (event.key === 'ArrowRight') camera.x += step;
    if (event.key === 'ArrowUp') camera.y -= step;
    if (event.key === 'ArrowDown') camera.y += step;
    if (event.key === '+' || event.key === '=') zoomGraph(1.3);
    if (event.key === '-') zoomGraph(1 / 1.3);
    if (event.key === 'Home') fitGraph();
    updateCamera();
  });
  if (window.ResizeObserver) new ResizeObserver(() => {
    if (document.getElementById('reseau-view').hidden || !cameraReady) return;
    const size = viewportSize(), center = { x:camera.x + camera.width / 2, y:camera.y + camera.height / 2 };
    camera.height = camera.width * size.height / size.width;
    camera.x = center.x - camera.width / 2; camera.y = center.y - camera.height / 2; updateCamera();
  }).observe(graphContainer);
  // Compteurs de toute la vue publiée, indépendants de la page chargée.
  document.getElementById('source-count').textContent = (stats.sources || sources.length);
  document.getElementById('announcement-count').textContent = (stats.announcements || 0);
  document.getElementById('relation-count').textContent = (stats.relations || 0);
  document.getElementById('entity-count').textContent = (stats.consultable_entities||stats.unique_entities||0).toLocaleString('fr-FR');
  document.getElementById('tab-announcement-count').textContent = (stats.announcements || 0);
  const coverageCount = document.getElementById('coverage-count');
  const coverageStatus = document.getElementById('coverage-status');
  const coverageNotes = document.getElementById('coverage-notes');
  const indexedYears = Array.isArray(coverage.available_years) ? coverage.available_years : (coverage.year ? [coverage.year] : []);
  coverageCount.textContent = `${(stats.announcements || 0).toLocaleString('fr-FR')} annonces indexées · ${(stats.sources || sources.length).toLocaleString('fr-FR')} journaux sources`;
  coverageStatus.textContent = indexedYears.length ? `Années présentes dans la base : ${indexedYears.join(', ')}` : 'Aucune année indexée';
  coverageNotes.textContent = 'La base regroupe les années déjà traitées. La couverture peut rester partielle pour chaque année.';

  // Vérifier seulement le petit marqueur de publication; le gros corpus n'est
  // rechargé que lorsque l'utilisateur choisit explicitement de l'actualiser.
  let pageRevision = String(data.revision || '');
  let statusRefreshActive = false;
  async function refreshLiveStatus() {
    if (statusRefreshActive || document.visibilityState !== 'visible') return;
    statusRefreshActive = true;
    try {
      const response = await fetch('/publication.json', { cache: 'no-store' });
      if (!response.ok) return;
      const live = await response.json();
      if (pageRevision && live.revision && live.revision !== pageRevision && live.state === 'ready') {
        const fresh=await api('/api/restitution/meta');
        Object.assign(stats,fresh.stats);Object.assign(coverage,fresh.coverage);
        const knownSources=new Set([...sourceFilter.options].map(option=>option.value));
        fresh.sources.forEach(source=>{
          sourcesById.set(String(source.id),source);
          if(!knownSources.has(String(source.id))){const option=document.createElement('option');option.value=String(source.id);option.textContent=sourceLabel(source);sourceFilter.appendChild(option);}
        });
        document.getElementById('source-count').textContent=fresh.sources.length.toLocaleString('fr-FR');
        document.getElementById('announcement-count').textContent=stats.announcements.toLocaleString('fr-FR');
        document.getElementById('relation-count').textContent=stats.relations.toLocaleString('fr-FR');
        document.getElementById('entity-count').textContent=(stats.consultable_entities||stats.unique_entities).toLocaleString('fr-FR');
        document.getElementById('tab-announcement-count').textContent=stats.announcements.toLocaleString('fr-FR');
        coverageCount.textContent=`${stats.announcements.toLocaleString('fr-FR')} annonces indexées · ${fresh.sources.length.toLocaleString('fr-FR')} journaux sources`;
        coverageStatus.textContent='Années présentes dans la base : '+coverage.available_years.join(', ');
        pageRevision=fresh.revision;
        if(currentView==='entites')renderEntities();
        if(currentView==='annonces')renderList();
        if(currentView==='reseau'){rootOverride=currentRoot;renderGraph(true);}
        document.getElementById('dataset-update-notice')?.removeAttribute('hidden');
      }
    } catch (_) {
      // Le dernier relevé intégré reste affiché si le serveur est temporairement indisponible.
    } finally {
      statusRefreshActive = false;
    }
  }
  document.getElementById('dataset-update-reload')?.addEventListener('click', () => {
    sessionStorage.setItem('jort-restauration-state',JSON.stringify({view:currentView,search:search.value,category:categoryFilter.value,source:sourceFilter.value,page:pageIndex,network:networkSearch.value,exact:exactSearchEnabled,root:currentRoot,depth:graphDepth,event:eventFilter.value,selected:selectedAnnouncement,entitySearch:entitySearch.value,entityType,entityPage,fixedEntity}));
    window.location.reload();
  });
  refreshLiveStatus();
  window.setInterval(refreshLiveStatus, 30000);
  window.addEventListener('focus', refreshLiveStatus);
  try {
    const saved = JSON.parse(sessionStorage.getItem('jort-restauration-state') || 'null');
    if (saved) {
      search.value = saved.search || '';
      categoryFilter.value = saved.category || 'toutes';
      if ([...sourceFilter.options].some(option => option.value === saved.source)) sourceFilter.value = saved.source;
      pageIndex = Number(saved.page || 0);
      networkSearch.value = saved.network || '';
      entitySearch.value=saved.entitySearch||'';entityType=saved.entityType||'';entityPage=Number(saved.entityPage||0);
      fixedEntity=saved.fixedEntity||null;
      if(fixedEntity){entityFilter.innerHTML=`<option value="">Toutes les entités trouvées</option><option value="${esc(fixedEntity.id)}">${esc(fixedEntity.name)}</option>`;entityFilter.value=fixedEntity.id;entityFilter.hidden=false;}
      document.querySelectorAll('[data-entity-type]').forEach(button=>{const active=button.dataset.entityType===entityType;button.classList.toggle('selected',active);button.setAttribute('aria-pressed',String(active));});
      exactSearchEnabled = Boolean(saved.exact);
      exactSearchControls.forEach(control => { control.checked = exactSearchEnabled; });
      rootOverride = saved.root || '';
      graphDepth = Math.max(1, Math.min(4, Number(saved.depth) || 2));
      if ([...eventFilter.options].some(option => option.value === saved.event)) eventFilter.value = saved.event;
      if (saved.selected) selectAnnouncement(saved.selected);
      renderGraph();
      if (saved.node && graphNodes.has(saved.node)) selectNode(graphNodes.get(saved.node));
      showView(saved.view || 'entites');
      sessionStorage.removeItem('jort-restauration-state');
    }
  } catch (_) { sessionStorage.removeItem('jort-restauration-state'); }
  const requestedAnnouncement = new URLSearchParams(location.search).get('annonce');
  const requestedEntity = new URLSearchParams(location.search).get('entite');
  if (requestedAnnouncement) selectAnnouncement(requestedAnnouncement);
  if (requestedEntity) {
    rootOverride = requestedEntity;
    showView('reseau', false);
  } else if(location.hash==='#annonces'||requestedAnnouncement) {
    showView('annonces', false);
  } else {
    showView('entites', false);
  }
  writeNavigationState('replace', currentView || 'entites');
})();
