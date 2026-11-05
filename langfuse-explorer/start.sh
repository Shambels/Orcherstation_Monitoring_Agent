#!/usr/bin/env bash
# =============================================================================
# Langfuse Explorer : démarrage en une commande (Linux / macOS / WSL)
#
#   ./start.sh            génère .env si besoin, démarre Langfuse, attend, injecte le seed
#   ./start.sh --reset    repart de zéro (supprime les données du kit, PAS celles du cours)
#   ./start.sh --no-seed  démarre sans injecter les données
#   ./start.sh --stop     arrête le kit (les données sont conservées)
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

RESET=0; SEED=1
for arg in "$@"; do
  case "$arg" in
    --reset) RESET=1 ;;
    --no-seed) SEED=0 ;;
    --stop) docker compose stop; echo "⏹  Kit arrêté (données conservées)."; exit 0 ;;
    -h|--help) sed -n 2,10p "$0"; exit 0 ;;
    *) echo "Option inconnue : $arg"; exit 2 ;;
  esac
done

echo "== 1/4 Prérequis"
command -v docker >/dev/null || { echo "❌ Docker absent : installer Docker Desktop ou Docker Engine."; exit 1; }
docker info >/dev/null 2>&1 || { echo "❌ Docker ne répond pas : démarrer Docker Desktop / le service docker."; exit 1; }
command -v uv >/dev/null || { echo "❌ uv absent : curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
if ss -ltn 2>/dev/null | grep -q ':3100 ' && ! docker compose ps --status running 2>/dev/null | grep -q langfuse-web; then
  echo "❌ Le port 3100 est déjà pris par un autre programme. Changer EXPLORER_PORT et LANGFUSE_BASE_URL dans .env."; exit 1
fi

echo "== 2/4 Configuration"
uv run --project seed generate_env.py

if [ "$RESET" = 1 ]; then
  echo "⚠️  --reset : suppression des volumes du projet « langfuse-explorer » uniquement."
  docker compose down -v
fi

echo "== 3/4 Démarrage de Langfuse (premier lancement : 2 à 4 min, migrations ClickHouse)"
docker compose up -d
PORT=$(grep -E '^EXPLORER_PORT=' .env | cut -d= -f2); PORT=${PORT:-3100}
for i in $(seq 1 90); do
  if curl -fs "http://localhost:${PORT}/api/public/health" >/dev/null 2>&1; then echo "✅ Langfuse répond."; break; fi
  [ "$i" = 90 ] && { echo "❌ Langfuse ne répond pas après 6 min : docker compose logs langfuse-web"; exit 1; }
  printf "."; sleep 4
done

if [ "$SEED" = 1 ]; then
  echo "== 4/4 Injection des données de démonstration"
  uv run --project seed seed/seed_langfuse.py
fi

PASS=$(grep -E '^LANGFUSE_INIT_USER_PASSWORD=' .env | cut -d= -f2)
cat <<EOF

────────────────────────────────────────────────────────────
  Langfuse Explorer est prêt
  UI          : http://localhost:${PORT}
  Login       : explorer@helpdesk.local
  Mot de passe: ${PASS}
  Guide       : ouvrir guide/index.html dans le navigateur
────────────────────────────────────────────────────────────
EOF
