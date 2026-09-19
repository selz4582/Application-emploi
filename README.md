# Carnet Emploi 42

Application locale, mono-utilisateur, d'aide à la recherche d'emploi dans la Loire.
Elle fonctionne avec Python 3 et SQLite, sans service cloud ni écran de connexion.
Le nom reflète un carnet personnel et ne présente pas l'application comme un service
institutionnel. Les règles de nommage sont détaillées dans [`docs/nomenclature.md`](docs/nomenclature.md).

## Démarrage (Windows 11)

Double-cliquer sur `demarrer.bat` : le serveur démarre d'abord, puis le navigateur s'ouvre
automatiquement sur <http://127.0.0.1:8765>. Les données sont créées dans
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
Les contacts doivent provenir d'une page publique de l'établissement. Les adresses
personnelles sont refusées et la source reste enregistrée pour pouvoir être vérifiée.
Ils peuvent être corrigés, désactivés, réactivés ou supprimés. Une source contrôlée
depuis plus de 180 jours est signalée comme étant à revérifier. Les trajets saisis
manuellement affichent également leur date de contrôle et leur origine.

## Données et sauvegardes

Les CV sont copiés dans `data/documents` et leur texte est extrait localement. Deux
fichiers DOCX ou ODT de 10 Mo maximum sont acceptés. Les informations extraites ne
sont considérées comme fiables qu'après validation humaine dans les cinq rubriques
de l'écran **Mes CV**. Un seul CV peut être choisi par défaut pour les futurs brouillons.
Un CV inutilisé peut être supprimé puis remplacé ; lorsqu'il appartient déjà à un dossier
de candidature, l'application demande d'abord de sélectionner un autre CV dans ce dossier.

L'écran **Mes CV** permet de créer une archive datée dans `data/backups` et un export
CSV dans `data/exports`. Une archive contient un instantané cohérent de SQLite, les
documents et un manifeste contrôlable. Les anciennes archives ne sont jamais écrasées.
La restauration accepte uniquement une archive ZIP valide de 30 Mo maximum, contrôle
son manifeste, ses chemins et l'intégrité de SQLite, puis crée automatiquement une
sauvegarde de sécurité des données actuelles avant leur remplacement.
Les archives locales sont listées dans l'application avec leur taille et leur état de
validité. Elles peuvent être téléchargées pour être conservées sur un autre support.
Une archive locale valide peut aussi être restaurée directement, sans devoir la
sélectionner de nouveau, ou supprimée après une confirmation explicite.
Le bouton **Vérifier les données** contrôle l'intégrité de la base, l'accès en écriture,
la version de Python utilisée et le nombre de sauvegardes disponibles.

## Voir les maquettes

Lancer l'application puis cliquer sur **Maquettes** dans la navigation. Les trois fichiers
sont aussi directement disponibles dans le dépôt :

- [`Carnet de route`](static/mockups/a-carnet-de-route.svg) ;
- [`Poste de pilotage`](static/mockups/b-poste-de-pilotage.svg) ;
- [`Parcours guidé`](static/mockups/c-parcours-guide.svg).
