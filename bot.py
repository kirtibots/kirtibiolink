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
# 🛡️ SHIELD GUARD
# =========================================================

TOKEN = os.getenv("BOT_TOKEN", "").strip()

OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)

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
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("shield_guard")

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
    max_warns INTEGER DEFAULT 3
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
# DEFAULT SETTINGS
# =========================================================

DEFAULT_SETTINGS = {
    "links": 1,
    "flood": 1,
    "max_warns": 3,
}

# =========================================================
# REGEX / CACHE
# =========================================================

LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/)",
    re.IGNORECASE
)

flood_cache = defaultdict(
    lambda: defaultdict(deque)
)

# =========================================================
# DATABASE FUNCTIONS
# =========================================================


async def get_settings(chat_id: int):

    row = db.execute(
        """
        SELECT links, flood, max_warns
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
        (chat_id, links, flood, max_warns)
        VALUES (?, ?, ?, ?)
        """,
        (
            chat_id,
            DEFAULT_SETTINGS["links"],
            DEFAULT_SETTINGS["flood"],
            DEFAULT_SETTINGS["max_warns"],
        )
    )

    db.commit()

    return (
        DEFAULT_SETTINGS["links"],
        DEFAULT_SETTINGS["flood"],
        DEFAULT_SETTINGS["max_warns"],
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

    return int(row[0]) if row else 0


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


async def is_global_auth(user_id: int):

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


async def add_global_auth(user_id: int):

    db.execute(
        """
        INSERT OR IGNORE INTO global_auth
        (user_id)
        VALUES (?)
        """,
        (user_id,)
    )

    db.commit()


async def remove_global_auth(user_id: int):

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

        member = await chat.get_member(uid)

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
# START
# =========================================================


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    user = update.effective_user

    try:

        me = await context.bot.get_me()

        bot_username = me.username or ""

    except Exception:

        bot_username = ""

    text = f"""
• Hᴇʟʟᴏ, {user.mention_html()} 🇨🇦 ✨

╭━━━━━━━━━━━━━━━━━━━━━━╮
   ✦ Wᴇʟᴄᴏᴍᴇ Tᴏ
      <b>Sʜɪᴇʟᴅ Gᴜᴀʀᴅ</b>
   Pʀᴇᴍɪᴜᴍ Gʀᴏᴜᴘ Pʀᴏᴛᴇᴄᴛɪᴏɴ
╰━━━━━━━━━━━━━━━━━━━━━━╯

⚡ Bʟᴀᴢɪɴɢ Fᴀsᴛ Pʀᴏᴛᴇᴄᴛɪᴏɴ
🛡️ Aᴜᴛᴏ Mᴏᴅᴇʀᴀᴛɪᴏɴ
🔗 Aɴᴛɪ-Lɪɴᴋ Pʀᴏᴛᴇᴄᴛɪᴏɴ
🌊 Aɴᴛɪ-Sᴘᴀᴍ Pʀᴏᴛᴇᴄᴛɪᴏɴ
⚙️ Sᴍᴀʀᴛ Gʀᴏᴜᴘ Mᴏᴅᴇʀᴀᴛɪᴏɴ

» Aᴅᴅ Mᴇ Tᴏ Yᴏᴜʀ Gʀᴏᴜᴘ
» Gɪᴠᴇ Mᴇ <b>Dᴇʟᴇᴛᴇ Mᴇssᴀɢᴇs</b>
  Pᴇʀᴍɪssɪᴏɴ.

✦ Bᴜɪʟᴛ Fᴏʀ A Sᴀғᴇʀ Cᴏᴍᴍᴜɴɪᴛʏ.
"""

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✚ Aᴅᴅ Mᴇ Iɴ Yᴏᴜʀ Gʀᴏᴜᴘ ✚",
                url=(
                    f"https://t.me/{bot_username}"
                    "?startgroup=true"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "💬 Oᴡɴᴇʀ",
                url=f"https://t.me/{OWNER_USERNAME}"
            ),
            InlineKeyboardButton(
                "🧑‍💼 Sᴜᴘᴘᴏʀᴛ ↗",
                url=f"https://t.me/{SUPPORT_USERNAME}"
            )
        ],
        [
            InlineKeyboardButton(
                "📚 Bᴏᴛ Cᴏᴍᴍᴀɴᴅ Hᴇʟᴘ",
                callback_data="help_page_0"
            )
        ]
    ])

    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

# =========================================================
# HELP TEXT
# =========================================================


HELP_PAGES = {

    0: """
📚 <b>Bᴏᴛ Cᴏᴍᴍᴀɴᴅ Hᴇʟᴘ</b>

Hᴇʀᴇ Yᴏᴜ'ʟʟ Fɪɴᴅ Dᴇᴛᴀɪʟs
Fᴏʀ Aʟʟ Aᴠᴀɪʟᴀʙʟᴇ Pʟᴜɢɪɴs
Aɴᴅ Fᴇᴀᴛᴜʀᴇs.

📌 Tᴀᴘ Tʜᴇ Bᴜᴛᴛᴏɴs Bᴇʟᴏᴡ
Tᴏ Vɪᴇᴡ Hᴇʟᴘ Fᴏʀ Eᴀᴄʜ Mᴏᴅᴜʟᴇ:
""",

    1: """
🔐 <b>Lᴏᴄᴀʟ Aᴜᴛʜ</b>

🔐 /auth
Aᴜᴛʜᴏʀɪᴢᴇ Rᴇᴘʟɪᴇᴅ Uѕᴇʀ

🔓 /unauth
Rᴇᴍᴏᴠᴇ Lᴏᴄᴀʟ Aᴜᴛʜ

📋 /authlist
Sʜᴏᴡ Lᴏᴄᴀʟ Aᴜᴛʜ Uѕᴇʀѕ

✦ Oɴʟʏ Aᴅᴍɪɴѕ Cᴀɴ
Mᴀɴᴀɢᴇ Lᴏᴄᴀʟ Aᴜᴛʜ.
""",

    2: """
🛡️ <b>Bɪɢ-Mᴏᴅᴇ</b>

🌐 /gauth
Aᴅᴅ Gʟᴏʙᴀʟ Aᴜᴛʜ

🚫 /gunauth
Rᴇᴍᴏᴠᴇ Gʟᴏʙᴀʟ Aᴜᴛʜ

👑 Oᴡɴᴇʀ Iѕ Aʟᴡᴀʏѕ
Aᴜᴛʜᴏʀɪᴢᴇᴅ.
""",

    3: """
📝 <b>Eᴄʜᴏ</b>

⚙️ /settings

🔗 /antilink on
🔗 /antilink off

🌊 /antispam on
🌊 /antispam off

✦ Cᴏɴᴛʀᴏʟ Yᴏᴜʀ Gʀᴏᴜᴘ
Pʀᴏᴛᴇᴄᴛɪᴏɴ Fʀᴏᴍ Hᴇʀᴇ.
""",

    4: """
🔗 <b>Lɪɴᴋ-Fɪʟᴛᴇʀ</b>

Tʜᴇ Bᴏᴛ Dᴇᴛᴇᴄᴛs:

• https://
• http://
• www.
• t.me/
• telegram.me/

⚡ /antilink on
⚡ /antilink off

Gɪᴠᴇ Tʜᴇ Bᴏᴛ
Dᴇʟᴇᴛᴇ Mᴇssᴀɢᴇѕ
Pᴇʀᴍɪssɪᴏɴ.
""",

    5: """
💬 <b>Mѕɢ-Dᴇʟᴇᴛᴇ</b>

🗑️ /purge 20

⚠️ /warn
📊 /warnings
♻️ /unwarn

🚫 /ban
🔓 /unban USER_ID
👢 /kick

🔇 /mute [minutes]
🔊 /unmute
""",

    6: """
🎬 <b>Mᴇᴅɪᴀ-Dᴇʟ</b>

🗑️ /purge [count]

⚡ Aᴜᴛᴏ Mᴇᴅɪᴀ
Aɴᴅ Mᴇssᴀɢᴇ Mᴏᴅᴇʀᴀᴛɪᴏɴ

✦ Rᴇᴘʟʏ Tᴏ A Uѕᴇʀ
Fᴏʀ Mᴏᴅᴇʀᴀᴛɪᴏɴ.
""",

    7: """
🚫 <b>Aʙᴜsᴇ</b>

⚠️ /warn
📊 /warnings
♻️ /unwarn

Mᴀx Wᴀʀɴɪɴɢs:
<b>3</b>

🚫 Aғᴛᴇʀ Mᴀx Wᴀʀɴɪɴɢs
Tʜᴇ Uѕᴇʀ Cᴀɴ Bᴇ
Aᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ Bᴀɴɴᴇᴅ.
""",

    8: """
⚙️ <b>Oᴛʜᴇʀ Cᴏᴍᴍᴀɴᴅs</b>

/start
/help
/settings

🔗 /antilink on|off
🌊 /antispam on|off

✦ Kᴇᴇᴘ Yᴏᴜʀ Gʀᴏᴜᴘ
Cʟᴇᴀɴ Aɴᴅ Sᴀғᴇ.
"""
}

# =========================================================
# HELP KEYBOARD
# =========================================================


def help_keyboard(page=0):

    if page == 0:

        return InlineKeyboardMarkup([

            [
                InlineKeyboardButton(
                    "🚫 Aʙᴜsᴇ",
                    callback_data="help_page_7"
                ),
                InlineKeyboardButton(
                    "🛡️ Bɪɢ-Mᴏᴅᴇ",
                    callback_data="help_page_2"
                )
            ],

            [
                InlineKeyboardButton(
                    "📝 Eᴄʜᴏ",
                    callback_data="help_page_3"
                ),
                InlineKeyboardButton(
                    "🔗 Lɪɴᴋ-Fɪʟᴛᴇʀ",
                    callback_data="help_page_4"
                )
            ],

            [
                InlineKeyboardButton(
                    "💬 Mѕɢ-Dᴇʟᴇᴛᴇ",
                    callback_data="help_page_5"
                ),
                InlineKeyboardButton(
                    "🎬 Mᴇᴅɪᴀ-Dᴇʟ",
                    callback_data="help_page_6"
                )
            ],

            [
                InlineKeyboardButton(
                    "🏠 Hᴏᴍᴇ",
                    callback_data="help_page_0"
                ),
                InlineKeyboardButton(
                    "Nᴇxᴛ ▶️",
                    callback_data="help_page_1"
                )
            ]
        ])

    # -----------------------------------------------------
    # PAGE NAVIGATION
    # -----------------------------------------------------

    next_page = page + 1

    if next_page > 8:
        next_page = 0

    previous_page = page - 1

    if previous_page < 0:
        previous_page = 0

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "↩️ Bᴀᴄᴋ",
                callback_data=f"help_page_{previous_page}"
            ),
            InlineKeyboardButton(
                "🏠 Hᴏᴍᴇ",
                callback_data="help_page_0"
            ),
            InlineKeyboardButton(
                "Nᴇxᴛ ▶️",
                callback_data=f"help_page_{next_page}"
            )
        ]
    ])

# =========================================================
# HELP COMMAND
# =========================================================


async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.message:

        await update.message.reply_text(
            HELP_PAGES[0],
            reply_markup=help_keyboard(0),
            parse_mode=ParseMode.HTML
        )

    elif update.callback_query:

        await update.callback_query.edit_message_text(
            HELP_PAGES[0],
            reply_markup=help_keyboard(0),
            parse_mode=ParseMode.HTML
        )


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
            8
        )
    )

    await query.edit_message_text(
        HELP_PAGES[page],
        reply_markup=help_keyboard(page),
        parse_mode=ParseMode.HTML
    )

# =========================================================
# TOGGLE
# =========================================================


async def toggle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    field: str,
    name: str
):

    if not await can_manage(update):
        return

    if not context.args:

        await update.message.reply_text(
            f"Uѕᴀɢᴇ: /{name} on|off"
        )

        return

    option = context.args[0].lower()

    if option not in (
        "on",
        "off"
    ):

        await update.message.reply_text(
            f"Uѕᴀɢᴇ: /{name} on|off"
        )

        return

    value = 1 if option == "on" else 0

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
        f"✅ {name.upper()} <b>{status}</b>.",
        parse_mode=ParseMode.HTML
    )


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

# =========================================================
# SETTINGS
# =========================================================


async def settings_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(update):
        return

    settings = await get_settings(
        update.effective_chat.id
    )

    await update.message.reply_text(
        f"""
⚙️ <b>Gʀᴏᴜᴘ Sᴇᴛᴛɪɴɢs</b>

🔗 Aɴᴛɪ-Lɪɴᴋ:
<b>{'Oɴ' if settings[0] else 'Oғғ'}</b>

🌊 Aɴᴛɪ-Sᴘᴀᴍ:
<b>{'Oɴ' if settings[1] else 'Oғғ'}</b>

⚠️ Mᴀx Wᴀʀɴɪɴɢs:
<b>{settings[2]}</b>
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

    if not await can_manage(update):
        return

    user = await get_target(update)

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
                f"<b>Hᴀѕ Bᴇᴇɴ Bᴀɴɴᴇᴅ</b>.",
                parse_mode=ParseMode.HTML
            )

        except Exception as e:

            log.warning(
                "Auto ban failed: %s",
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
⚠️ <b>Wᴀʀɴɪɴɢѕ</b>

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

    if not await can_manage(update):
        return

    user = await get_target(update)

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
        f"<b>Wᴀʀɴɪɴɢѕ Rᴇѕᴇᴛ</b>.",
        parse_mode=ParseMode.HTML
    )

# =========================================================
# BAN
# =========================================================


async def ban(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(update):
        return

    user = await get_target(update)

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

    if not await can_manage(update):
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

    if not await can_manage(update):
        return

    user = await get_target(update)

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

    if not await can_manage(update):
        return

    user = await get_target(update)

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
                    int(context.args[0]),
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
            until_date=int(time.time())
            + minutes * 60
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

    if not await can_manage(update):
        return

    user = await get_target(update)

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

    if not await can_manage(update):
        return

    count = 10

    if context.args:

        try:

            count = max(
                1,
                min(
                    int(context.args[0]),
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

    if not await can_manage(update):
        return

    user = await get_target(update)

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

    if not await can_manage(update):
        return

    user = await get_target(update)

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
        f"<b>Rᴇᴍᴏᴠᴇᴅ Fʀᴏᴍ Aᴜᴛʜ</b>.",
        parse_mode=ParseMode.HTML
    )


async def authlist(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not await can_manage(update):
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

    text = """
🔐 <b>Lᴏᴄᴀʟ Aᴜᴛʜ Lɪѕᴛ</b>

"""

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

    if update.effective_user.id != OWNER_ID:
        return

    user = await get_target(update)

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

    if update.effective_user.id != OWNER_ID:
        return

    user = await get_target(update)

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

    # Admin bypass
    if await is_admin(
        update,
        user.id
    ):
        return

    # Local auth bypass
    if await is_local_auth(
        chat.id,
        user.id
    ):
        return

    # Global auth bypass
    if await is_global_auth(
        user.id
    ):
        return

    settings = await get_settings(
        chat.id
    )

    # -----------------------------------------------------
    # LINK FILTER
    # -----------------------------------------------------

    if settings[0]:

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
                    (
                        "🔗 Lɪɴᴋ Dᴇʟᴇᴛᴇᴅ\n"
                        f"Cʜᴀᴛ: {chat.id}\n"
                        f"Uѕᴇʀ: {user.id}"
                    )
                )

            except Exception as e:

                log.warning(
                    "Link delete failed: %s",
                    e
                )

            return

    # -----------------------------------------------------
    # ANTI SPAM
    # -----------------------------------------------------

    if settings[1]:

        now = time.time()

        queue = flood_cache[
            chat.id
        ][user.id]

        queue.append(now)

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
                    until_date=int(
                        time.time()
                    ) + 60
                )

                await message.delete()

                queue.clear()

                await log_action(
                    context,
                    (
                        "🌊 Sᴘᴀᴍ Uѕᴇʀ Mᴜᴛᴇᴅ\n"
                        f"Cʜᴀᴛ: {chat.id}\n"
                        f"Uѕᴇʀ: {user.id}"
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

    application = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    # -----------------------------------------------------
    # BASIC
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_cmd
        )
    )

    application.add_handler(
        CommandHandler(
            "settings",
            settings_cmd
        )
    )

    # -----------------------------------------------------
    # PROTECTION
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "antilink",
            antilink
        )
    )

    application.add_handler(
        CommandHandler(
            "antispam",
            antispam
        )
    )

    # -----------------------------------------------------
    # MODERATION
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "warn",
            warn
        )
    )

    application.add_handler(
        CommandHandler(
            "warnings",
            warnings
        )
    )

    application.add_handler(
        CommandHandler(
            "unwarn",
            unwarn
        )
    )

    application.add_handler(
        CommandHandler(
            "ban",
            ban
        )
    )

    application.add_handler(
        CommandHandler(
            "unban",
            unban
        )
    )

    application.add_handler(
        CommandHandler(
            "kick",
            kick
        )
    )

    application.add_handler(
        CommandHandler(
            "mute",
            mute
        )
    )

    application.add_handler(
        CommandHandler(
            "unmute",
            unmute
        )
    )

    application.add_handler(
        CommandHandler(
            "purge",
            purge
        )
    )

    # -----------------------------------------------------
    # LOCAL AUTH
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "auth",
            auth
        )
    )

    application.add_handler(
        CommandHandler(
            "unauth",
            unauth
        )
    )

    application.add_handler(
        CommandHandler(
            "authlist",
            authlist
        )
    )

    # -----------------------------------------------------
    # GLOBAL AUTH
    # -----------------------------------------------------

    application.add_handler(
        CommandHandler(
            "gauth",
            gauth
        )
    )

    application.add_handler(
        CommandHandler(
            "gunauth",
            gunauth
        )
    )

    # -----------------------------------------------------
    # HELP CALLBACK
    # -----------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            help_callback,
            pattern=r"^help_page_"
        )
    )

    # -----------------------------------------------------
    # MESSAGE PROTECTION
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            protection
        )
    )

    application.add_error_handler(
        error_handler
    )

    log.info(
        "🛡️ Sʜɪᴇʟᴅ Gᴜᴀʀᴅ Sᴛᴀʀᴛᴇᴅ."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
