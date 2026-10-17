(() => {
  'use strict';
  const container = document.getElementById('service-health');
  if (!container) return;
  const overallBadge = container.querySelector('[data-service="overall"]');
  const apiBadge = container.querySelector('[data-service="api"]');
  const taskBadge = container.querySelector('[data-service="processing"]');
  const labels = { overall:'État général de l’application', api:'État du serveur local et des contrôles au démarrage', processing:'État des traitements locaux' };
  const dialog = document.createElement('dialog');
  dialog.className = 'health-details';
  dialog.innerHTML = '<header><h2></h2><button type="button" aria-label="Fermer">Fermer</button></header><p></p>';
  document.body.append(dialog);
  dialog.querySelector('button').addEventListener('click', () => dialog.close());
  container.addEventListener('click', event => {
    const badge = event.target.closest('button[data-service]');
    if (!badge) return;
    dialog.querySelector('h2').textContent = labels[badge.dataset.service] || 'État du service';
    dialog.querySelector('p').textContent = badge.title || badge.textContent.trim();
    dialog.showModal();
  });

  function setBadge(element, state, label, detail = '') {
    if (!element) return;
    element.dataset.state = state;
    const text = element.querySelector('span');
    if (text) text.textContent = label;
    element.title = detail || label;
  }

  async function refreshHealth() {
    if (document.visibilityState !== 'visible') return;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3500);
    try {
      const response = await fetch('/api/health', { cache: 'no-store', signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const health = await response.json();
      const startup = health.startup_check || {};
      const warnings = Array.isArray(startup.warnings) ? startup.warnings : [];
      const preflight = startup.application_preflight || {};
      const apiError = startup.state === 'failed' || preflight.state === 'failed';
      const apiWarning = !apiError && (startup.state && startup.state !== 'ok' || warnings.length > 0 || startup.message || preflight.state === 'attention');
      const apiState = apiError ? 'error' : apiWarning ? 'warning' : 'ok';
      const deferred = Array.isArray(startup.deferred_checks) ? startup.deferred_checks : [];
      setBadge(apiBadge, apiState, apiError ? 'API · erreur' : apiWarning ? 'API · attention' : 'API · opérationnelle',
        [startup.message, ...warnings, ...(preflight.warnings || []), ...(deferred.length ? [`Contrôles approfondis reportés (contrôle léger au démarrage) :`, ...deferred] : [])].filter(Boolean).join('\n') || 'Le serveur local et les contrôles de démarrage répondent normalement.');

      const states = Object.values(health.lanes || {}).map(lane => typeof lane === 'string' ? lane : lane?.state);
      const publication = health.publication || {};
      if (states.some(state => state === 'failed')) {
        setBadge(taskBadge, 'error', 'Traitement · erreur', 'Une tâche récente a échoué. Consultez les logs dans l’administration.');
      } else if (states.some(state => state === 'paused')) {
        setBadge(taskBadge, 'warning', 'Traitement · en pause', 'Une tâche attend une reprise. Consultez les logs et les ressources disponibles.');
      } else if (health.worker_active || ['pending', 'publishing'].includes(publication.state) || states.some(state => ['running', 'stopping', 'pending'].includes(state))) {
        setBadge(taskBadge, 'warning', 'Traitement · en cours', publication.error ? `La restitution attend une nouvelle publication : ${publication.error}` : 'Une tâche locale est en cours.');
      } else {
        setBadge(taskBadge, 'ok', 'Traitements · prêts', 'Aucun traitement local en cours.');
      }
      const taskState = taskBadge?.dataset.state || 'unknown';
      const overallState = apiState === 'error' || taskState === 'error' ? 'error'
        : apiState === 'warning' || taskState === 'warning' || taskState === 'unknown' ? 'warning' : 'ok';
      const overallLabel = overallState === 'error' ? 'Application · erreur' : overallState === 'warning' ? 'Application · attention' : 'Application · opérationnelle';
      setBadge(overallBadge, overallState, overallLabel, `${apiBadge?.title || ''}\n${taskBadge?.title || ''}`.trim());
    } catch (_) {
      setBadge(apiBadge, 'error', 'API · indisponible', 'Le serveur local ne répond pas. Relancez demarrer.ps1 puis actualisez la page.');
      setBadge(taskBadge, 'unknown', 'Traitements · inconnus', 'État indisponible tant que l’API locale ne répond pas.');
      setBadge(overallBadge, 'error', 'Application · indisponible', 'Le serveur local ne répond pas. Relancez demarrer.ps1.');
    } finally {
      clearTimeout(timeout);
    }
  }

  refreshHealth();
  window.setInterval(refreshHealth, 10000);
  window.addEventListener('focus', refreshHealth);
  document.addEventListener('visibilitychange', refreshHealth);
})();
