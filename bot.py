import os
import re
import time
import asyncio
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
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# 🛡️ SHIELD GUARD BOT
# =========================================================

TOKEN = os.getenv("BOT_TOKEN", "").strip()

OWNER_ID = int(
    os.getenv("OWNER_ID", "0") or 0
)

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
    ""
).strip()

DB_PATH = os.getenv(
    "DB_PATH",
    "shieldbot.db"
).strip()

# =========================================================
# 🖼️ HELP IMAGE
# =========================================================
# Telegram file_id ya direct image URL.
#
# Example:
# HELP_IMAGE=AgACAgUAA...
#
# Agar empty chhoda to image ke bina help message aayega.
# =========================================================

HELP_IMAGE = os.getenv(
    "HELP_IMAGE",
    ""
).strip()

# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger(
    "shield_guard"
)

# =========================================================
# DATABASE
# =========================================================

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False
)

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

db.commit()

# =========================================================
# OLD DATABASE MIGRATION
# =========================================================

for column, definition in [
    ("edit_delete", "INTEGER DEFAULT 0"),
    ("bio_link", "INTEGER DEFAULT 0"),
    ("media_delete", "INTEGER DEFAULT 0"),
    ("abuse", "INTEGER DEFAULT 1"),
]:
    try:
        db.execute(
            f"ALTER TABLE settings ADD COLUMN "
            f"{column} {definition}"
        )
        db.commit()
    except sqlite3.OperationalError:
        pass

# =========================================================
# DEFAULT SETTINGS
# =========================================================

DEFAULT_SETTINGS = {
    "links": 1,
    "flood": 1,
    "max_warns": 3,
    "edit_delete": 0,
    "bio_link": 0,
    "media_delete": 0,
    "abuse": 1,
}

# =========================================================
# REGEX
# =========================================================

LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/)",
    re.IGNORECASE
)

BIO_LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/)",
    re.IGNORECASE
)

# Basic abuse filter
ABUSE_WORDS = {
    "fuck",
    "fucking",
    "bitch",
    "bastard",
    "asshole",
}

# =========================================================
# FLOOD CACHE
# =========================================================

flood_cache = defaultdict(
    lambda: defaultdict(deque)
)

# =========================================================
# DATABASE FUNCTIONS
# =========================================================


async def get_settings(chat_id: int):

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
        (chat_id,)
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            1,
            1,
            3,
            0,
            0,
            0,
            1,
        )
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


async def set_setting(
    chat_id: int,
    field: str,
    value: int
):

    if field not in DEFAULT_SETTINGS:
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
        )
    )

    db.commit()


async def get_warn_count(
    chat_id: int,
    user_id: int
):

    row = db.execute(
        """
        SELECT count
        FROM warns
        WHERE chat_id=? AND user_id=?
        """,
        (
            chat_id,
            user_id,
        )
    ).fetchone()

    return int(
        row[0]
    ) if row else 0


async def set_warn_count(
    chat_id: int,
    user_id: int,
    count: int
):

    if count <= 0:

        db.execute(
            """
            DELETE FROM warns
            WHERE chat_id=? AND user_id=?
            """,
            (
                chat_id,
                user_id,
            )
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
            )
        )

    db.commit()

# =========================================================
# AUTH FUNCTIONS
# =========================================================


async def is_local_auth(
    chat_id: int,
    user_id: int
):

    row = db.execute(
        """
        SELECT 1
        FROM local_auth
        WHERE chat_id=? AND user_id=?
        """,
        (
            chat_id,
            user_id,
        )
    ).fetchone()

    return bool(row)


async def is_global_auth(
    user_id: int
):

    if user_id == OWNER_ID:
        return True

    row = db.execute(
        """
        SELECT 1
        FROM global_auth
        WHERE user_id=?
        """,
        (user_id,)
    ).fetchone()

    return bool(row)


async def add_local_auth(
    chat_id: int,
    user_id: int
):

    db.execute(
        """
        INSERT OR IGNORE INTO local_auth
        (chat_id, user_id)
        VALUES (?, ?)
        """,
        (
            chat_id,
            user_id,
        )
    )

    db.commit()


async def remove_local_auth(
    chat_id: int,
    user_id: int
):

    db.execute(
        """
        DELETE FROM local_auth
        WHERE chat_id=? AND user_id=?
        """,
        (
            chat_id,
            user_id,
        )
    )

    db.commit()


async def add_global_auth(
    user_id: int
):

    db.execute(
        """
        INSERT OR IGNORE INTO global_auth
        (user_id)
        VALUES (?)
        """,
        (user_id,)
    )

    db.commit()


async def remove_global_auth(
    user_id: int
):

    db.execute(
        """
        DELETE FROM global_auth
        WHERE user_id=?
        """,
        (user_id,)
    )

    db.commit()

# =========================================================
# PERMISSION FUNCTIONS
# =========================================================


async def is_admin(
    update: Update,
    user_id: int = None
):

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

        member = await chat.get_member(
            uid
        )

        return member.status in (
            "administrator",
            "creator",
        )

    except Exception:

        return False


async def can_manage(
    update: Update,
    user_id: int = None
):

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

    return await is_admin(
        update,
        uid
    )

# =========================================================
# LOGGING
# =========================================================


async def log_action(
    context: ContextTypes.DEFAULT_TYPE,
    text: str
):

    if not LOG_CHAT:
        return

    try:

        await context.bot.send_message(
            chat_id=LOG_CHAT,
            text=text
        )

    except Exception:

        pass

# =========================================================
# HELP HOME
# =========================================================


HELP_HOME = """
📖 <b>Hᴇʟᴘ & Cᴏᴍᴍᴀɴᴅs ~</b>

◇ <b>Cʜᴏᴏsᴇ A Cᴀᴛᴇɢᴏʀʏ Bᴇʟᴏᴡ Tᴏ Vɪᴇᴡ
Iᴛs Cᴏᴍᴍᴀɴᴅs.</b>

⚡ <b>Aᴅᴅ Mᴇ Iɴ Yᴏᴜʀ Gʀᴏᴜᴘ Aɴᴅ Gɪᴠᴇ
Mᴇ "Dᴇʟᴇᴛᴇ Mᴇssᴀɢᴇs" Pᴇʀᴍɪssɪᴏɴ.</b>

✨ <b>Kᴇᴇᴘ Yᴏᴜʀ Gʀᴏᴜᴘ Cʟᴇᴀɴ Aɴᴅ Sᴀғᴇ.</b>
"""

# =========================================================
# HELP PAGES
# =========================================================


HELP_PAGES = {

    0: HELP_HOME,

    1: """
🔐 <b>Lᴏᴄᴀʟ Aᴜᴛʜ</b>

🔐 /auth
Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ Tᴏ Aᴜᴛʜᴏʀɪᴢᴇ.

🔓 /unauth
Rᴇᴍᴏᴠᴇ Lᴏᴄᴀʟ Aᴜᴛʜ.

📋 /authlist
Sʜᴏᴡ Lᴏᴄᴀʟ Aᴜᴛʜ Uѕᴇʀѕ.

✦ Aᴜᴛʜ Uѕᴇʀѕ Aʀᴇ Eᴍᴘᴛ Fʀᴏᴍ
Aɴᴛɪ-Lɪɴᴋ & Aɴᴛɪ-Sᴘᴀᴍ.
""",

    2: """
🌐 <b>Gʟᴏʙᴀʟ Aᴜᴛʜ</b>

🌐 /gauth
Aᴅᴅ Gʟᴏʙᴀʟ Aᴜᴛʜ.

🔓 /gunauth
Rᴇᴍᴏᴠᴇ Gʟᴏʙᴀʟ Aᴜᴛʜ.

👑 Oɴʟʏ Bᴏᴛ Oᴡɴᴇʀ
Cᴀɴ Mᴀɴᴀɢᴇ Gʟᴏʙᴀʟ Aᴜᴛʜ.
""",

    3: """
📝 <b>Eᴅɪᴛ Dᴇʟᴇᴛᴇ</b>

⚡ /editdelete on
Eɴᴀʙʟᴇ Eᴅɪᴛ Dᴇʟᴇᴛᴇ.

⚡ /editdelete off
Dɪѕᴀʙʟᴇ Eᴅɪᴛ Dᴇʟᴇᴛᴇ.

✦ Dᴇʟᴇᴛᴇs Mᴇssᴀɢᴇs
Wʜᴇɴ A Uѕᴇʀ Eᴅɪᴛѕ A Mᴇssᴀɢᴇ.
""",

    4: """
🔗 <b>Bɪᴏ Lɪɴᴋ</b>

🔗 /biolink on
Eɴᴀʙʟᴇ Bɪᴏ Lɪɴᴋ Fɪʟᴛᴇʀ.

🔗 /biolink off
Dɪѕᴀʙʟᴇ Bɪᴏ Lɪɴᴋ Fɪʟᴛᴇʀ.

✦ Kᴇᴇᴘ Yᴏᴜʀ Gʀᴏᴜᴘ Cʟᴇᴀɴ
Fʀᴏᴍ Uɴᴡᴀɴᴛᴇᴅ Lɪɴᴋѕ.
""",

    5: """
🎬 <b>Mᴇᴅɪᴀ Dᴇʟ</b>

🎬 /mediadel on
Eɴᴀʙʟᴇ Mᴇᴅɪᴀ Dᴇʟᴇᴛᴇ.

🎬 /mediadel off
Dɪѕᴀʙʟᴇ Mᴇᴅɪᴀ Dᴇʟᴇᴛᴇ.

🗑️ /purge 20
Dᴇʟᴇᴛᴇ Rᴇᴄᴇɴᴛ Mᴇssᴀɢᴇs.
""",

    6: """
🚫 <b>Nᴏ Aʙᴜsᴇ</b>

🚫 /abuse on
Eɴᴀʙʟᴇ Aʙᴜѕᴇ Fɪʟᴛᴇʀ.

🚫 /abuse off
Dɪѕᴀʙʟᴇ Aʙᴜѕᴇ Fɪʟᴛᴇʀ.

⚠️ /warn
Wᴀʀɴ A Uѕᴇʀ.

📊 /warnings
Cʜᴇᴄᴋ Wᴀʀɴɪɴɢs.

♻️ /unwarn
Rᴇѕᴇᴛ Wᴀʀɴɪɴɢs.
""",

    7: """
🔗 <b>Lɪɴᴋ Fɪʟᴛᴇʀ</b>

🔗 /antilink on
Eɴᴀʙʟᴇ Aɴᴛɪ-Lɪɴᴋ.

🔗 /antilink off
Dɪѕᴀʙʟᴇ Aɴᴛɪ-Lɪɴᴋ.

🌊 /antispam on
Eɴᴀʙʟᴇ Aɴᴛɪ-Sᴘᴀᴍ.

🌊 /antispam off
Dɪѕᴀʙʟᴇ Aɴᴛɪ-Sᴘᴀᴍ.
""",

    8: """
🛡️ <b>Aᴅᴍɪɴ Cᴏᴍᴍᴀɴᴅs</b>

🚫 /ban
Bᴀɴ A Rᴇᴘʟɪᴇᴅ Uѕᴇʀ.

🔓 /unban USER_ID
Uɴʙᴀɴ Uѕᴇʀ.

👢 /kick
Kɪᴄᴋ A Rᴇᴘʟɪᴇᴅ Uѕᴇʀ.

🔇 /mute [minutes]
Mᴜᴛᴇ Uѕᴇʀ.

🔊 /unmute
Uɴᴍᴜᴛᴇ Uѕᴇʀ.

🗑️ /purge [count]
Dᴇʟᴇᴛᴇ Mᴇssᴀɢᴇs.
""",

    9: """
⚙️ <b>Oᴛʜᴇʀ Cᴏᴍᴍᴀɴᴅs</b>

/start
/hᴇlp
/settings

⚡ /antilink on|off
⚡ /antispam on|off
⚡ /editdelete on|off
⚡ /biolink on|off
⚡ /mediadel on|off
⚡ /abuse on|off
"""
}

# =========================================================
# HELP BUTTONS
# =========================================================


def help_keyboard(
    page=0
):

    if page == 0:

        return InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "Lᴏᴄᴀʟ Aᴜᴛʜ",
                    callback_data="help_page_1"
                ),

                InlineKeyboardButton(
                    "Gʟᴏʙᴀʟ Aᴜᴛʜ",
                    callback_data="help_page_2"
                )
            ],

            [
                InlineKeyboardButton(
                    "Eᴅɪᴛ Dᴇʟᴇᴛᴇ",
                    callback_data="help_page_3"
                ),

                InlineKeyboardButton(
                    "Bɪᴏ Lɪɴᴋ",
                    callback_data="help_page_4"
                )
            ],

            [
                InlineKeyboardButton(
                    "Mᴇᴅɪᴀ Dᴇʟ",
                    callback_data="help_page_5"
                ),

                InlineKeyboardButton(
                    "Nᴏ Aʙᴜsᴇ",
                    callback_data="help_page_6"
                )
            ],

            [
                InlineKeyboardButton(
                    "Bᴀᴄᴋ ↩",
                    callback_data="help_page_0"
                ),

                InlineKeyboardButton(
                    "• Hᴏᴍᴇ •",
                    callback_data="help_page_0"
                ),

                InlineKeyboardButton(
                    "Nᴇxᴛ",
                    callback_data="help_page_1"
                )
            ]
        ])

    previous_page = page - 1

    if previous_page < 0:
        previous_page = 0

    next_page = page + 1

    if next_page > 9:
        next_page = 0

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "Bᴀᴄᴋ ↩",
                callback_data=f"help_page_{previous_page}"
            ),

            InlineKeyboardButton(
                "• Hᴏᴍᴇ •",
                callback_data="help_page_0"
            ),

            InlineKeyboardButton(
                "Nᴇxᴛ",
                callback_data=f"help_page_{next_page}"
            )
        ]
    ])

# =========================================================
# SEND HELP
# =========================================================


async def send_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page=0
):

    text = HELP_PAGES.get(
        page,
        HELP_HOME
    )

    keyboard = help_keyboard(
        page
    )

    # -----------------------------------------------------
    # NORMAL MESSAGE
    # -----------------------------------------------------

    if update.message:

        if HELP_IMAGE:

            try:

                await update.message.reply_photo(
                    photo=HELP_IMAGE,
                    caption=text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML
                )

                return

            except Exception as e:

                log.warning(
                    "HELP_IMAGE failed: %s",
                    e
                )

        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

        return

    # -----------------------------------------------------
    # CALLBACK MESSAGE
    # -----------------------------------------------------

    query = update.callback_query

    if not query:
        return

    try:

        await query.edit_message_caption(
            caption=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

    except Exception:

        try:

            await query.edit_message_text(
                text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )

        except Exception as e:

            log.warning(
                "Help edit failed: %s",
                e
            )

# =========================================================
# START
# =========================================================


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # /start = EXACT HELP MENU

    await send_help(
        update,
        context,
        0
    )

# =========================================================
# HELP
# =========================================================


async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_help(
        update,
        context,
        0
    )

# =========================================================
# HELP CALLBACK
# =========================================================


async def help_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    try:

        page = int(
            query.data.replace(
                "help_page_",
                ""
            )
        )

    except Exception:

        page = 0

    page = max(
        0,
        min(
            page,
            9
        )
    )

    await send_help(
        update,
        context,
        page
    )

# =========================================================
# GENERIC TOGGLE
# =========================================================


async def toggle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    field: str,
    name: str
):

    if not await can_manage(
        update
    ):
        return

    if not context.args:

        await update.message.reply_text(
            f"Uѕᴀɢᴇ: /{name} on|off"
        )

        return

    option = (
        context.args[0]
        .lower()
    )

    if option not in (
        "on",
        "off"
    ):

        await update.message.reply_text(
            f"Uѕᴀɢᴇ: /{name} on|off"
        )

        return

    value = (
        1
        if option == "on"
        else 0
    )

    await set_setting(
        update.effective_chat.id,
        field,
        value
    )

    status = (
        "Eɴᴀʙʟᴇᴅ"
        if value
        else "Dɪѕᴀʙʟᴇᴅ"
    )

    await update.message.reply_text(
        f"✅ {name.upper()} <b>{status}</b>",
        parse_mode=ParseMode.HTML
    )

# =========================================================
# TOGGLE COMMANDS
# =========================================================


async def antilink(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await toggle(
        update,
        context,
        "links",
        "antilink"
    )


async def antispam(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await toggle(
        update,
        context,
        "flood",
        "antispam"
    )


async def editdelete(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await toggle(
        update,
        context,
        "edit_delete",
        "editdelete"
    )


async def biolink(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await toggle(
        update,
        context,
        "bio_link",
        "biolink"
    )


async def mediadel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await toggle(
        update,
        context,
        "media_delete",
        "mediadel"
    )


async def abuse(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await toggle(
        update,
        context,
        "abuse",
        "abuse"
    )

# =========================================================
# SETTINGS
# =========================================================


async def settings_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    s = await get_settings(
        update.effective_chat.id
    )

    await update.message.reply_text(
        f"""
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
""",
        parse_mode=ParseMode.HTML
    )

# =========================================================
# TARGET
# =========================================================


async def get_target(
    update: Update
):

    if (
        update.message
        and update.message.reply_to_message
        and update.message.reply_to_message.from_user
    ):

        return (
            update.message
            .reply_to_message
            .from_user
        )

    return None

# =========================================================
# WARN
# =========================================================


async def warn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    user = await get_target(
        update
    )

    if not user:

        await update.message.reply_text(
            "⚠️ Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ'ѕ Mᴇѕѕᴀɢᴇ."
        )

        return

    if await is_admin(
        update,
        user.id
    ):

        await update.message.reply_text(
            "❌ Aᴅᴍɪɴ Cᴀɴɴᴏᴛ Bᴇ Wᴀʀɴᴇᴅ."
        )

        return

    chat_id = update.effective_chat.id

    count = (
        await get_warn_count(
            chat_id,
            user.id
        )
        + 1
    )

    await set_warn_count(
        chat_id,
        user.id,
        count
    )

    max_warns = (
        await get_settings(
            chat_id
        )
    )[2]

    await update.message.reply_text(
        f"""
⚠️ {user.mention_html()} <b>Wᴀʀɴᴇᴅ</b>

📊 Wᴀʀɴɪɴɢs:
<b>{count}/{max_warns}</b>
""",
        parse_mode=ParseMode.HTML
    )

    if count >= max_warns:

        try:

            await update.effective_chat.ban_member(
                user.id
            )

            await set_warn_count(
                chat_id,
                user.id,
                0
            )

            await update.message.reply_text(
                f"🚫 {user.mention_html()} "
                f"<b>Bᴀɴɴᴇᴅ Aғᴛᴇʀ Wᴀʀɴɪɴɢs</b>.",
                parse_mode=ParseMode.HTML
            )

        except Exception as e:

            log.warning(
                "Auto-ban failed: %s",
                e
            )

# =========================================================
# WARNINGS
# =========================================================


async def warnings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = (
        await get_target(update)
        or update.effective_user
    )

    count = await get_warn_count(
        update.effective_chat.id,
        user.id
    )

    await update.message.reply_text(
        f"""
⚠️ <b>Wᴀʀɴɪɴɢs</b>

👤 {user.mention_html()}
📊 Cᴏᴜɴᴛ: <b>{count}</b>
""",
        parse_mode=ParseMode.HTML
    )

# =========================================================
# UNWARN
# =========================================================


async def unwarn(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    user = await get_target(
        update
    )

    if not user:

        await update.message.reply_text(
            "⚠️ Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ."
        )

        return

    await set_warn_count(
        update.effective_chat.id,
        user.id,
        0
    )

    await update.message.reply_text(
        f"✅ {user.mention_html()} "
        f"<b>Wᴀʀɴɪɴɢs Rᴇѕᴇᴛ</b>.",
        parse_mode=ParseMode.HTML
    )

# =========================================================
# BAN
# =========================================================


async def ban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    user = await get_target(
        update
    )

    if not user:

        await update.message.reply_text(
            "⚠️ Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ."
        )

        return

    if await is_admin(
        update,
        user.id
    ):

        await update.message.reply_text(
            "❌ Cᴀɴɴᴏᴛ Bᴀɴ Aɴ Aᴅᴍɪɴ."
        )

        return

    try:

        await update.effective_chat.ban_member(
            user.id
        )

        await update.message.reply_text(
            f"🚫 {user.mention_html()} "
            f"<b>Bᴀɴɴᴇᴅ</b>.",
            parse_mode=ParseMode.HTML
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ {e}"
        )

# =========================================================
# UNBAN
# =========================================================


async def unban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    if not context.args:

        await update.message.reply_text(
            "Uѕᴀɢᴇ: /unban USER_ID"
        )

        return

    try:

        user_id = int(
            context.args[0]
        )

        await update.effective_chat.unban_member(
            user_id
        )

        await update.message.reply_text(
            "✅ Uѕᴇʀ Uɴʙᴀɴɴᴇᴅ."
        )

    except ValueError:

        await update.message.reply_text(
            "❌ Iɴᴠᴀʟɪᴅ USER_ID."
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ {e}"
        )

# =========================================================
# KICK
# =========================================================


async def kick(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    user = await get_target(
        update
    )

    if not user:

        await update.message.reply_text(
            "⚠️ Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ."
        )

        return

    if await is_admin(
        update,
        user.id
    ):

        await update.message.reply_text(
            "❌ Cᴀɴɴᴏᴛ Kɪᴄᴋ Aɴ Aᴅᴍɪɴ."
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
            parse_mode=ParseMode.HTML
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ {e}"
        )

# =========================================================
# MUTE
# =========================================================


async def mute(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    user = await get_target(
        update
    )

    if not user:

        await update.message.reply_text(
            "⚠️ Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ."
        )

        return

    if await is_admin(
        update,
        user.id
    ):

        await update.message.reply_text(
            "❌ Cᴀɴɴᴏᴛ Mᴜᴛᴇ Aɴ Aᴅᴍɪɴ."
        )

        return

    minutes = 10

    if context.args:

        try:

            minutes = max(
                1,
                min(
                    int(
                        context.args[0]
                    ),
                    10080
                )
            )

        except ValueError:

            pass

    try:

        await update.effective_chat.restrict_member(
            user.id,
            ChatPermissions(
                can_send_messages=False
            ),
            until_date=(
                int(time.time())
                + minutes * 60
            )
        )

        await update.message.reply_text(
            f"""
🔇 {user.mention_html()} <b>Mᴜᴛᴇᴅ</b>

⏱️ Tɪᴍᴇ:
<b>{minutes} Mɪɴᴜᴛᴇs</b>
""",
            parse_mode=ParseMode.HTML
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ {e}"
        )

# =========================================================
# UNMUTE
# =========================================================


async def unmute(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    user = await get_target(
        update
    )

    if not user:

        await update.message.reply_text(
            "⚠️ Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ."
        )

        return

    try:

        await update.effective_chat.restrict_member(
            user.id,
            ChatPermissions(
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
                can_invite_users=True,
            )
        )

        await update.message.reply_text(
            f"🔊 {user.mention_html()} "
            f"<b>Uɴᴍᴜᴛᴇᴅ</b>.",
            parse_mode=ParseMode.HTML
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ {e}"
        )

# =========================================================
# PURGE
# =========================================================


async def purge(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    count = 10

    if context.args:

        try:

            count = max(
                1,
                min(
                    int(
                        context.args[0]
                    ),
                    100
                )
            )

        except ValueError:

            await update.message.reply_text(
                "Uѕᴀɢᴇ: /purge 20"
            )

            return

    chat_id = update.effective_chat.id
    start_id = update.message.message_id

    deleted = 0

    for message_id in range(
        start_id,
        max(
            0,
            start_id - count
        ),
        -1
    ):

        try:

            await context.bot.delete_message(
                chat_id,
                message_id
            )

            deleted += 1

        except Exception:

            pass

    try:

        msg = await context.bot.send_message(
            chat_id,
            f"🗑️ <b>Pᴜʀɢᴇ Cᴏᴍᴘʟᴇᴛᴇ</b>\n"
            f"Dᴇʟᴇᴛᴇᴅ: <b>{deleted}</b>",
            parse_mode=ParseMode.HTML
        )

        await asyncio.sleep(3)

        await context.bot.delete_message(
            chat_id,
            msg.message_id
        )

    except Exception:

        pass

# =========================================================
# LOCAL AUTH
# =========================================================


async def auth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    user = await get_target(
        update
    )

    if not user:

        await update.message.reply_text(
            "⚠️ Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ."
        )

        return

    await add_local_auth(
        update.effective_chat.id,
        user.id
    )

    await update.message.reply_text(
        f"🔐 {user.mention_html()} "
        f"<b>Aᴜᴛʜᴏʀɪᴢᴇᴅ</b>.",
        parse_mode=ParseMode.HTML
    )


async def unauth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    user = await get_target(
        update
    )

    if not user:

        await update.message.reply_text(
            "⚠️ Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ."
        )

        return

    await remove_local_auth(
        update.effective_chat.id,
        user.id
    )

    await update.message.reply_text(
        f"🔓 {user.mention_html()} "
        f"<b>Uɴᴀᴜᴛʜᴏʀɪᴢᴇᴅ</b>.",
        parse_mode=ParseMode.HTML
    )


async def authlist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(
        update
    ):
        return

    rows = db.execute(
        """
        SELECT user_id
        FROM local_auth
        WHERE chat_id=?
        """,
        (
            update.effective_chat.id,
        )
    ).fetchall()

    if not rows:

        await update.message.reply_text(
            "📋 Lᴏᴄᴀʟ Aᴜᴛʜ Lɪѕᴛ Iѕ Eᴍᴘᴛʏ."
        )

        return

    text = (
        "🔐 <b>Lᴏᴄᴀʟ Aᴜᴛʜ</b>\n\n"
    )

    for index, row in enumerate(
        rows,
        1
    ):

        text += (
            f"▸ {index}. "
            f"<code>{row[0]}</code>\n"
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML
    )

# =========================================================
# GLOBAL AUTH
# =========================================================


async def gauth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if (
        not update.effective_user
        or update.effective_user.id != OWNER_ID
    ):
        return

    user = await get_target(
        update
    )

    if not user:

        await update.message.reply_text(
            "⚠️ Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ."
        )

        return

    await add_global_auth(
        user.id
    )

    await update.message.reply_text(
        f"🌐 {user.mention_html()} "
        f"<b>Gʟᴏʙᴀʟ Aᴜᴛʜ Aᴅᴅᴇᴅ</b>.",
        parse_mode=ParseMode.HTML
    )


async def gunauth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if (
        not update.effective_user
        or update.effective_user.id != OWNER_ID
    ):
        return

    user = await get_target(
        update
    )

    if not user:

        await update.message.reply_text(
            "⚠️ Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ."
        )

        return

    await remove_global_auth(
        user.id
    )

    await update.message.reply_text(
        f"🌐 {user.mention_html()} "
        f"<b>Gʟᴏʙᴀʟ Aᴜᴛʜ Rᴇᴍᴏᴠᴇᴅ</b>.",
        parse_mode=ParseMode.HTML
    )

# =========================================================
# MEDIA FILTER
# =========================================================


def is_media_message(message):

    return any([
        bool(message.photo),
        bool(message.video),
        bool(message.animation),
        bool(message.document),
        bool(message.audio),
        bool(message.voice),
        bool(message.video_note),
        bool(message.sticker),
    ])

# =========================================================
# ABUSE FILTER
# =========================================================


def contains_abuse(text):

    if not text:
        return False

    words = re.findall(
        r"[a-zA-Z]+",
        text.lower()
    )

    return any(
        word in ABUSE_WORDS
        for word in words
    )

# =========================================================
# MESSAGE PROTECTION
# =========================================================


async def protection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.effective_message

    if not message:
        return

    chat = update.effective_chat
    user = update.effective_user

    if not chat or not user:
        return

    if chat.type not in (
        "group",
        "supergroup"
    ):
        return

    # -----------------------------------------------------
    # ADMIN BYPASS
    # -----------------------------------------------------

    if await is_admin(
        update,
        user.id
    ):
        return

    # -----------------------------------------------------
    # LOCAL AUTH BYPASS
    # -----------------------------------------------------

    if await is_local_auth(
        chat.id,
        user.id
    ):
        return

    # -----------------------------------------------------
    # GLOBAL AUTH BYPASS
    # -----------------------------------------------------

    if await is_global_auth(
        user.id
    ):
        return

    settings = await get_settings(
        chat.id
    )

    (
        links,
        flood,
        max_warns,
        edit_delete,
        bio_link,
        media_delete,
        abuse_enabled,
    ) = settings

    # -----------------------------------------------------
    # LINK FILTER
    # -----------------------------------------------------

    if links:

        text = (
            message.text
            or message.caption
            or ""
        )

        if LINK_RE.search(
            text
        ):

            try:

                await message.delete()

                await log_action(
                    context,
                    (
                        "🔗 LINK DELETED\n"
                        f"CHAT: {chat.id}\n"
                        f"USER: {user.id}"
                    )
                )

            except Exception as e:

                log.warning(
                    "Link delete failed: %s",
                    e
                )

            return

    # -----------------------------------------------------
    # MEDIA DELETE
    # -----------------------------------------------------

    if media_delete:

        if is_media_message(
            message
        ):

            try:

                await message.delete()

            except Exception:

                pass

            return

    # -----------------------------------------------------
    # ABUSE FILTER
    # -----------------------------------------------------

    if abuse_enabled:

        text = (
            message.text
            or message.caption
            or ""
        )

        if contains_abuse(
            text
        ):

            try:

                await message.delete()

                count = (
                    await get_warn_count(
                        chat.id,
                        user.id
                    )
                    + 1
                )

                await set_warn_count(
                    chat.id,
                    user.id,
                    count
                )

                if count >= max_warns:

                    await chat.ban_member(
                        user.id
                    )

                    await set_warn_count(
                        chat.id,
                        user.id,
                        0
                    )

                else:

                    try:

                        warn_msg = await context.bot.send_message(
                            chat.id,
                            (
                                f"⚠️ {user.mention_html()} "
                                f"<b>Wᴀʀɴᴇᴅ</b>\n"
                                f"📊 {count}/{max_warns}"
                            ),
                            parse_mode=ParseMode.HTML
                        )

                        await asyncio.sleep(3)

                        await warn_msg.delete()

                    except Exception:

                        pass

            except Exception as e:

                log.warning(
                    "Abuse action failed: %s",
                    e
                )

            return

    # -----------------------------------------------------
    # ANTI SPAM
    # -----------------------------------------------------

    if flood:

        now = time.time()

        queue = flood_cache[
            chat.id
        ][user.id]

        queue.append(
            now
        )

        while queue and (
            now - queue[0] > 8
        ):

            queue.popleft()

        if len(queue) >= 6:

            try:

                await chat.restrict_member(
                    user.id,
                    ChatPermissions(
                        can_send_messages=False
                    ),
                    until_date=(
                        int(time.time())
                        + 60
                    )
                )

                await message.delete()

                queue.clear()

                await log_action(
                    context,
                    (
                        "🌊 SPAM USER MUTED\n"
                        f"CHAT: {chat.id}\n"
                        f"USER: {user.id}"
                    )
                )

            except Exception as e:

                log.warning(
                    "Spam action failed: %s",
                    e
                )

# =========================================================
# ERROR HANDLER
# =========================================================


async def error_handler(
    update,
    context: ContextTypes.DEFAULT_TYPE
):

    log.error(
        "Unhandled error: %s",
        context.error
    )

# =========================================================
# MAIN
# =========================================================


def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    # =====================================================
    # START / HELP
    # =====================================================

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_cmd
        )
    )

    app.add_handler(
        CommandHandler(
            "settings",
            settings_cmd
        )
    )

    # =====================================================
    # PROTECTION
    # =====================================================

    app.add_handler(
        CommandHandler(
            "antilink",
            antilink
        )
    )

    app.add_handler(
        CommandHandler(
            "antispam",
            antispam
        )
    )

    app.add_handler(
        CommandHandler(
            "editdelete",
            editdelete
        )
    )

    app.add_handler(
        CommandHandler(
            "biolink",
            biolink
        )
    )

    app.add_handler(
        CommandHandler(
            "mediadel",
            mediadel
        )
    )

    app.add_handler(
        CommandHandler(
            "abuse",
            abuse
        )
    )

    # =====================================================
    # MODERATION
    # =====================================================

    app.add_handler(
        CommandHandler(
            "warn",
            warn
        )
    )

    app.add_handler(
        CommandHandler(
            "warnings",
            warnings
        )
    )

    app.add_handler(
        CommandHandler(
            "unwarn",
            unwarn
        )
    )

    app.add_handler(
        CommandHandler(
            "ban",
            ban
        )
    )

    app.add_handler(
        CommandHandler(
            "unban",
            unban
        )
    )

    app.add_handler(
        CommandHandler(
            "kick",
            kick
        )
    )

    app.add_handler(
        CommandHandler(
            "mute",
            mute
        )
    )

    app.add_handler(
        CommandHandler(
            "unmute",
            unmute
        )
    )

    app.add_handler(
        CommandHandler(
            "purge",
            purge
        )
    )

    # =====================================================
    # LOCAL AUTH
    # =====================================================

    app.add_handler(
        CommandHandler(
            "auth",
            auth
        )
    )

    app.add_handler(
        CommandHandler(
            "unauth",
            unauth
        )
    )

    app.add_handler(
        CommandHandler(
            "authlist",
            authlist
        )
    )

    # =====================================================
    # GLOBAL AUTH
    # =====================================================

    app.add_handler(
        CommandHandler(
            "gauth",
            gauth
        )
    )

    app.add_handler(
        CommandHandler(
            "gunauth",
            gunauth
        )
    )

    # =====================================================
    # HELP BUTTON CALLBACK
    # =====================================================

    app.add_handler(
        CallbackQueryHandler(
            help_callback,
            pattern=r"^help_page_"
        )
    )

    # =====================================================
    # GROUP PROTECTION
    # =====================================================

    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            protection
        )
    )

    # =====================================================
    # ERROR
    # =====================================================

    app.add_error_handler(
        error_handler
    )

    log.info(
        "🛡️ Sʜɪᴇʟᴅ Gᴜᴀʀᴅ Bᴏᴛ Sᴛᴀʀᴛᴇᴅ."
    )

    # =====================================================
    # RUN
    # =====================================================

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


# =========================================================
# START BOT
# =========================================================

if __name__ == "__main__":
    main()
