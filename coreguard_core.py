"""CoreGuard - logique commune de modération (FR / EN / RU) avec contrôle d'accès.

Utilisé par bot_polling.py et bot_webhook.py.
Ne mets jamais le token du bot dans ce fichier : utilise la variable
d'environnement TELEGRAM_BOT_TOKEN.

Contrôle d'accès :
  - Le propriétaire est défini par OWNER_ID (id Telegram numérique) ou, à défaut,
    par un premier `/login <ADMIN_PASSWORD>` envoyé au bot en message privé.
  - Les autres personnes demandent l'accès en privé ; le propriétaire accepte ou refuse.
  - Seules les personnes autorisées peuvent ajouter le bot à un groupe.
  - Le propriétaire peut mettre le bot en maintenance (/off, /on ou via /admin).
"""

from __future__ import annotations

import asyncio
import hmac
import html
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
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


def _int_env(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    return int(value) if value.isdigit() else None


OWNER_ID_ENV = _int_env("OWNER_ID")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

ALLOWED_UPDATES = ["message", "edited_message", "callback_query", "my_chat_member"]

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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Stockage JSON (avertissements / mutes / accès)
# --------------------------------------------------------------------------- #

META_KEY = "__access__"


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

    # --- avertissements ---------------------------------------------------- #

    def get(self, chat_id: int, user_id: int) -> tuple[int, int]:
        row = self._data.get(self._key(chat_id, user_id), {})
        warnings = row.get("warnings", 0)
        mutes = row.get("mutes", 0)
        return (warnings if isinstance(warnings, int) else 0, mutes if isinstance(mutes, int) else 0)

    async def set(self, chat_id: int, user_id: int, warnings: int, mutes: int) -> None:
        self._data[self._key(chat_id, user_id)] = {
            "warnings": warnings,
            "mutes": mutes,
            "updatedAt": now_iso(),
        }
        await self.save()

    # --- contrôle d'accès -------------------------------------------------- #

    def meta(self) -> dict:
        meta = self._data.get(META_KEY)
        if not isinstance(meta, dict):
            meta = {}
            self._data[META_KEY] = meta
        meta.setdefault("owner_id", None)
        meta.setdefault("maintenance", False)
        for key in ("users", "groups", "pending"):
            if not isinstance(meta.get(key), dict):
                meta[key] = {}
        return meta

    # --- écriture ---------------------------------------------------------- #

    async def save(self) -> None:
        async with self._lock:
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
# Contrôle d'accès : propriétaire, utilisateurs autorisés, groupes, maintenance
# --------------------------------------------------------------------------- #

def owner_id() -> int | None:
    if OWNER_ID_ENV is not None:
        return OWNER_ID_ENV
    stored = store.meta().get("owner_id")
    return stored if isinstance(stored, int) else None


def is_owner(user_id: int) -> bool:
    oid = owner_id()
    return oid is not None and int(user_id) == oid


def is_approved(user_id: int) -> bool:
    return is_owner(user_id) or str(user_id) in store.meta()["users"]


def is_group_enabled(chat_id: int) -> bool:
    return str(chat_id) in store.meta()["groups"]


def in_maintenance() -> bool:
    return bool(store.meta()["maintenance"])


async def register_group(chat, user) -> None:
    store.meta()["groups"][str(chat.id)] = {
        "title": chat.title or "",
        "addedBy": user.id,
        "at": now_iso(),
    }
    await store.save()


def short(text: str, limit: int = 28) -> str:
    text = (text or "").strip() or "?"
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
    # Maintenance, ou groupe non autorisé par le propriétaire : on ne fait rien.
    if in_maintenance() or not is_group_enabled(chat.id):
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
# Commandes générales
# --------------------------------------------------------------------------- #

REQUEST_KEYBOARD = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Demander l'accès", callback_data="req")]])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return

    if chat.type != ChatType.PRIVATE:
        await message.reply_text(
            "🛡️ CoreGuard.\nPour activer la modération dans ce groupe : ajoute-moi comme administrateur "
            "(« Supprimer les messages », « Restreindre les membres », « Bannir les membres »), "
            "puis un utilisateur autorisé envoie /activer."
        )
        return

    if is_approved(user.id):
        text = (
            "🛡️ <b>CoreGuard</b> — accès autorisé ✅\n\n"
            "1. Ajoute-moi à ton groupe.\n"
            "2. Donne-moi les droits admin : « Supprimer les messages », « Restreindre les membres » "
            "et « Bannir les membres ».\n"
            "3. Si le groupe n'est pas activé automatiquement, envoie /activer dans le groupe."
        )
        if is_owner(user.id):
            text += "\n\n👑 Tu es le propriétaire : /admin pour ouvrir le panneau."
        await message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    await message.reply_text(
        "🛡️ <b>CoreGuard</b>\n\nCe bot est privé. Pour l'utiliser dans tes groupes, "
        "demande l'accès : le propriétaire doit l'accepter.",
        parse_mode=ParseMode.HTML,
        reply_markup=REQUEST_KEYBOARD,
    )


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user:
        await update.effective_message.reply_text(
            f"Ton user_id : <code>{update.effective_user.id}</code>", parse_mode=ParseMode.HTML
        )


async def cmd_resetwarn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("Réponds au message d'un utilisateur avec /resetwarn.")
        return
    if not is_owner(user.id) and not await is_chat_admin(context, chat.id, user.id):
        return
    await store.set(chat.id, message.reply_to_message.from_user.id, 0, 0)
    await message.reply_text("✅ Avertissements et sourdines réinitialisés.")


# --------------------------------------------------------------------------- #
# Groupes : activation / désactivation
# --------------------------------------------------------------------------- #

async def cmd_activer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if not is_approved(user.id):
        await message.reply_text("⛔ Seules les personnes autorisées peuvent activer CoreGuard. Demande l'accès au bot en privé.")
        return
    if not is_owner(user.id) and not await is_chat_admin(context, chat.id, user.id):
        await message.reply_text("⛔ Il faut être administrateur de ce groupe.")
        return
    await register_group(chat, user)
    await message.reply_text("✅ CoreGuard est activé dans ce groupe. Vérifie que j'ai bien les droits d'administrateur.")


async def cmd_desactiver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    info = store.meta()["groups"].get(str(chat.id))
    if not info:
        await message.reply_text("CoreGuard n'est pas activé dans ce groupe.")
        return
    if not is_owner(user.id) and info.get("addedBy") != user.id:
        return
    store.meta()["groups"].pop(str(chat.id), None)
    await store.save()
    await message.reply_text("🔕 CoreGuard est désactivé dans ce groupe.")


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    event = update.my_chat_member
    if not event or event.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    chat = event.chat
    old, new = event.old_chat_member.status, event.new_chat_member.status

    # Le bot a été retiré du groupe.
    if new in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
        if store.meta()["groups"].pop(str(chat.id), None) is not None:
            await store.save()
        return

    joined = old in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED) and new in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.RESTRICTED,
    )
    if not joined or is_group_enabled(chat.id):
        return

    adder = event.from_user
    if adder and is_approved(adder.id):
        await register_group(chat, adder)
        try:
            await chat.send_message(
                "✅ CoreGuard est activé dans ce groupe.\n"
                "Donne-moi les droits administrateur : « Supprimer les messages », "
                "« Restreindre les membres » et « Bannir les membres »."
            )
        except TelegramError as exc:
            log.warning("Message d'activation non envoyé : %s", exc)
        oid = owner_id()
        if oid is not None and not is_owner(adder.id):
            try:
                await context.bot.send_message(
                    oid,
                    f"ℹ️ CoreGuard a été ajouté au groupe « {html.escape(chat.title or '?')} » "
                    f"par {html.escape(adder.full_name)}.",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass
        return

    # Ajouté par quelqu'un qui n'est pas autorisé : on prévient puis on quitte.
    try:
        await chat.send_message(
            "⛔ CoreGuard est un bot privé. Demande l'accès en m'écrivant en message privé "
            f"(@{context.bot.username}), puis ajoute-moi à nouveau."
        )
    except TelegramError:
        pass
    try:
        await chat.leave()
        log.info("Groupe %s quitté : ajout par un utilisateur non autorisé.", chat.id)
    except TelegramError as exc:
        log.warning("Impossible de quitter le groupe %s : %s", chat.id, exc)


# --------------------------------------------------------------------------- #
# Connexion du propriétaire et maintenance
# --------------------------------------------------------------------------- #

_login_attempts: dict[int, int] = {}
MAX_LOGIN_ATTEMPTS = 5


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or chat.type != ChatType.PRIVATE:
        return

    try:
        await message.delete()  # le mot de passe ne doit pas rester dans le chat
    except TelegramError:
        pass

    if owner_id() is not None:
        text = "👑 Tu es déjà connecté en tant que propriétaire." if is_owner(user.id) else "Un propriétaire est déjà défini."
        await chat.send_message(text)
        return
    if not ADMIN_PASSWORD:
        await chat.send_message("Aucun mot de passe n'est configuré (variable ADMIN_PASSWORD) ni OWNER_ID.")
        return
    if _login_attempts.get(user.id, 0) >= MAX_LOGIN_ATTEMPTS:
        await chat.send_message("⛔ Trop de tentatives. Redémarre le bot pour réessayer.")
        return
    if not context.args:
        await chat.send_message("Utilisation : /login ton-mot-de-passe")
        return

    if hmac.compare_digest(" ".join(context.args).encode(), ADMIN_PASSWORD.encode()):
        store.meta()["owner_id"] = user.id
        await store.save()
        _login_attempts.pop(user.id, None)
        log.info("Propriétaire défini : %s", user.id)
        await chat.send_message("✅ Connecté. Tu es le propriétaire de CoreGuard. Utilise /admin.")
    else:
        _login_attempts[user.id] = _login_attempts.get(user.id, 0) + 1
        await chat.send_message("❌ Mot de passe incorrect.")


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    if not user or not message or not is_owner(user.id):
        return
    store.meta()["maintenance"] = True
    await store.save()
    await message.reply_text("🔴 Maintenance activée : CoreGuard ne modère plus. /on pour réactiver.")


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, message = update.effective_user, update.effective_message
    if not user or not message or not is_owner(user.id):
        return
    store.meta()["maintenance"] = False
    await store.save()
    await message.reply_text("🟢 CoreGuard est de nouveau actif.")


# --------------------------------------------------------------------------- #
# Panneau du propriétaire (/admin)
# --------------------------------------------------------------------------- #

BACK_BUTTON = [InlineKeyboardButton("⬅️ Retour", callback_data="panel")]


def panel_view() -> tuple[str, InlineKeyboardMarkup]:
    meta = store.meta()
    state = "🔴 En maintenance" if meta["maintenance"] else "🟢 Actif"
    text = (
        "🛡️ <b>Panneau CoreGuard</b>\n\n"
        f"État : {state}\n"
        f"Utilisateurs autorisés : {len(meta['users'])}\n"
        f"Groupes actifs : {len(meta['groups'])}\n"
        f"Demandes en attente : {len(meta['pending'])}"
    )
    rows = [
        [InlineKeyboardButton("🟢 Réactiver" if meta["maintenance"] else "🔴 Mettre en maintenance", callback_data="toggle")],
        [
            InlineKeyboardButton("👥 Utilisateurs", callback_data="users"),
            InlineKeyboardButton("💬 Groupes", callback_data="groups"),
        ],
    ]
    if meta["pending"]:
        rows.append([InlineKeyboardButton(f"📨 Demandes ({len(meta['pending'])})", callback_data="pending")])
    return text, InlineKeyboardMarkup(rows)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    if chat.type != ChatType.PRIVATE:
        await message.reply_text("Utilise /admin en message privé avec moi.")
        return
    if not is_owner(user.id):
        return
    text, markup = panel_view()
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def edit_view(query, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def handle_access_request(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = query.from_user
    meta = store.meta()

    if is_approved(user.id):
        await query.answer("Tu as déjà accès.", show_alert=True)
        return
    oid = owner_id()
    if oid is None:
        await query.answer("Le propriétaire du bot n'est pas encore configuré.", show_alert=True)
        return
    if str(user.id) in meta["pending"]:
        await query.answer("Ta demande est déjà envoyée, patiente.", show_alert=True)
        return

    meta["pending"][str(user.id)] = {
        "name": user.full_name,
        "username": user.username or "",
        "at": now_iso(),
    }
    await store.save()
    await query.answer("Demande envoyée ✅")
    try:
        await query.edit_message_text("⏳ Demande envoyée. Tu seras prévenu dès que le propriétaire aura répondu.")
    except TelegramError:
        pass

    username = f"@{html.escape(user.username)}" if user.username else "—"
    await context.bot.send_message(
        oid,
        "🔑 <b>Nouvelle demande d'accès</b>\n\n"
        f"Nom : {html.escape(user.full_name)}\n"
        f"Pseudo : {username}\n"
        f"ID : <code>{user.id}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("✅ Accepter", callback_data=f"ok:{user.id}"),
                InlineKeyboardButton("❌ Refuser", callback_data=f"no:{user.id}"),
            ]]
        ),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user:
        return
    data = query.data or ""

    if data == "req":
        await handle_access_request(query, context)
        return

    # Tout le reste est réservé au propriétaire.
    if not is_owner(query.from_user.id):
        await query.answer("Réservé au propriétaire.", show_alert=True)
        return
    await query.answer()
    meta = store.meta()

    if data == "panel":
        text, markup = panel_view()
        await edit_view(query, text, markup)

    elif data == "toggle":
        meta["maintenance"] = not meta["maintenance"]
        await store.save()
        text, markup = panel_view()
        await edit_view(query, text, markup)

    elif data == "users":
        rows = [
            [InlineKeyboardButton(f"❌ {short(info.get('name', uid))}", callback_data=f"rm:{uid}")]
            for uid, info in meta["users"].items()
        ]
        rows.append(BACK_BUTTON)
        text = "👥 <b>Utilisateurs autorisés</b>\nAppuie sur un nom pour retirer son accès."
        if not meta["users"]:
            text = "👥 Aucun utilisateur autorisé pour le moment."
        await edit_view(query, text, InlineKeyboardMarkup(rows))

    elif data == "groups":
        rows = [
            [InlineKeyboardButton(f"🚪 {short(info.get('title') or cid)}", callback_data=f"lv:{cid}")]
            for cid, info in meta["groups"].items()
        ]
        rows.append(BACK_BUTTON)
        text = "💬 <b>Groupes actifs</b>\nAppuie sur un groupe pour que CoreGuard le quitte."
        if not meta["groups"]:
            text = "💬 Aucun groupe actif pour le moment."
        await edit_view(query, text, InlineKeyboardMarkup(rows))

    elif data == "pending":
        rows = []
        for uid, info in meta["pending"].items():
            name = short(info.get("name", uid), 18)
            rows.append([
                InlineKeyboardButton(f"✅ {name}", callback_data=f"ok:{uid}"),
                InlineKeyboardButton("❌", callback_data=f"no:{uid}"),
            ])
        rows.append(BACK_BUTTON)
        await edit_view(query, "📨 <b>Demandes en attente</b>", InlineKeyboardMarkup(rows))

    elif data.startswith("ok:") or data.startswith("no:"):
        action, uid = data.split(":", 1)
        if not uid.isdigit():
            return
        info = meta["pending"].pop(uid, None)
        name = (info or {}).get("name") or uid
        if action == "ok":
            meta["users"][uid] = {"name": name, "at": now_iso()}
        await store.save()
        verdict = f"✅ {html.escape(name)} est maintenant autorisé." if action == "ok" else f"❌ Demande de {html.escape(name)} refusée."
        await edit_view(query, verdict, InlineKeyboardMarkup([BACK_BUTTON]))
        try:
            await context.bot.send_message(
                int(uid),
                "✅ Ton accès à CoreGuard a été accepté ! Envoie /start pour voir comment l'utiliser."
                if action == "ok"
                else "❌ Ta demande d'accès à CoreGuard a été refusée.",
            )
        except TelegramError as exc:
            log.warning("Utilisateur %s non prévenu : %s", uid, exc)

    elif data.startswith("rm:"):
        uid = data.split(":", 1)[1]
        meta["users"].pop(uid, None)
        await store.save()
        text, markup = panel_view()
        await edit_view(query, "🗑️ Accès retiré.\n\n" + text, markup)

    elif data.startswith("lv:"):
        cid = data.split(":", 1)[1]
        if not cid.lstrip("-").isdigit():
            return
        meta["groups"].pop(cid, None)
        await store.save()
        try:
            await context.bot.send_message(int(cid), "👋 CoreGuard quitte ce groupe (accès retiré par le propriétaire).")
            await context.bot.leave_chat(int(cid))
        except TelegramError as exc:
            log.warning("Impossible de quitter le groupe %s : %s", cid, exc)
        text, markup = panel_view()
        await edit_view(query, "🚪 Groupe quitté.\n\n" + text, markup)


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
    if owner_id() is None:
        if ADMIN_PASSWORD:
            log.warning("Aucun propriétaire défini : envoie /login <ADMIN_PASSWORD> au bot en message privé.")
        else:
            log.warning("Aucun propriétaire défini : configure OWNER_ID (ton id Telegram) ou ADMIN_PASSWORD.")
    else:
        log.info("Propriétaire : %s | utilisateurs autorisés : %d | groupes actifs : %d",
                 owner_id(), len(store.meta()["users"]), len(store.meta()["groups"]))

    app = ApplicationBuilder().token(get_token()).build()

    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("resetwarn", cmd_resetwarn))
    app.add_handler(CommandHandler("activer", cmd_activer))
    app.add_handler(CommandHandler("desactiver", cmd_desactiver))
    app.add_handler(CommandHandler("login", cmd_login, filters=private))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("off", cmd_off))
    app.add_handler(CommandHandler("on", cmd_on))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    # group=1 : les commandes (group 0) restent prioritaires.
    # Les messages édités sont aussi couverts par MessageHandler.
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, moderate), group=1)
    app.add_error_handler(on_error)
    return app
