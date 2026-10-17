# Plan d’évolution de la recherche et des liens JORT

Ce document prépare la prochaine évolution de l’application privée JORT. Il ne lance aucune collecte, extraction, migration ou publication. Les opérations qui écrivent dans les bases attendent l’arrêt propre des batchs.

## Résultat attendu

- Rechercher rapidement dans toutes les annonces indexées, tous millésimes confondus, avec les annonces les plus récentes en premier.
- Donner à chaque personne et organisation une fiche durable, un identifiant stable et des preuves consultables.
- Réduire les doublons sans perdre les occurrences, CIN, identifiants ou annonces sources.
- Extraire de meilleurs titres et les parties des ventes de fonds de commerce.
- Recentrer le graphe en un clic, régler sa profondeur et garder les preuves de chaque lien.
- Présenter une interface cohérente, lisible et adaptée aux grands graphes comme aux longues listes.

## Dépendances entre les programmes

```text
PDF et inventaire annuel
        ↓
scripts/texte_pdf.py → scripts/extraire_entites.py
        ↓                         ↓
scripts/construire_base_2024.py (base SQLite et base.json par année)
        ↓
scripts/referentiel_entites.py (fiches, identifiants canoniques, fusions et redirections)
        ↓
scripts/agregation_base.py (vue globale dist/data.json et dist/data.js)
        ↓
scripts/serveur_local.py (recherche, annonces, administration et état)
        ↓
dist/app.js et dist/index.html (recherche, fiches et graphe)
```

`scripts/maintenance_base.py` orchestre les vérifications, sauvegardes, réparations, fusions, réextractions, reconstructions et optimisations. Les pages d’administration dans `dist/collecte.html`, `dist/collecte.js` et `dist/administration.css` déclenchent ces actions et affichent leur progression. Les scripts de démarrage/arrêt gèrent le serveur et la reprise; ils ne doivent pas lancer implicitement une migration de données.

Le couplage principal est le suivant : l’extracteur écrit les entités et relations annuelles; le registre leur assigne les identités durables; l’agrégateur applique les identités aux annonces de toutes les années; la recherche et le graphe consomment cette vue. Toute modification d’une étape doit donc versionner son résultat et invalider seulement les étapes dépendantes.

## Ordre d’exécution

### 0. Arrêt et point de reprise

1. Demander l’arrêt normal des batchs de collecte et d’indexation depuis l’interface ou le lanceur.
2. Attendre que chaque tâche active soit terminée ou marquée arrêtée avec son numéro courant et son point de reprise durable.
3. Vérifier que les processus JORT et le serveur ont quitté, que les verrous sont cohérents et que les fichiers `.tmp` ne sont pas en cours d’écriture.
4. Réconcilier `data/collecte-traitement.json` avec `data/collecte-integrale/etat.json` et les journaux d’exécution. L’état « running » de l’un et « stopped » de l’autre observé le 12 octobre ne suffit pas à prouver qu’un worker travaille encore.

**Arrêt obligatoire si** une tâche apparaît active sans processus correspondant, ou si un processus écrit encore dans `data/`, `dist/` ou un journal. Ne pas lancer deux opérations de maintenance concurrentes.

### 1. Sauvegarde et diagnostic global

À serveur arrêté :

```powershell
& '<python-du-lanceur-JORT>' scripts/maintenance_base.py --mode backup
& '<python-du-lanceur-JORT>' scripts/maintenance_base.py --mode check
```

Conserver le rapport, les bases annuelles SQLite/JSON, `data/referentiel-entites.sqlite`, `data/identity-merges.json`, les sources PDF et les références Drive. Les sauvegardes sont enregistrées par la maintenance sous `data/maintenance/`.

Examiner les doublons d’annonces, les identifiants contradictoires, les entités sans titre, les relations sans annonce source et les états d’indexation incomplets. Résoudre les erreurs bloquantes avant le réindexage.

### 2. Mettre à jour le schéma d’identités

Modifier ensemble :

- `scripts/schema_evolution.py` et `scripts/referentiel_entites.py` pour conserver les UUID canoniques déjà émis, ajouter un numéro lisible séquentiel immuable, les types d’identifiants multiples, leurs références et les redirections après fusion.
- `scripts/agregation_base.py` pour rapprocher les identités sur CIN personne et MF/RNE/RC organisation seulement si le type, la valeur et la preuve concordent.
- `scripts/maintenance_base.py` pour ajouter un rapport préalable, une migration reprenable par lots et un retour arrière à partir de la sauvegarde.

Ne pas remplacer les identifiants existants. Le numéro séquentiel devient un alias stable, jamais réutilisé. Le type personne/société/association/organisation reste distinct du type d’identifiant. Préserver les zéros initiaux et qualifier un RC par son registre si nécessaire. Une contradiction d’identifiant bloque la fusion automatique et devient une intervention humaine.

**Vérifier avant de poursuivre :** chaque occurrence annuelle renvoie à un identifiant canonique; les anciens identifiants résolvent vers la fiche retenue; les fusions gardent leur historique réversible et les preuves.

### 3. Corriger la déduplication sans toucher aux annonces sources

Modifier `scripts/referentiel_entites.py`, `scripts/maintenance_base.py` et les règles d’agrégation :

- Fusion automatique si un identifiant officiel concordant et correctement attribué établit la même entité.
- Pour une personne sans CIN exploitable, regrouper par nom exactement normalisé avec statut « rapprochement présumé »; garder séparés les noms seulement ressemblants.
- Détecter les incohérences de CIN, MF, RNE ou RC et les présenter à l’administrateur.
- Exclure les objets génériques comme « Fonds de commerce » de la fusion par nom.
- Conserver les occurrences, variantes de nom, CIN, événements de fusion et annonces sources. Ne supprimer que les fiches canoniques redondantes après création d’une redirection et d’une sauvegarde.

Réutiliser les mécanismes existants `merge_duplicates`, `merge_person`, `merge_events`, `entity_redirects` et `identity_exclusions`; étendre leur rapport au lieu de créer une seconde logique de fusion.

### 4. Compléter l’extraction et les titres

Modifier `scripts/extraire_entites.py`, `scripts/construire_base_2024.py`, puis `scripts/maintenance_base.py` :

- Séparer le titre de l’annonce, l’entité principale, les autres entités citées et le type d’événement.
- Pour une vente, extraire séparément vendeur, acheteur et objet du fonds (nom commercial, activité, adresse ou lieu quand ils sont explicitement publiés).
- Éviter les titres constitués de codes d’annonce, de catégorie seule ou de l’expression générique « Fonds de commerce » si un objet est présent.
- Garder pour chaque champ le passage source, la page et la référence PDF; signaler les valeurs incertaines.
- Incrémenter la version d’extraction. Ne retraiter que les journaux dont la version ou les données sont absentes/incomplètes; ne pas télécharger de nouveau les PDF déjà indexés.

Exécuter d’abord la réextraction sur un échantillon de journaux déjà connus, inspecter les sorties et comparer titres, entités, relations et références avant de planifier le rattrapage complet.

### 5. Reconstruire les index de recherche et les liens

Modifier `scripts/construire_base_2024.py`, `scripts/agregation_base.py`, `scripts/serveur_local.py` et les points de maintenance :

- Ajouter/compléter un index de recherche plein texte et des index SQLite sur date de publication, catégorie, identifiants et références.
- Trier par date de publication décroissante, puis par identifiant stable; garder séparée toute date d’effet.
- Paginer côté serveur. Ne plus envoyer ni analyser les quelque 406 Mo actuels de `dist/data.js` au premier affichage.
- Charger les détails et preuves au besoin; publier les vues avec un marqueur de révision et un remplacement atomique.
- Construire le graphe à partir des identités canoniques et de relations explicitement justifiées. Une relation indirecte via une société doit afficher le chemin et les preuves de chaque segment.

Après validation de l’échantillon, reconstruire les index de recherche, le registre d’entités, les relations et les vues globales. Opérer par lots reprenables, en conservant la dernière vue publiable si un lot échoue.

### 6. Mettre à jour la restitution et l’administration

Modifier `dist/app.js`, `dist/index.html`, les styles associés (`dist/administration.css` et/ou feuilles de style existantes), puis `dist/collecte.html` et `dist/collecte.js` pour l’administration :

- Recherche visible avec filtres actifs faciles à retirer, pagination et retour à la même position après consultation d’une preuve.
- Fiche d’entité avec identifiant stable, catégorie, variantes, état du rapprochement, annonces, liens, chronologie et sources.
- Un clic sur un nœud le place au centre et charge ses voisins comme une recherche de cette entité; commandes de profondeur, zoom `+/-`, recentrage, ajustement au cadre, retour et plein écran.
- Ajuster automatiquement le graphe au cadre, garder noms d’entités lisibles et rendre visibles les relations trop nombreuses ainsi que le nombre affiché/total.
- Codes couleur stables complétés par des libellés; distinguer relation publiée et chemin indirect calculé.
- Dans l’administration, décrire les effets, la durée approximative quand mesurable, l’avancement, le dernier point de reprise et le résultat des contrôles.

Pour le style : surfaces claires et opaques derrière les textes et preuves; transparence réservée aux outils flottants du graphe; contraste accessible; typographie lisible; panneau de preuves redimensionnable; animation courte et respectueuse de la réduction des mouvements; effets visuels limités pour préserver le chargement.

### 7. Contrôle de publication et reprise

1. Exécuter le contrôle global et les contrôles d’intégrité relationnelle.
2. Contrôler les références source de toutes les relations publiées, les dates, les identifiants contradictoires et les fusions présumées.
3. Comparer le nombre de sources, annonces, occurrences, entités canoniques et relations avant/après; expliquer tout écart.
4. Vérifier par échantillon des annonces anciennes et récentes, des ventes, des nominations et des cessations.
5. Publier la nouvelle révision seulement si les contrôles passent; garder la révision précédente pour retour arrière.
6. Redémarrer le serveur par le lanceur JORT et vérifier l’état API, la recherche, les liens et les commandes d’administration.

## Fichiers concernés

| Zone | Fichiers principaux | Dépendance à respecter |
|---|---|---|
| Identifiants et fusions | `scripts/schema_evolution.py`, `scripts/referentiel_entites.py`, `scripts/rapprochement_identites.py` | Garder les identifiants historiques et preuves; enregistrer les redirections |
| Extraction et titres | `scripts/extraire_entites.py`, `scripts/texte_pdf.py`, `scripts/construire_base_2024.py` | Versionner l’extracteur; conserver annonce, page et passage |
| Recherche et index | `scripts/construire_base_2024.py`, `scripts/agregation_base.py`, `scripts/serveur_local.py` | Indexer après réextraction; filtrer/trier/paginer côté serveur |
| Graphe | `scripts/agregation_base.py`, `scripts/referentiel_entites.py`, `scripts/serveur_local.py`, `dist/app.js` | Résoudre les identités avant de calculer les voisins |
| Maintenance | `scripts/maintenance_base.py`, interface admin `dist/collecte.html`, `dist/collecte.js` | Sauvegarder, reprendre par lots, journaliser, publier après validation |
| Interface | `dist/index.html`, `dist/app.js`, styles de restitution et `dist/administration.css` | Consommer les API paginées; ne pas recharger le gros corpus global |
| Cycle de vie | `scripts/jort-lifecycle.ps1`, scripts `lancer-jort.ps1` / `arreter-jort.ps1` | Contrôler les workers et verrous; aucun nettoyage des données durables |

## Risques et protections

- **Registre pendant l’indexation :** éviter deux écrivains. Arrêter proprement les batchs avant migration et vérifier les checkpoints par journal.
- **Fusion excessive :** les noms exacts seuls restent présumés; les identifiants contradictoires bloquent l’automatisme; les fonds génériques ne sont jamais fusionnés par leur libellé.
- **Réextraction globale :** elle peut être longue et accroître temporairement la charge. Utiliser une file reprenable, des lots mesurables et un petit échantillon de validation.
- **Publication volumineuse :** le fichier actuel d’environ 406 Mo ralentit la première page. La pagination serveur nécessite de changer ensemble API et interface; ne déployer qu’une fois les deux compatibles.
- **Google Drive :** garder les références `drive/...` et les empreintes; l’indisponibilité du lecteur ne doit pas invalider une indexation locale déjà réussie ni provoquer un nouveau téléchargement.
- **Dates :** présenter la date de publication du JORT et la date d’effet comme deux informations distinctes.

## Benchmark à utiliser pour les critères d’interface

- **Pappers** : recherche multi-critères, fiches entreprises/dirigeants, documents et historique : <https://www.pappers.fr/fonctionnalites/>.
- **OpenCorporates** : identifiants, historique des noms, liens documentés et provenance : <https://api.opencorporates.com/documentation/API-Reference>.
- **Companies House** : historique de dépôt, dirigeants et présentation de données datées : <https://www.gov.uk/get-information-about-a-company>.
- **GLEIF** : relation entre identifiant légal et données de filiales/maison mère; utile comme modèle de types d’identifiants, sans supposer que les annonces fournissent ces liens : <https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-2-data-who-owns-whom>.
- **OCCRP Aleph** : recherche dans des corpus documentaires, entités, liens et chronologies : <https://docs.aleph.occrp.org/>.
- **Linkurious** et **Kumu** : exploration des réseaux, profondeur, filtres et recentrage : <https://doc.linkurious.com/user-manual/latest/page.html> et <https://docs.kumu.io/guides/focus>.
- **IBM Carbon** et **GOV.UK Design System** : listes, tri, pagination, détails et navigation accessibles : <https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines> et <https://design-system.service.gov.uk/components/pagination/>.
- **WCAG 2.2** et **Web Vitals** : contraste, clavier, stabilité visuelle et performance mesurable : <https://www.w3.org/WAI/WCAG22/quickref/> et <https://web.dev/articles/vitals>.
- **Tendances visuelles 2027** de l’article fourni : palette plus affirmée, typographie expressive et transparence mesurée : <https://www.newtarget.com/web-insights-blog/website-design-trends/>. Pour JORT, la lisibilité des preuves et la vitesse priment sur les effets décoratifs.

## Préparation effective des changements

Le point de reprise constaté le 12 octobre est `cancelled` dans `data/collecte-traitement.json`, sans tâche active; aucun serveur ne répondait sur le port 8765. Le rapport `data/maintenance/dernier-rapport.json` doit confirmer l’achèvement du rattrapage en cours avant de redémarrer.

Le premier lot de code est appliqué aux programmes et à l’interface : identifiant séquentiel lisible ajouté aux UUID, rapprochement des sociétés sur MF/RNE/RC, séparation des fonds génériques fusionnés automatiquement, extraction versionnée des objets de vente, tri chronologique, contrôle de profondeur du graphe, actualisation explicite au lieu du rechargement périodique complet, et mode de complétion accessible en ligne de commande.

Une sauvegarde complète a été créée par la commande `scripts/maintenance_base.py --mode backup`. Le rattrapage `scripts/maintenance_base.py --mode reextract_entities` est en cours sur les textes conservés; il ne télécharge pas et ne rouvre pas les PDF. Une fois fini, suivre les étapes 7 du présent plan et le rapport de maintenance.

Le premier lot ne remplace pas encore le chargement initial du corpus complet par une recherche paginée côté serveur : `dist/data.js` pèse environ 406 Mo. Le cache conditionnel et l’arrêt des rechargements automatiques réduisent les transferts répétitifs, mais un lot séparé reste nécessaire pour déplacer la recherche et le chargement des preuves vers des API paginées.
