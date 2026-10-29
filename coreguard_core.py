"""CoreGuard - logique commune de modération (FR / EN / RU).

Utilisé par bot_polling.py et bot_webhook.py.
Ne mets jamais le token du bot dans ce fichier : utilise la variable
d'environnement TELEGRAM_BOT_TOKEN.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import ChatPermissions, Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

log = logging.getLogger("coreguard")

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

MAX_WARNINGS = 3
MUTE_DURATION = timedelta(hours=1)
BAN_AFTER_MUTES = 3

DATA_FILE = Path(os.getenv("COREGUARD_DATA_FILE", Path(__file__).with_name("coreguard-data.json")))

# Mettre COREGUARD_EXEMPT_ADMINS=true pour ne jamais modérer les admins du groupe.
# Par défaut : tout le monde est modéré (Telegram empêche toutefois de mute/ban un admin).
EXEMPT_ADMINS = os.getenv("COREGUARD_EXEMPT_ADMINS", "false").strip().lower() in {"1", "true", "yes", "oui"}

ALLOWED_UPDATES = ["message", "edited_message"]

BAD_WORDS = [
    "pute", "putain", "merde", "connard", "con", "fdp", "ntm", "salope",
    "enculé", "encule", "batard", "bouffon", "clochard", "pd", "tg",
    "fuck", "shit", "bitch", "asshole", "motherfucker", "bastard",
    "блядь", "блять", "сука", "хуй", "пизда", "ебать", "ебал", "мудак",
    "пидор", "пидр", "долбоёб", "ёб", "нахуй", "пиздец", "ебаный",
    "хуйло", "гандон", "говно", "заебал", "заебись", "ебанутый",
    "хуесос", "пиздос", "блядина", "сучка", "мразь", "тварь",
    "blyat", "blyad", "suka", "hui", "pizda", "ebat", "ebal", "mudak",
    "pidor", "pidr", "dolboeb", "nahui", "nahuy", "pizdec", "ebanuy",
    "huilo", "gandon", "govno", "zaebal", "ebanutiy", "huesos",
    "blyadina", "suchka", "mraz", "tvar",
]

_BAD_PATTERN = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(w) for w in sorted(BAD_WORDS, key=len, reverse=True))
    + r")(?!\w)",
    re.IGNORECASE,
)

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().translate(_LEET)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def contains_bad_word(text: str) -> bool:
    return bool(_BAD_PATTERN.search(normalize(text)))


# --------------------------------------------------------------------------- #
# Stockage JSON (avertissements / mutes)
# --------------------------------------------------------------------------- #

class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._data: dict = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.error("Fichier de données illisible, démarrage à vide : %s", exc)
            return {}

    @staticmethod
    def _key(chat_id: int, user_id: int) -> str:
        return f"{chat_id}:{user_id}"

    def get(self, chat_id: int, user_id: int) -> tuple[int, int]:
        row = self._data.get(self._key(chat_id, user_id), {})
        warnings = row.get("warnings", 0)
        mutes = row.get("mutes", 0)
        return (warnings if isinstance(warnings, int) else 0, mutes if isinstance(mutes, int) else 0)

    async def set(self, chat_id: int, user_id: int, warnings: int, mutes: int) -> None:
        async with self._lock:
            self._data[self._key(chat_id, user_id)] = {
                "warnings": warnings,
                "mutes": mutes,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }
            snapshot = json.dumps(self._data, ensure_ascii=False, indent=2)
            await asyncio.to_thread(self._write, snapshot)

    def _write(self, content: str) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.error("Impossible de sauvegarder les données : %s", exc)


store = Store(DATA_FILE)


# --------------------------------------------------------------------------- #
# Modération
# --------------------------------------------------------------------------- #

async def is_chat_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
    except TelegramError as exc:
        log.warning("Statut du membre introuvable : %s", exc)
        return False
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


async def moderate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not message.text or not user or not chat:
        return
    if chat.type not in ("group", "supergroup") or user.is_bot:
        return
    if not contains_bad_word(message.text):
        return
    if EXEMPT_ADMINS and await is_chat_admin(context, chat.id, user.id):
        return

    try:
        await message.delete()
        log.info("Message supprimé (user %s, chat %s)", user.id, chat.id)
    except TelegramError as exc:
        log.error("Suppression impossible, vérifie les droits admin : %s", exc)
        return  # pas d'avertissement si le message n'a pas pu être supprimé

    mention = user.mention_html()
    warnings, mutes = store.get(chat.id, user.id)
    warnings += 1

    if warnings < MAX_WARNINGS:
        await store.set(chat.id, user.id, warnings, mutes)
        await chat.send_message(
            f"⚠️ {mention}, message inapproprié supprimé.\nAvertissement <b>{warnings}/{MAX_WARNINGS}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    await chat.send_message(
        f"⚠️ {mention}, message inapproprié supprimé.\n"
        f"Avertissement <b>{warnings}/{MAX_WARNINGS}</b>.\n🔇 Mise en sourdine en cours…",
        parse_mode=ParseMode.HTML,
    )

    try:
        await context.bot.restrict_chat_member(
            chat.id,
            user.id,
            permissions=ChatPermissions.no_permissions(),
            until_date=datetime.now(timezone.utc) + MUTE_DURATION,
            use_independent_chat_permissions=True,
        )
        mutes += 1
        warnings = 0

        if mutes >= BAN_AFTER_MUTES:
            await context.bot.ban_chat_member(chat.id, user.id)
            await store.set(chat.id, user.id, 0, 0)
            await chat.send_message(
                f"🚫 {mention} a été banni après plusieurs infractions.", parse_mode=ParseMode.HTML
            )
        else:
            await store.set(chat.id, user.id, warnings, mutes)
            await chat.send_message(
                f"🔇 {mention} mis en sourdine pendant 1 heure (mute {mutes}/{BAN_AFTER_MUTES}).",
                parse_mode=ParseMode.HTML,
            )
    except TelegramError as exc:
        log.error("Mute/ban impossible : %s", exc)
        await store.set(chat.id, user.id, warnings, mutes)  # on garde l'infraction
        await chat.send_message(
            f"❌ Impossible de restreindre {mention}. Vérifie que CoreGuard est administrateur "
            "avec les droits de restriction et de bannissement.\n"
            "(Telegram interdit de restreindre ou bannir un administrateur ou le créateur du groupe.)",
            parse_mode=ParseMode.HTML,
        )


# --------------------------------------------------------------------------- #
# Commandes
# --------------------------------------------------------------------------- #

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🛡️ CoreGuard est actif.\n"
        "Ajoute-moi comme administrateur avec les droits « Supprimer les messages », "
        "« Restreindre les membres » et « Bannir les membres »."
    )


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user:
        await update.effective_message.reply_text(
            f"Ton user_id : <code>{update.effective_user.id}</code>", parse_mode=ParseMode.HTML
        )


async def cmd_resetwarn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("Réponds au message d'un utilisateur avec /resetwarn.")
        return
    if not await is_chat_admin(context, chat.id, user.id):
        return
    await store.set(chat.id, message.reply_to_message.from_user.id, 0, 0)
    await message.reply_text("✅ Avertissements et sourdines réinitialisés.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Erreur non gérée", exc_info=context.error)


# --------------------------------------------------------------------------- #
# Construction de l'application
# --------------------------------------------------------------------------- #

def get_token() -> str:
    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
        or os.getenv("TOKEN")
    )
    if not token:
        raise SystemExit("[CoreGuard] Token manquant : définis TELEGRAM_BOT_TOKEN dans les variables d'environnement.")
    return token


def setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )
    # Évite que le token apparaisse dans les logs des requêtes HTTP.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_application() -> Application:
    app = ApplicationBuilder().token(get_token()).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("resetwarn", cmd_resetwarn))
    # group=1 : les commandes (group 0) restent prioritaires.
    # Les messages édités sont aussi couverts par MessageHandler.
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, moderate), group=1)
    app.add_error_handler(on_error)
    return app
