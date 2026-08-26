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
# SHIELD GUARD BOT - COMPLETE VERSION
# =========================================================

TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "8857291657") or 0)

OWNER_USERNAME = os.getenv("OWNER_USERNAME", "YourOwnerUsername").strip().lstrip("@")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "YourSupportUsername").strip().lstrip("@")

LOG_CHAT = os.getenv("LOG_CHAT_ID", "-1003979103138").strip()
DB_PATH = os.getenv("DB_PATH", "shieldbot.db").strip()

# Telegram file_id or direct image URL.
START_IMAGE = os.getenv("START_IMAGE", "https://h.uguu.se/FekWWcsz.jpg").strip()
HELP_IMAGE = os.getenv("HELP_IMAGE", "https://h.uguu.se/FekWWcsz.jpg").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("shield_guard")

# =========================================================
# DATABASE
# =========================================================

db = sqlite3.connect(DB_PATH, check_same_thread=False)
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

for column, definition in (
    ("edit_delete", "INTEGER DEFAULT 0"),
    ("bio_link", "INTEGER DEFAULT 0"),
    ("media_delete", "INTEGER DEFAULT 0"),
    ("abuse", "INTEGER DEFAULT 1"),
):
    try:
        db.execute(f"ALTER TABLE settings ADD COLUMN {column} {definition}")
        db.commit()
    except sqlite3.OperationalError:
        pass

# =========================================================
# REGEX / CACHE
# =========================================================

LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/|tg://)",
    re.IGNORECASE,
)

ABUSE_WORDS = {
    "fuck", "fucking", "bitch", "bastard", "asshole",
}

flood_cache = defaultdict(lambda: defaultdict(deque))

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
    except Exception:
        pass


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
    except Exception:
        pass


async def get_settings(chat_id):
    row = db.execute(
        """
        SELECT links, flood, max_warns, edit_delete,
               bio_link, media_delete, abuse
        FROM settings WHERE chat_id=?
        """,
        (chat_id,),
    ).fetchone()

    if row:
        return row

    db.execute(
        """
        INSERT OR IGNORE INTO settings
        (chat_id, links, flood, max_warns, edit_delete,
         bio_link, media_delete, abuse)
        VALUES (?, 1, 1, 3, 0, 0, 0, 1)
        """,
        (chat_id,),
    )
    db.commit()
    return (1, 1, 3, 0, 0, 0, 1)


async def set_setting(chat_id, field, value):
    allowed = {
        "links", "flood", "max_warns",
        "edit_delete", "bio_link",
        "media_delete", "abuse",
    }
    if field not in allowed:
        return
    await get_settings(chat_id)
    db.execute(
        f"UPDATE settings SET {field}=? WHERE chat_id=?",
        (int(value), chat_id),
    )
    db.commit()


async def get_warn_count(chat_id, user_id):
    row = db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    return int(row[0]) if row else 0


async def set_warn_count(chat_id, user_id, count):
    if count <= 0:
        db.execute(
            "DELETE FROM warns WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )
    else:
        db.execute(
            """
            INSERT OR REPLACE INTO warns(chat_id, user_id, count)
            VALUES (?, ?, ?)
            """,
            (chat_id, user_id, int(count)),
        )
    db.commit()

# =========================================================
# AUTH
# =========================================================

async def is_local_auth(chat_id, user_id):
    row = db.execute(
        "SELECT 1 FROM local_auth WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    ).fetchone()
    return bool(row)


async def is_global_auth(user_id):
    if user_id == OWNER_ID:
        return True
    row = db.execute(
        "SELECT 1 FROM global_auth WHERE user_id=?",
        (user_id,),
    ).fetchone()
    return bool(row)


async def add_local_auth(chat_id, user_id):
    db.execute(
        "INSERT OR IGNORE INTO local_auth(chat_id, user_id) VALUES (?, ?)",
        (chat_id, user_id),
    )
    db.commit()


async def remove_local_auth(chat_id, user_id):
    db.execute(
        "DELETE FROM local_auth WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    db.commit()


async def add_global_auth(user_id):
    db.execute(
        "INSERT OR IGNORE INTO global_auth(user_id) VALUES (?)",
        (user_id,),
    )
    db.commit()


async def remove_global_auth(user_id):
    db.execute(
        "DELETE FROM global_auth WHERE user_id=?",
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

    uid = user_id or (update.effective_user.id if update.effective_user else 0)

    try:
        member = await chat.get_member(uid)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def can_manage(update, user_id=None):
    uid = user_id or (update.effective_user.id if update.effective_user else 0)

    if uid == OWNER_ID or await is_global_auth(uid):
        return True

    return await is_admin(update, uid)


async def require_group(update):
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        if update.message:
            await update.message.reply_text("❌ This command works in groups only.")
        return False
    return True

# =========================================================
# LOGGING
# =========================================================

async def log_action(context, text):
    if not LOG_CHAT:
        return
    try:
        await context.bot.send_message(chat_id=LOG_CHAT, text=text)
    except Exception:
        pass

# =========================================================
# START / HOME
# =========================================================

def home_keyboard(bot_username):
    rows = []

    if bot_username:
        rows.append([
            InlineKeyboardButton(
                "✚ Aᴅᴅ Mᴇ Iɴ Yᴏᴜʀ Gʀᴏᴜᴘ ✚",
                url=f"https://t.me/{bot_username}?startgroup=true",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "👑 Oᴡɴᴇʀ",
            url=f"https://t.me/{OWNER_USERNAME}",
        ),
        InlineKeyboardButton(
            "💬 Sᴜᴘᴘᴏʀᴛ",
            url=f"https://t.me/{SUPPORT_USERNAME}",
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
│ 🛡️ <b>Sʜɪᴇʟᴅ Gᴜᴀʀᴅ</b>
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
            log.warning("START_IMAGE failed: %s", e)

    await update.message.reply_text(
        text,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
    )

# =========================================================
# HELP
# =========================================================

HELP_PAGES = {
0: """📖 <b>Hᴇʟᴘ & Cᴏᴍᴍᴀɴᴅs</b>

◇ Cʜᴏᴏsᴇ A Cᴀᴛᴇɢᴏʀʏ Bᴇʟᴏᴡ.

⚡ Aᴅᴅ Mᴇ Tᴏ Yᴏᴜʀ Gʀᴏᴜᴘ Aɴᴅ Gɪᴠᴇ
Mᴇ Dᴇʟᴇᴛᴇ Mᴇssᴀɢᴇs Pᴇʀᴍɪssɪᴏɴ.

✨ Kᴇᴇᴘ Yᴏᴜʀ Gʀᴏᴜᴘ Cʟᴇᴀɴ Aɴᴅ Sᴀғᴇ.""",

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

✦ Edited messages containing links are deleted.""",

4: """🔗 <b>Bɪᴏ Lɪɴᴋ</b>

/biolink on
/biolink off

⚠️ Telegram Bot API does not expose ordinary users' profile bio text.
This switch is therefore reserved for future/MTProto integration and
does NOT falsely claim to scan user bios.""",

5: """🎬 <b>Mᴇᴅɪᴀ Dᴇʟ</b>

/mediadel on
/mediadel off

/purge 20 — Delete recent messages.""",

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

Owner:
/broadcast <text>""",
}


def help_keyboard(page):
    if page == 0:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Lᴏᴄᴀʟ Aᴜᴛʜ", callback_data="help_page_1"),
                InlineKeyboardButton("Gʟᴏʙᴀʟ Aᴜᴛʜ", callback_data="help_page_2"),
            ],
            [
                InlineKeyboardButton("Eᴅɪᴛ Dᴇʟᴇᴛᴇ", callback_data="help_page_3"),
                InlineKeyboardButton("Bɪᴏ Lɪɴᴋ", callback_data="help_page_4"),
            ],
            [
                InlineKeyboardButton("Mᴇᴅɪᴀ Dᴇʟ", callback_data="help_page_5"),
                InlineKeyboardButton("Nᴏ Aʙᴜsᴇ", callback_data="help_page_6"),
            ],
            [
                InlineKeyboardButton("Bᴀᴄᴋ ↩", callback_data="home"),
                InlineKeyboardButton("• Hᴏᴍᴇ •", callback_data="home"),
                InlineKeyboardButton("Nᴇxᴛ ➜", callback_data="help_page_1"),
            ],
        ])

    previous_page = max(page - 1, 0)
    next_page = page + 1 if page < 9 else 0

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("↩ Bᴀᴄᴋ", callback_data=f"help_page_{previous_page}"),
            InlineKeyboardButton("🏠 Hᴏᴍᴇ", callback_data="home"),
            InlineKeyboardButton("Nᴇxᴛ ➜", callback_data=f"help_page_{next_page}"),
        ]
    ])


async def render_help(query, page):
    text = HELP_PAGES.get(page, HELP_PAGES[0])
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
        log.warning("Help render failed: %s", e)


async def help_cmd(update, context):
    await send_help_message(update, context, 0)


async def send_help_message(update, context, page=0):
    text = HELP_PAGES.get(page, HELP_PAGES[0])
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
    await query.answer()

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
                log.warning("Home render failed: %s", e)
        return

    try:
        page = int(query.data.replace("help_page_", ""))
    except Exception:
        page = 0

    await render_help(query, max(0, min(page, 9)))

# =========================================================
# TARGET
# =========================================================

async def get_target(update):
    if (
        update.message
        and update.message.reply_to_message
        and update.message.reply_to_message.from_user
    ):
        return update.message.reply_to_message.from_user
    return None

# =========================================================
# TOGGLES
# =========================================================

async def toggle(update, context, field, name):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return

    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text(f"Usage: /{name} on|off")
        return

    value = 1 if context.args[0].lower() == "on" else 0
    await set_setting(update.effective_chat.id, field, value)

    await update.message.reply_text(
        f"✅ <b>{name.upper()}</b> {'Eɴᴀʙʟᴇᴅ' if value else 'Dɪsᴀʙʟᴇᴅ'}",
        parse_mode=ParseMode.HTML,
    )


async def antilink(update, context):
    await toggle(update, context, "links", "antilink")


async def antispam(update, context):
    await toggle(update, context, "flood", "antispam")


async def editdelete(update, context):
    await toggle(update, context, "edit_delete", "editdelete")


async def biolink(update, context):
    await toggle(update, context, "bio_link", "biolink")


async def mediadel(update, context):
    await toggle(update, context, "media_delete", "mediadel")


async def abuse(update, context):
    await toggle(update, context, "abuse", "abuse")

# =========================================================
# SETTINGS
# =========================================================

async def settings_cmd(update, context):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return

    s = await get_settings(update.effective_chat.id)

    await update.message.reply_text(
        f"""⚙️ <b>Gʀᴏᴜᴘ Sᴇᴛᴛɪɴɢs</b>

🔗 Aɴᴛɪ-Lɪɴᴋ: <b>{'Oɴ' if s[0] else 'Oғғ'}</b>
🌊 Aɴᴛɪ-Sᴘᴀᴍ: <b>{'Oɴ' if s[1] else 'Oғғ'}</b>
⚠️ Mᴀx Wᴀʀɴɪɴɢs: <b>{s[2]}</b>
📝 Eᴅɪᴛ Dᴇʟᴇᴛᴇ: <b>{'Oɴ' if s[3] else 'Oғғ'}</b>
🔗 Bɪᴏ Lɪɴᴋ: <b>{'Oɴ' if s[4] else 'Oғғ'}</b>
🎬 Mᴇᴅɪᴀ Dᴇʟ: <b>{'Oɴ' if s[5] else 'Oғғ'}</b>
🚫 Aʙᴜsᴇ: <b>{'Oɴ' if s[6] else 'Oғғ'}</b>""",
        parse_mode=ParseMode.HTML,
    )

# =========================================================
# WARN
# =========================================================

async def warn(update, context):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return

    user = await get_target(update)
    if not user:
        await update.message.reply_text("⚠️ Reply to a user's message.")
        return

    if await is_admin(update, user.id):
        await update.message.reply_text("❌ Admin cannot be warned.")
        return

    chat_id = update.effective_chat.id
    count = await get_warn_count(chat_id, user.id) + 1
    await set_warn_count(chat_id, user.id, count)

    max_warns = (await get_settings(chat_id))[2]

    await update.message.reply_text(
        f"⚠️ {user.mention_html()} <b>Wᴀʀɴᴇᴅ</b>\n\n"
        f"📊 Wᴀʀɴɪɴɢs: <b>{count}/{max_warns}</b>",
        parse_mode=ParseMode.HTML,
    )

    if count >= max_warns:
        try:
            await update.effective_chat.ban_member(user.id)
            await set_warn_count(chat_id, user.id, 0)
            await update.message.reply_text(
                f"🚫 {user.mention_html()} <b>Bᴀɴɴᴇᴅ Aғᴛᴇʀ Wᴀʀɴɪɴɢs</b>.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            log.warning("Auto-ban failed: %s", e)

async def warnings(update, context):
    if not await require_group(update):
        return

    user = await get_target(update) or update.effective_user
    count = await get_warn_count(update.effective_chat.id, user.id)

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
        return

    user = await get_target(update)
    if not user:
        await update.message.reply_text("⚠️ Reply to a user.")
        return

    await set_warn_count(update.effective_chat.id, user.id, 0)
    await update.message.reply_text(
        f"♻️ Warnings reset for {user.mention_html()}.",
        parse_mode=ParseMode.HTML,
    )

# =========================================================
# AUTH COMMANDS
# =========================================================

async def auth(update, context):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return
    user = await get_target(update)
    if not user:
        await update.message.reply_text("⚠️ Reply to a user.")
        return
    await add_local_auth(update.effective_chat.id, user.id)
    await update.message.reply_text(
        f"🔐 {user.mention_html()} authorized in this group.",
        parse_mode=ParseMode.HTML,
    )

async def unauth(update, context):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return
    user = await get_target(update)
    if not user:
        await update.message.reply_text("⚠️ Reply to a user.")
        return
    await remove_local_auth(update.effective_chat.id, user.id)
    await update.message.reply_text(
        f"🔓 {user.mention_html()} removed from local auth.",
        parse_mode=ParseMode.HTML,
    )

async def authlist(update, context):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return

    rows = db.execute(
        """
        SELECT user_id FROM local_auth
        WHERE chat_id=?
        ORDER BY user_id
        """,
        (update.effective_chat.id,),
    ).fetchall()

    if not rows:
        await update.message.reply_text("📋 Local auth list is empty.")
        return

    text = "📋 <b>Lᴏᴄᴀʟ Aᴜᴛʜ</b>\n\n"
    text += "\n".join(f"• <code>{r[0]}</code>" for r in rows)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def gauth(update, context):
    if update.effective_user.id != OWNER_ID:
        return
    user = await get_target(update)
    if not user:
        await update.message.reply_text("⚠️ Reply to a user.")
        return
    await add_global_auth(user.id)
    await update.message.reply_text(
        f"🌐 {user.mention_html()} added to global auth.",
        parse_mode=ParseMode.HTML,
    )

async def gunauth(update, context):
    if update.effective_user.id != OWNER_ID:
        return
    user = await get_target(update)
    if not user:
        await update.message.reply_text("⚠️ Reply to a user.")
        return
    await remove_global_auth(user.id)
    await update.message.reply_text(
        f"🔓 {user.mention_html()} removed from global auth.",
        parse_mode=ParseMode.HTML,
    )

# =========================================================
# MODERATION
# =========================================================

async def ban(update, context):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return

    user = await get_target(update)
    if not user:
        await update.message.reply_text("⚠️ Reply to a user.")
        return
    if await is_admin(update, user.id):
        await update.message.reply_text("❌ Cannot ban an admin.")
        return

    try:
        await update.effective_chat.ban_member(user.id)
        await update.message.reply_text(
            f"🚫 {user.mention_html()} <b>Bᴀɴɴᴇᴅ</b>.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ Ban failed: {e}")

async def unban(update, context):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return

    if not context.args or not context.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /unban USER_ID")
        return

    uid = int(context.args[0])
    try:
        await update.effective_chat.unban_member(uid, only_if_banned=True)
        await update.message.reply_text(f"🔓 <code>{uid}</code> unbanned.", parse_mode=ParseMode.HTML)
    except TelegramError as e:
        await update.message.reply_text(f"❌ Unban failed: {e}")

async def kick(update, context):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return

    user = await get_target(update)
    if not user:
        await update.message.reply_text("⚠️ Reply to a user.")
        return
    if await is_admin(update, user.id):
        await update.message.reply_text("❌ Cannot kick an admin.")
        return

    try:
        await update.effective_chat.ban_member(user.id)
        await update.effective_chat.unban_member(user.id)
        await update.message.reply_text(
            f"👢 {user.mention_html()} <b>Kɪᴄᴋᴇᴅ</b>.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ Kick failed: {e}")

async def mute(update, context):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return

    user = await get_target(update)
    if not user:
        await update.message.reply_text("⚠️ Reply to a user.")
        return
    if await is_admin(update, user.id):
        await update.message.reply_text("❌ Cannot mute an admin.")
        return

    minutes = 0
    if context.args:
        try:
            minutes = max(0, int(context.args[0]))
        except ValueError:
            await update.message.reply_text("Usage: /mute [minutes]")
            return

    until_date = None
    if minutes:
        until_date = time.time() + minutes * 60

    try:
        await update.effective_chat.restrict_member(
            user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until_date,
        )
        suffix = f" for {minutes} min" if minutes else ""
        await update.message.reply_text(
            f"🔇 {user.mention_html()} <b>Mᴜᴛᴇᴅ</b>{suffix}.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ Mute failed: {e}")

async def unmute(update, context):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return

    user = await get_target(update)
    if not user:
        await update.message.reply_text("⚠️ Reply to a user.")
        return

    try:
        await update.effective_chat.restrict_member(
            user.id,
            permissions=ChatPermissions(
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
            ),
        )
        await update.message.reply_text(
            f"🔊 {user.mention_html()} <b>Uɴᴍᴜᴛᴇᴅ</b>.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        await update.message.reply_text(f"❌ Unmute failed: {e}")

# =========================================================
# PURGE
# =========================================================

async def purge(update, context):
    if not await require_group(update):
        return
    if not await can_manage(update):
        return

    count = 10
    if context.args:
        try:
            count = max(1, min(int(context.args[0]), 100))
        except ValueError:
            await update.message.reply_text("Usage: /purge [1-100]")
            return

    chat_id = update.effective_chat.id
    start_id = update.message.message_id
    deleted = 0

    for message_id in range(start_id, max(0, start_id - count), -1):
        try:
            await context.bot.delete_message(chat_id, message_id)
            deleted += 1
        except TelegramError:
            pass

    msg = await context.bot.send_message(
        chat_id,
        f"🗑️ Deleted <b>{deleted}</b> messages.",
        parse_mode=ParseMode.HTML,
    )
    await context.bot.delete_message(chat_id, msg.message_id)

# =========================================================
# BROADCAST - OWNER ONLY
# =========================================================

async def broadcast(update, context):
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        return

    if update.effective_chat.type == "private" and update.message.reply_to_message:
        source = update.message.reply_to_message
    else:
        source = None

    if not context.args and not source:
        await update.message.reply_text(
            "Usage:\n/broadcast your message\n\n"
            "Or reply to any message with /broadcast"
        )
        return

    rows = db.execute(
        "SELECT chat_id FROM known_chats ORDER BY chat_id"
    ).fetchall()

    if not rows:
        await update.message.reply_text("📢 No saved chats/users yet.")
        return

    status = await update.message.reply_text(
        f"📢 Broadcasting to <b>{len(rows)}</b> chats...",
        parse_mode=ParseMode.HTML,
    )

    success = 0
    failed = 0

    for (chat_id,) in rows:
        try:
            if source:
                await context.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=source.chat_id,
                    message_id=source.message_id,
                )
            else:
                text = " ".join(context.args)
                await context.bot.send_message(chat_id=chat_id, text=text)

            success += 1

        except RetryAfter as e:
            try:
                await __import__("asyncio").sleep(e.retry_after)
                if source:
                    await context.bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=source.chat_id,
                        message_id=source.message_id,
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=" ".join(context.args),
                    )
                success += 1
            except Exception:
                failed += 1

        except (Forbidden, BadRequest):
            failed += 1
            db.execute("DELETE FROM known_chats WHERE chat_id=?", (chat_id,))
            db.commit()

        except Exception:
            failed += 1

        await __import__("asyncio").sleep(0.05)

    try:
        await status.edit_text(
            f"""📢 <b>Bʀᴏᴀᴅᴄᴀѕᴛ Cᴏᴍᴘʟᴇᴛᴇ</b>

✅ Success: <b>{success}</b>
❌ Failed: <b>{failed}</b>
📊 Total: <b>{len(rows)}</b>""",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

# =========================================================
# MESSAGE FILTER
# =========================================================

def message_has_abuse(text):
    if not text:
        return False
    words = re.findall(r"[a-zA-Z]+", text.lower())
    return any(word in ABUSE_WORDS for word in words)


async def safe_delete(message):
    try:
        await message.delete()
        return True
    except Exception:
        return False


async def moderate_message(update, context):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user:
        return

    save_chat(chat)
    save_user(user)

    if chat.type not in ("group", "supergroup"):
        return

    if await is_admin(update, user.id):
        return

    if await is_local_auth(chat.id, user.id) or await is_global_auth(user.id):
        return

    s = await get_settings(chat.id)
    links, flood, max_warns, edit_delete, bio_link, media_delete, abuse = s

    text = message.text or message.caption or ""

    # Anti-link
    if links and LINK_RE.search(text):
        if await safe_delete(message):
            await log_action(
                context,
                f"🔗 Link deleted in {chat.id} from {user.id}",
            )
        return

    # Abuse filter
    if abuse and message_has_abuse(text):
        if await safe_delete(message):
            await log_action(
                context,
                f"🚫 Abuse deleted in {chat.id} from {user.id}",
            )
        return

    # Media delete
    if media_delete and (
        message.photo
        or message.video
        or message.animation
        or message.document
        or message.audio
        or message.voice
        or message.video_note
    ):
        await safe_delete(message)
        return

    # Anti-spam: 5 messages in 8 seconds
    if flood:
        now = time.monotonic()
        dq = flood_cache[chat.id][user.id]
        dq.append(now)

        while dq and now - dq[0] > 8:
            dq.popleft()

        if len(dq) >= 5:
            dq.clear()
            try:
                await chat.restrict_member(
                    user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=time.time() + 60,
                )
                await safe_delete(message)
                await log_action(
                    context,
                    f"🌊 Spam mute in {chat.id} for {user.id}",
                )
            except Exception:
                pass


async def edited_message_handler(update, context):
    message = update.edited_message
    if not message or not message.chat:
        return

    chat = message.chat
    user = message.from_user
    if not user or chat.type not in ("group", "supergroup"):
        return

    if await is_admin(update, user.id):
        return

    if await is_local_auth(chat.id, user.id) or await is_global_auth(user.id):
        return

    s = await get_settings(chat.id)
    if not s[3]:
        return

    text = message.text or message.caption or ""
    if LINK_RE.search(text):
        await safe_delete(message)

# =========================================================
# COMMAND ERROR HANDLER
# =========================================================

async def error_handler(update, context):
    err = context.error
    if isinstance(err, RetryAfter):
        log.warning("FloodWait: %s", err.retry_after)
    else:
        log.exception("Unhandled error: %s", err)

# =========================================================
# MAIN
# =========================================================

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")
    if OWNER_ID == 0:
        log.warning("OWNER_ID is 0. Owner-only commands will not work.")

    app = Application.builder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    app.add_handler(CommandHandler("antilink", antilink))
    app.add_handler(CommandHandler("antispam", antispam))
    app.add_handler(CommandHandler("editdelete", editdelete))
    app.add_handler(CommandHandler("biolink", biolink))
    app.add_handler(CommandHandler("mediadel", mediadel))
    app.add_handler(CommandHandler("abuse", abuse))
    app.add_handler(CommandHandler("settings", settings_cmd))

    app.add_handler(CommandHandler("warn", warn))
    app.add_handler(CommandHandler("warnings", warnings))
    app.add_handler(CommandHandler("unwarn", unwarn))

    app.add_handler(CommandHandler("auth", auth))
    app.add_handler(CommandHandler("unauth", unauth))
    app.add_handler(CommandHandler("authlist", authlist))
    app.add_handler(CommandHandler("gauth", gauth))
    app.add_handler(CommandHandler("gunauth", gunauth))

    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("unmute", unmute))
    app.add_handler(CommandHandler("purge", purge))

    app.add_handler(CommandHandler("broadcast", broadcast))

    # Buttons
    app.add_handler(
        CallbackQueryHandler(
            help_callback,
            pattern=r"^(help_page_\d+|home)$",
        )
    )

    # Edited messages must be before generic message moderation.
    app.add_handler(
        MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & filters.ALL,
            edited_message_handler,
        )
    )

    # Group message moderation.
    app.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS & filters.ALL,
            moderate_message,
        )
    )

    app.add_error_handler(error_handler)

    log.info("Shield Guard started.")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
