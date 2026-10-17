# Réconciliation globale des entités — lot prêt à lancer

Ce lot traite la base PostgreSQL existante. Il ne relit pas les PDF et n'appelle
aucun modèle ni service payant. Le suivi se fait sur le PC, sans réveiller Codex.
La base de production n'a pas été corrigée pendant la préparation.

## Analyse du premier script et corrections préparées

| Point observé | Correction préparée |
| --- | --- |
| Une transaction unique pour toutes les fusions | Une transaction et un point de reprise par groupe |
| Pas de reprise après interruption | Plan SQLite durable et validation PostgreSQL enregistrée dans la même transaction que la fusion |
| Toutes les entités et leurs textes chargés en mémoire | Lecture par paquets de 1 000, plan sur disque, aucun stockage supplémentaire du corpus complet |
| Normalisation des identifiants ligne par ligne | Mise à jour SQL ensembliste, uniquement des valeurs dont le format est reconnu |
| Lettre du MF perdue lors du rapprochement | Contrôle de cohérence de la lettre avant fusion, conservation de la valeur et de la citation publiées |
| Même CIN attribuée à des noms différents | Groupe bloqué pour contrôler l'attribution ; les civilités sont neutralisées |
| Pas de regroupement des noms strictement identiques | Regroupement privé présumé des personnes, refus si CIN contradictoires |
| CIN susceptible d'être attribuée à une organisation | Vérification du type de titulaire avant d'utiliser un identifiant comme clé |
| Pas de reconnaissance des changements de dénomination | Reconnaissance de formulations explicites, preuve et ancien nom conservés |
| Date de publication utilisée comme prise d'effet | `observed_on` pour la publication ; prise d'effet laissée inconnue sans preuve |
| Libellé risquant de revenir à un ancien nom pendant une réextraction | Protection chronologique du dernier nom publié dans l'ingestion |
| Une erreur pouvait annuler tout le travail | Erreurs isolées consignées, groupes suivants poursuivis ; arrêt propre sur panne globale de base ou de disque |
| Compteurs et publication inchangés après fusion | Reconstruction des compteurs, retrait des compteurs périmés et nouvelle révision de publication |
| Index de recherche supplémentaire non repris après interruption | Construction sans blocage, réparation d'un index incomplet, puis statistiques PostgreSQL |
| Suivi peu lisible | État léger, point console/log toutes les 20 minutes, rapport et CSV des arbitrages |

## Règles de décision

- **Personnes :** CIN explicite et nom normalisé concordant ; ou nom normalisé
  identique avec rapprochement présumé. Une civilité, un accent ou la ponctuation
  ne créent pas une nouvelle personne. Aucun rapprochement phonétique.
- **Sociétés et associations :** MF ou RC explicite, même pays et type compatible.
  Les présentations historiques d'un MF sont rapprochées, sa lettre reste contrôlée.
  Un MF réellement différent bloque le groupe. Un RC différent sans MF commun bloque
  également la fusion automatique.
- **Dénomination :** une phrase explicite peut relier deux noms d'une même société.
  Une fusion par absorption seule ne transforme pas toutes les sociétés citées en
  une entité unique. « Tunisian Telecom Electric Distribution » n'est pas assimilée
  à « Tunisian Telecom Electric » sur la ressemblance de leurs noms.
- **Sans identifiant ou preuve suffisante :** conserver les fiches et signaler les
  cas non résolus. Les fonds de commerce restent des objets distincts ; le nom d'un
  café ou une adresse ne suffit pas à fusionner leurs propriétaires ou leurs fonds.
- **Traçabilité :** ID public le plus ancien retenu, redirections des anciens ID,
  mentions et annonces conservées, décision et sauvegarde pour retrouver l'état
  antérieur. Une annulation guidée des fusions reste un travail futur.

La reconnaissance des changements de dénomination est volontairement limitée aux
phrases explicites prises en charge. Les rattachements ambigus restent à vérifier.
Un nom principal correspond au dernier nom publié retenu, sans certification de la
situation juridique actuelle.

## Ordre du lancement

1. Arrêter le site et ses batchs avec `arreter-jort.ps1`.
2. Le lanceur de réconciliation démarre PostgreSQL seul.
3. Préparer le plan en lecture seule, avec une copie de travail locale bornée.
4. Avant toute application, créer une sauvegarde PostgreSQL et vérifier son catalogue.
5. Préparer les tables, l'index de variantes de noms et les clés normalisées.
6. Traiter chaque groupe séparément, en revérifiant son état courant avant fusion.
7. Contrôler la conservation des nombres de sources, annonces, mentions, relations et preuves.
8. Publier les nouveaux compteurs et mettre à jour les statistiques de recherche.
9. Relancer le site avec `lancer-jort.ps1`.

Le lanceur du site refuse un démarrage pendant la réconciliation. Le script d'arrêt
demande une pause de la réconciliation avant de fermer PostgreSQL. Si une étape
longue n'a pas encore atteint son point de reprise, PostgreSQL reste ouvert.

## Commandes PowerShell

Depuis `C:\ChatGPT\JORT` :

```powershell
# Simulation, sans fusion ; produit le plan et le rapport
.\lancer-reconciliation-jort.ps1 -Action Simulation

# Premier échantillon vérifiable de 10 groupes (sauvegarde incluse)
.\lancer-reconciliation-jort.ps1 -Action Appliquer -LimiteGroupes 10

# Continuer le même plan, sans refaire les groupes terminés
.\lancer-reconciliation-jort.ps1 -Action Reprendre

# Lire l'état, sans accès à PostgreSQL et sans nouveau traitement
.\lancer-reconciliation-jort.ps1 -Action Etat

# Demander une pause au prochain point de reprise
.\lancer-reconciliation-jort.ps1 -Action Pause

# Après correction d'une erreur, retenter uniquement les groupes en erreur
.\lancer-reconciliation-jort.ps1 -Action Reprendre -RetenterErreurs
```

`Appliquer` réutilise automatiquement un plan simulé disponible. Pour un premier
passage intégral sans limite, omettre `-LimiteGroupes`. Une simulation reprise n'est
pas une application : l'option Python `--apply` reste nécessaire.

## Suivi sans consommation de tokens

Dans `data/maintenance/reconciliation-entites/` :

- `etat.json` : étape active, groupes terminés/en attente, fusions, erreurs, heure
  de dernière activité, estimation pendant les fusions ; écrit au plus toutes les
  deux secondes et lors d'un point de suivi.
- `progression.log` : démarrage, point toutes les 20 minutes, bilan final. L'heure
  d'un compte rendu et l'heure de dernière activité sont distinguées.
- `rapport.json` : bilan synthétique, compteurs et emplacement de la sauvegarde.
- `a-verifier.csv` : conflits, erreurs et rattachements historiques à confirmer.
- `plan.sqlite` : plan complet et points de reprise. À conserver jusqu'à validation.
- `pause.flag` : demande d'arrêt après le travail en cours ; retirée à la reprise.

L'estimation ne devient disponible qu'une fois les groupes identifiés et un premier
groupe traité. Les sauvegardes et créations d'index ont une durée variable.

Les fichiers de suivi ne contiennent pas de copie complète des annonces. Le plan
et la sauvegarde sont utiles à la reprise et ne sont pas des temporaires à purger.
Le script ne crée aucune automation Codex et ne publie pas de messages dans le chat.

## Validation de la préparation

Tests locaux isolés : normalisation MF et lettre de contrôle, CIN contradictoires,
RC pollués, renommage TTE/OTBS, absorption distincte du renommage, erreur isolée qui
laisse continuer le passage, reprise sélective et échantillon limité.
Contrôles de syntaxe Python, JavaScript et PowerShell inclus dans la préparation.
Le passage sur l'ensemble de la base et les mesures de performance après index
restent à effectuer au lancement. Aucun gain chiffré n'est annoncé à ce stade.
