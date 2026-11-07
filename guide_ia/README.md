# Construire votre orchestration avec votre assistant IA

Ce guide vous aide à reproduire, sur **votre** projet, ce que HelpDesk-Ops a construit pendant la formation :
un agent LangChain, orchestré par un graphe LangGraph, observé et évalué dans Langfuse. Le code, votre
assistant IA l'écrira (Claude Code, GitHub Copilot, Cursor, Codex, Gemini CLI…). **Les décisions, c'est vous.**
Ce guide vous donne les décisions à prendre, dans l'ordre, avec un choix recommandé et sa raison.

Trois fichiers :

| Fichier | Rôle |
|---|---|
| ce guide | la méthode, étape par étape |
| [`modele_PROJET.md`](modele_PROJET.md) | la fiche de **votre** projet, à remplir avant d'écrire une ligne de code |
| [`modele_AGENTS.md`](modele_AGENTS.md) | les règles que votre IA doit suivre : versions, API à jour, interdits, méthode |

---

## Le principe : l'IA code vite, donc elle se trompe vite

Un assistant IA connaît LangChain tel qu'il était **pendant son entraînement**. Or LangChain 1.x, LangGraph 1.x
et le SDK Langfuse v4 ont beaucoup changé. Sans consignes, il produira du code plausible et dépassé
(`initialize_agent`, `LLMChain`, `from langfuse.decorators import …`). Trois réflexes :

1. **Lui donner le contexte** : la fiche projet, les règles, la documentation à jour (section 2).
2. **Avancer par petites étapes vérifiables**, dans l'ordre de la construction HelpDesk-Ops (section 3). Une étape =
   un fichier qui tourne, un dessin du graphe, une trace Langfuse.
3. **Lui faire proposer un plan avant le code**, et relire ce plan avec la grille de la section 4.

---

## 1. Définir votre projet (30 minutes, sans IA ou presque)

Copiez `modele_PROJET.md` à la racine de votre projet sous le nom `PROJET.md`, et remplissez-le. Vous pouvez
demander à l'IA de **vous interviewer** pour le remplir (prompt en fin de section), mais les réponses sont les
vôtres.

Les questions qui décident de l'architecture :

| Question | Pourquoi elle compte |
|---|---|
| Quelle **décision** le système prend-il ? (trier, répondre, router, rédiger…) | c'est elle qu'on va structurer et mesurer |
| Quelles **actions** touchent le monde réel ? (créer un ticket, envoyer un mail, payer) | chacune demandera peut-être une validation humaine |
| Quelles étapes doivent **toujours** se produire, dans quel ordre ? | ce qui est fixe va dans le graphe ; ce qui varie, l'agent le choisit |
| Quelles **données** sont sensibles ? (personnelles, confidentielles) | décide du modèle (local ou non) et de ce qu'on trace |
| À quoi voit-on que **ça marche** ? Un chiffre | sans critère mesurable, impossible de comparer deux versions |
| **10 à 20 cas réels** avec la réponse attendue | votre futur dataset d'évaluation, écrit **avant** le code |

> ✅ **Choix recommandé : écrire les cas de test d'abord.** Raison : c'est ce que l'on a vu en J4. Sans dataset,
> chaque changement de prompt ou de modèle se juge « à l'œil », et l'œil se trompe. Avec 20 cas, une experiment
> Langfuse tranche en quelques minutes (défi B : catégorie juste 50 % → 100 %, mesuré, pas ressenti).

Prompt pour l'interview :

```text
Lis PROJET.md. Pose-moi une à une les questions nécessaires pour le compléter,
en commençant par la décision que le système doit prendre. N'écris aucun code.
Quand c'est fini, réécris PROJET.md avec mes réponses et liste ce qui reste flou.
```

---

## 2. Configurer votre IA

### 2.1 Le fichier de règles : `AGENTS.md`

Copiez `modele_AGENTS.md` à la racine de votre projet sous le nom `AGENTS.md`, et adaptez la section « Projet ».

> ✅ **Choix recommandé : `AGENTS.md`, un seul fichier pour tous les outils.** Raison : c'est une convention
> ouverte (<https://agents.md>) lue par Codex, Cursor, Gemini CLI, Windsurf, Zed, Aider, l'agent de codage de
> GitHub Copilot et d'autres. Changer d'outil ne vous oblige pas à tout réécrire.

Selon l'outil :

| Outil | Ce qu'il lit | À faire |
|---|---|---|
| Codex, Cursor, Gemini CLI, Windsurf… | `AGENTS.md` | rien de plus |
| Claude Code | `CLAUDE.md` | créer un `CLAUDE.md` d'une ligne : `@AGENTS.md` (il importe le fichier) |
| GitHub Copilot (chat dans VS Code) | `.github/copilot-instructions.md` | y écrire « Suis les règles de AGENTS.md », ou copier le contenu |
| Un chat sans accès aux fichiers | rien | coller `PROJET.md` puis `AGENTS.md` en début de conversation |

### 2.2 La documentation à jour

> ✅ **Choix recommandé : brancher les serveurs MCP de documentation de LangChain et de Langfuse.** Raison : l'IA
> lit alors la doc **du jour** au lieu de sa mémoire. C'est le remède le plus efficace contre le code dépassé.

| Source | Adresse | Usage |
|---|---|---|
| Doc LangChain + LangGraph (MCP) | `https://docs.langchain.com/mcp` | guides et concepts |
| Référence API LangChain (MCP) | `https://reference.langchain.com/mcp` | signatures exactes |
| Doc Langfuse (MCP) | `https://langfuse.com/api/mcp` | SDK v4, experiments, scores |
| Sans MCP : index pour LLM | `https://docs.langchain.com/llms.txt`, `https://langfuse.com/llms.txt` | à donner en lien à l'IA |

Exemple avec Claude Code :

```bash
claude mcp add --transport http docs-langchain https://docs.langchain.com/mcp
claude mcp add --transport http langfuse-docs https://langfuse.com/api/mcp
```

Les autres outils (Cursor, VS Code, Gemini CLI…) ont un fichier ou un écran de configuration MCP : cherchez
« MCP » dans leur documentation, et indiquez l'adresse en transport HTTP.

> 🛡️ Un serveur MCP est du code ou du contenu d'un tiers que votre IA va lire et suivre. Ne branchez que des
> serveurs officiels, comme ceux-ci (rappel J3 : la liste blanche).

### 2.3 Les versions figées

Partez du `pyproject.toml` de la formation (`infrastructure/pyproject.toml`) ou recopiez ses bornes : elles sont
testées ensemble. Les principales : `langchain 1.4`, `langgraph 1.2`, `langgraph-checkpoint-sqlite 3.1`,
`langfuse 4.15` (SDK v4), `pydantic 2.13`, `langchain-ollama 1.1` ou `langchain-openai 1.6`.

> ✅ **Choix recommandé : `uv` avec un fichier `uv.lock` commité.** Raison : l'IA a tendance à « réparer » une erreur
> en changeant une version. Avec un lock, toute mise à jour devient visible, voulue, et se teste.

### 2.4 Le mode de travail avec l'IA

- **Plan d'abord.** Utilisez le mode « plan » de votre outil s'il en a un, sinon demandez explicitement un plan
  sans code. Relisez-le avec la grille de la section 4.
- **Une étape par conversation**, ou presque. Un contexte court = moins de dérive.
- **Elle lance, vous regardez.** Chaque étape se termine par une exécution réelle, le dessin du graphe
  (`draw_ascii()`) et la trace Langfuse. Pas de « ça devrait marcher ».
- **Commit à chaque étape qui tourne.** Si l'IA casse tout à l'étape suivante, on revient en arrière en une commande.

---

## 3. Les étapes, et les choix à chaque étape

Le même ordre que la construction HelpDesk-Ops (`construction/etape_NN_*.py`). Pour chaque étape : le choix
recommandé, la raison, le critère « c'est fini », et la référence dans le cours. Le prompt type est en fin de section.

### Étape 0 · Le socle : modèle, stack, configuration

| Décision | Choix recommandé | Raison |
|---|---|---|
| Modèle | **local** (Ollama sur un poste, vLLM sur un GPU partagé), choisi via une seule fonction `get_llm()` | données qui ne sortent pas, coût nul, et changer de modèle ne touche qu'une fonction. Un modèle externe par API reste possible : c'est une décision de confidentialité et de coût, à écrire dans `PROJET.md` |
| Observabilité | **Langfuse auto-hébergé** (Docker), comme en formation | les traces contiennent les questions des utilisateurs : elles restent chez vous |
| Secrets | `.env` ignoré par git, un `.env.example` commité | l'IA ne doit jamais écrire une clé dans le code |

**Fini quand** : un script de vérification (comme `infrastructure/smoke_test.py`) répond « OK » pour le modèle,
Langfuse et, si besoin, Qdrant. 📚 J0-01.

### Étape 1 · Observer avant de construire

| Décision | Choix recommandé | Raison |
|---|---|---|
| Quand brancher Langfuse | **dès le premier appel** au modèle | sinon vous déboguez à l'aveugle les étapes suivantes |
| Comment | `CallbackHandler` de `langfuse.langchain` dans `config={"callbacks": [...]}`, et `propagate_attributes(session_id=…, user_id=…, tags=[…])` | une conversation = une session, regroupée et filtrable |

**Fini quand** : un appel apparaît dans Langfuse avec sa session et ses tags. 📚 étape 1, J4-01.

### Étape 2 · Un agent avec des outils

| Décision | Choix recommandé | Raison |
|---|---|---|
| Point de départ | `create_agent` (de `langchain.agents`) avec 2 ou 3 outils `@tool` | le plus simple qui marche ; on passe au graphe quand on sait pourquoi |
| Les outils | une **docstring** claire par outil, des paramètres typés, un retour court | pour un outil, la docstring **est** lue par le modèle : c'est elle qui décide s'il l'appelle |
| Les droits | chaque outil ne donne **que** ce dont il a besoin (lecture seule par défaut) | un agent peut être manipulé par le texte qu'il lit (OWASP, injection de prompt) |

**Fini quand** : la trace montre le bon outil appelé avec les bons arguments, sur 3 questions. 📚 étape 2, J1-02.

### Étape 3 · Le graphe : rendre le déroulé explicite

| Décision | Choix recommandé | Raison |
|---|---|---|
| Agent seul ou graphe ? | **graphe** dès qu'une étape doit **toujours** avoir lieu (tri, garde-fou, validation) | un agent choisit ; un graphe garantit. Le fixe dans le graphe, le variable dans l'agent |
| L'état | un `TypedDict` **minimal** (ou `MessagesState` + quelques champs) ; les nœuds renvoient une mise à jour **partielle** | un état gros et flou devient impossible à relire et à tester |
| Le routage | `add_conditional_edges` avec une fonction annotée `Literal["a", "b"]` | le dessin du graphe montre les branches ; une faute de nom casse à la compilation, pas en production |
| L'agent dans le graphe | l'agent de l'étape 2 devient **un nœud** | on garde ce qui marche |

**Fini quand** : `draw_ascii()` montre le graphe attendu et chaque branche a été parcourue une fois. 📚 étape 3,
défi 3, J1-03.

### Étape 4 · La mémoire

| Décision | Choix recommandé | Raison |
|---|---|---|
| Checkpointer | `SqliteSaver` en développement, Postgres en production ; `InMemorySaver` seulement dans les tests | la mémoire doit survivre à un redémarrage |
| Identifiant | `thread_id` = une conversation | c'est aussi la `session_id` Langfuse : la trace et la mémoire racontent la même histoire |
| Données personnelles | prévoir dès maintenant comment **supprimer** un thread | RGPD : le droit à l'effacement vaut aussi pour les checkpoints |

**Fini quand** : on relance le script avec le même `thread_id` et l'agent se souvient. 📚 étape 4, J2-01.

### Étape 5 · L'humain dans la boucle

| Décision | Choix recommandé | Raison |
|---|---|---|
| Quoi valider | **toute action irréversible ou sensible** listée dans `PROJET.md` | le modèle peut se tromper ou être manipulé ; l'humain garde la main là où ça coûte |
| Comment | `interrupt()` dans un nœud, reprise par `Command(resume=…)` | la pause est persistée : la validation peut venir des heures plus tard |
| Où placer l'action | **après** l'`interrupt()`, jamais avant | à la reprise, le nœud repart **du début** : une action placée avant serait exécutée deux fois |

**Fini quand** : une action sensible se met en pause, est approuvée, et n'est exécutée **qu'une fois** même si on
relance la reprise. 📚 étape 5, J2-02.

### Étape 6 · Une décision structurée et un système robuste

| Décision | Choix recommandé | Raison |
|---|---|---|
| Sortie du tri | un modèle Pydantic avec des `Literal`, obtenu par `with_structured_output(Schema, method="json_schema")` | le format est **garanti** : le graphe route sur des valeurs connues |
| Où mettre les consignes | dans le **prompt**, pas dans les `Field(description=…)` | vérifié le 07/11 : en `json_schema`, le modèle ne lit pas les descriptions du schéma ; elles contraignent la forme, pas le fond |
| Le dernier mot | une **règle Python** quand elle suffit (plancher d'urgence, hameçonnage → accès) | explicable, testable, et non manipulable par le texte de l'utilisateur |
| Les pannes | `RetryPolicy` sur les nœuds qui appellent le réseau, `TimeoutPolicy` **mesurée sur votre matériel**, un `error_handler` qui **journalise** l'erreur avant de répondre poliment | vécu en formation : un repli silencieux a caché un timeout pendant des heures |

**Fini quand** : 3 cas tests donnent la bonne catégorie, et une panne simulée (serveur coupé) produit un message
propre **et** une erreur visible dans Langfuse. 📚 étapes 6a et 6, J2-03, J2-04.

### Étape 7 · Connaissances et spécialistes

| Décision | Choix recommandé | Raison |
|---|---|---|
| Un ou plusieurs agents ? | **un seul** tant qu'il suffit | chaque agent ajouté coûte du temps, des appels et des erreurs de routage |
| Si plusieurs | un **coordinateur** qui appelle des **spécialistes exposés comme outils**, chacun avec SES outils et SA consigne | le pattern le plus simple à tracer et à tester ; la description de l'outil décide du routage |
| Les documents | RAG **hybride** (mots-clés + vecteurs) sur Qdrant, avec la source citée dans la réponse | les sigles et les références (VPN, KB-001) se ratent en vectoriel pur ; une source citée se vérifie |
| Outils externes | MCP, avec une **liste blanche** côté client | un serveur MCP peut exposer plus d'outils que vous n'en voulez |

**Fini quand** : la trace montre le coordinateur, le spécialiste appelé, et les passages récupérés ; la réponse cite
sa source. 📚 étape 7, défi A, J3-01, J3-02, J3-04.

### Étape 8 · Mesurer et se défendre

| Décision | Choix recommandé | Raison |
|---|---|---|
| Le dataset | les cas de `PROJET.md`, dans un **JSON du dépôt** synchronisé vers un dataset Langfuse | le dépôt reste la source de vérité, versionnée avec le code |
| Les mesures | `dataset.run_experiment(...)` avec des évaluateurs **déterministes** d'abord (catégorie juste, priorité juste) | un chiffre exact et reproductible ; on compare deux runs cas par cas |
| Le juge LLM | seulement pour ce qu'une règle ne mesure pas (fidélité aux sources), et **calibré** sur quelques cas notés à la main | un juge non calibré donne des chiffres faux avec assurance |
| La sécurité | un garde-fou en entrée, des tests d'attaque (injection, demande hors périmètre) **dans le dataset** | une défense non testée est une impression ; OWASP Top 10 for Agentic Applications comme check-list |

**Fini quand** : deux runs comparés dans Langfuse, et une phrase honnête : « x passe de a/20 à b/20 ; ce cas reste
faux ». 📚 étape 8, défis B et C, J4-04, J4-05, J5-01, J5-02.

### Ensuite · Vers la production

- `langgraph.json` + `langgraph dev` pour servir le graphe et l'inspecter dans le Studio (voir celui de la racine
  du cours).
- Une **gate d'évaluation en CI** : l'experiment tourne à chaque modification, la fusion est bloquée si un score
  baisse (J5-04).
- La conformité : registre des traitements, durée de conservation des traces, information des utilisateurs (J5-03).

### Le prompt type pour chaque étape

```text
Lis PROJET.md et AGENTS.md. Nous sommes à l'étape <N> : <titre>.
Objectif : <une phrase>. Critère de fin : <le critère de l'étape>.
1. Vérifie dans la documentation (MCP) les API que tu comptes utiliser.
2. Propose un plan court : fichiers touchés, nœuds/outils ajoutés, ce qui change dans l'état.
   N'écris pas de code avant que j'aie validé le plan.
3. Ensuite : code, exécution réelle, dessin du graphe (draw_ascii), et dis-moi quoi regarder dans Langfuse.
Marque les blocs ajoutés par « # ═══ NOUVEAU (étape N) ».
```

---

## 4. Relire ce que l'IA propose : la grille

À passer sur chaque plan et chaque diff. Un « non » = on corrige avant d'avancer.

- [ ] Les imports viennent des API **actuelles** (`langchain.agents.create_agent`, `langgraph.graph.StateGraph`,
      `langfuse.langchain.CallbackHandler`, `from langfuse import observe, propagate_attributes`) ? Aucun
      `initialize_agent`, `AgentExecutor`, `LLMChain`, `langfuse.decorators`, `langfuse.callback` ?
- [ ] Aucune version modifiée dans `pyproject.toml` sans que vous l'ayez demandé ?
- [ ] Aucune clé, aucun mot de passe dans le code ?
- [ ] Aucun effet de bord **avant** un `interrupt()` ?
- [ ] Chaque outil n'a que les droits dont il a besoin ?
- [ ] Les consignes au modèle sont dans le **prompt** (pas seulement dans un schéma) ?
- [ ] Les erreurs sont **journalisées**, pas seulement remplacées par un message aimable ?
- [ ] Le graphe dessiné correspond à ce que vous attendiez ?
- [ ] La trace Langfuse montre une session, des tags, et le chemin attendu ?
- [ ] Un changement de prompt, de modèle ou de règle repasse par l'experiment ?

## 5. Quand l'IA tourne en rond

- **Elle invente une API** : « Vérifie dans la doc MCP LangChain que `<fonction>` existe en version 1.x, avec sa
  signature exacte, et cite la page. »
- **Elle corrige une erreur en en créant deux** : arrêtez. Revenez au dernier commit, donnez-lui le message
  d'erreur **complet** et demandez une hypothèse avant tout correctif.
- **Elle veut tout refaire** : rappelez l'étape en cours et son critère de fin. Une étape à la fois.
- **Vous ne comprenez plus le code** : demandez « explique ce fichier nœud par nœud, comme à un collègue », puis
  comparez avec l'étape de construction correspondante du cours.
