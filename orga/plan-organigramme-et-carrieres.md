# Journaux officiels : administration, postes et carrières

Plan préparé le 12 octobre 2026. Usage privé. Ce document prépare la réalisation ; il ne lance pas de téléchargement ni de traitement.

## Résultat recherché

Construire progressivement, des journaux les plus anciens vers les plus récents, une base permettant :

- de savoir qui occupe quelle fonction à une date choisie, selon les publications disponibles ;
- de produire un organigramme daté de l'État ou d'une administration ;
- de consulter le parcours d'une personne : nominations, affectations, promotions de grade, cessations et autres événements publiés ;
- de consulter l'histoire d'un poste : titulaires, intérims, changements de rattachement et transformations ;
- de remonter de chaque résultat au texte et au PDF qui le justifient.

« À tout moment » signifie deux choses : suivre l'avancement du traitement et interroger une date historique. Une occupation sans fin publiée reste « dernière occupation connue », pas « titulaire actuel confirmé ». Les lacunes du corpus et les événements non publiés limitent la reconstruction.

## Acquis du projet et adaptations

Les programmes actuels fournissent un socle : sessions IORT, inventaires annuels, contrôle du numéro et de la date du PDF, empreintes de fichiers, écritures durables, reprises, bases SQLite, interventions et preuves. La collecte intégrale dispose déjà d'une file persistante et distingue téléchargement, analyse et restitution.

La lecture PDF et l'OCR local sont réutilisables. Cependant, `scripts/texte_pdf.py` déclenche aujourd'hui l'OCR seulement si une page n'a aucun texte : pour les scans anciens, il faudra aussi détecter un texte incomplet ou illisible, les colonnes et les pages mêlant image et texte.

Le collecteur actuel ouvre explicitement « Journal annonces légales ». Il faut un adaptateur spécifique « Journal officiel : lois, décrets, arrêtés et avis », avec contrôle de la série et de la langue. Les expressions d'extraction des sociétés ne constituent pas un extracteur de décisions administratives.

Le bilan conservé de 2024 indique 151 PDF téléchargés/importés sur 151 attendus, mais aussi 7 078 annonces à classer sur 11 203. Ces chiffres décrivent ce bilan enregistré, pas une mesure nouvelle du corpus courant. Ils rappellent que réussir le téléchargement ne prouve pas la qualité de reconnaissance.

Enfin, la restitution actuelle charge encore un gros fichier global : le nouveau volet doit utiliser dès le départ une recherche et des fiches chargées par petites pages depuis la base, plutôt qu'un export de tout le corpus dans le navigateur.

## Périmètre et archives

Premier périmètre : 1956 à aujourd'hui, en commençant par les premiers numéros réellement disponibles. Faire un inventaire initial des années et langues proposées, incluant les éventuelles séries antérieures : « depuis le début » ne doit pas devenir silencieusement « depuis 1956 » si des archives plus anciennes existent. Conserver ces années dans le catalogue et expliciter leur inclusion dans le calendrier.

Le lien fourni contient un contexte de session ; ne pas le stocker comme point d'entrée durable. Repartir de l'accueil officiel, renouveler la session et retrouver chaque numéro dans le catalogue. Tester l'ancien site et l'accueil actuel avant de choisir l'adaptateur.

Le 12 octobre 2026, l'outil web n'a pas pu lire la page fournie. L'accueil https://www.iort.tn/ redirige vers https://www.iort.tn/siteiort/. Cela ne valide ni la navigation ni la disponibilité des PDF anciens. Ne pas annoncer un corpus complet avant un inventaire vérifié.

Collecter les éditions française et arabe disponibles ; les inventorier séparément. Une édition française seule ne garantit pas la couverture de tous les actes. Relier les traductions d'un même acte avec une justification, sans compter deux nominations. Les divergences restent visibles et soumises à revue.

## Modèle de données

| Objet | Contenu à conserver |
|---|---|
| Journal | Série, année, numéro, date, langue, supplément/édition, lien source, chemin conservé, empreinte, couverture et états de traitement |
| Texte administratif | Nature, numéro lorsqu'il existe, date de signature, titre, autorité, articles, passages, pages PDF et imprimées, lien vers le journal |
| Unité administrative | Identifiant stable, noms datés, type, existence connue, création/suppression, rattachements datés et preuves |
| Poste | Identifiant stable, intitulés datés, unité, nombre de places si publié, création/suppression, transformation et preuves |
| Personne | Occurrences originales, nom normalisé, graphies, identifiants publiés, rapprochements présumés et décisions de revue |
| Événement | Type, personne, poste/unité/grade concernés, valeurs avant/après si explicites, date d'effet, précision de date, preuve et confiance |
| Occupation | Personne–poste, début et fin connus ou bornés, titulaire/intérimaire, événement justificatif et incertitudes |
| Revue | Correction, auteur, date, motif, anciennes valeurs et source ; correction conservée lors des réextractions |

Séparer le grade, le corps, la fonction, le poste et l'unité. Une promotion de grade ne signifie pas un changement de poste. Une mention de signataire ne constitue pas une nouvelle nomination. Une délégation de signature ne crée pas une relation hiérarchique.

Séparer publication, signature et prise d'effet. Si aucune date d'effet n'est indiquée, conserver « inconnue » ; une vue fondée sur la date de publication doit annoncer cette convention. Les rectificatifs, abrogations et décisions rétroactives doivent pouvoir modifier une reconstruction passée sans effacer les versions antérieures.

Conserver deux chronologies : la date à laquelle une décision produit son effet, lorsqu'elle est connue, et la date à laquelle elle devient connue par publication. Cela permet de distinguer « situation reconstituée à cette date » et « informations publiées à cette date ».

## Reconnaissance des textes

Traiter chaque journal complet, pas seulement les pages contenant le mot « nomination ».

1. Extraire le texte page par page et conserver son emplacement ; lancer la reconnaissance des caractères des scans lorsque nécessaire.
2. Reconstituer les colonnes, paragraphes, titres, articles et textes répartis sur plusieurs pages. Le sommaire aide à repérer les actes, mais ne remplace pas leur contenu.
3. Classer les textes : organisation des services, création/suppression/rattachement, nomination, affectation/mutation, promotion, intérim, fin de fonction, retraite, rectification, autres.
4. Extraire chaque événement avec une citation exacte et sa page. Les listes et tableaux peuvent produire plusieurs événements dans un même acte.
5. Relier les événements aux personnes, unités et postes sans inventer les niveaux hiérarchiques manquants.
6. Contrôler les incohérences et publier progressivement les résultats dans l'application privée.

Commencer par les règles locales pour les formulations récurrentes. Mesurer ensuite ce qui leur échappe avant d'ajouter une reconnaissance par modèle. Tout outil supplémentaire devra conserver un résultat structuré, sa version, ses preuves et les données incertaines ; son coût et sa qualité seront mesurés sur le même échantillon.

Pour les personnes, appliquer la règle du projet : noms normalisés identiques regroupés provisoirement, occurrences et preuves préservées, homonymie affichée. Ne pas fusionner automatiquement les noms voisins ni les translittérations arabe/français. Une carrière composite présumée doit être identifiable et pouvoir être scindée.

## Reconstruction progressive

Les nominations alimentent les occupations. Les textes d'organisation alimentent la structure et les compétences des services. Un rattachement déduit uniquement d'un titre reste une piste, jamais une hiérarchie confirmée.

Chaque occupation et chaque relation hiérarchique possède ses dates, sa preuve et son statut. Ne pas mettre automatiquement fin à un mandat au seul motif qu'un autre nom apparaît : vérifier qu'il s'agit du même poste individuel et d'un remplacement explicite. Pour un conseil ou un corps collectif, plusieurs membres peuvent coexister.

Un changement de nom peut conserver l'identité d'un service s'il est documenté. Une fusion, une scission ou une suppression doit créer des événements et des liens de succession explicites ; ne pas remplacer rétroactivement tous les anciens noms par le nom actuel.

Au début du corpus, certains services et titulaires existaient déjà. Afficher « existence attestée au plus tard le… » ou « début inconnu » ; la première mention n'est pas une création ou une nomination.

La collecte progresse dans l'ordre des publications anciennes. Les vues temporelles sont recalculées à partir des dates d'effet, y compris quand un texte plus récent corrige le passé. Une modification ne reconstruit que les personnes, postes, unités et périodes affectés.

## Étapes et critères de réussite

| Étape | Travail | Critère de réussite |
|---|---|---|
| 1. Accès et inventaire | Adapter la navigation, relever les années/langues/séries, vérifier quelques numéros anciens | Session renouvelable ; bons PDF identifiés ; lacunes et langues explicitement recensées |
| 2. Pilote ancien | Traiter 10 à 20 journaux du début disponible et quelques formats différents, dont des scans et de l'arabe | Chaque page est lue ou signalée ; les actes traversant plusieurs pages restent complets |
| 3. Jeu de référence | Annoter manuellement environ 100 actes, avec cas d'organisation, nomination, promotion et cessation | Référence indépendante permettant de mesurer erreurs et omissions par catégorie/langue |
| 4. Première reconstruction | Produire la structure d'un ministère sur une courte période, des fiches de personnes et de postes | Chaque élément affiché renvoie à sa preuve ; dates inconnues et rapprochements présumés visibles |
| 5. Première année | Télécharger et traiter tous les numéros disponibles de la première année retenue | Inventaire réconcilié ; aucun numéro/page en échec silencieux ; reprise après interruption vérifiée |
| 6. Collecte intégrale | Étendre année par année vers le présent, en traitant les fichiers reçus immédiatement | Suivi durable, limites de disque, relances ciblées, restitution régulière et rapport annuel |
| 7. Suivi récent | Après le rattrapage, inventorier les nouvelles parutions | Nouveaux textes intégrés sans retraiter les journaux inchangés |

Objectifs initiaux à confirmer sur le pilote : au moins 95 % de précision pour les événements acceptés automatiquement et 90 % de rappel sur les catégories prioritaires du jeu de référence. Mesurer séparément noms, postes, unités et dates, ainsi que les résultats arabes et français. Une moyenne globale ne doit pas masquer une catégorie défaillante. Si les objectifs ne sont pas atteints, conserver les résultats en revue et corriger avant l'élargissement automatique.

Le premier livrable concret sera un ministère, un organigramme daté, plusieurs parcours de personnes et plusieurs historiques de postes, accompagnés d'un rapport de couverture et d'erreurs.

## Exécution et reprise

Créer le volet dans `C:\ChatGPT\JORT\orga\`, avec ses propres documents, données, scripts, tests, interface et journaux. Réutiliser les composants génériques du projet sans modifier les bases d'annonces légales. Prévoir des identifiants incluant série, langue, édition et journal pour éviter les collisions.

Commencer par un téléchargement à la fois et une tâche d'analyse à la fois. La collecte alimente une file durable ; elle ralentit lorsque l'analyse accumule du retard. Adapter le débit aux réponses du site, respecter les éventuelles limites publiées et espacer les reprises après erreur.

Enregistrer séparément les états inventorié, téléchargé, PDF vérifié, texte lu, actes segmentés, événements extraits, structure recalculée, disponible dans l'interface. Associer chaque résultat à l'empreinte du PDF et aux versions de ses étapes. Une correction d'extraction ne provoque pas un nouveau téléchargement ou une nouvelle lecture des pages inchangées.

Vérifier le contenu PDF, la série, le numéro, la date et la langue : une réponse HTML ou un autre journal ne doit pas être validé. Conserver toutes les sources, les événements de traitement et les interventions. Une copie distante n'autorise le retrait local qu'après vérification de sa conservation et de sa consultation.

Ne pas partager une session IORT mutable entre traitements concurrents. Un seul responsable par volet et un seul processus d'écriture par base ; aucun agent supplémentaire n'est nécessaire pour préparer ce plan.

## Écrans attendus

- **Suivi** : années/numéros disponibles, téléchargés, lus et extraits ; retard de la file, erreurs, pages à revoir, espace utilisé et dernier point de reprise. Distinguer « tout le catalogue connu traité » de « archives exhaustives confirmées ».
- **Qui fait quoi** : recherche personne, fonction, grade, corps, ministère, unité et territoire ; choix de date ; occupant connu, intérim, fin inconnue ou conflit.
- **Organigramme** : date choisie, périmètre, niveaux développés à la demande, postes et titulaires, unités sans titulaire connu, liens justifiés. Afficher les compétences publiées des unités lorsqu'elles sont extraites. Export SVG/PDF et tableau de sources, avec date et couverture.
- **Personne** : événements chronologiques, grade distinct des fonctions, sources, graphies et avertissement d'homonymie.
- **Poste** : titulaires successifs, périodes inconnues, chevauchements, changements d'intitulé, rattachement et succession.
- **Revue** : scans difficiles, noms ambigus, postes non résolus, contradictions, doublons bilingues et décisions humaines conservées.

## Mesure du volume et calendrier

Ne pas annoncer une durée ou un coût global avant le pilote. Mesurer le nombre réel de journaux/pages, la taille moyenne des fichiers, la part des scans, le temps de lecture/extraction, le taux de revue et, si un modèle est retenu, son coût par acte. Estimer ensuite le stockage, les sauvegardes et la durée avec une marge fondée sur ces mesures.

Priorité d'exécution : accès officiel → pilote ancien → preuves et modèle temporel → première restitution vérifiable → première année complète → collecte intégrale chronologique. L'autorisation de collecter tous les PDF figure dans la demande ; ce plan n'ajoute pas de confirmation préalable systématique. La collecte effective relève de la mise en œuvre qui suit ce livrable.

## Points restant à vérifier

Profondeur réelle des archives et éventuelles séries antérieures à 1956 ; disponibilité respective des langues ; stabilité des accès IORT ; qualité des scans anciens ; finesse des textes d'organisation ; actes manquants ou non publiés ; volume et coût de traitement. Aucun de ces points ne justifie d'inventer une structure ou un titulaire.
