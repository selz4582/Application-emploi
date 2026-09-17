# Cap Emploi 42

Application locale, mono-utilisateur, d'aide à la recherche d'emploi dans la Loire.
Elle fonctionne avec Python 3 et SQLite, sans service cloud ni écran de connexion.

## Démarrage (Windows 11)

Double-cliquer sur `demarrer.bat`, puis ouvrir <http://127.0.0.1:8765>. Les données sont
créées dans `data/emploi.sqlite3`. Le serveur n'écoute que l'ordinateur local.

## Développement

```bash
python -m unittest discover -s tests -v
python app.py
```

Le connecteur SIRENE est désactivé tant qu'aucune clé API n'est fournie. Il utilise
exclusivement l'API officielle INSEE, sur action manuelle. Aucun scraping n'est actif.
Les offres peuvent toujours être ajoutées manuellement dans **Mes offres**, puis notées
localement et transformées en brouillons de candidature sans aucun envoi automatique.
Les trajets sont saisis/vérifiés manuellement tant qu'aucun fournisseur officiel de
calcul d'itinéraire n'est configuré ; une information inconnue n'est jamais pénalisée.

## Données et sauvegardes

Les CV sont copiés dans `data/documents` et leur texte est extrait localement. Deux
fichiers DOCX ou ODT de 10 Mo maximum sont acceptés. Les informations extraites ne
sont considérées comme fiables qu'après validation humaine dans les cinq rubriques
de l'écran **Mes CV**. Un seul CV peut être choisi par défaut pour les futurs brouillons.

L'écran **Mes CV** permet de créer une archive datée dans `data/backups` et un export
CSV dans `data/exports`. Une archive contient un instantané cohérent de SQLite, les
documents et un manifeste contrôlable. Les anciennes archives ne sont jamais écrasées.

## Voir les maquettes

Lancer l'application puis cliquer sur **Maquettes** dans la navigation. Les trois fichiers
sont aussi directement disponibles dans le dépôt :

- [`Carnet de route`](static/mockups/a-carnet-de-route.svg) ;
- [`Poste de pilotage`](static/mockups/b-poste-de-pilotage.svg) ;
- [`Parcours guidé`](static/mockups/c-parcours-guide.svg).
