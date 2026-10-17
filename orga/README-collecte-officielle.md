# Téléchargement des journaux officiels : lois et décrets

Préparé le 13 octobre 2026. Éditions arabe et française. Les annonces légales sont exclues. Aucun abonnement jort.tn n'est utilisé. L'IORT sera utilisé ensuite pour compléter les archives.

## Source retenue et vérifications

- **Library of Congress** : catalogue JSON public accessible lors de l'essai ; notices avec date, langue, numéro et URL PDF. La collection tunisienne présentée en 2023 couvre 2010–2018, avec près de 1 000 documents, toutes séries comprises. Le script ne présume pas que cette couverture est exhaustive ni inchangée : il parcourt le catalogue actuel et sélectionne uniquement les lois/décrets en arabe/français. https://blogs.loc.gov/law/2023/04/foreign-legal-gazette-collections-winter-2023-recap/
- **Jurisite Tunisie** : les pages examinées présentent des publications officielles **en extraits**, avec certains liens français/arabe et un suivi des traductions. Source complémentaire de textes, pas un inventaire complet vérifié des journaux. Les extraits ne sont donc pas mélangés aux PDF de journaux complets. https://www.jurisitetunisie.com/se/index.php?board=46.0 et https://www.jurisitetunisie.com/se/index.php?topic=6055.0
- **PIST/CNUDST** : anciens liens directs documentés, mais essais actuels en échec (connexion interrompue). Aucun téléchargement massif lancé sur cette source.
- **Idaraty** : notices de journaux anciens repérées ; essai direct refusé (HTTP 403). Non intégré sans accès validé.
- **Archives nationales** : fonds historique annoncé, sans accès PDF en ligne vérifié pour la collecte. https://www.archives.nat.tn/fr/fonds-documentaire/

Le pilote réel a réussi le 13 octobre 2026 : édition arabe du 27 décembre 2016, n°105 (240 pages, 253 209 029 octets), et édition française du 12 mai 2017, n°38 (32 pages, 34 532 986 octets). Les PDF et leurs références sont conservés dans `H:\Mon Drive\JORT\journaux-officiels`. Les empreintes de la copie finale ont été vérifiées. Une seconde exécution a réutilisé les deux PDF, sans téléchargement. La synchronisation distante Google Drive n'est pas confirmée par ce contrôle local.

Le parcours intégral du catalogue s'est arrêté après 26 pages reçues sur une réponse tronquée (`IncompleteRead`). Le script a été corrigé pour reprendre ce cas automatiquement ; les pages reçues restent en cache. Le pilote de téléchargement a ensuite utilisé directement les deux notices connues : il ne démontre donc pas encore l'achèvement de l'inventaire intégral.

Les dix tests locaux ont réussi : langues/numéro, exclusion des annonces légales, refus de sources hors périmètre, PDF invalide, reprise sans réseau, fichier modifié, liste IORT, pagination et exclusion, verrou concurrent, reprise d'une réponse tronquée sans cache partiel. La syntaxe du lanceur PowerShell est valide.

## Commandes PowerShell

Depuis `C:\ChatGPT\JORT` :

```powershell
# Relever le catalogue, sans télécharger de PDF ni écrire sur H:
.\orga\telecharger-journaux-officiels.ps1 -Inventaire

# Petit pilote : deux premiers journaux retenus, chronologiquement
.\orga\telecharger-journaux-officiels.ps1 -Limite 2

# Télécharger tous les journaux retenus dans la source alternative
.\orga\telecharger-journaux-officiels.ps1

# Reprendre : même commande, sans retélécharger les fichiers validés
.\orga\telecharger-journaux-officiels.ps1

# Adapter le dossier au nom réellement utilisé par Drive
.\orga\telecharger-journaux-officiels.ps1 -Destination 'H:\My Drive\JORT\journaux-officiels'

# Actualiser le catalogue (les PDF inchangés restent réutilisés)
.\orga\telecharger-journaux-officiels.ps1 -Actualiser
```

Python et `pypdf` sont nécessaires. Le lanceur utilise le Python installé avec Codex lorsqu'il est disponible ; `-Python 'chemin\python.exe'` permet de choisir un autre environnement. Le nom « Mon Drive » est une valeur par défaut configurable ; ce chemin exact n'a pas été confirmé sur H:.

## Classement

```text
H:\Mon Drive\JORT\journaux-officiels\
  2016\
    ar\JORT-lois-decrets_2016-12-27_n105_ar_IDENTIFIANT.pdf
    fr\JORT-lois-decrets_DATE_nNUMERO_fr_IDENTIFIANT.pdf
  collecte.lock

C:\ChatGPT\JORT\orga\data\collecte-officielle\
  inventaire.json
  rapport.json
  a-completer-sur-iort.csv
  reprise.sqlite
  cache\
  temporaires\
  collecte.lock
```

Chaque PDF possède un fichier `.source.json` voisin : notice, URL de téléchargement, numéro, date, langue, taille, nombre de pages, empreinte SHA-256 et date de contrôle. L'identifiant de notice dans le nom évite d'écraser deux éditions distinctes d'un numéro. La série et la langue sont vérifiées à partir des notices ; le PDF est contrôlé pour sa lisibilité. L'identité du contenu n'est pas confirmée par OCR.

Le PDF est reçu dans un temporaire local sous JORT, contrôlé, copié sous un nom temporaire sur Drive, puis renommé après comparaison des empreintes. La copie finale est relue. La synchronisation de cette copie vers les serveurs Google n'est pas vérifiée par le script.

Un seul téléchargement à la fois, avec une seconde d'espacement par défaut ; trois essais maximum pour les erreurs temporaires. Un refus 401/403 ou un débit 429 persistant arrête la collecte. Les erreurs propres aux fichiers sont signalées ; une panne de stockage arrête l'exécution. Ctrl+C conserve les fichiers validés. Les petits temporaires de téléchargement sont repris depuis le début, sans reprendre une réponse partielle non validée. Les verrous sont libérés automatiquement quand le processus se termine ; conserver leurs fichiers vides.

## Compléter ensuite sur IORT

Le CSV distingue :

- une édition arabe/française absente pour un numéro/date connu dans l'autre langue : sa parution reste à vérifier ;
- une année/langue absente de la source alternative : inventaire IORT à effectuer ;
- un téléchargement d'une notice connue qui a échoué.

Ce CSV ne prétend pas identifier tous les numéros manquants : si un numéro est absent dans les deux langues du catalogue alternatif, il ne peut pas être découvert par comparaison des deux éditions. Il faudra donc inventorier toutes les années sur IORT, puis comparer le catalogue officiel aux PDF conservés. Les différences de numérotation, suppléments et changements de série devront être rapprochés avec date/langue/édition, pas seulement avec le numéro.

Le script actuel ne contacte pas IORT et ne lance pas d'extraction de textes administratifs. Le lecteur Drive et les téléchargements français/arabe ont été validés sur le pilote. L'inventaire intégral reste à terminer avant le lot complet ; le CSV courant porte sur les deux notices du pilote et ne constitue pas une liste exhaustive des manques.
