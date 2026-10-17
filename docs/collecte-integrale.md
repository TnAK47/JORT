# Collecte et analyse de toutes les années

Le lanceur `lancer-collecte-integrale.ps1` utilise le serveur local existant.
Il attend la fin des traitements, charge les corrections par un redémarrage
contrôlé, puis demande les années de l’année actuelle à 1956 dans cet ordre.

Avant la collecte, `scripts/preparer_collecte.py` sauvegarde la base,
élimine les copies d’annonces strictement identiques, répare les références
et titres récupérables, regroupe les entités et optimise SQLite. Les PDF
ne sont pas relus. Le contrôle est réutilisé si les données et scripts
n’ont pas changé. Rapport : `data/maintenance/preparation-collecte.json`.

Un seul téléchargement IORT est actif. En parallèle, un seul PDF est analysé
et indexé à la fois. Une file persistante conserve les reprises par journal.
Le téléchargement attend si 20 PDF restent à analyser, pour limiter
l'occupation disque. Il n'y a pas de pause fixe entre téléchargements.

Les PDF présents sur Drive sont reconnus. Après une analyse réussie,
la copie Drive est vérifiée avant retrait de la copie locale. Un échec de
transfert conserve le PDF local et apparaît dans les logs.
Un journal déjà indexé est exclu des téléchargements même si son inventaire
est ancien. Un original absent reste signalé comme absent ; l’index conservé
n’est pas une preuve de présence du fichier. Les fichiers reçus sans index
sont mis en file immédiatement ; les reprises ne relisent pas une extraction
déjà terminée à la version actuelle.

Un fichier illisible ou un échec propre à un journal est signalé, puis le
traitement poursuit sur le suivant. Une panne générale de disque, mémoire
ou accès Drive suspend la collecte pour éviter une perte de données.
Un catalogue indisponible est signalé et l'année suivante est examinée.
Les anciens catalogues peuvent être incomplets : le rapport distingue
l'inventaire connu de la couverture effectivement confirmée.

Chaque journal est sauvegardé immédiatement dans sa base annuelle.
La restitution globale est reconstruite après 10 journaux ou environ une
minute à une frontière de traitement, puis à la fin de la file. Un fichier
temporairement verrouillé provoque une nouvelle tentative de publication,
sans recommencer l'extraction du PDF.

## Suivi

- `data/collecte-integrale/etat.json` : phase et progression actualisées.
- `data/collecte-integrale/rapport.json` : statistiques et erreurs.
- `data/collecte-integrale/rapport.md` : rapport lisible par année.
- `data/collecte-integrale/logs/` : journal du lanceur.
- Administration du site : fichiers, traitements et logs unitaires.

Le lanceur s'arrête si l'application est arrêtée volontairement.
Relancer le même script reprend son suivi sans soumettre deux fois une
collecte dont la réception est incertaine. L'ensemble fonctionne localement,
sans appel à ChatGPT ni clé API.
Après un arrêt volontaire, le même plan reprend au dernier point de contrôle
durable : les années déjà parcourues sont sautées, et les journaux déjà tentés
dans l'année interrompue restent suivis individuellement. Les téléchargements
échoués restent signalés pour une relance ciblée ; ils ne provoquent pas un
retour automatique au début du plan.
Le rapport distingue la réussite complète, les fichiers à reprendre et
l’échec global. La commande `collecter_2024.py --download --year ANNEE`
soumet également sa collecte au serveur local, afin de partager le même moteur.
