# Correction des entités et des connexions

## Lancer depuis l'administration

Après redémarrage du serveur, ouvrir **Entretien de la base** et lancer
**Corriger les titres et compléter tous les liens**. L'action couvre toutes
les années indexées. Elle attend la fin du PDF en cours et utilise les textes
enregistrés : aucun téléchargement, aucune relecture de PDF et aucun appel à
un modèle d'intelligence artificielle.

Le rapport indique les annonces réexaminées, les titres corrigés, les entités
et relations ajoutées, les années enregistrées et les erreurs à reprendre.
Une erreur dans une année n'interrompt pas l'examen des suivantes. Les
sauvegardes précèdent les écritures. Les textes, références et pages des
annonces sont contrôlés pour rester strictement identiques.

## Lancer sans le serveur

Depuis la racine du projet, avec le Python utilisé par le lanceur :

```powershell
python scripts/corriger_entites_et_liens.py
```

`--annees 2010 2012` limite cette commande aux années indiquées ; `--forcer`
réexamine les textes déjà corrigés par la même version. Le script refuse de
fonctionner en parallèle du serveur. Une relance normale réutilise les
points de reprise enregistrés par journal dans chaque base annuelle.

## Fiches uniques et fusion

`data/referentiel-entites.sqlite` conserve les identifiants permanents,
les occurrences, les noms publiés, les redirections et l'historique des
fusions. Il fait partie des sauvegardes à conserver avec les bases annuelles.
Un déplacement de PDF vers le Drive ne change pas l'identifiant d'une fiche.

Les personnes sont rapprochées par les éléments publiés et les confirmations
humaines ; les noms ressemblants seuls ne déclenchent pas de fusion.
Les regroupements d'organisations sur un nom exact sans identifiant
corroborant restent signalés comme présumés. Des identifiants contradictoires
bloquent une nouvelle fusion. La stratégie phonétique existante reste désactivée.

Dans **Fusionner les entités**, rechercher les fiches, consulter les annonces,
sélectionner au moins deux fiches, examiner les connexions réunies, saisir le
nom principal et le motif vérifié, puis confirmer. La première sélectionnée
garde son identifiant ; les autres redirigent vers elle. Les preuves sont
conservées. L'historique permet d'annuler la fusion. Il faut annuler les
fusions ultérieures liées avant une fusion antérieure ; les occurrences
ajoutées depuis une fusion annulée restent explicitement à vérifier.

## Graphe

Un clic sur une entité en fait le centre et affiche ses connexions sur toutes
les années. **Retour** retrouve le point de départ précédent. Les couleurs
distinguent administration, direction, contrôle des comptes, capital, vente
et cessation. Chaque lien garde son annonce source et ses dates. Un rôle
ancien n'est pas présenté comme la situation actuelle.

La fenêtre du graphe reste limitée à 200 entités pour garder une navigation
fluide ; l'indication de limite et le recentrage permettent de poursuivre.

## Fichiers à conserver

- Bases annuelles : `data/ANNEE/base.json` et `jort-ANNEE-fr.sqlite`.
- Référentiel global : `data/referentiel-entites.sqlite`.
- Anciennes confirmations : `data/identity-merges.json`, lorsqu'il existe.
- Rapports : `data/maintenance/rapport-*.json` et `dernier-rapport.json`.
- Sauvegardes : emplacement indiqué dans chaque rapport de maintenance.
- PDF sources : leurs chemins locaux ou Drive configurés restent inchangés.

Le référentiel contient des informations personnelles ; il reste réservé à
l'administration privée. Sa consultation publique demande une autorisation
explicite et les contrôles d'accès prévus par le projet.
