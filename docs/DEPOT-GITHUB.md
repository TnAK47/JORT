# Dépôt GitHub JORT : périmètre

## Dépôt privé obligatoire

Le dépôt GitHub doit rester privé. Il contient le code de l'application JORT,
mais aucune base, annonce extraite, donnée personnelle, PDF source, sauvegarde,
journal de traitement, mot de passe ou clé de service.

## Inclus

- scripts Python et PowerShell ;
- interface statique : HTML, CSS et JavaScript hors jeux de données générés ;
- tests ;
- documentation de conception et d'exploitation ;
- modèles de schéma PostgreSQL ;
- scripts d'installation, démarrage et arrêt.

## Exclus

Les règles `.gitignore` excluent notamment `data/`, `documents/`, les PDF
publiés sous `dist/sources/`, les fichiers de restitution générés, les caches,
les environnements Python, les binaires PostgreSQL, les sauvegardes et les
secrets.

## Avant le premier envoi

1. Créer un dépôt **privé** nommé `JORT` dans le compte GitHub du propriétaire.
2. Connecter ce dépôt local à son URL `origin`.
3. Contrôler les fichiers préparés avec `git status` puis lancer une recherche
   de secrets.
4. Créer le premier commit et pousser la branche `main`.

Les données et PDF restent dans les stockages dédiés : PostgreSQL, Google Drive
ou stockage objet. GitHub sert au code, aux tests et à la documentation.