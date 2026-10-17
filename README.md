# JORT — administration privée

Collecte des annonces légales tunisiennes, extraction et analyse locales, avec conservation des sources. Le périmètre sélectionnable va de 1956 à l’année actuelle. La disponibilité des archives et la reconnaissance des formats anciens doivent être vérifiées ; les fonctions courantes ciblent la version française.

## Démarrer

Double-cliquez sur **JORT privé** sur le Bureau, ou exécutez `./lancer-jort.ps1`. Le lanceur vérifie Python et le module de lecture PDF, démarre le serveur local qui fournit le site et l’API, puis ouvre l’administration. Les scripts de collecte et d’extraction sont pilotés par ce serveur après votre clic.

`./demarrer.ps1` utilise le même lanceur et garde une console de suivi ; Ctrl+C appelle le même script d’arrêt. Le lancement et l’arrêt utilisent un verrou commun pour éviter deux actions concurrentes. Le serveur existant n’est réutilisé que si son processus et sa version correspondent au projet.

## Dans l’administration

- Sélectionner une année.
- Vérifier les fichiers déjà téléchargés ou déposés dans `documents/ANNEE/a-importer/`.
- Ajouter directement des PDF avec le formulaire de dépôt.
- Relever l’inventaire IORT puis télécharger les PDF manquants.
- Lancer l’extraction et l’analyse automatiques, par lot ou pour tous les fichiers disponibles.
- Suivre les événements et les interventions exceptionnelles, exporter les journaux.

Les PDF et résultats du projet restent dans `C:\ChatGPT\JORT` : documents dans `documents/ANNEE/`, bases dans `data/ANNEE/`, journaux de traitement dans `data/ANNEE/logs/`. Les PDF à contrôler peuvent être déposés dans le dossier local `data/downloads/` ; le dossier Téléchargements de Windows n’est pas parcouru. À l’arrêt, les journaux durables, les sources et les extractions restent disponibles ; seuls les journaux de démarrage du serveur et les fichiers temporaires reconnus sont nettoyés. Les documents identiques et les extractions en cache sont réutilisés. Une nouvelle tentative reprend les étapes nécessaires.

## Sauvegarde des bases sur Google Drive

Connectez Google Drive pour ordinateur. Depuis Maintenance, choisissez une année ou toute la base puis lancez **Transférer les PDF indexés vers Drive**. Les PDF vérifiés vont dans `Mon Drive/JORT/ANNÉE/`; les fichiers d’autres états restent en local. Le chemin peut être ajusté avec `JORT_GOOGLE_DRIVE_ROOT` (par défaut `G:\Mon Drive`). Après avoir arrêté JORT, lancez `./sauvegarder-base-google.ps1` pour sauvegarder les bases SQLite dans `Mon Drive/JORT/sauvegardes-base/AAAA-MM-JJ/HHMMSS/`. Le manifeste note les empreintes des bases source et copie ainsi que le contrôle d’intégrité.

Aucun appel à ChatGPT ni clé API. La reconnaissance du texte des PDF scannés utilise éventuellement Tesseract installé localement ; sans cet outil, une intervention est signalée. Dans le graphe privé, les occurrences au même nom normalisé sont réunies provisoirement pour éviter de perdre des connexions ; un avertissement indique que des homonymes peuvent être différents. Chaque relation garde sa propre annonce et son passage source. Les rapprochements de noms seulement ressemblants et l’analyse arabe restent des évolutions futures.

Les résultats restent automatiques et peuvent être incertains. Chaque lien conserve son annonce et sa citation. Une CIN commune crée un dossier de rapprochement : elle ne fusionne pas les occurrences tant qu’une personne n’a pas confirmé le rapprochement depuis l’administration. Les erreurs temporaires ont trois essais au maximum, soit deux relances ; une erreur déterministe conserve un état à reprendre et une indisponibilité du disque ou de la mémoire met le traitement en pause. Les fichiers terminés avec les versions courantes de lecture et d’extraction sont ignorés lors des reprises.

La recherche porte sur toutes les années présentes dans la base publiée locale. Le graphe montre d’abord les relations proches et peut être élargi par étapes ; un mandat publié n’établit pas à lui seul un actionnariat ou une situation actuelle. Aucun résultat n’est publié sur Internet.

Pour les scans, l’OCR facultatif demande Tesseract avec les langues française et arabe ainsi que `pypdfium2`. Sans eux, le texte lisible des PDF est toujours traité et les pages non lues sont signalées dans les interventions. Le lanceur affiche un avertissement si l’ensemble OCR manque.

Voir [le guide de collecte](UTILISATION-COLLECTE.md) pour les dossiers, les documents anciens et les erreurs de démarrage.

Le [plan d’évolution de la restitution](docs/plan-evolution-restitution.md) prévoit les identités, une stratégie phonétique inactive, les comptes, les rôles, les sessions, les droits, l’audit et une restitution publique distincte. Ces tables seront créées lors d’une prochaine synchronisation PostgreSQL. L’authentification et la publication publique ne sont pas activées.

La charte des écrans d’administration et d’exploration est partagée dans `dist/interface.css`. Elle adapte les contrôles aux petits écrans, au clavier et à la réduction des animations. Les aperçus dans `outputs/apercu-interface-*.png` utilisent des données fictives.
