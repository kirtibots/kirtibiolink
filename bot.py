import os
import re
import time
import logging
import sqlite3
from collections import defaultdict, deque

from telegram import (
    Update,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# CONFIG
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

LOG_CHAT = os.getenv("LOG_CHAT_ID", "").strip()

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

log = logging.getLogger("shieldbot")

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
    lambda: defaultdict(
        deque
    )
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

    if not row:
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

    return row


async def set_setting(
    chat_id: int,
    field: str,
    value: int
):
    if field not in DEFAULT_SETTINGS:
        return

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
# PERMISSION FUNCTIONS
# =========================================================


async def is_admin(
    update: Update,
    user_id: int = None
):
    if not update.effective_chat:
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
        member = await update.effective_chat.get_member(uid)

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

    text = f"""• HELLO, {user.mention_html()} 🇨🇦 ✨

✦ WELCOME TO <b>Shield Guard</b>
<b>PREMIUM GROUP PROTECTION</b>

╔════════════════════════════╗
⚡ <b>BLAZING FAST PROTECTION</b>
🛡️ <b>AUTO MODERATION</b>
🔗 <b>ANTI-LINK PROTECTION</b>
🌊 <b>ANTI-SPAM PROTECTION</b>
⚙️ <b>SMART GROUP MODERATION</b>
╚════════════════════════════╝

» ADD ME TO YOUR GROUP
» GIVE ME DELETE MESSAGES PERMISSION

• BUILT FOR A SAFER COMMUNITY."""

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✚ ADD ME IN YOUR GROUP ✚",
                url=(
                    f"https://t.me/{bot_username}"
                    "?startgroup=true"
                )
            )
        ],
        [
            InlineKeyboardButton(
                "💬 OWNER",
                url=f"https://t.me/{OWNER_USERNAME}"
            ),
            InlineKeyboardButton(
                "🧑‍💼 SUPPORT ↗",
                url=f"https://t.me/{SUPPORT_USERNAME}"
            ),
        ],
        [
            InlineKeyboardButton(
                "📚 HELP AND COMMANDS",
                callback_data="help_home"
            )
        ],
    ])

    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


# =========================================================
# HELP
# =========================================================

HELP = {
    "help_link":
        "🔗 <b>LINK DEL</b>\n\n"
        "/antilink on|off\n\n"
        "Deletes detected links from non-admin users.",

    "help_admin":
        "🛡️ <b>ADMIN COMMANDS</b>\n\n"
        "/warn\n"
        "/warnings\n"
        "/unwarn\n"
        "/ban\n"
        "/unban USER_ID\n"
        "/kick\n"
        "/mute [minutes]\n"
        "/unmute\n"
        "/purge [count]",

    "help_other":
        "⚙️ <b>OTHER</b>\n\n"
        "/start\n"
        "/help\n"
        "/settings\n"
        "/antispam on|off",
}


def help_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔗 LINK DEL",
                callback_data="help_link"
            ),
            InlineKeyboardButton(
                "🛡️ ADMIN",
                callback_data="help_admin"
            ),
        ],
        [
            InlineKeyboardButton(
                "⚙️ OTHER",
                callback_data="help_other"
            )
        ],
    ])


async def help_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = """📚 <b>HELP & COMMANDS</b>

◇ <b>CHOOSE A CATEGORY BELOW.</b>

⚡ ADD ME IN YOUR GROUP AND GIVE ME
<b>DELETE MESSAGES</b> PERMISSION.

✨ KEEP YOUR GROUP CLEAN & SAFE."""

    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=help_keyboard(),
            parse_mode="HTML",
        )

    elif update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=help_keyboard(),
            parse_mode="HTML",
        )


async def help_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    if query.data in HELP:

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "↩ BACK",
                    callback_data="help_back"
                ),
                InlineKeyboardButton(
                    "🏠 HOME",
                    callback_data="help_home"
                ),
            ]
        ])

        await query.edit_message_text(
            HELP[query.data],
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    elif query.data in (
        "help_home",
        "help_back"
    ):
        await help_cmd(
            update,
            context
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
        value
    )

    status = (
        "enabled"
        if value
        else "disabled"
    )

    await update.message.reply_text(
        f"✅ {name.title()} {status}."
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
        f"""⚙️ <b>SETTINGS</b>

🔗 Anti-link: {'ON' if settings[0] else 'OFF'}
🌊 Anti-spam: {'ON' if settings[1] else 'OFF'}
⚠️ Max warnings: {settings[2]}""",
        parse_mode="HTML"
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
    ):
        return update.message.reply_to_message.from_user

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
            "⚠️ Reply to a user's message."
        )
        return

    if await is_admin(
        update,
        user.id
    ):
        await update.message.reply_text(
            "❌ Admin cannot be warned."
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
        await get_settings(chat_id)
    )[2]

    await update.message.reply_text(
        f"⚠️ {user.mention_html()} warned. "
        f"({count}/{max_warns})",
        parse_mode="HTML"
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
                f"has been banned after "
                f"{max_warns} warnings.",
                parse_mode="HTML"
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
        f"⚠️ {user.full_name}: "
        f"{count} warning(s)."
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
            "⚠️ Reply to a user's message."
        )
        return

    await set_warn_count(
        update.effective_chat.id,
        user.id,
        0
    )

    await update.message.reply_text(
        "✅ Warnings reset."
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
            "⚠️ Reply to a user's message."
        )
        return

    if await is_admin(
        update,
        user.id
    ):
        await update.message.reply_text(
            "❌ Cannot ban an admin."
        )
        return

    try:
        await update.effective_chat.ban_member(
            user.id
        )

        await update.message.reply_text(
            f"🚫 Banned {user.mention_html()}.",
            parse_mode="HTML"
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
            "Usage: /unban USER_ID"
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
            "✅ User unbanned."
        )

    except ValueError:
        await update.message.reply_text(
            "❌ Invalid USER_ID."
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
            "⚠️ Reply to a user's message."
        )
        return

    if await is_admin(
        update,
        user.id
    ):
        await update.message.reply_text(
            "❌ Cannot kick an admin."
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
            f"👢 Kicked {user.mention_html()}.",
            parse_mode="HTML"
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
            "⚠️ Reply to a user's message."
        )
        return

    if await is_admin(
        update,
        user.id
    ):
        await update.message.reply_text(
            "❌ Cannot mute an admin."
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
            f"🔇 {user.mention_html()} muted "
            f"for {minutes} minutes.",
            parse_mode="HTML"
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
            "⚠️ Reply to a user's message."
        )
        return

    try:
        await update.effective_chat.restrict_member(
            user.id,
            ChatPermissions(
                can_send_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_send_polls=True,
                can_invite_users=True,
            )
        )

        await update.message.reply_text(
            f"🔊 {user.mention_html()} unmuted.",
            parse_mode="HTML"
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
            pass

    deleted = 0

    start_id = (
        update.message.message_id
    )

    for i in range(count):
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=start_id - i
            )

            deleted += 1

        except Exception:
            pass

    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🧹 Deleted {deleted} messages."
    )

    await context.application.create_task(
        delete_later(
            context,
            update.effective_chat.id,
            msg.message_id
        )
    )


async def delete_later(
    context,
    chat_id,
    message_id
):
    await context.application.create_task(
        _delete_after_delay(
            context,
            chat_id,
            message_id
        )
    )


async def _delete_after_delay(
    context,
    chat_id,
    message_id
):
    await __import__("asyncio").sleep(3)

    try:
        await context.bot.delete_message(
            chat_id,
            message_id
        )
    except Exception:
        pass


# =========================================================
# ANTI-LINK / ANTI-SPAM
# =========================================================


async def moderation_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message

    if not message:
        return

    if not update.effective_chat:
        return

    if not update.effective_user:
        return

    # Ignore commands
    if message.text and message.text.startswith("/"):
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Never moderate admins
    if await is_admin(
        update,
        user_id
    ):
        return

    settings = await get_settings(
        chat_id
    )

    # =====================================================
    # ANTI-LINK
    # =====================================================

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
                    f"🔗 Link deleted\n"
                    f"Chat: {chat_id}\n"
                    f"User: {user_id}"
                )

            except Exception as e:
                log.warning(
                    "Link delete failed: %s",
                    e
                )

            return

    # =====================================================
    # ANTI-SPAM
    # =====================================================

    if settings[1]:

        now = time.time()

        user_cache = flood_cache[
            chat_id
        ][user_id]

        user_cache.append(now)

        while (
            user_cache
            and now - user_cache[0] > 8
        ):
            user_cache.popleft()

        # 7 messages in 8 seconds
        if len(user_cache) >= 7:

            try:
                await update.effective_chat.restrict_member(
                    user_id,
                    ChatPermissions(
                        can_send_messages=False
                    ),
                    until_date=int(time.time())
                    + 60
                )

                user_cache.clear()

                await log_action(
                    context,
                    f"🌊 Spam mute\n"
                    f"Chat: {chat_id}\n"
                    f"User: {user_id}"
                )

                try:
                    await message.delete()
                except Exception:
                    pass

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
    context
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
        Application.builder()
        .token(TOKEN)
        .build()
    )

    # =====================================================
    # COMMANDS
    # =====================================================

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

    # =====================================================
    # HELP CALLBACK
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            help_callback,
            pattern=r"^help_"
        )
    )

    # =====================================================
    # GROUP MODERATION
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.ChatType.GROUPS
            & ~filters.COMMAND,
            moderation_handler
        )
    )

    # =====================================================
    # ERRORS
    # =====================================================

    application.add_error_handler(
        error_handler
    )

    log.info(
        "Shield Guard started successfully."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
