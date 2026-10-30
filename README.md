# 🛡️ CoreGuard

**Bot Telegram de modération pour groupes** — supprime les insultes, avertit, puis met en sourdine ou bannit.

> 🚧 **Projet en cours de développement.** CoreGuard est utilisable, mais il évolue chaque jour : des bugs sont possibles et certaines fonctions manquent encore. Merci de ta patience et de tes retours !

🇷🇺 *Версия на русском — ниже.*

---

## 👋 À qui s'adresse ce projet ?

Si tu administres un groupe Telegram (communauté, classe, projet) et que tu veux garder les discussions respectueuses sans surveiller chaque message, CoreGuard est fait pour toi. C'est aussi un projet d'apprentissage : le code est volontairement simple à lire.

Bot de démonstration : **@EducoreCoreGuard_bot** (accès sur demande, voir plus bas).

---

## ✅ Ce que CoreGuard peut faire

**Modération automatique**
- Supprime les messages contenant des insultes en **français, anglais et russe** (y compris en lettres latines pour le russe).
- Reconnaît quelques astuces courantes (`m3rd3`, majuscules…) et vérifie aussi les **messages modifiés**.
- Système d'avertissements : **3 avertissements → mute d'1 heure → ban après 3 mutes**.
- `/resetwarn` (en réponse à un message) remet les compteurs d'un membre à zéro.

**Contrôle d'accès (le bot est privé)**
- Personne n'utilise le bot sans l'accord du propriétaire : on appuie sur **« 🔑 Demander l'accès »** en message privé, le propriétaire accepte ou refuse.
- Si une personne non autorisée ajoute le bot à un groupe, il prévient puis **quitte le groupe**.
- **Panneau `/admin`** (en privé) : demandes, utilisateurs, groupes, co-admins.
- **Co-admins** : des personnes de confiance peuvent gérer les demandes d'accès, sans pouvoir retirer le rôle de propriétaire, couper le bot ou gérer les groupes.
- **Mode maintenance** (`/off`, `/on` ou bouton du panneau) : la modération s'arrête et **tous les groupes sont prévenus**.

**Deux modes de fonctionnement**
- **Polling** : le bot interroge Telegram, aucune adresse publique nécessaire.
- **Webhook** : Telegram envoie les messages à ton serveur (adresse HTTPS requise).

---

## ⚠️ Contraintes et limites connues

Pour éviter les mauvaises surprises :

- **Droits administrateur obligatoires** : dans chaque groupe, le bot doit être administrateur avec « Supprimer les messages », « Restreindre les membres » et « Bannir les membres ».
- **Les administrateurs et le créateur d'un groupe ne peuvent pas être mute ni bannis** : c'est une règle de Telegram. Leurs messages sont supprimés et les avertissements s'accumulent, mais le bot affiche une erreur au lieu de les restreindre.
- **Liste de mots fixe** : elle est écrite dans le code (`BAD_WORDS`) et ne se modifie pas encore par commande.
- **Détection simple** : seuls les mots entiers sont repérés. Une insulte déguisée (lettres espacées, fautes volontaires, etc.) peut passer, et certains mots courts (`con`, `pd`, `tg`) peuvent déclencher à tort.
- **Texte uniquement** : les images, stickers et légendes de photos ne sont pas analysés.
- **Réglages dans le code** : nombre d'avertissements, durée du mute et seuil de ban se changent dans `coreguard_core.py`.
- **Stockage dans un simple fichier** (`coreguard-data.json`) : selon l'hébergeur, il peut être **effacé à chaque redéploiement** (avertissements, utilisateurs autorisés, groupes actifs et co-admins repartent alors de zéro). Le propriétaire reste reconnu s'il est défini par `OWNER_ID`.
- **Une seule instance par token** : ne lance jamais le polling et le webhook en même temps avec le même token.
- **Le bot répond uniquement aux commandes en privé**, pas aux messages libres.
- **Interface en français** pour l'instant.

---

## 🚀 Installation rapide

**1. Créer le bot** avec [@BotFather](https://t.me/BotFather) (`/newbot`) et récupérer le token.

**2. Installer**
```bash
pip install -r requirements.txt
```

**3. Configurer les variables d'environnement**

| Variable | Rôle |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token du bot (**obligatoire**) |
| `OWNER_ID` | Ton identifiant Telegram numérique (obtenu avec `/myid`) : tu deviens propriétaire |
| `ADMIN_PASSWORD` | Alternative à `OWNER_ID` : `/login <mot de passe>` en privé |
| `WEBHOOK_URL` | Adresse publique HTTPS (mode webhook uniquement) |
| `PORT` | Port d'écoute (souvent fourni par l'hébergeur) |
| `COREGUARD_EXEMPT_ADMINS` | `true` pour ne jamais modérer les admins des groupes |
| `COREGUARD_DATA_FILE` | Chemin du fichier de données |

**4. Lancer**
```bash
python bot_polling.py   # mode polling
python bot_webhook.py   # mode webhook
```

**5. Dans Telegram** : ouvre le bot en privé, envoie `/admin`, puis ajoute-le à ton groupe avec les droits administrateur (ou envoie `/activer` dans le groupe).

---

## 🔒 Sécurité

- **Ne publie jamais ton token** dans le code ni sur GitHub : utilise les variables d'environnement. Si tu l'as exposé, révoque-le avec `/revoke` dans @BotFather.
- Ne mets pas `coreguard-data.json` sur GitHub : il contient des identifiants d'utilisateurs.

---

## 🗺️ Prévu pour la suite

- Liste de mots modifiable par commande, sans toucher au code.
- Stockage durable qui survit aux redéploiements.
- Réglages (avertissements, durée du mute) configurables depuis le panneau.
- Réponse automatique aux messages privés.
- Idées de la communauté bienvenues !

---

## 💬 Retours et bugs

Un bug, une idée, une question ? Ouvre une **Issue** sur ce dépôt. Comme le projet est en développement, tes retours aident vraiment à l'améliorer.

---

# 🇷🇺 CoreGuard — на русском

**Telegram-бот для модерации групп**: удаляет оскорбления, выдаёт предупреждения, затем отключает возможность писать или банит.

> 🚧 **Проект в разработке.** Бот уже работает, но постоянно развивается: возможны ошибки, а некоторые функции ещё не готовы. Спасибо за терпение и обратную связь!

## ✅ Возможности
- Автоматически удаляет сообщения с оскорблениями на **французском, английском и русском** (в том числе русские слова латиницей), проверяет и **изменённые** сообщения.
- Система предупреждений: **3 предупреждения → мут на 1 час → бан после 3 мутов**. Команда `/resetwarn` (в ответ на сообщение) сбрасывает счётчик.
- **Закрытый бот**: доступ по запросу. Нужно нажать «🔑 Запросить доступ» в личных сообщениях, владелец принимает или отклоняет запрос. Если бота добавит в группу неодобренный пользователь, бот сам покинет группу.
- **Панель управления `/admin`** (в личных сообщениях): запросы, пользователи, группы, со-администраторы.
- **Со-администраторы**: доверенные люди могут обрабатывать запросы на доступ, но не могут отключить бота, управлять группами или лишить владельца прав.
- **Режим обслуживания** (`/off`, `/on` или кнопка в панели): модерация отключается, а **все группы получают уведомление**.
- Два режима работы: **polling** и **webhook**.

## ⚠️ Ограничения
- Боту нужны **права администратора** в группе: удаление сообщений, ограничение и блокировка участников.
- **Администраторов и создателя группы нельзя замутить или забанить** — это правило Telegram. Их сообщения удаляются, но бот показывает ошибку.
- Список слов задан в коде и пока не редактируется командой. Обнаруживаются только целые слова; завуалированные оскорбления могут пройти, а короткие слова (`con`, `pd`, `tg`) иногда срабатывают ошибочно.
- Анализируется только текст (не изображения и не стикеры).
- Данные хранятся в файле `coreguard-data.json`, который хостинг может **стирать при каждом деплое**. Владелец остаётся, если задан через `OWNER_ID`.
- Нельзя запускать polling и webhook одновременно с одним токеном.
- Бот отвечает в личных сообщениях только на команды. Интерфейс пока на французском.

## 🔒 Безопасность
**Никогда не публикуйте токен бота** в коде или на GitHub — используйте переменные окружения. Если токен попал в открытый доступ, отзовите его командой `/revoke` в @BotFather.

## 💬 Обратная связь
Нашли ошибку или есть идея? Создайте **Issue** в этом репозитории — проект в разработке, и ваши отзывы очень помогают.
