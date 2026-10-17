# État du projet JORT

## Fonctionnement préparé

- Collecte annuelle et dépôt manuel des PDF depuis l’interface d’administration.
- Indexation locale, séquentielle par journal, avec journalisation de chaque tâche et reprise des fichiers dont l’empreinte ou la version d’extraction ne correspond plus.
- Sauvegarde des fragments d’annonces au fil des imports et mise à jour SQLite par journal, au lieu d’une reconstruction complète à chaque fichier.
- Recherche privée sur l’ensemble des années déjà présentes dans `dist/data.js`.
- Graphe de relations avec preuves sources, date de publication et date d’effet lorsqu’elle est explicitement relevée.
- Tickets de rapprochement de personnes : la CIN commune est un candidat, et une confirmation manuelle est nécessaire avant toute fusion dans le graphe.

## Limites connues

- Les règles d’extraction restent heuristiques. Les chiffres décrivent des annonces et occurrences extraites, pas le nombre d’entités distinctes ni leur situation actuelle.
- Les contrôles hors ligne valident les règles sur des données synthétiques : sources et CIN, événements datés, reprise par journal, versions, synchronisation SQLite, statuts/logs, protections locales et lanceurs Windows. Aucun traitement de la base réelle, téléchargement ou serveur JORT n’a été lancé pendant ces modifications.
- La recherche du site charge encore le jeu `dist/data.js` en entier. La pagination de l’écran et du graphe réduit ce qui est affiché, mais pas le volume initial téléchargé par le navigateur.
- Une extraction sans annonce repérée reste marquée « à vérifier » ; ce n’est pas une réussite silencieuse.
- Avant toute publication publique, prévoir une revue juridique et confidentialité, une authentification, des sauvegardes hors du disque principal et des contrôles de restauration.

## Préparation future

Le constructeur ajoute un schéma évolutif lors des prochaines synchronisations : identités et décisions sourcées, comptes désactivés, rôles et permissions, sessions révocables, audit, tâches et états de publication privés. La stratégie phonétique est raccordée mais retourne toujours une liste vide. L’API de connexion et les accès publics restent à construire. La restitution publique devra être préparée dans les tables dédiées, sans exposer les fichiers privés actuels.

La charte graphique est commune à l’administration et à l’exploration, avec navigation, tables et états de tâches plus lisibles. Les captures de vérification représentent des données fictives. Les contrôles hors ligne ne mesurent pas la précision de l’extraction sur les PDF réels ni les performances à grande échelle.

## Application démarrée après vérification

À la demande explicite de l’utilisateur, le lanceur a démarré l’application à la fin du travail. La page d’administration et l’API répondent; aucun traitement n’est actif. L’OCR facultatif reste à compléter pour les PDF scannés. Voir `outputs/verification-implementation-2026-10-10.md` pour les 22 tests et les limites pratiques.
