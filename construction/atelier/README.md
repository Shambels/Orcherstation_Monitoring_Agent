# Votre atelier

C'est ici que **vous** construisez HelpDesk-Ops, en même temps que le formateur.

1. Au début : l'étape 0, distribuée par le formateur, devient votre fichier de travail.
   ```bash
   cp etape_00_chaine_tri.py construction/atelier/mon_helpdesk.py
   ```
2. À chaque étape, recopiez dans `mon_helpdesk.py` les blocs marqués `NOUVEAU` que le formateur écrit à l'écran.
3. Lancez votre fichier après chaque ajout et regardez ce qui change : le terminal, le dessin du graphe, Langfuse.
   ```bash
   uv run --project infrastructure python construction/atelier/mon_helpdesk.py
   ```
4. **En fin d'étape, la correction est distribuée.** Perdu ou en retard ? Elle remplace votre fichier, et vous repartez de là.
   ```bash
   cp etape_03_stategraph.py construction/atelier/mon_helpdesk.py
   ```

Ce dossier est ignoré par Git : votre code reste sur votre poste.
