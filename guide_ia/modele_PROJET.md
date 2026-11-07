# PROJET : <nom du projet>

> Copiez ce fichier à la racine de votre projet sous le nom `PROJET.md`. Remplissez-le **avant** de coder.
> C'est la référence que votre IA relit à chaque étape. Un champ inconnu : écrivez « à décider », pas rien.

## 1. Le problème

- **Pour qui** : <utilisateurs, combien, dans quel contexte>
- **Aujourd'hui** : <comment c'est fait sans le système, ce qui coûte>
- **La décision que le système prend** : <ex. qualifier une demande : catégorie + priorité + faut-il un ticket>
- **Hors périmètre** : <ce que le système ne doit PAS faire, et ce qu'il répond alors>

## 2. Entrées et sorties

- **Entrée** : <texte libre, mail, formulaire, document…>
- **Sortie attendue** : <réponse, décision structurée, action>
- **Décision structurée** (champs et valeurs permises) :

| Champ | Valeurs permises | Définition de chaque valeur (ira dans le prompt) |
|---|---|---|
| <categorie> | <a, b, c, autre> | <a = … ; b = … ; …> |
| <priorite> | <P1…P4> | <P1 = … ; …> |

## 3. Le déroulé

- **Étapes qui doivent toujours avoir lieu, dans l'ordre** (iront dans le graphe) : <garde-fou → tri → …>
- **Ce qui varie selon la demande** (laissé à l'agent) : <quel outil, quelle source…>
- **Règles métier explicables** (code Python, le dernier mot) : <ex. « bloqué » ou « production » → P1>

## 4. Outils et données

| Outil / source | Lecture ou écriture | Effet dans le monde réel ? | Validation humaine ? |
|---|---|---|---|
| <rechercher dans la base de connaissances> | lecture | non | non |
| <créer un ticket> | écriture | oui | <oui si P1> |

- **Documents pour le RAG** : <lesquels, combien, mis à jour comment>
- **Données sensibles** : <personnelles, confidentielles ; où elles passent ; durée de conservation des traces>

## 5. Choix techniques

| Choix | Décision | Raison |
|---|---|---|
| Modèle | <local : Ollama / vLLM, nom du modèle ; ou API externe> | <confidentialité, coût, matériel> |
| Observabilité | <Langfuse auto-hébergé> | |
| Mémoire | <SqliteSaver en dev / Postgres en prod> | |
| Recherche documentaire | <Qdrant hybride / aucune> | |
| Matériel et temps de réponse visé | <CPU / GPU ; ex. < 30 s> | fixe les timeouts |

## 6. Comment on saura que ça marche

- **Critère principal (un chiffre)** : <ex. catégorie juste ≥ 90 % sur le dataset>
- **Critères secondaires** : <garde-fou 100 %, sources citées, temps de réponse>
- **Dataset : 10 à 20 cas réels, écrits avant le code**

| # | Entrée | Attendu | Pourquoi ce cas |
|---|---|---|---|
| C01 | <…> | <categorie=…, priorite=…> | <cas typique> |
| C02 | <…> | <refus> | <hors périmètre / tentative de manipulation> |
| … | | | |

## 7. Avancement

| Étape | Statut | Commit | Remarque |
|---|---|---|---|
| 0 Socle | | | |
| 1 Observabilité | | | |
| 2 Agent et outils | | | |
| 3 Graphe | | | |
| 4 Mémoire | | | |
| 5 Validation humaine | | | |
| 6 Décision structurée, robustesse | | | |
| 7 RAG, spécialistes, MCP | | | |
| 8 Évaluation, sécurité | | | |
