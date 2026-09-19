# Audit d’accessibilité de Carnet Emploi 42

Audit interne du 19 septembre 2026. Il ne constitue pas une déclaration de conformité RGAA.

## Contrôles réalisés

- langue française et titre de page présents ;
- lien d’évitement vers le contenu principal ;
- navigation principale nommée et page courante exposée avec `aria-current` ;
- focus clavier renforcé sur les contrôles et non supprimé ;
- annonces des confirmations et erreurs via des zones `aria-live` ;
- dialogues nommés et fermeture accessible au clavier ;
- agrandissement du texte et thème sombre conservés ;
- animations neutralisées lorsque `prefers-reduced-motion` est actif ;
- parcours automatisé clavier ajouté avec Playwright.

## Contrôles manuels à refaire avant chaque publication

1. Naviguer dans toutes les fonctions avec Tab, Maj+Tab, Entrée, Espace et Échap.
2. Tester avec NVDA et la dernière version stable de Firefox sous Windows.
3. Vérifier les contrastes avec les thèmes clair et sombre.
4. Tester le zoom navigateur à 200 % et 400 %, puis une largeur de 320 pixels.
5. Contrôler les documents téléchargés séparément de l’interface HTML.
