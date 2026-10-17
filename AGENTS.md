# Consignes du projet JORT

## Objectif
Développer un outil privé pour étudier les annonces légales tunisiennes,
avec une première année de travail 2024 puis une extension progressive de
1956 à aujourd’hui, pour les sociétés et associations.
Construire progressivement des fiches d'organisations et de dirigeants,
une recherche et une visualisation de leurs connexions, dans l'esprit de
Pappers. Les fiches reflètent les annonces disponibles ; elles ne doivent
pas prétendre contenir toutes les informations actuelles sur une entité.
Toute publication publique nécessite une instruction explicite.

## Sources et fiabilité
- Chaque information doit conserver une référence vers son annonce source :
  identifiant disponible, numéro et date du journal, page ou passage,
  et lien ou chemin vers le document original.
- Conserver les documents sources pour permettre les vérifications.
- Signaler clairement les données incertaines et les rapprochements
  d'identités à vérifier.
- Pour la restitution privée, regrouper par défaut les occurrences de
  personnes dont le nom normalisé est identique, même si ce sont peut-être
  des homonymes. Marquer ce rapprochement comme présumé et à revoir ; ne pas
  effacer les occurrences, leurs CIN ni leurs preuves d'origine.
- Ne pas étendre automatiquement ce regroupement aux noms seulement
  ressemblants. Garder la stratégie phonétique et le rapprochement arabe
  comme pistes futures, avec version et indication d'incertitude.
- Distinguer les informations publiées à une date donnée de la situation
  actuelle ; conserver les dates des annonces et des événements lorsqu'elles
  sont disponibles.
- Chaque lien affiché entre personnes et organisations doit être justifié
  par une annonce source ; une simple présence dans le même journal ne
  constitue pas une relation.
- Conserver une occurrence par annonce et ses références de source. Le graphe
  réunit les noms normalisés identiques sous un nœud de personne présumé ;
  afficher clairement le doute et garder la source propre à chaque relation.
  Une analyse plus fine, une version arabe ou une revue humaine pourra
  ensuite scinder ou confirmer ces regroupements.
- Distinguer le rôle publié, une nomination, une cessation et une vente.
  Garder séparées la date du journal et la date d’effet explicite.
- Pour une reprise, partir de l’état durable de chaque fichier, de son
  empreinte et de la version d’extraction. Ne retraiter que les journaux
  absents ou incomplets ; sauvegarder un point de reprise par journal.

## Méthode de travail
- Développer par petits résultats vérifiables, avec un critère de réussite
  clair pour chaque étape.
- Commencer par un petit échantillon avant d'élargir le traitement.
- Ne pas télécharger massivement les documents sans instruction explicite
  de l'utilisateur.
- Garder un seul agent responsable par volet. Définir son périmètre et le
  résultat attendu avant de déléguer ; déléguer seulement si cela est utile.
- Éviter les tâches en double et les modifications concurrentes des mêmes
  fichiers.
- Utiliser le modèle le plus léger adapté à la difficulté de la tâche.
  Passer à un modèle plus puissant seulement si nécessaire et lorsque les
  outils disponibles permettent ce choix.
- Réutiliser les résultats déjà obtenus et ne relancer que les étapes
  nécessaires après une modification ou un échec.

## Fichiers et communication
- Utiliser des noms de fichiers clairs et descriptifs, avec le numéro et
  la date du journal lorsque cela convient.
- Garder le code, les données, les téléchargements et les fichiers temporaires
  de JORT sous `C:\ChatGPT\JORT`. Ne pas parcourir le dossier personnel
  Téléchargements ; utiliser `data/downloads/` pour un dépôt facultatif.
- À l’arrêt, supprimer uniquement les journaux de démarrage, marqueurs de
  processus et temporaires reconnus. Conserver les événements de traitement,
  interventions, PDF sources, bases, clés et vues de restitution. Le fichier
  vide `data/logs/lancement.lock` est un verrou nécessaire et reste en place.
- Avant de modifier des fichiers, annoncer les changements prévus et
  leur objectif.
- Expliquer les termes techniques simplement, en français.
- À chaque résultat, indiquer ce qui a été réalisé, ce qui a été vérifié
  et les incertitudes restantes.
