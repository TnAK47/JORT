# JORT — reprise après arrêt

Cette fiche décrit la reprise locale du projet. Les traitements restent arrêtés tant
qu'un lancement explicite n'est pas demandé.

## Arrêt propre

Depuis `C:\ChatGPT\JORT`, exécuter `.arreter-jort.ps1`. Le script arrête les
processus JORT, écrit le point d'arrêt durable et conserve les journaux utiles.

## Démarrage

Exécuter `.lancer-jort.ps1`. Le script vérifie la session Windows, le lecteur
G:, les verrous et les points de reprise, prépare la base de consultation, puis
ouvre le serveur local. Il ne relance pas un batch interrompu sans état durable.

## Reprendre un batch

Consulter d'abord `data/maintenance/point-arret-application.json`, puis les états
`data/collecte-integrale/etat.json` et `data/collecte-traitement.json`. Les journaux
déjà terminés sont ignorés ; les fichiers absents ou incomplets sont les seuls
à remettre en file. Un PDF illisible est conservé et signalé pour reprise
individuelle.

## Contrôles administratifs

Le contrôle de la base peut être lancé avec `python scripts/verifier_restitution.py`.
La sortie console est volontairement courte ; le rapport complet est enregistré
dans `data/maintenance/rapport-restitution.json`. Les rapprochements portant des
identifiants officiels contradictoires restent à revoir manuellement.

## Données et Drive

Les PDF entièrement indexés sont référencés dans `G:\Mon Drive\JORT\<année>`.
Les annonces et leurs preuves restent conservées localement dans la base et les
référentiels. Si G: est momentanément indisponible, le traitement peut rester
local et le transfert est repris ultérieurement.

## État actuel

Le dernier arrêt a laissé zéro tâche active ou en attente. La couverture
historique avant les années déjà indexées reste volontairement différée.
