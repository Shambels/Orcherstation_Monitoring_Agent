"""
generate_env.py : crée le .env du kit Langfuse Explorer (bibliothèque standard uniquement).

Chaque poste reçoit des secrets ALÉATOIRES et des clés API Langfuse pré-provisionnées
(LANGFUSE_INIT_*) : au premier démarrage, Langfuse crée l'organisation, le projet,
le compte et les clés. Aucun clic, aucun secret partagé dans l'archive distribuée.

Usage :
    python generate_env.py           # crée .env (refuse d'écraser un .env existant)
    python generate_env.py --force   # régénère : à faire AVANT le premier démarrage,
                                     # ou après `docker compose down -v` (sinon Postgres
                                     # garde l'ancien mot de passe)
"""

import argparse
import secrets
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"


def hx(n: int = 16) -> str:
    return secrets.token_hex(n)


def build() -> tuple[str, str]:
    pg = hx()
    password = secrets.token_urlsafe(12)
    public_key = f"pk-lf-explorer-{hx(10)}"
    secret_key = f"sk-lf-explorer-{hx(16)}"
    lines = [
        "# Généré par generate_env.py : NE PAS DISTRIBUER (secrets propres à ce poste)",
        "",
        "# ----- Version figée : doit correspondre au guide (guide/index.html, pied de page) -----",
        "LANGFUSE_VERSION=4.46.0",
        "EXPLORER_PORT=3100",
        "",
        "# ----- Accès à l'UI -----",
        "LANGFUSE_INIT_USER_EMAIL=explorer@helpdesk.local",
        f"LANGFUSE_INIT_USER_PASSWORD={password}",
        "",
        "# ----- SDK / seed (mêmes clés que le projet pré-provisionné) -----",
        "LANGFUSE_BASE_URL=http://localhost:3100",
        f"LANGFUSE_PUBLIC_KEY={public_key}",
        f"LANGFUSE_SECRET_KEY={secret_key}",
        "",
        "# ----- Secrets internes -----",
        f"NEXTAUTH_SECRET={hx(32)}",
        f"SALT={hx(16)}",
        f"ENCRYPTION_KEY={hx(32)}",
        "TELEMETRY_ENABLED=false",
        f"POSTGRES_PASSWORD={pg}",
        f"DATABASE_URL=postgresql://postgres:{pg}@postgres:5432/postgres",
        f"CLICKHOUSE_PASSWORD={hx()}",
        f"REDIS_AUTH={hx()}",
        f"MINIO_ROOT_PASSWORD={hx()}",
        "",
    ]
    return "\n".join(lines), password


def main() -> None:
    p = argparse.ArgumentParser(description="Génère le .env du kit Langfuse Explorer")
    p.add_argument("--force", action="store_true", help="écrase un .env existant")
    args = p.parse_args()
    if ENV_PATH.exists() and not args.force:
        print(f"ℹ️  {ENV_PATH.name} existe déjà : conservé.")
        return
    content, password = build()
    # newline="\n" : un .env en CRLF casse la lecture des valeurs côté Docker/bash
    ENV_PATH.write_text(content, encoding="utf-8", newline="\n")
    print(f"✅ {ENV_PATH.name} créé.")
    print("   UI      : http://localhost:3100")
    print("   Login   : explorer@helpdesk.local")
    print(f"   Mot de passe : {password}   (aussi dans .env)")


if __name__ == "__main__":
    main()
