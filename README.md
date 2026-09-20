# Carnet Emploi 42

Application locale, mono-utilisateur, d'aide à la recherche d'emploi dans la Loire.
Elle fonctionne avec Python 3 et SQLite, sans service cloud ni écran de connexion.
Le nom reflète un carnet personnel et ne présente pas l'application comme un service
institutionnel. Les règles de nommage sont détaillées dans [`docs/nomenclature.md`](docs/nomenclature.md).

## Démarrage (Windows 11)

Double-cliquer sur `demarrer.bat` : le serveur démarre d'abord, puis le navigateur s'ouvre
automatiquement après 10 secondes sur <http://127.0.0.1:8765>. Ce délai laisse à Windows
le temps de préparer complètement le serveur local. Les données sont créées dans
`data/emploi.sqlite3`. Le serveur n'écoute que l'ordinateur local.

Un code HTTP `200` signifie **succès**, et non erreur. Les requêtes réussies ne sont plus
affichées dans la console afin de ne pas inquiéter l'utilisateur ; seules les véritables
erreurs HTTP (`400`, `404`, `500`…) y apparaissent. Si le port est déjà utilisé, fermez
l'ancienne fenêtre Carnet Emploi avant de relancer `demarrer.bat`.

## Développement

```bash
python -m unittest discover -s tests -v
python app.py
```

Le connecteur SIRENE est désactivé tant qu'aucune clé API n'est fournie. Il utilise
exclusivement l'API officielle INSEE, sur action manuelle. Aucun scraping n'est actif.
Sans clé INSEE, un établissement peut être ajouté manuellement avec son nom, son adresse,
son code postal et sa commune : le parcours de candidature spontanée reste donc entièrement
fonctionnel hors ligne.
Les établissements peuvent ensuite être corrigés, désactivés, réactivés ou supprimés
lorsqu'ils ne sont liés à aucun contact, aucune offre et aucune candidature. Un
établissement utilisé reste désactivable afin de préserver l'historique.
Les offres peuvent toujours être ajoutées manuellement dans **Mes offres**, puis notées
localement et transformées en brouillons de candidature sans aucun envoi automatique.
Elles peuvent aussi être corrigées après leur saisie : le score est alors recalculé.
La liste peut être filtrée par texte, type de contrat et score minimum.
Les scores sont également actualisés après une modification du profil ou du CV par
défaut. Seules les rubriques de CV relues dans l'écran **Mes CV** participent au score ;
le texte extrait automatiquement n'est jamais considéré comme une donnée vérifiée.
Les trajets sont saisis/vérifiés manuellement tant qu'aucun fournisseur officiel de
calcul d'itinéraire n'est configuré ; une information inconnue n'est jamais pénalisée.
Chaque candidature possède un dossier local permettant de relire le message, la lettre,
le destinataire et le CV. L'application n'envoie aucun courriel : le statut « envoyée »
ne peut être confirmé qu'après validation explicite des trois contrôles humains.
Pour une candidature spontanée, le CV peut être choisi avant la création des brouillons.
Si une candidature similaire existe déjà, aucun nouveau brouillon n'est créé avant une
confirmation explicite de l'utilisateur.
Un brouillon créé par erreur peut être supprimé depuis son dossier tant qu'il n'a pas
été marqué comme envoyé. Une candidature envoyée reste conservée dans l'historique.
Le tableau de suivi peut être filtré par texte, statut et type de candidature. Les
relances dont la date est dépassée sont signalées dans le tableau et le kanban, et la
cloche ramène directement vers les notifications ou le suivi.
La date de relance choisie est prioritaire sur la date de réponse attendue. Aucune
nouvelle relance n'est créée pour une candidature acceptée, refusée, abandonnée, déjà
classée sans réponse. Une candidature passée en entretien produit une relance uniquement
si une date de suivi a été renseignée.
Les statistiques de réponse, d'entretien et de résultat portent uniquement sur les
candidatures réellement marquées comme envoyées. Les brouillons sont affichés séparément
et ne réduisent donc plus artificiellement les taux de réponse.
Le passage à « Entretien », « Acceptée » ou « Refusée » enregistre automatiquement la
date de réponse si elle est absente. Les statuts terminaux effacent la relance et la
prochaine action devenues inutiles.
Les contacts doivent provenir d'une page publique de l'établissement. Les adresses
personnelles sont refusées et la source reste enregistrée pour pouvoir être vérifiée.
Ils peuvent être corrigés, désactivés, réactivés ou supprimés. Une source contrôlée
depuis plus de 180 jours est signalée comme étant à revérifier. Les trajets saisis
manuellement affichent également leur date de contrôle et leur origine.

## Données et sauvegardes

Les CV sont copiés dans `data/documents` et leur texte est extrait localement. Deux
fichiers PDF texte, DOCX ou ODT de 10 Mo maximum sont acceptés. Les PDF scannés sans
couche texte sont refusés avec un message explicite : aucune OCR implicite n’est lancée. Les informations extraites ne sont
considérées comme fiables qu'après validation humaine dans les cinq rubriques
de l'écran **Mes CV**. Un seul CV peut être choisi par défaut pour les futurs brouillons.
Un CV inutilisé peut être supprimé puis remplacé ; lorsqu'il appartient déjà à un dossier
de candidature, l'application demande d'abord de sélectionner un autre CV dans ce dossier.

L'écran **Mes CV** permet de créer une archive datée dans `data/backups` et un export
CSV dans `data/exports`. Une archive contient un instantané cohérent de SQLite, les
documents et un manifeste contrôlable. Les anciennes archives ne sont jamais écrasées.
L'export CSV est téléchargé par le navigateur et contient l'entreprise, l'établissement,
la commune, le type de candidature, les dates de suivi et la prochaine action.
La restauration accepte uniquement une archive ZIP valide de 30 Mo maximum, contrôle
son manifeste, ses chemins et l'intégrité de SQLite, puis crée automatiquement une
sauvegarde de sécurité des données actuelles avant leur remplacement.
Les archives locales sont listées dans l'application avec leur taille et leur état de
validité. Elles peuvent être téléchargées pour être conservées sur un autre support.
Une archive locale valide peut aussi être restaurée directement, sans devoir la
sélectionner de nouveau, ou supprimée après une confirmation explicite.
Le bouton **Vérifier les données** contrôle l'intégrité de la base, l'accès en écriture,
les liaisons entre les données, la version de Python utilisée et le nombre de sauvegardes
disponibles. Les écritures HTTP sont sérialisées et SQLite attend brièvement lorsqu'une
autre opération est en cours, ce qui évite les conflits pendant une sauvegarde ou une
restauration.


## Installation et distribution Windows

Pour utiliser le projet depuis ses sources, lancer une fois `installer-dependances.bat`,
puis `demarrer.bat`. La dépendance `pypdf` sert uniquement à lire les CV PDF.

Pour produire l’exécutable autonome :

```bat
construire-windows.bat
```

Le script installe les outils de développement, exécute PyInstaller et crée
`dist\CarnetEmploi42.exe`. Si Inno Setup (`iscc`) est installé, il produit aussi
`dist\Carnet-Emploi-42-Installation.exe` avec les raccourcis Windows. Dans la version
installée, les données utilisateur restent séparées de l’exécutable dans
`%LOCALAPPDATA%\Carnet Emploi 42\data`, afin qu’une mise à jour ne les écrase pas. La
variable `CARNET_EMPLOI_DATA_DIR` permet de choisir un autre emplacement.

## Tests navigateur

Les parcours Playwright vérifient la navigation réelle et la création d’une offre :

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python -m unittest tests.test_browser -v
```

Si Playwright ou Chromium n’est pas installé, ces tests sont explicitement ignorés ; les
tests métier et HTTP restent exécutés.


## Recherche officielle France Travail

La recherche d’offres utilise exclusivement l’API partenaire officielle et reste désactivée
tant que `FRANCE_TRAVAIL_CLIENT_ID` et `FRANCE_TRAVAIL_CLIENT_SECRET` ne sont pas
configurés. Une recherche est lancée uniquement après un clic de l’utilisateur, limitée au
département 42, puis chaque résultat doit être importé explicitement. La saisie manuelle
reste disponible sans connexion.

## Sauvegarde sur un support externe

Exécuter `configurer-sauvegarde-externe.bat` en lui donnant un dossier de clé USB, disque
externe ou dossier synchronisé, puis redémarrer l’application. Le bouton **Copier vers le
support externe** crée d’abord une archive locale cohérente, la copie sans écraser une
archive existante et vérifie la copie. Le dossier peut aussi être défini directement avec
`CARNET_EMPLOI_BACKUP_DIR`.

## Accessibilité et structure du client

L’interface possède un lien d’évitement, une indication de page courante, des focus visibles,
des dialogues nommés et respecte la préférence de réduction des animations. Le résultat de
l’audit et la liste des contrôles manuels sont dans [`docs/accessibilite.md`](docs/accessibilite.md).
Les fonctions communes de navigation, API, confirmation et échappement HTML sont isolées
dans `static/common.js`; les parcours fonctionnels restent dans `static/app.js`.

## Configuration guidée et état des services

Sous Windows, lancer `configurer-connecteurs.bat` pour enregistrer les identifiants France
Travail et le jeton INSEE dans les variables de l’utilisateur, puis redémarrer l’application.
L’écran **Mon profil** indique uniquement si France Travail, l’INSEE et la sauvegarde externe
sont configurés : les jetons et secrets ne sont jamais renvoyés au navigateur. Une panne
réseau, un refus d’identifiants ou une réponse illisible produit désormais un message
compréhensible sans afficher le détail technique ni les secrets.

## Enregistrement unique des clés API

L’écran **Mon profil** contient un formulaire pour enregistrer une seule fois l’identifiant et
le secret France Travail, le jeton INSEE et le dossier de sauvegarde externe. Les valeurs sont
écrites atomiquement dans `data/configuration.json` avec des permissions restreintes lorsque
le système le permet. Les champs secrets ne sont jamais relus dans le navigateur, ne sont pas
inclus dans les sauvegardes et peuvent être supprimés explicitement depuis le même écran.
Les variables d’environnement restent prioritaires pour une installation administrée.

Indeed ne propose pas ici de connecteur officiel configuré. L’application ne contourne pas
les protections d’un site et ne lance pas de scraping susceptible de casser ou de violer ses
conditions d’utilisation. Pour Indeed ou une autre source sans API autorisée, le parcours sûr
reste la saisie dans **Mes offres** avec le lien original ; la notation et la préparation de la
candidature fonctionnent ensuite normalement.

## Import ponctuel depuis les sites d’emploi

Dans **Mes offres**, coller l’URL HTTPS d’une offre précise Indeed, HelloWork, Meteojob,
Apec, Cadremploi, Monster, LinkedIn ou Welcome to the Jungle pour tenter un préremplissage.
L’import est volontairement limité aux domaines reconnus, vérifie `robots.txt`, refuse les
redirections vers un autre domaine, limite la réponse à 2 Mo et lit uniquement le bloc public
standard `JobPosting` en JSON-LD. Il ne parcourt jamais les listes de résultats et ne contourne
ni connexion, ni CAPTCHA, ni blocage. Si le site refuse l’accès, n’expose pas de données
structurées ou change son format, l’application demande de revenir à la saisie manuelle.
Le préremplissage n’enregistre rien : l’utilisateur doit relire les champs puis confirmer avec
**Enregistrer et évaluer**. La source et la référence sont conservées après modification.
