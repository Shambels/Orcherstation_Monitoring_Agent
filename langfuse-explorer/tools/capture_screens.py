"""
capture_screens.py : (re)génère les captures du guide depuis TON instance Langfuse du kit.

    uv run --project seed --group captures playwright install chromium   # une seule fois
    uv run --project seed --group captures tools/capture_screens.py

À relancer après le seed (attendre 1 min) et à chaque changement de version de Langfuse :
des captures faites à la main sont une dette technique qui expire à la prochaine mise à jour.
Les captures sont écrites dans guide/screens/<écran>.png ; le guide les affiche
automatiquement (sinon il montre un schéma).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "seed"))

from moteur import trace_id_de  # noqa: E402
from seed_langfuse import lire_env  # noqa: E402

PROJET = "explorer"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--visible", action="store_true", help="affiche le navigateur pendant la capture")
    ap.add_argument("--theme", choices=["light", "dark"], default="light")
    ap.add_argument("--seulement", nargs="*", help="identifiants d'écrans à capturer (ex. tracing trace)")
    args = ap.parse_args()

    env = lire_env(RACINE / ".env")
    base = env["LANGFUSE_BASE_URL"].rstrip("/")
    ecrans = yaml.safe_load((RACINE / "guide" / "source" / "ecrans.yaml").read_text(encoding="utf-8"))["ecrans"]
    sortie = RACINE / "guide" / "screens"
    sortie.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        nav = p.chromium.launch(headless=not args.visible)
        ctx = nav.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2,
                              color_scheme=args.theme, locale="en-US")
        page = ctx.new_page()

        # Connexion (formulaire en deux temps : e-mail, puis mot de passe)
        page.goto(f"{base}/auth/sign-in", wait_until="networkidle")
        page.fill('input[name="email"]', env["LANGFUSE_INIT_USER_EMAIL"])
        bouton = page.locator('[data-testid="submit-email-password-sign-in-form"]')
        if page.locator('input[name="password"]').count() == 0:
            bouton.click()
        page.fill('input[name="password"]', env["LANGFUSE_INIT_USER_PASSWORD"])
        bouton.click()
        page.wait_for_url(lambda u: "/auth/" not in u, timeout=30_000)
        print("✅ connecté")

        for ident, e in ecrans.items():
            if args.seulement and ident not in args.seulement:
                continue
            route = e["route"].replace("{trace_id}", trace_id_de(e["exemple"])) if e.get("exemple") else e["route"]
            url = f"{base}/project/{PROJET}/{route}".rstrip("/")
            url += ("&" if "?" in url else "?") + "dateRange=7d"
            try:
                page.goto(url, wait_until="networkidle", timeout=45_000)
            except Exception:  # noqa: BLE001  (certaines pages gardent des requêtes ouvertes)
                pass
            page.wait_for_timeout(2500)
            for texte in ("Got it", "Dismiss", "Close"):  # bandeaux d'accueil éventuels
                btn = page.get_by_role("button", name=texte)
                if btn.count():
                    try:
                        btn.first.click(timeout=1000)
                    except Exception:  # noqa: BLE001
                        pass
            fichier = sortie / f"{ident}.png"
            page.screenshot(path=str(fichier))
            print(f"📸 {ident:13} {url}")

        nav.close()
    print(f"\nCaptures écrites dans {sortie}. Ouvre guide/index.html pour vérifier.")


if __name__ == "__main__":
    main()
