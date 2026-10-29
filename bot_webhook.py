"""CoreGuard - version WEBHOOK.

Telegram envoie les mises à jour à ton serveur via HTTPS : il faut une URL
publique en HTTPS (Render, Railway, Fly.io, VPS + nginx, ngrok pour tester...).

Variables d'environnement :
  TELEGRAM_BOT_TOKEN  (obligatoire) token du bot
  WEBHOOK_URL         (obligatoire) URL publique de base, ex. https://monbot.onrender.com
  PORT                (optionnel)   port d'écoute, défaut 8080 (souvent fourni par l'hébergeur)
  WEBHOOK_PATH        (optionnel)   chemin du webhook, défaut "telegram"
  WEBHOOK_SECRET      (optionnel)   secret vérifié par Telegram (A-Z a-z 0-9 _ -),
                                    généré aléatoirement s'il est absent

Lancement :  python bot_webhook.py
"""

import os
import secrets

from coreguard_core import ALLOWED_UPDATES, build_application, setup_logging


def main() -> None:
    setup_logging()

    base_url = os.getenv("WEBHOOK_URL", "https://educorecoreguard-bot.fadehost.app").strip().rstrip("/")
    if not base_url.startswith("https://"):
        raise SystemExit("[CoreGuard] WEBHOOK_URL manquant ou invalide : il doit commencer par https://")

    port = int(os.getenv("PORT", "8080"))
    path = os.getenv("WEBHOOK_PATH", "telegram").strip().strip("/")
    secret = os.getenv("WEBHOOK_SECRET") or secrets.token_urlsafe(32)

    app = build_application()
    print(f"[CoreGuard] Démarré en mode webhook sur le port {port} -> {base_url}/{path}")
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=path,
        webhook_url=f"{base_url}/{path}",
        secret_token=secret,
        allowed_updates=ALLOWED_UPDATES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
