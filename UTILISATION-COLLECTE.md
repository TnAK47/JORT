# Utiliser JORT privé

Double-cliquez sur **JORT privé** sur le Bureau. Le lanceur démarre le serveur local et vérifie son API avant d’ouvrir la page de collecte. Le site et les scripts utilisent Python installé sur cette machine. Aucun appel à ChatGPT ni clé API n’est nécessaire.

Le raccourci **Arrêter JORT** sur le Bureau ferme le serveur et les traitements associés. Il demande d’abord un arrêt propre puis arrête les processus identifiés si nécessaire. L’identité est vérifiée par le PID, la date de démarrage, l’exécutable et la commande du projet. Le lancement et l’arrêt partagent un verrou pour éviter les actions concurrentes. Les documents et les résultats enregistrés restent conservés. Le raccourci de lancement permet ensuite de redémarrer.

## Une année à la fois

Sélectionnez une année entre 1956 et l’année actuelle. La sélection d’une année ne télécharge rien. Les années réellement proposées par IORT et la disponibilité des documents doivent être relevées dans l’inventaire ; une année sélectionnable ne prouve pas que toutes ses archives sont disponibles.

1. **Vérifier les PDF déposés** : examine `documents/ANNEE/a-importer/` et le dossier interne `data/downloads/`, contrôle le contenu et copie les PDF reconnus dans `documents/ANNEE/fr/`. Aucun dossier extérieur à `C:\ChatGPT\JORT` n’est parcouru ; les originaux déposés sont conservés.
2. **Mettre à jour l’inventaire** : consulte les formulaires IORT sans télécharger les PDF.
3. **Télécharger les PDF** : collecte les fichiers absents, dans la limite choisie. « Tous » traite l’année sélectionnée. Les échecs sont consignés ; après trois échecs consécutifs le traitement s’arrête.
4. **Extraire et analyser** : traite les journaux présents un par un. Le tableau de suivi indique l’état et le dernier détail de chaque journal. Les erreurs sont reprises automatiquement ; les problèmes de mémoire ou de disque mettent le traitement en pause. Un journal terminé avec la version courante est ignoré lors de la relance.

À la fin de chaque journal, le site met à jour les données de recherche et le graphe. La page de restitution détecte la nouvelle version même si le batch continue ; la recherche et les filtres sont conservés lors de l’actualisation. Il n’est pas nécessaire de relancer Codex ni de lancer un script séparément.

Les étapes fonctionnent automatiquement après leur lancement. Les interventions sont réservées aux anomalies majeures. Dans le graphe privé, les occurrences d’une personne au même nom normalisé sont regroupées d’office afin de ne pas perdre de liens. Ce regroupement est marqué « présumé » parce que des homonymes peuvent être différents. Les annonces, dates, passages et preuves restent séparés sous chaque relation. Une analyse des noms seulement ressemblants et des versions arabes viendra plus tard ; le rapprochement CIN reste consultable dans ses interventions.

## Déposer un document manuellement

Déposez-le dans `documents/ANNEE/a-importer/`, puis lancez la vérification. Le numéro et la date sont contrôlés dans le PDF. Deux fichiers différents pour le même journal sont signalés sans écraser l’archive existante. Un fichier illisible reste disponible pour correction.

Les journaux persistants sont dans `data/ANNEE/logs/` : événements, fichiers manquants et interventions. Ils restent conservés à l’arrêt car ils expliquent les reprises et les cas signalés. Les journaux de démarrage/erreur du serveur, enregistrements de processus et fichiers temporaires reconnus sont supprimés à l’arrêt. Les numéros non encore inventoriés ne sont pas déclarés manquants. Le suivi des traitements est paginé ; l’export CSV donne accès à l’historique conservé.

## Documents anciens

Le traitement courant reconnaît surtout les journaux d’annonces légales en français. Les formats historiques et la couverture des versions arabes restent à vérifier sur des documents réels ; sélectionner 1956 ne garantit pas que son format est reconnu. Les PDF scannés demandent une reconnaissance du texte, appelée OCR. Le moteur local complémentaire est **Tesseract**, avec ses données de langue française et arabe, ainsi que le module Python `pypdfium2`. Le lanceur signale si ces outils manquent. Sans moteur disponible, les documents concernés sont signalés ; aucun service payant n’est appelé.

## Démarrage et erreurs

Le serveur écoute uniquement sur `127.0.0.1:8765`. Fermer le navigateur laisse le serveur disponible. Une seconde ouverture réutilise le serveur. Les erreurs de démarrage figurent dans `data/logs/serveur-erreurs.log`.

Une mise à jour des règles de lecture ou d’extraction peut remettre les journaux concernés dans « À traiter ». Cela déclenche uniquement les étapes dont la version a changé, lors de votre prochain lancement explicite. Les PDF déjà présents ne sont pas retéléchargés. Un journal sans annonce exploitable reste « Aucune annonce · vérifier » et ne revient pas indéfiniment dans la file d’extraction.

Le site reste privé. Aucune publication publique n’est effectuée.

## Sauvegarder les données

Une sauvegarde n’est pas lancée automatiquement. Arrêtez complètement le serveur JORT, puis exécutez `./sauvegarder-jort.ps1 -Destination E:\Sauvegardes-JORT` en remplaçant le chemin par un autre lecteur ou un emplacement réseau disponible. Le script refuse une destination sur le disque du projet, copie les bases, journaux, PDF et versions du code, puis compare les empreintes SHA-256 pour repérer une copie incomplète. Il n’efface pas la source. Avant une restauration, comparez l’inventaire et restaurez sur une copie de travail ; aucune restauration automatique n’est fournie.
