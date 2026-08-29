import os
import re
import time
import sqlite3
import logging
from collections import defaultdict, deque

from telegram import (
    Update,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, RetryAfter, Forbidden, BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# SHIELD GUARD BOT
# COMPLETE WORKING VERSION
# python-telegram-bot
# =========================================================

TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
except (TypeError, ValueError):
    OWNER_ID = 0

OWNER_USERNAME = os.getenv(
    "OWNER_USERNAME",
    "YourOwnerUsername"
).strip().lstrip("@")

SUPPORT_USERNAME = os.getenv(
    "SUPPORT_USERNAME",
    "YourSupportUsername"
).strip().lstrip("@")

LOG_CHAT = os.getenv(
    "LOG_CHAT_ID",
    "-1003979103138"
).strip()

DB_PATH = os.getenv(
    "DB_PATH",
    "shieldbot.db"
).strip()

START_IMAGE = os.getenv(
    "START_IMAGE",
    "https://h.uguu.se/FekWWcsz.jpg"
).strip()

HELP_IMAGE = os.getenv(
    "HELP_IMAGE",
    "https://h.uguu.se/FekWWcsz.jpg"
).strip()


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("shield_guard")


# =========================================================
# DATABASE
# =========================================================

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False
)

db.execute("PRAGMA journal_mode=WAL")

db.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id INTEGER PRIMARY KEY,
    links INTEGER DEFAULT 1,
    flood INTEGER DEFAULT 1,
    max_warns INTEGER DEFAULT 3,
    edit_delete INTEGER DEFAULT 0,
    bio_link INTEGER DEFAULT 0,
    media_delete INTEGER DEFAULT 0,
    abuse INTEGER DEFAULT 1
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS warns (
    chat_id INTEGER,
    user_id INTEGER,
    count INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS local_auth (
    chat_id INTEGER,
    user_id INTEGER,
    PRIMARY KEY(chat_id, user_id)
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS global_auth (
    user_id INTEGER PRIMARY KEY
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS known_chats (
    chat_id INTEGER PRIMARY KEY,
    chat_type TEXT,
    title TEXT,
    username TEXT
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT
)
""")

db.commit()


# =========================================================
# DATABASE MIGRATION
# =========================================================

for column, definition in (
    ("edit_delete", "INTEGER DEFAULT 0"),
    ("bio_link", "INTEGER DEFAULT 0"),
    ("media_delete", "INTEGER DEFAULT 0"),
    ("abuse", "INTEGER DEFAULT 1"),
):

    try:
        db.execute(
            f"ALTER TABLE settings ADD COLUMN {column} {definition}"
        )
        db.commit()

    except sqlite3.OperationalError:
        pass


# =========================================================
# REGEX
# =========================================================

LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/|tg://)",
    re.IGNORECASE,
)

ABUSE_WORDS = {
    "fuck",
    "fucking",
    "bitch",
    "bastard",
    "asshole",
    "motherfucker",
    "madarchod",
    "mc",
    "bhosdike",
    "bhosdi",
    "chutiya",
    "harami",
    "gandu",
    "gaand",
    "randi",
}

# chat_id -> user_id -> deque(timestamps)
flood_cache = defaultdict(
    lambda: defaultdict(deque)
)


# =========================================================
# DATABASE HELPERS
# =========================================================

def save_chat(chat):
    if not chat:
        return

    try:
        db.execute(
            """
            INSERT OR REPLACE INTO known_chats
            (chat_id, chat_type, title, username)
            VALUES (?, ?, ?, ?)
            """,
            (
                chat.id,
                str(chat.type),
                chat.title or "",
                chat.username or "",
            ),
        )

        db.commit()

    except Exception as e:
        log.warning("save_chat error: %s", e)


def save_user(user):
    if not user:
        return

    try:
        db.execute(
            """
            INSERT OR REPLACE INTO users
            (user_id, username, first_name)
            VALUES (?, ?, ?)
            """,
            (
                user.id,
                user.username or "",
                user.first_name or "",
            ),
        )

        db.commit()

    except Exception as e:
        log.warning("save_user error: %s", e)


async def get_settings(chat_id):

    row = db.execute(
        """
        SELECT
            links,
            flood,
            max_warns,
            edit_delete,
            bio_link,
            media_delete,
            abuse
        FROM settings
        WHERE chat_id=?
        """,
        (chat_id,),
    ).fetchone()

    if row:
        return row

    db.execute(
        """
        INSERT OR IGNORE INTO settings
        (
            chat_id,
            links,
            flood,
            max_warns,
            edit_delete,
            bio_link,
            media_delete,
            abuse
        )
        VALUES (?, 1, 1, 3, 0, 0, 0, 1)
        """,
        (chat_id,),
    )

    db.commit()

    return (
        1,
        1,
        3,
        0,
        0,
        0,
        1,
    )


async def set_setting(chat_id, field, value):

    allowed = {
        "links",
        "flood",
        "max_warns",
        "edit_delete",
        "bio_link",
        "media_delete",
        "abuse",
    }

    if field not in allowed:
        return

    await get_settings(chat_id)

    db.execute(
        f"""
        UPDATE settings
        SET {field}=?
        WHERE chat_id=?
        """,
        (
            int(value),
            chat_id,
        ),
    )

    db.commit()


async def get_warn_count(chat_id, user_id):

    row = db.execute(
        """
        SELECT count
        FROM warns
        WHERE chat_id=? AND user_id=?
        """,
        (
            chat_id,
            user_id,
        ),
    ).fetchone()

    return int(row[0]) if row else 0


async def set_warn_count(chat_id, user_id, count):

    if count <= 0:

        db.execute(
            """
            DELETE FROM warns
            WHERE chat_id=? AND user_id=?
            """,
            (
                chat_id,
                user_id,
            ),
        )

    else:

        db.execute(
            """
            INSERT OR REPLACE INTO warns
            (chat_id, user_id, count)
            VALUES (?, ?, ?)
            """,
            (
                chat_id,
                user_id,
                int(count),
            ),
        )

    db.commit()


# =========================================================
# AUTH
# =========================================================

async def is_local_auth(chat_id, user_id):

    row = db.execute(
        """
        SELECT 1
        FROM local_auth
        WHERE chat_id=? AND user_id=?
        """,
        (
            chat_id,
            user_id,
        ),
    ).fetchone()

    return bool(row)


async def is_global_auth(user_id):

    if user_id == OWNER_ID:
        return True

    row = db.execute(
        """
        SELECT 1
        FROM global_auth
        WHERE user_id=?
        """,
        (user_id,),
    ).fetchone()

    return bool(row)


async def add_local_auth(chat_id, user_id):

    db.execute(
        """
        INSERT OR IGNORE INTO local_auth
        (chat_id, user_id)
        VALUES (?, ?)
        """,
        (
            chat_id,
            user_id,
        ),
    )

    db.commit()


async def remove_local_auth(chat_id, user_id):

    db.execute(
        """
        DELETE FROM local_auth
        WHERE chat_id=? AND user_id=?
        """,
        (
            chat_id,
            user_id,
        ),
    )

    db.commit()


async def add_global_auth(user_id):

    db.execute(
        """
        INSERT OR IGNORE INTO global_auth
        (user_id)
        VALUES (?)
        """,
        (user_id,),
    )

    db.commit()


async def remove_global_auth(user_id):

    db.execute(
        """
        DELETE FROM global_auth
        WHERE user_id=?
        """,
        (user_id,),
    )

    db.commit()


# =========================================================
# PERMISSIONS
# =========================================================

async def is_admin(update, user_id=None):

    chat = update.effective_chat

    if not chat:
        return False

    uid = (
        user_id
        or (
            update.effective_user.id
            if update.effective_user
            else 0
        )
    )

    try:

        member = await chat.get_member(uid)

        return member.status in (
            "administrator",
            "creator",
        )

    except Exception:

        return False


async def can_manage(update, user_id=None):

    uid = (
        user_id
        or (
            update.effective_user.id
            if update.effective_user
            else 0
        )
    )

    if uid == OWNER_ID:
        return True

    if await is_global_auth(uid):
        return True

    return await is_admin(update, uid)


async def require_group(update):

    chat = update.effective_chat

    if not chat or chat.type not in (
        "group",
        "supergroup",
    ):

        if update.message:

            await update.message.reply_text(
                "❌ This command works in groups only."
            )

        return False

    return True


# =========================================================
# LOGGING
# =========================================================

async def log_action(context, text):

    if not LOG_CHAT:
        return

    try:

        await context.bot.send_message(
            chat_id=LOG_CHAT,
            text=text,
        )

    except Exception:
        pass


# =========================================================
# TARGET USER
# =========================================================

async def get_target(update):

    if (
        update.message
        and update.message.reply_to_message
        and update.message.reply_to_message.from_user
    ):

        return update.message.reply_to_message.from_user

    return None


async def get_target_id(update):

    user = await get_target(update)

    if user:
        return user.id

    if update.message and update.message.text:

        parts = update.message.text.split()

        if len(parts) >= 2:

            try:
                return int(parts[1])

            except ValueError:
                pass

    return None


# =========================================================
# START
# =========================================================

def home_keyboard(bot_username):

    rows = []

    if bot_username:

        rows.append([
            InlineKeyboardButton(
                "✚ Aᴅᴅ Mᴇ Iɴ Yᴏᴜʀ Gʀᴏᴜᴘ ✚",
                url=(
                    f"https://t.me/"
                    f"{bot_username}"
                    f"?startgroup=true"
                ),
            )
        ])

    if OWNER_USERNAME != "YourOwnerUsername":

        owner_url = f"https://t.me/{OWNER_USERNAME}"

    else:

        owner_url = "https://t.me/"

    if SUPPORT_USERNAME != "YourSupportUsername":

        support_url = f"https://t.me/{SUPPORT_USERNAME}"

    else:

        support_url = "https://t.me/"

    rows.append([
        InlineKeyboardButton(
            "👑 Oᴡɴᴇʀ",
            url=owner_url,
        ),
        InlineKeyboardButton(
            "💬 Sᴜᴘᴘᴏʀᴛ",
            url=support_url,
        ),
    ])

    rows.append([
        InlineKeyboardButton(
            "📖 Hᴇʟᴘ & Cᴏᴍᴍᴀɴᴅs",
            callback_data="help_page_0",
        )
    ])

    return InlineKeyboardMarkup(rows)


def home_text(user):

    return f"""
✦ <b>Hᴇʟʟᴏ, {user.mention_html()}!</b> ✨

╭━━━━━━━━━━━━━━━━━━╮
│ 🛡️ <b>𝐊ɪʀᴛɪ Gᴜᴀʀᴅ</b>
│
│ ⚡ <b>Fᴀsᴛ & Sᴍᴀʀᴛ Pʀᴏᴛᴇᴄᴛɪᴏɴ</b>
│ 🔗 <b>Aɴᴛɪ-Lɪɴᴋ</b>
│ 🌊 <b>Aɴᴛɪ-Sᴘᴀᴍ</b>
│ 🛡️ <b>Aᴜᴛᴏ Mᴏᴅᴇʀᴀᴛɪᴏɴ</b>
╰━━━━━━━━━━━━━━━━━━╯

» <b>Aᴅᴅ Mᴇ Tᴏ Yᴏᴜʀ Gʀᴏᴜᴘ</b>
» Gɪᴠᴇ Mᴇ <b>Dᴇʟᴇᴛᴇ Mᴇssᴀɢᴇs</b> Pᴇʀᴍɪssɪᴏɴ.

✨ <b>Kᴇᴇᴘ Yᴏᴜʀ Gʀᴏᴜᴘ Cʟᴇᴀɴ & Sᴀғᴇ.</b>
"""


async def start(update, context):

    if not update.message:
        return

    save_chat(update.effective_chat)
    save_user(update.effective_user)

    user = update.effective_user

    try:

        me = await context.bot.get_me()
        bot_username = me.username or ""

    except Exception:

        bot_username = ""

    text = home_text(user)
    markup = home_keyboard(bot_username)

    if START_IMAGE:

        try:

            await update.message.reply_photo(
                photo=START_IMAGE,
                caption=text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )

            return

        except Exception as e:

            log.warning(
                "START_IMAGE failed: %s",
                e,
            )

    await update.message.reply_text(
        text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# PING / DIAGNOSTIC
# =========================================================

async def ping(update, context):
    if not update.message:
        return
    try:
        await update.message.reply_text("🏓 <b>Pᴏɴɢ!</b> Bot is online.", parse_mode=ParseMode.HTML)
    except TelegramError as e:
        log.exception("Ping reply failed: %s", e)


# =========================================================
# HELP
# =========================================================

HELP_PAGES = {

0: """📖 <b>Hᴇʟᴘ & Cᴏᴍᴍᴀɴᴅs</b>

◇ Cʜᴏᴏsᴇ A Cᴀᴛᴇɢᴏʀʏ Bᴇʟᴏᴡ.

⚡ Aᴅᴅ Mᴇ Tᴏ Yᴏᴜʀ Gʀᴏᴜᴘ
Aɴᴅ Gɪᴠᴇ Mᴇ Dᴇʟᴇᴛᴇ Mᴇssᴀɢᴇs Pᴇʀᴍɪssɪᴏɴ.""",

1: """🔐 <b>Lᴏᴄᴀʟ Aᴜᴛʜ</b>

/auth — Reply to user
/unauth — Remove local auth
/authlist — Show local auth users""",

2: """🌐 <b>Gʟᴏʙᴀʟ Aᴜᴛʜ</b>

/gauth — Add global auth
/gunauth — Remove global auth

👑 Only OWNER_ID can manage global auth.""",

3: """📝 <b>Eᴅɪᴛ Dᴇʟᴇᴛᴇ</b>

/editdelete on
/editdelete off

✦ Edited messages containing links
are deleted when this feature is enabled.""",

4: """🔗 <b>Bɪᴏ Lɪɴᴋ</b>

/biolink on
/biolink off

⚠️ Ordinary Telegram Bot API does not expose
normal users' profile bio text.
This switch is kept for future integration.""",

5: """🎬 <b>Mᴇᴅɪᴀ Dᴇʟ</b>

/mediadel on
/mediadel off

/purge 20

✦ Deletes recent messages.""",

6: """🚫 <b>Nᴏ Aʙᴜsᴇ</b>

/abuse on
/abuse off

/warn — Warn replied user
/warnings — Check warnings
/unwarn — Reset warnings""",

7: """🔗 <b>Lɪɴᴋ Fɪʟᴛᴇʀ</b>

/antilink on
/antilink off

🌊 /antispam on
🌊 /antispam off""",

8: """🛡️ <b>Aᴅᴍɪɴ Cᴏᴍᴍᴀɴᴅs</b>

/ban — Reply to user
/unban USER_ID
/kick — Reply to user
/mute [minutes] — Reply to user
/unmute — Reply to user
/purge [count]""",

9: """⚙️ <b>Oᴛʜᴇʀ Cᴏᴍᴍᴀɴᴅs</b>

/start
/help
/settings

/antilink on|off
/antispam on|off
/editdelete on|off
/biolink on|off
/mediadel on|off
/abuse on|off

👑 Owner:
/broadcast text""",
}


def help_keyboard(page):

    if page == 0:

        return InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "Lᴏᴄᴀʟ Aᴜᴛʜ",
                    callback_data="help_page_1",
                ),
                InlineKeyboardButton(
                    "Gʟᴏʙᴀʟ Aᴜᴛʜ",
                    callback_data="help_page_2",
                ),
            ],

            [
                InlineKeyboardButton(
                    "Eᴅɪᴛ Dᴇʟᴇᴛᴇ",
                    callback_data="help_page_3",
                ),
                InlineKeyboardButton(
                    "Bɪᴏ Lɪɴᴋ",
                    callback_data="help_page_4",
                ),
            ],

            [
                InlineKeyboardButton(
                    "Mᴇᴅɪᴀ Dᴇʟ",
                    callback_data="help_page_5",
                ),
                InlineKeyboardButton(
                    "Nᴏ Aʙᴜsᴇ",
                    callback_data="help_page_6",
                ),
            ],

            [
                InlineKeyboardButton(
                    "Bᴀᴄᴋ ↩",
                    callback_data="home",
                ),
                InlineKeyboardButton(
                    "• Hᴏᴍᴇ •",
                    callback_data="home",
                ),
                InlineKeyboardButton(
                    "Nᴇxᴛ ➜",
                    callback_data="help_page_1",
                ),
            ],
        ])

    previous_page = max(
        page - 1,
        0,
    )

    next_page = (
        page + 1
        if page < 9
        else 0
    )

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "↩ Bᴀᴄᴋ",
                callback_data=f"help_page_{previous_page}",
            ),
            InlineKeyboardButton(
                "🏠 Hᴏᴍᴇ",
                callback_data="home",
            ),
            InlineKeyboardButton(
                "Nᴇxᴛ ➜",
                callback_data=f"help_page_{next_page}",
            ),
        ]
    ])


async def render_help(query, page):

    text = HELP_PAGES.get(
        page,
        HELP_PAGES[0],
    )

    markup = help_keyboard(page)

    try:

        await query.edit_message_caption(
            caption=text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )

        return

    except Exception:
        pass

    try:

        await query.edit_message_text(
            text=text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )

    except Exception as e:

        log.warning(
            "Help render failed: %s",
            e,
        )


async def help_cmd(update, context):

    await send_help_message(
        update,
        context,
        0,
    )


async def send_help_message(
    update,
    context,
    page=0,
):

    text = HELP_PAGES.get(
        page,
        HELP_PAGES[0],
    )

    markup = help_keyboard(page)

    if HELP_IMAGE:

        try:

            await update.message.reply_photo(
                photo=HELP_IMAGE,
                caption=text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )

            return

        except Exception:
            pass

    await update.message.reply_text(
        text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )


async def help_callback(update, context):

    query = update.callback_query

    try:
        await query.answer()
    except Exception:
        pass

    if query.data == "home":

        user = query.from_user

        try:

            me = await context.bot.get_me()
            username = me.username or ""

        except Exception:

            username = ""

        text = home_text(user)
        markup = home_keyboard(username)

        try:

            await query.edit_message_caption(
                caption=text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )

        except Exception:

            try:

                await query.edit_message_text(
                    text=text,
                    reply_markup=markup,
                    parse_mode=ParseMode.HTML,
                )

            except Exception as e:

                log.warning(
                    "Home render failed: %s",
                    e,
                )

        return

    try:

        page = int(
            query.data.replace(
                "help_page_",
                "",
            )
        )

    except Exception:

        page = 0

    await render_help(
        query,
        max(
            0,
            min(page, 9),
        ),
    )


# =========================================================
# TOGGLES
# =========================================================

async def toggle(
    update,
    context,
    field,
    name,
):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ You don't have permission to use this command."
        )
        return

    if (
        not context.args
        or context.args[0].lower()
        not in ("on", "off")
    ):

        await update.message.reply_text(
            f"Usage: /{name} on|off"
        )

        return

    value = (
        1
        if context.args[0].lower() == "on"
        else 0
    )

    await set_setting(
        update.effective_chat.id,
        field,
        value,
    )

    status = (
        "Eɴᴀʙʟᴇᴅ"
        if value
        else "Dɪsᴀʙʟᴇᴅ"
    )

    await update.message.reply_text(
        f"✅ <b>{name.upper()}</b> {status}",
        parse_mode=ParseMode.HTML,
    )


async def antilink(update, context):
    await toggle(
        update,
        context,
        "links",
        "antilink",
    )


async def antispam(update, context):
    await toggle(
        update,
        context,
        "flood",
        "antispam",
    )


async def editdelete(update, context):
    await toggle(
        update,
        context,
        "edit_delete",
        "editdelete",
    )


async def biolink(update, context):
    await toggle(
        update,
        context,
        "bio_link",
        "biolink",
    )


async def mediadel(update, context):
    await toggle(
        update,
        context,
        "media_delete",
        "mediadel",
    )


async def abuse(update, context):
    await toggle(
        update,
        context,
        "abuse",
        "abuse",
    )


# =========================================================
# SETTINGS
# =========================================================

async def settings_cmd(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ You don't have permission."
        )
        return

    s = await get_settings(
        update.effective_chat.id
    )

    text = f"""
⚙️ <b>Gʀᴏᴜᴘ Sᴇᴛᴛɪɴɢs</b>

🔗 Aɴᴛɪ-Lɪɴᴋ:
<b>{'Oɴ' if s[0] else 'Oғғ'}</b>

🌊 Aɴᴛɪ-Sᴘᴀᴍ:
<b>{'Oɴ' if s[1] else 'Oғғ'}</b>

⚠️ Mᴀx Wᴀʀɴɪɴɢs:
<b>{s[2]}</b>

📝 Eᴅɪᴛ Dᴇʟᴇᴛᴇ:
<b>{'Oɴ' if s[3] else 'Oғғ'}</b>

🔗 Bɪᴏ Lɪɴᴋ:
<b>{'Oɴ' if s[4] else 'Oғғ'}</b>

🎬 Mᴇᴅɪᴀ Dᴇʟ:
<b>{'Oɴ' if s[5] else 'Oғғ'}</b>

🚫 Aʙᴜsᴇ:
<b>{'Oɴ' if s[6] else 'Oғғ'}</b>
"""

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# WARN SYSTEM
# =========================================================

async def warn(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ You don't have permission."
        )
        return

    user = await get_target(update)

    if not user:

        await update.message.reply_text(
            "⚠️ Reply to a user's message."
        )

        return

    if user.is_bot:

        await update.message.reply_text(
            "❌ Bots cannot be warned."
        )

        return

    if await is_admin(update, user.id):

        await update.message.reply_text(
            "❌ Admin cannot be warned."
        )

        return

    chat_id = update.effective_chat.id

    count = (
        await get_warn_count(
            chat_id,
            user.id,
        )
        + 1
    )

    await set_warn_count(
        chat_id,
        user.id,
        count,
    )

    max_warns = (
        await get_settings(chat_id)
    )[2]

    await update.message.reply_text(
        f"⚠️ {user.mention_html()} "
        f"<b>Wᴀʀɴᴇᴅ</b>\n\n"
        f"📊 Wᴀʀɴɪɴɢs: "
        f"<b>{count}/{max_warns}</b>",
        parse_mode=ParseMode.HTML,
    )

    if count >= max_warns:

        try:

            await update.effective_chat.ban_member(
                user.id
            )

            await set_warn_count(
                chat_id,
                user.id,
                0,
            )

            await update.message.reply_text(
                f"🚫 {user.mention_html()} "
                f"<b>Bᴀɴɴᴇᴅ Aғᴛᴇʀ Wᴀʀɴɪɴɢs</b>.",
                parse_mode=ParseMode.HTML,
            )

            await log_action(
                context,
                f"Auto-ban after warnings: "
                f"{user.id} in {chat_id}",
            )

        except TelegramError as e:

            log.warning(
                "Auto-ban failed: %s",
                e,
            )


async def warnings(update, context):

    if not await require_group(update):
        return

    user = (
        await get_target(update)
        or update.effective_user
    )

    count = await get_warn_count(
        update.effective_chat.id,
        user.id,
    )

    await update.message.reply_text(
        f"⚠️ <b>Wᴀʀɴɪɴɢs</b>\n\n"
        f"👤 {user.mention_html()}\n"
        f"📊 Cᴏᴜɴᴛ: <b>{count}</b>",
        parse_mode=ParseMode.HTML,
    )


async def unwarn(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ You don't have permission."
        )
        return

    user = await get_target(update)

    if not user:

        await update.message.reply_text(
            "⚠️ Reply to a user's message."
        )

        return

    await set_warn_count(
        update.effective_chat.id,
        user.id,
        0,
    )

    await update.message.reply_text(
        f"✅ Warnings reset for "
        f"{user.mention_html()}.",
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# BAN
# =========================================================

async def ban(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ You don't have permission."
        )
        return

    user = await get_target(update)

    if not user:

        await update.message.reply_text(
            "⚠️ Reply to a user's message."
        )

        return

    if user.id == OWNER_ID:

        await update.message.reply_text(
            "❌ Owner cannot be banned."
        )

        return

    if await is_admin(update, user.id):

        await update.message.reply_text(
            "❌ You cannot ban an admin."
        )

        return

    try:

        await update.effective_chat.ban_member(
            user.id
        )

        await update.message.reply_text(
            f"🚫 {user.mention_html()} "
            f"<b>Bᴀɴɴᴇᴅ</b>.",
            parse_mode=ParseMode.HTML,
        )

        await log_action(
            context,
            f"Banned {user.id} "
            f"in {update.effective_chat.id}",
        )

    except TelegramError as e:

        await update.message.reply_text(
            f"❌ Ban failed:\n{e}"
        )


# =========================================================
# UNBAN
# =========================================================

async def unban(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ You don't have permission."
        )
        return

    user_id = await get_target_id(update)

    if not user_id:

        await update.message.reply_text(
            "Usage: /unban USER_ID\n"
            "or reply to a user's message."
        )

        return

    try:

        await update.effective_chat.unban_member(
            user_id
        )

        await update.message.reply_text(
            f"✅ User <code>{user_id}</code> "
            f"has been unbanned.",
            parse_mode=ParseMode.HTML,
        )

    except TelegramError as e:

        await update.message.reply_text(
            f"❌ Unban failed:\n{e}"
        )


# =========================================================
# KICK
# =========================================================

async def kick(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ You don't have permission."
        )
        return

    user = await get_target(update)

    if not user:

        await update.message.reply_text(
            "⚠️ Reply to a user's message."
        )

        return

    if await is_admin(update, user.id):

        await update.message.reply_text(
            "❌ You cannot kick an admin."
        )

        return

    try:

        await update.effective_chat.ban_member(
            user.id
        )

        await update.effective_chat.unban_member(
            user.id
        )

        await update.message.reply_text(
            f"👢 {user.mention_html()} "
            f"<b>Kɪᴄᴋᴇᴅ</b>.",
            parse_mode=ParseMode.HTML,
        )

    except TelegramError as e:

        await update.message.reply_text(
            f"❌ Kick failed:\n{e}"
        )


# =========================================================
# MUTE
# =========================================================

async def mute(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ You don't have permission."
        )
        return

    user = await get_target(update)

    if not user:

        await update.message.reply_text(
            "⚠️ Reply to a user's message."
        )

        return

    if await is_admin(update, user.id):

        await update.message.reply_text(
            "❌ You cannot mute an admin."
        )

        return

    minutes = 0

    if context.args:

        try:

            minutes = int(context.args[0])

        except ValueError:

            minutes = 0

    permissions = ChatPermissions(
        can_send_messages=False
    )

    try:

        if minutes > 0:

            until_date = (
                time.time()
                + minutes * 60
            )

            await update.effective_chat.restrict_member(
                user.id,
                permissions=permissions,
                until_date=int(until_date),
            )

            duration_text = (
                f"{minutes} minute(s)"
            )

        else:

            await update.effective_chat.restrict_member(
                user.id,
                permissions=permissions,
            )

            duration_text = "permanently"

        await update.message.reply_text(
            f"🔇 {user.mention_html()} "
            f"<b>Mᴜᴛᴇᴅ</b>\n\n"
            f"⏱️ Duration: {duration_text}",
            parse_mode=ParseMode.HTML,
        )

    except TelegramError as e:

        await update.message.reply_text(
            f"❌ Mute failed:\n{e}"
        )


# =========================================================
# UNMUTE
# =========================================================

async def unmute(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ You don't have permission."
        )
        return

    user = await get_target(update)

    if not user:

        await update.message.reply_text(
            "⚠️ Reply to a user's message."
        )

        return

    permissions = ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False,
        can_manage_topics=False,
    )

    try:

        await update.effective_chat.restrict_member(
            user.id,
            permissions=permissions,
        )

        await update.message.reply_text(
            f"🔊 {user.mention_html()} "
            f"<b>Uɴᴍᴜᴛᴇᴅ</b>.",
            parse_mode=ParseMode.HTML,
        )

    except TelegramError as e:

        await update.message.reply_text(
            f"❌ Unmute failed:\n{e}"
        )


# =========================================================
# PURGE
# =========================================================

async def purge(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ You don't have permission."
        )
        return

    count = 20

    if context.args:

        try:

            count = int(context.args[0])

        except ValueError:

            count = 20

    count = max(
        1,
        min(count, 100),
    )

    chat = update.effective_chat

    message_id = update.message.message_id

    deleted = 0

    for i in range(count):

        msg_id = message_id - i

        try:

            await context.bot.delete_message(
                chat_id=chat.id,
                message_id=msg_id,
            )

            deleted += 1

        except RetryAfter as e:

            await asyncio_sleep(e.retry_after)

        except TelegramError:

            pass

    try:

        status = await chat.send_message(
            f"🧹 Deleted <b>{deleted}</b> messages.",
            parse_mode=ParseMode.HTML,
        )

        await asyncio_sleep(3)

        try:

            await status.delete()

        except Exception:
            pass

    except Exception:
        pass


async def asyncio_sleep(seconds):

    import asyncio

    await asyncio.sleep(seconds)


# =========================================================
# LOCAL AUTH
# =========================================================

async def auth(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ Only admins can use this command."
        )
        return

    user = await get_target(update)

    if not user:

        await update.message.reply_text(
            "⚠️ Reply to a user's message."
        )

        return

    await add_local_auth(
        update.effective_chat.id,
        user.id,
    )

    await update.message.reply_text(
        f"✅ {user.mention_html()} "
        f"<b>added to local auth.</b>",
        parse_mode=ParseMode.HTML,
    )


async def unauth(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ Only admins can use this command."
        )
        return

    user = await get_target(update)

    if not user:

        await update.message.reply_text(
            "⚠️ Reply to a user's message."
        )

        return

    await remove_local_auth(
        update.effective_chat.id,
        user.id,
    )

    await update.message.reply_text(
        f"✅ {user.mention_html()} "
        f"<b>removed from local auth.</b>",
        parse_mode=ParseMode.HTML,
    )


async def authlist(update, context):

    if not await require_group(update):
        return

    if not await can_manage(update):
        await update.message.reply_text(
            "❌ You don't have permission."
        )
        return

    rows = db.execute(
        """
        SELECT user_id
        FROM local_auth
        WHERE chat_id=?
        """,
        (update.effective_chat.id,),
    ).fetchall()

    if not rows:

        await update.message.reply_text(
            "📋 No local auth users."
        )

        return

    text = "🔐 <b>Lᴏᴄᴀʟ Aᴜᴛʜ</b>\n\n"

    for index, row in enumerate(rows, 1):

        user_id = row[0]

        user_row = db.execute(
            """
            SELECT username, first_name
            FROM users
            WHERE user_id=?
            """,
            (user_id,),
        ).fetchone()

        if user_row:

            username, first_name = user_row

            name = (
                f"@{username}"
                if username
                else first_name
                or str(user_id)
            )

        else:

            name = str(user_id)

        text += (
            f"{index}. "
            f"{name} "
            f"<code>{user_id}</code>\n"
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# GLOBAL AUTH
# =========================================================

async def gauth(update, context):

    if not update.effective_user:
        return

    if update.effective_user.id != OWNER_ID:

        await update.message.reply_text(
            "❌ Owner only."
        )

        return

    user = await get_target(update)

    if user:

        user_id = user.id

    elif context.args:

        try:

            user_id = int(context.args[0])

        except ValueError:

            await update.message.reply_text(
                "Usage: /gauth USER_ID"
            )

            return

    else:

        await update.message.reply_text(
            "Reply to a user or use /gauth USER_ID"
        )

        return

    await add_global_auth(user_id)

    await update.message.reply_text(
        f"🌐 Global auth added:\n"
        f"<code>{user_id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def gunauth(update, context):

    if not update.effective_user:
        return

    if update.effective_user.id != OWNER_ID:

        await update.message.reply_text(
            "❌ Owner only."
        )

        return

    user = await get_target(update)

    if user:

        user_id = user.id

    elif context.args:

        try:

            user_id = int(context.args[0])

        except ValueError:

            await update.message.reply_text(
                "Usage: /gunauth USER_ID"
            )

            return

    else:

        await update.message.reply_text(
            "Reply to a user or use /gunauth USER_ID"
        )

        return

    await remove_global_auth(user_id)

    await update.message.reply_text(
        f"🌐 Global auth removed:\n"
        f"<code>{user_id}</code>",
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# BROADCAST
# =========================================================

async def broadcast(update, context):

    if not update.effective_user:
        return

    if update.effective_user.id != OWNER_ID:

        await update.message.reply_text(
            "❌ Owner only."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Usage: /broadcast Your message"
        )

        return

    text = " ".join(context.args)

    rows = db.execute(
        """
        SELECT chat_id
        FROM known_chats
        """
    ).fetchall()

    sent = 0
    failed = 0

    for row in rows:

        chat_id = row[0]

        try:

            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )

            sent += 1

        except RetryAfter as e:

            await asyncio_sleep(
                e.retry_after
            )

        except Exception:

            failed += 1

    await update.message.reply_text(
        f"📢 <b>Broadcast Finished</b>\n\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
        parse_mode=ParseMode.HTML,
    )


# =========================================================
# MESSAGE MODERATION
# =========================================================

async def moderation(update, context):

    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user:
        return

    if chat.type not in (
        "group",
        "supergroup",
    ):
        return

    save_chat(chat)
    save_user(user)

    # Never moderate bots
    if user.is_bot:
        return

    # Never moderate admins
    if await is_admin(update, user.id):
        return

    settings = await get_settings(chat.id)

    links_enabled = bool(settings[0])
    flood_enabled = bool(settings[1])
    edit_delete_enabled = bool(settings[3])
    media_delete_enabled = bool(settings[5])
    abuse_enabled = bool(settings[6])

    text = (
        message.text
        or message.caption
        or ""
    )

    # -----------------------------------------------------
    # LOCAL AUTH / GLOBAL AUTH BYPASS
    # -----------------------------------------------------

    if await is_global_auth(user.id):
        return

    if await is_local_auth(
        chat.id,
        user.id,
    ):
        return

    # -----------------------------------------------------
    # LINK FILTER
    # -----------------------------------------------------

    if links_enabled and LINK_RE.search(text):

        try:

            await message.delete()

            await log_action(
                context,
                f"🔗 Link deleted\n"
                f"User: {user.id}\n"
                f"Chat: {chat.id}",
            )

        except TelegramError:
            pass

        return

    # -----------------------------------------------------
    # ABUSE FILTER
    # -----------------------------------------------------

    if abuse_enabled and text:

        lower_text = text.lower()

        words = re.findall(
            r"\b[\w'-]+\b",
            lower_text,
        )

        found = any(
            word in ABUSE_WORDS
            for word in words
        )

        if found:

            try:

                await message.delete()

                await log_action(
                    context,
                    f"🚫 Abuse message deleted\n"
                    f"User: {user.id}\n"
                    f"Chat: {chat.id}",
                )

            except TelegramError:
                pass

            return

    # -----------------------------------------------------
    # MEDIA DELETE
    # -----------------------------------------------------

    if media_delete_enabled:

        media_exists = any([
            bool(message.photo),
            bool(message.video),
            bool(message.animation),
            bool(message.document),
            bool(message.audio),
            bool(message.voice),
            bool(message.video_note),
            bool(message.sticker),
        ])

        if media_exists:

            try:
                await message.delete()
            except TelegramError:
                pass

            return

    # -----------------------------------------------------
    # ANTISPAM
    # -----------------------------------------------------

    if flood_enabled:

        now = time.time()

        user_cache = flood_cache[
            chat.id
        ][
            user.id
        ]

        user_cache.append(now)

        while (
            user_cache
            and now - user_cache[0] > 5
        ):
            user_cache.popleft()

        # 6 messages in 5 seconds
        if len(user_cache) >= 6:

            user_cache.clear()

            try:

                await message.delete()

            except TelegramError:
                pass

            try:

                permissions = ChatPermissions(
                    can_send_messages=False
                )

                await chat.restrict_member(
                    user.id,
                    permissions=permissions,
                    until_date=int(
                        time.time() + 60
                    ),
                )

                warning = await chat.send_message(
                    f"🌊 {user.mention_html()} "
                    f"<b>Fʟᴏᴏᴅ Sᴘᴀᴍ Dᴇᴛᴇᴄᴛᴇᴅ</b>\n"
                    f"🔇 Muted for 1 minute.",
                    parse_mode=ParseMode.HTML,
                )

                await asyncio_sleep(5)

                try:
                    await warning.delete()
                except Exception:
                    pass

            except TelegramError as e:

                log.warning(
                    "Flood action failed: %s",
                    e,
                )

            return


# =========================================================
# EDITED MESSAGE MODERATION
# =========================================================

async def edited_moderation(update, context):

    message = update.edited_message

    if not message:
        return

    chat = message.chat
    user = message.from_user

    if not chat or not user:
        return

    if chat.type not in (
        "group",
        "supergroup",
    ):
        return

    if user.is_bot:
        return

    if await is_admin(update, user.id):
        return

    if await is_global_auth(user.id):
        return

    if await is_local_auth(
        chat.id,
        user.id,
    ):
        return

    settings = await get_settings(chat.id)

    if not settings[3]:
        return

    text = (
        message.text
        or message.caption
        or ""
    )

    if LINK_RE.search(text):

        try:

            await message.delete()

            await log_action(
                context,
                f"📝 Edited link deleted\n"
                f"User: {user.id}\n"
                f"Chat: {chat.id}",
            )

        except TelegramError:
            pass


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update, context):

    error = context.error

    log.error(
        "Update handling error | update=%r | error=%r",
        update,
        error,
        exc_info=(type(error), error, error.__traceback__) if error else None,
    )

    if isinstance(
        error,
        RetryAfter,
    ):

        log.warning(
            "Telegram flood wait: %s",
            error,
        )

        return

    if isinstance(
        error,
        Forbidden,
    ):

        log.warning(
            "Telegram forbidden: %s",
            error,
        )

        return

    if isinstance(
        error,
        BadRequest,
    ):

        log.warning(
            "Telegram bad request: %s",
            error,
        )

        return

    log.exception(
        "Unhandled error:",
        exc_info=error,
    )


# =========================================================
# POST INIT
# =========================================================

async def post_init(application):

    try:

        me = await application.bot.get_me()

        log.info(
            "Bot started: @%s (%s)",
            me.username,
            me.id,
        )

        try:

            await application.bot.set_my_commands([
                ("start", "Start the bot"),
                ("help", "Help and commands"),
                ("settings", "Group settings"),
                ("antilink", "Enable/disable anti-link"),
                ("antispam", "Enable/disable anti-spam"),
                ("warn", "Warn a user"),
                ("warnings", "Check warnings"),
                ("unwarn", "Reset warnings"),
                ("ban", "Ban a user"),
                ("unban", "Unban a user"),
                ("kick", "Kick a user"),
                ("mute", "Mute a user"),
                ("unmute", "Unmute a user"),
                ("purge", "Delete messages"),
            ])

        except Exception as e:

            log.warning(
                "set_my_commands failed: %s",
                e,
            )

    except Exception as e:

        log.exception(
            "post_init failed: %s",
            e,
        )


# =========================================================
# MAIN
# =========================================================

def main():

    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN environment variable is missing. "
            "Set BOT_TOKEN in Heroku Config Vars."
        )

    if not OWNER_ID:
        log.warning(
            "OWNER_ID is not configured. Owner-only commands will be unavailable."
        )

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    # -----------------------------------------------------
    # BASIC
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_cmd,
        )
    )

    application.add_handler(
        CommandHandler(
            "ping",
            ping,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            help_callback,
            pattern=r"^(home|help_page_\d+)$",
        )
    )

    # -----------------------------------------------------
    # SETTINGS
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "settings",
            settings_cmd,
        )
    )

    application.add_handler(
        CommandHandler(
            "antilink",
            antilink,
        )
    )

    application.add_handler(
        CommandHandler(
            "antispam",
            antispam,
        )
    )

    application.add_handler(
        CommandHandler(
            "editdelete",
            editdelete,
        )
    )

    application.add_handler(
        CommandHandler(
            "biolink",
            biolink,
        )
    )

    application.add_handler(
        CommandHandler(
            "mediadel",
            mediadel,
        )
    )

    application.add_handler(
        CommandHandler(
            "abuse",
            abuse,
        )
    )

    # -----------------------------------------------------
    # WARN
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "warn",
            warn,
        )
    )

    application.add_handler(
        CommandHandler(
            "warnings",
            warnings,
        )
    )

    application.add_handler(
        CommandHandler(
            "unwarn",
            unwarn,
        )
    )

    # -----------------------------------------------------
    # MODERATION
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "ban",
            ban,
        )
    )

    application.add_handler(
        CommandHandler(
            "unban",
            unban,
        )
    )

    application.add_handler(
        CommandHandler(
            "kick",
            kick,
        )
    )

    application.add_handler(
        CommandHandler(
            "mute",
            mute,
        )
    )

    application.add_handler(
        CommandHandler(
            "unmute",
            unmute,
        )
    )

    application.add_handler(
        CommandHandler(
            "purge",
            purge,
        )
    )

    # -----------------------------------------------------
    # AUTH
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "auth",
            auth,
        )
    )

    application.add_handler(
        CommandHandler(
            "unauth",
            unauth,
        )
    )

    application.add_handler(
        CommandHandler(
            "authlist",
            authlist,
        )
    )

    application.add_handler(
        CommandHandler(
            "gauth",
            gauth,
        )
    )

    application.add_handler(
        CommandHandler(
            "gunauth",
            gunauth,
        )
    )

    # -----------------------------------------------------
    # OWNER
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "broadcast",
            broadcast,
        )
    )

    # -----------------------------------------------------
    # NEW MESSAGES
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.ALL
            & ~filters.COMMAND,
            moderation,
        ),
        group=10,
    )

    # -----------------------------------------------------
    # EDITED MESSAGES
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.UpdateType.EDITED_MESSAGE,
            edited_moderation,
        ),
        group=20,
    )

    # -----------------------------------------------------
    # ERRORS
    # -----------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    # -----------------------------------------------------
    # RUN
    # -----------------------------------------------------

    log.info(
        "Starting ᴋɪʀᴛɪ Guard..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()
