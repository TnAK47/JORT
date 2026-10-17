(() => {
  'use strict';
  const panel = document.getElementById('identity-manager');
  if (!panel) return;
  const el = id => document.getElementById(id);
  const selected = new Map();
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let previewRevision = 0;
  let previewReady = false;
  let sending = false;
  async function read(path) {
    const response = await fetch('/api/collecte/' + path, { cache:'no-store' });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'Référentiel indisponible');
    return body;
  }
  function enableMerge() {
    el('identity-merge').disabled = sending || !previewReady || selected.size < 2 || !el('identity-confirmed').checked || el('identity-reason').value.trim().length < 5;
  }
  async function preview() {
    previewReady = false; enableMerge();
    const revision = ++previewRevision;
    el('identity-selected').innerHTML = [...selected.values()].map(row => `<span class="entity-tag">${esc(row.label)} <button type="button" data-remove="${esc(row.id)}" aria-label="Retirer ${esc(row.label)}">×</button></span>`).join('') || 'Aucune fiche sélectionnée.';
    el('identity-selected').querySelectorAll('[data-remove]').forEach(button => button.addEventListener('click', () => {
      selected.delete(button.dataset.remove);
      const checkbox = panel.querySelector(`[data-identity="${button.dataset.remove}"]`);
      if (checkbox) checkbox.checked = false;
      preview();
    }));
    el('identity-confirmed').checked = false;
    if (selected.size < 2) {
      el('identity-preview').textContent = 'Choisissez au moins deux fiches. La première sélectionnée garde son identifiant permanent.';
      return;
    }
    el('identity-preview').textContent = 'Préparation de l’aperçu des connexions…';
    try {
      const result = await read('identity-preview?ids=' + encodeURIComponent([...selected.keys()].join(',')));
      if (revision !== previewRevision) return;
      el('identity-preview').innerHTML = `<p><strong>${result.neighbors_count} entités voisines · ${result.relations} preuves de liens · ${result.announcements} annonces avec liens.</strong> Les annonces et toutes les occurrences seront conservées.</p><ul>${result.neighbors.map(row => `<li>${esc(row.name)} — ${esc(row.roles.join(', '))}</li>`).join('')}</ul>${result.neighbors_count > 50 ? '<p>Aperçu limité aux 50 premiers voisins ; toutes les connexions seront réunies.</p>' : ''}`;
      previewReady = true; enableMerge();
    } catch (error) { if (revision === previewRevision) el('identity-preview').textContent = error.message; }
  }
  async function refresh() {
    el('identity-message').textContent = 'Recherche dans toutes les années…';
    try {
      const result = await read('identities?q=' + encodeURIComponent(el('identity-query').value));
      el('identity-message').textContent = result.ready ? `${result.entities.length} fiches affichées (80 maximum). Affinez le nom si nécessaire.` : 'Lancez « Compléter les titres et les liens » pour créer le référentiel global.';
      el('identity-results').innerHTML = result.entities.map(row => `<article class="identity-admin-card"><label><input type="checkbox" data-identity="${esc(row.id)}" ${selected.has(row.id) ? 'checked' : ''}><strong>${esc(row.label)}</strong></label><p>${esc(row.type)} · ${row.occurrences} occurrences · <code>${esc(row.id)}</code></p><p>Variantes : ${row.names.map(esc).join(' · ')}</p><details><summary>Consulter les preuves (${row.evidence.length} premières occurrences)</summary>${row.evidence.map(item => `<p>${esc(item.name)}${item.cin ? ` · CIN ${esc(item.cin)}` : ''}${item.identifier ? ` · identifiant ${esc(item.identifier)}` : ''} · <a href="/?annonce=${encodeURIComponent(item.announcement_id)}#annonces" target="_blank" rel="noopener">Annonce ${esc(item.announcement_id.split(':')[1] || item.announcement_id)} ↗</a></p>`).join('')}</details><a href="/?entite=${encodeURIComponent(row.id)}#liens" target="_blank" rel="noopener">Explorer les connexions ↗</a></article>`).join('');
      const byId = new Map(result.entities.map(row => [row.id, row]));
      el('identity-results').querySelectorAll('[data-identity]').forEach(input => input.addEventListener('change', () => {
        if (input.checked) {
          if (selected.size >= 100) { input.checked = false; return; }
          selected.set(input.dataset.identity, byId.get(input.dataset.identity));
          if (selected.size === 1) el('identity-label').value = byId.get(input.dataset.identity).label;
        } else selected.delete(input.dataset.identity);
        preview();
      }));
      el('identity-history').innerHTML = result.merges.map(row => `<article class="identity-admin-card"><strong>Fusion ${esc(new Date(row.created_at).toLocaleString('fr-FR'))}</strong><p>${esc(row.reason)}</p><button class="button quiet" type="button" data-undo="${esc(row.id)}" data-maintenance-scope="all">Annuler cette fusion</button></article>`).join('') || '<p>Aucune fusion manuelle annulable.</p>';
      el('identity-history').querySelectorAll('[data-undo]').forEach(button => button.addEventListener('click', async () => {
        await window.JORT_ADMIN.runMaintenance('maintenance_undo_merge', null, null, button, { event_id:button.dataset.undo, confirmed:true });
      }));
    } catch (error) { el('identity-message').textContent = error.message; }
  }
  el('identity-search').addEventListener('click', refresh);
  el('identity-query').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); refresh(); } });
  el('identity-confirmed').addEventListener('change', enableMerge);
  el('identity-reason').addEventListener('input', enableMerge);
  el('identity-merge').addEventListener('click', async () => {
    if (!previewReady || sending || !el('identity-confirmed').checked || selected.size < 2) return;
    sending = true; enableMerge();
    const accepted = await window.JORT_ADMIN.runMaintenance('maintenance_merge_entities', null, null, el('identity-merge'), {
      canonical_ids:[...selected.keys()], label:el('identity-label').value.trim(), reason:el('identity-reason').value.trim(), confirmed:true
    });
    sending = false;
    if (accepted) { selected.clear(); preview(); el('identity-message').textContent = 'Fusion demandée. Suivez la maintenance en haut de la page, puis actualisez les fiches.'; }
    enableMerge();
  });
  window.addEventListener('jort:entities-updated', () => { selected.clear(); preview(); refresh(); });
})();
