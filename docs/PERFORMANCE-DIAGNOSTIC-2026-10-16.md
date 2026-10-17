# Diagnostic et correctif préparé — 16 octobre 2026

## Conclusion

Le principal problème constaté est la requête de recherche des entités. Les index
de recherche existent et sont valides, mais le plan actuel ne les exploite pas
efficacement. Aucune fuite mémoire n'est établie par les données disponibles.
Le correctif est préparé ; il n'est pas installé dans le serveur.

## Mesures et limites

- `data/logs/observation-performance-jort.jsonl` contient 90 échantillons entre
  **10:13:28 et 10:23:13, heure Africa/Lagos**. Les 90 connexions PostgreSQL et les
  270 sondes API ont échoué pour défaut d'accès réseau. Les petites durées de ces
  erreurs ne représentent pas la vitesse du site. Les processus visibles dans
  cette session restreinte ne permettent pas d'évaluer la mémoire du serveur.
- L'ancien observateur sondait `/api/recherche` et `/api/annonces`, qui ne sont
  pas les routes utilisées par l'application. Le nouvel observateur les corrige.
- `data/logs/postgresql.log` montre des recherches d'entités interrompues par
  `statement timeout` entre **10:14:56 et 10:17:40**. Le connecteur fixe ce délai
  à **120 secondes**. Plusieurs connexions exécutent des recherches semblables.
- Le journal HTTP indique les réponses, mais pas leur durée : il ne permet pas
  de mesurer précisément le chargement d'une annonce ni le rendu du graphe.
- Le diagnostic PostgreSQL en lecture seule confirme les index valides
  `jort_entity_search`, `jort_names_search_trgm` et `jort_notice_search`.
- Pour « cofat », EXPLAIN de l'ancienne requête prévoit un parcours des
  **644 532** lignes estimées de `jort_entity_metrics`, puis des recherches
  répétées dans les alias. Coût estimé : **13 738 182,7**. La proposition utilise
  les deux index de noms ; coût estimé du plan initial : **4 061,2**. Ces nombres
  sont des unités du planificateur, pas des durées ni un gain mesuré.
- Paramètres observés : shared_buffers 128 Mio, work_mem 8 Mio,
  max_connections 50. Une seule connexion, celle du diagnostic, était active
  à cet instant. Cela ne décrit pas le pic pendant les manipulations.
- Les statistiques estiment 43 577 anciennes versions de lignes dans les
  entités, 45 604 dans les mentions et 42 613 dans les alias après les fusions.
  Un VACUUM ordinaire peut rendre cet espace réutilisable. Il ne réduit pas
  nécessairement les fichiers sur disque et ne supprime pas d'annonces.

Trois appels directs au code proposé ont ensuite été effectués en transactions
READ ONLY, avec un délai maximum de 5 secondes **par instruction SQL** et
20 résultats maximum par page. L'ancienne requête lente n'a pas été relancée.

| Recherche | Total exact | Fiches retournées | Temps consultation |
|---|---:|---:|---:|
| cofat | 78 | 20 | 684,1 ms |
| one tech mobile | 1 | 1 | 547,4 ms |
| hedi karim sellami | 2 | 2 | 535,0 ms |

Ces mesures incluent connexion, comptage, lecture de la page, des mentions et
de l'historique. Elles excluent le transport HTTP et le rendu dans le navigateur.
Un seul passage par recherche a été réalisé : ce n'est pas un test de charge.

## Correctif proposé

1. Chercher les identifiants correspondants dans les noms principaux et les
   alias via leurs index, avec UNION pour éviter les résultats en double.
2. Pour plusieurs mots, croiser les ensembles avec INTERSECT : chaque mot doit
   correspondre à la même entité, y compris dans des alias différents.
3. Compter et trier ces candidats. Conserver les totaux exacts, l'ordre des
   résultats, l'exclusion des fiches retirées, les filtres et la pagination.
4. Actualiser les statistiques PostgreSQL. En option, VACUUM ordinaire des
   tables de consultation après les fusions.
5. En option, ajouter deux index ciblés : liste des entités actives par nom,
   et annonces visibles par date décroissante avec les dates inconnues à la fin.
   L'index de date actuel utilise par défaut les dates NULL en tête, contrairement
   au tri demandé par la requête ; le nouvel index correspond à ce tri.

Le patch ne plafonne pas le nombre de candidats, ne fusionne aucune fiche,
ne modifie pas les annonces, et ne change aucun réglage mémoire global.

## Scripts prêts, non appliqués

- `scripts/ameliorer_performance_jort.py` : plan par défaut, installation et retour.
- `scripts/optimisation_performance/recherche_entites.py` : méthode SQL proposée.
- `optimiser-performance-jort.ps1` : lanceur utilisant Python du projet.
- `scripts/observer_performance_jort.py` : observateur corrigé, non relancé.

Afficher le plan, sans accès PostgreSQL ni changement :

```powershell
.\optimiser-performance-jort.ps1 -Action Plan
```

Après décision d'appliquer, arrêter le site et les traitements avec le lanceur
habituel. Puis lancer **dans la session Windows disposant de l'accès local** :

```powershell
.\optimiser-performance-jort.ps1 -Action Appliquer -AvecIndex -NettoyerVersionsMortes
```

Le lanceur démarre PostgreSQL seul. Le script contrôle l'arrêt du port HTTP,
l'absence d'autre opération SQL active et l'empreinte du code attendu ; il
sauvegarde le module avant toute installation. Les index sont créés un par un
avec `CONCURRENTLY` ; seuls ceux absents sont ajoutés. Un index invalide provoque
un arrêt explicite, sans suppression automatique. Une nouvelle exécution reprend
les index valides déjà créés. Le code est remplacé après le succès de la maintenance.

État et sauvegarde : `data/maintenance/performance/installation.json` et
`consultation_postgresql.avant-performance.py`. Le script laisse le redémarrage
du site au lanceur habituel. Il ne l'a pas exécuté durant cette préparation.

Pour remettre le code précédent, site arrêté :

```powershell
.\optimiser-performance-jort.ps1 -Action Retour
```

Le retour refuse d'écraser des changements ultérieurs. Les index créés restent
en place ; aucun index existant n'est supprimé.

## Nouvelle observation

Après redémarrage du site, lancement manuel pour 10 minutes :

```powershell
.\.venv\Scripts\python.exe -B scripts\observer_performance_jort.py --minutes 10
```

Le script vérifie d'abord les accès à PostgreSQL et à `/api/health`. S'ils sont
refusés, il s'arrête et le signale. Il observe les requêtes et leurs attentes,
les connexions, le cache et les fichiers temporaires toutes les 5 secondes.
Les index et la mémoire des processus sont relevés une fois par minute.
Les relevés de processus Python/PostgreSQL doivent être rapprochés des PID de
JORT : d'autres logiciels peuvent utiliser ces mêmes exécutables.

Les sessions sont séparées dans `data/logs/observations-performance/`, limitées
à 10 Mio et 20 minutes par défaut. L'ancien journal est conservé. `--probe` ajoute
une seule sonde API par minute, sur les vraies routes, en lisant la réponse
complète jusqu'à 8 Mio. Les sondes sont désactivées après un échec pour ne pas
empiler des requêtes dont le serveur pourrait poursuivre l'exécution.

Ces mesures ne donnent toujours pas le temps de rendu du navigateur. Le prochain
contrôle doit chronométrer l'ouverture d'annonce, la recherche et le graphe dans
le navigateur, puis les rapprocher de l'activité SQL.

## Suite proposée après la correction principale

- Charger les variantes détaillées et l'historique au clic plutôt que pour
  chaque carte de résultat : `_nodes` enrichit actuellement toute la page.
- Ajouter un temps d'exécution et un identifiant de requête aux logs HTTP.
- Éviter plusieurs recherches identiques en vol et limiter le travail SQL
  poursuivi après une annulation navigateur. Le code actuel ferme bien la
  connexion en fin de requête, mais une annulation navigateur ne suffit pas à
  arrêter immédiatement la requête PostgreSQL.
- Réduire les appels de santé répétés quand un onglet est masqué. Les logs en
  montrent plusieurs à la même seconde ; la présence de plusieurs onglets ou
  composants n'est pas distinguable dans ces logs.
- Mesurer la pagination sans recherche, l'annonce et le graphe avant d'ajouter
  d'autres index ou de modifier la mémoire. La lenteur des annonces reste à isoler.

## Vérifications de préparation

Six tests isolés réussis, dont 270 comparaisons ancienne/nouvelle recherche sur
une petite base en mémoire : alias répartis entre plusieurs mots, doublons,
recherche exacte, filtres, fiches retirées, classement et pagination. Vérification
du refus d'un patch périmé et du plan sans connexion à la base. Trois appels
PostgreSQL en lecture seule confirment l'exécution de la nouvelle requête.
L'installation, le VACUUM et la création d'index n'ont pas été exécutés.
