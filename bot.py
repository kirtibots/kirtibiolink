import os
import re
import time
import logging
from collections import defaultdict, deque
import motor.motor_asyncio
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "YourOwnerUsername")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "YourSupportUsername")
LOG_CHAT = os.getenv("LOG_CHAT_ID", "")

MONGO_DB_URI = os.getenv("MONGO_DB_URI", "").strip()
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "shield_guard")
DB_PATH = os.getenv("DB_PATH", "shieldbot.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("shieldbot")

mongo_client = None
mongo = None
settings_col = warns_col = local_auth_col = global_auth_col = None
sqlite_db = None

if MONGO_DB_URI:
    mongo_client = motor.motor_asyncio.AsyncIOMotorClient(
        MONGO_DB_URI, serverSelectionTimeoutMS=8000, connectTimeoutMS=8000
    )
    mongo = mongo_client[MONGO_DB_NAME]
    settings_col = mongo["settings"]
    warns_col = mongo["warns"]
    local_auth_col = mongo["local_auth"]
    global_auth_col = mongo["global_auth"]
else:
    import sqlite3
    sqlite_db = sqlite3.connect(DB_PATH, check_same_thread=False)
    sqlite_db.execute("""CREATE TABLE IF NOT EXISTS settings(
        chat_id INTEGER PRIMARY KEY, links INTEGER DEFAULT 1, flood INTEGER DEFAULT 1,
        welcome INTEGER DEFAULT 0, max_warns INTEGER DEFAULT 3, edit_delete INTEGER DEFAULT 0,
        bio_link INTEGER DEFAULT 0, media_del INTEGER DEFAULT 0, no_abuse INTEGER DEFAULT 0)""")
    sqlite_db.execute("""CREATE TABLE IF NOT EXISTS warns(
        chat_id INTEGER, user_id INTEGER, count INTEGER DEFAULT 0,
        PRIMARY KEY(chat_id,user_id))""")
    sqlite_db.execute("""CREATE TABLE IF NOT EXISTS local_auth(
        chat_id INTEGER, user_id INTEGER, PRIMARY KEY(chat_id,user_id))""")
    sqlite_db.execute("""CREATE TABLE IF NOT EXISTS global_auth(
        user_id INTEGER PRIMARY KEY)""")
    sqlite_db.commit()

DEFAULT_SETTINGS = {
    "links": 1, "flood": 1, "welcome": 0, "max_warns": 3,
    "edit_delete": 0, "bio_link": 0, "media_del": 0, "no_abuse": 0
}

ABUSE_WORDS = {
    "fuck", "fucking", "motherfucker", "bitch", "bastard", "asshole",
    "idiot", "stupid", "mc", "bc", "bsdk", "chutiya", "gali",
    "madarchod", "bhenchod", "behenchod", "harami"
}
LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/)", re.I)
flood_cache = defaultdict(lambda: defaultdict(deque))


async def init_db():
    if mongo is not None:
        await mongo_client.admin.command("ping")
        await settings_col.create_index("chat_id", unique=True)
        await warns_col.create_index([("chat_id", 1), ("user_id", 1)], unique=True)
        await local_auth_col.create_index([("chat_id", 1), ("user_id", 1)], unique=True)
        await global_auth_col.create_index("user_id", unique=True)
        log.info("MongoDB connected: %s", MONGO_DB_NAME)
    else:
        log.info("SQLite fallback database active")


async def close_db():
    if mongo_client:
        mongo_client.close()


async def get_settings(chat_id):
    if mongo is not None:
        row = await settings_col.find_one({"chat_id": chat_id})
        if not row:
            doc = {"chat_id": chat_id, **DEFAULT_SETTINGS}
            await settings_col.insert_one(doc)
            return tuple(doc[k] for k in DEFAULT_SETTINGS)
        return tuple(row.get(k, v) for k, v in DEFAULT_SETTINGS.items())

    row = sqlite_db.execute(
        "SELECT links,flood,welcome,max_warns,edit_delete,bio_link,media_del,no_abuse "
        "FROM settings WHERE chat_id=?", (chat_id,)
    ).fetchone()
    if not row:
        sqlite_db.execute("INSERT OR IGNORE INTO settings(chat_id) VALUES(?)", (chat_id,))
        sqlite_db.commit()
        return tuple(DEFAULT_SETTINGS.values())
    return row


async def set_setting(chat_id, field, value):
    if field not in DEFAULT_SETTINGS:
        return
    if mongo is not None:
        await settings_col.update_one(
            {"chat_id": chat_id}, {"$set": {field: int(value)}}, upsert=True
        )
    else:
        sqlite_db.execute(f"UPDATE settings SET {field}=? WHERE chat_id=?", (int(value), chat_id))
        sqlite_db.commit()


async def get_warn_count(chat_id, user_id):
    if mongo is not None:
        row = await warns_col.find_one({"chat_id": chat_id, "user_id": user_id})
        return int(row["count"]) if row else 0
    row = sqlite_db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id)
    ).fetchone()
    return int(row[0]) if row else 0


async def set_warn_count(chat_id, user_id, count):
    if mongo is not None:
        if count <= 0:
            await warns_col.delete_one({"chat_id": chat_id, "user_id": user_id})
        else:
            await warns_col.update_one(
                {"chat_id": chat_id, "user_id": user_id},
                {"$set": {"count": int(count)}}, upsert=True
            )
    else:
        if count <= 0:
            sqlite_db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id,user_id))
        else:
            sqlite_db.execute(
                "INSERT OR REPLACE INTO warns VALUES(?,?,?)",
                (chat_id,user_id,int(count))
            )
        sqlite_db.commit()


async def local_auth_add(chat_id, user_id):
    if mongo is not None:
        await local_auth_col.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {"$set": {"chat_id": chat_id, "user_id": user_id}}, upsert=True
        )
    else:
        sqlite_db.execute("INSERT OR IGNORE INTO local_auth VALUES(?,?)", (chat_id,user_id))
        sqlite_db.commit()


async def local_auth_remove(chat_id, user_id):
    if mongo is not None:
        await local_auth_col.delete_one({"chat_id": chat_id, "user_id": user_id})
    else:
        sqlite_db.execute("DELETE FROM local_auth WHERE chat_id=? AND user_id=?", (chat_id,user_id))
        sqlite_db.commit()


async def local_auth_list(chat_id):
    if mongo is not None:
        return [x["user_id"] async for x in local_auth_col.find({"chat_id":chat_id}).sort("user_id",1)]
    return [x[0] for x in sqlite_db.execute(
        "SELECT user_id FROM local_auth WHERE chat_id=? ORDER BY user_id",(chat_id,)
    ).fetchall()]


async def global_auth_add(user_id):
    if mongo is not None:
        await global_auth_col.update_one(
            {"user_id": user_id}, {"$set": {"user_id": user_id}}, upsert=True
        )
    else:
        sqlite_db.execute("INSERT OR IGNORE INTO global_auth VALUES(?)",(user_id,))
        sqlite_db.commit()


async def global_auth_remove(user_id):
    if mongo is not None:
        await global_auth_col.delete_one({"user_id":user_id})
    else:
        sqlite_db.execute("DELETE FROM global_auth WHERE user_id=?",(user_id,))
        sqlite_db.commit()


async def global_auth_list():
    if mongo is not None:
        return [x["user_id"] async for x in global_auth_col.find({}).sort("user_id",1)]
    return [x[0] for x in sqlite_db.execute(
        "SELECT user_id FROM global_auth ORDER BY user_id"
    ).fetchall()]


async def is_global_auth(user_id):
    if mongo is not None:
        return bool(await global_auth_col.find_one({"user_id":user_id}))
    return bool(sqlite_db.execute(
        "SELECT 1 FROM global_auth WHERE user_id=?", (user_id,)
    ).fetchone())


async def is_admin(update, user_id=None):
    if not update.effective_chat:
        return False
    uid = user_id or (update.effective_user.id if update.effective_user else 0)
    try:
        return (await update.effective_chat.get_member(uid)).status in ("administrator","creator")
    except Exception:
        return False


async def can_manage(update, user_id=None):
    uid = user_id or update.effective_user.id
    return uid == OWNER_ID or await is_admin(update, uid) or await is_global_auth(uid)


async def log_action(context, text):
    if LOG_CHAT:
        try:
            await context.bot.send_message(LOG_CHAT, text)
        except Exception:
            pass


async def start(update, context):
    user = update.effective_user
    me = await context.bot.get_me()
    text = f"""• HELLO, {user.mention_html()} 🇨🇦 ✨

✦ WELCOME TO <b>Shield Guard</b>
<b>PREMIUM ABUSE & GROUP PROTECTION</b>

╔════════════════════════════╗
⚡ <b>BLAZING FAST PROTECTION</b>
🛡️ <b>AUTO ABUSE DETECTION</b>
🔗 <b>BIO & LINK PROTECTION</b>
🧹 <b>MEDIA AUTO-DELETE</b>
⚙️ <b>SMART GROUP MODERATION</b>
╚════════════════════════════╝

» ADD ME TO YOUR GROUP
» GIVE ME DELETE MESSAGES PERMISSION

• BUILT FOR A SAFER COMMUNITY."""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✚ ADD ME IN YOUR GROUP ✚",
                              url=f"https://t.me/{me.username}?startgroup=true")],
        [InlineKeyboardButton("💬 OWNER", url=f"https://t.me/{OWNER_USERNAME}"),
         InlineKeyboardButton("🧑‍💼 SUPPORT ↗", url=f"https://t.me/{SUPPORT_USERNAME}")],
        [InlineKeyboardButton("📚 HELP AND COMMANDS", callback_data="help_home")]
    ])
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


HELP = {
"help_link": "🔗 <b>LINK DEL</b>\n\n/antilink on|off\nDeletes detected links from non-admin users.",
"help_admin": "🛡️ <b>ADMIN DETECT</b>\n\n/warn /warnings /unwarn /ban /unban USER_ID /kick /mute [minutes] /unmute /purge [count]",
"help_broadcast": "📢 <b>BROADCAST</b>\n\n/broadcast MESSAGE\nOwner-only placeholder; mass messaging is disabled in this build.",
"help_other": "⚙️ <b>OTHER</b>\n\n/start /help /settings /antispam on|off",
"help_local_auth": "🔐 <b>LOCAL AUTH</b>\n\n/auth USER_ID\n/unauth USER_ID\n/authlist",
"help_global_auth": "🌐 <b>GLOBAL AUTH</b>\n\n/gauth USER_ID\n/gunauth USER_ID\n/gauthlist",
"help_edit_delete": "✏️ <b>EDIT DELETE</b>\n\n/editdelete on|off",
"help_bio_link": "🔗 <b>BIO LINK</b>\n\n/biolink on|off",
"help_media_del": "🧹 <b>MEDIA DEL</b>\n\n/mediadel on|off",
"help_no_abuse": "🛡️ <b>NO ABUSE</b>\n\n/noabuse on|off"
}


def help_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("LINK DEL", callback_data="help_link"),
         InlineKeyboardButton("ADMIN DETECT", callback_data="help_admin")],
        [InlineKeyboardButton("BROADCAST", callback_data="help_broadcast"),
         InlineKeyboardButton("OTHER", callback_data="help_other")],
        [InlineKeyboardButton("LOCAL AUTH", callback_data="help_local_auth"),
         InlineKeyboardButton("GLOBAL AUTH", callback_data="help_global_auth")],
        [InlineKeyboardButton("EDIT DELETE", callback_data="help_edit_delete"),
         InlineKeyboardButton("BIO LINK", callback_data="help_bio_link")],
        [InlineKeyboardButton("MEDIA DEL", callback_data="help_media_del"),
         InlineKeyboardButton("NO ABUSE", callback_data="help_no_abuse")],
        [InlineKeyboardButton("BACK ↩", callback_data="help_back"),
         InlineKeyboardButton("• HOME •", callback_data="help_home"),
         InlineKeyboardButton("NEXT", callback_data="help_next")]
    ])


async def help_cmd(update, context):
    text = """📚 <b>HELP & COMMANDS ~</b>

◇ <b>CHOOSE A CATEGORY BELOW TO VIEW ITS COMMANDS.</b>

⚡ ADD ME IN YOUR GROUP AND GIVE ME <b>"DELETE MESSAGES"</b> PERMISSION.
✨ KEEP YOUR GROUP CLEAN AND SAFE."""
    if update.message:
        await update.message.reply_text(text, reply_markup=help_keyboard(), parse_mode="HTML")
    else:
        await update.callback_query.edit_message_text(text, reply_markup=help_keyboard(), parse_mode="HTML")


async def help_callback(update, context):
    q = update.callback_query
    await q.answer()
    if q.data in HELP:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("BACK ↩",callback_data="help_back"),
                                    InlineKeyboardButton("• HOME •",callback_data="help_home")]])
        await q.edit_message_text(HELP[q.data], reply_markup=kb, parse_mode="HTML")
    elif q.data == "help_home" or q.data == "help_back":
        await help_cmd(update, context)
    elif q.data == "help_next":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("LOCAL AUTH",callback_data="help_local_auth"),
             InlineKeyboardButton("GLOBAL AUTH",callback_data="help_global_auth")],
            [InlineKeyboardButton("EDIT DELETE",callback_data="help_edit_delete"),
             InlineKeyboardButton("BIO LINK",callback_data="help_bio_link")],
            [InlineKeyboardButton("MEDIA DEL",callback_data="help_media_del"),
             InlineKeyboardButton("NO ABUSE",callback_data="help_no_abuse")],
            [InlineKeyboardButton("BACK ↩",callback_data="help_back"),
             InlineKeyboardButton("• HOME •",callback_data="help_home")]
        ])
        await q.edit_message_text("📚 <b>MORE COMMANDS</b>\n\nChoose a category:",
                                  reply_markup=kb, parse_mode="HTML")


async def toggle(update, context, field, name):
    if not await can_manage(update):
        return
    if not context.args or context.args[0].lower() not in ("on","off"):
        await update.message.reply_text(f"Usage: /{name} on|off")
        return
    value = 1 if context.args[0].lower() == "on" else 0
    await set_setting(update.effective_chat.id, field, value)
    await update.message.reply_text(f"✅ {name.title()} {'enabled' if value else 'disabled'}.")


async def settings_cmd(update, context):
    if not await can_manage(update):
        return
    s = await get_settings(update.effective_chat.id)
    await update.message.reply_text(
        f"⚙️ <b>SETTINGS</b>\n\n"
        f"🔗 Anti-link: {'ON' if s[0] else 'OFF'}\n"
        f"🌊 Anti-flood: {'ON' if s[1] else 'OFF'}\n"
        f"✏️ Edit-delete: {'ON' if s[4] else 'OFF'}\n"
        f"🔗 Bio-link: {'ON' if s[5] else 'OFF'}\n"
        f"🧹 Media-delete: {'ON' if s[6] else 'OFF'}\n"
        f"🛡️ No-abuse: {'ON' if s[7] else 'OFF'}\n"
        f"⚠️ Max warnings: {s[3]}",
        parse_mode="HTML"
    )


async def get_target(update):
    if update.message and update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    return None


async def warn(update, context):
    if not await can_manage(update): return
    u = await get_target(update)
    if not u: return await update.message.reply_text("Reply to a user's message.")
    if await is_admin(update,u.id): return await update.message.reply_text("❌ Admin cannot be warned.")
    count = await get_warn_count(update.effective_chat.id,u.id) + 1
    await set_warn_count(update.effective_chat.id,u.id,count)
    maxw = (await get_settings(update.effective_chat.id))[3]
    await update.message.reply_text(f"⚠️ {u.mention_html()} warned. ({count}/{maxw})",parse_mode="HTML")
    if count >= maxw:
        try:
            await update.effective_chat.ban_member(u.id)
            await set_warn_count(update.effective_chat.id,u.id,0)
        except Exception as e:
            log.warning(e)


async def warnings(update, context):
    u = await get_target(update) or update.effective_user
    count = await get_warn_count(update.effective_chat.id,u.id)
    await update.message.reply_text(f"⚠️ {u.full_name}: {count} warning(s).")


async def unwarn(update, context):
    if not await can_manage(update): return
    u = await get_target(update)
    if not u: return await update.message.reply_text("Reply to a user's message.")
    await set_warn_count(update.effective_chat.id,u.id,0)
    await update.message.reply_text("✅ Warnings reset.")


async def ban(update, context):
    if not await can_manage(update): return
    u = await get_target(update)
    if not u: return await update.message.reply_text("Reply to a user's message.")
    if await is_admin(update,u.id): return
    try:
        await update.effective_chat.ban_member(u.id)
        await update.message.reply_text(f"🚫 Banned {u.mention_html()}.",parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def unban(update, context):
    if not await can_manage(update): return
    if not context.args: return await update.message.reply_text("Usage: /unban USER_ID")
    try:
        await update.effective_chat.unban_member(int(context.args[0]))
        await update.message.reply_text("✅ User unbanned.")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def kick(update, context):
    if not await can_manage(update): return
    u = await get_target(update)
    if not u: return await update.message.reply_text("Reply to a user's message.")
    if await is_admin(update,u.id): return
    try:
        await update.effective_chat.ban_member(u.id)
        await update.effective_chat.unban_member(u.id)
        await update.message.reply_text("👢 Kicked.")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def mute(update, context):
    if not await can_manage(update): return
    u = await get_target(update)
    if not u: return await update.message.reply_text("Reply to a user's message.")
    minutes = 10
    if context.args:
        try: minutes = max(1,min(int(context.args[0]),10080))
        except ValueError: pass
    try:
        await update.effective_chat.restrict_member(
            u.id, ChatPermissions(can_send_messages=False),
            until_date=int(time.time())+minutes*60
        )
        await update.message.reply_text(f"🔇 Muted for {minutes} minutes.")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def unmute(update, context):
    if not await can_manage(update): return
    u = await get_target(update)
    if not u: return await update.message.reply_text("Reply to a user's message.")
    try:
        await update.effective_chat.restrict_member(
            u.id, ChatPermissions(can_send_messages=True,can_send_other_messages=True)
        )
        await update.message.reply_text("🔊 Unmuted.")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def purge(update, context):
    if not await can_manage(update): return
    count = 10
    if context.args:
        try: count=max(1,min(int(context.args[0]),100))
        except ValueError: pass
    deleted=0
    for i in range(count):
        try:
            await context.bot.delete_message(update.effective_chat.id,update.message.message_id-i)
            deleted+=1
        except Exception: pass
    await update.message.reply_text(f"🧹 Deleted {deleted} messages.")


async def auth(update, context):
    if not await is_admin(update): return
    uid = int(context.args[0]) if context.args and context.args[0].isdigit() else (
        update.message.reply_to_message.from_user.id if update.message.reply_to_message else 0)
    if not uid: return await update.message.reply_text("Usage: /auth USER_ID or reply.")
    await local_auth_add(update.effective_chat.id,uid)
    await update.message.reply_text(f"✅ Local auth added: {uid}")


async def unauth(update, context):
    if not await is_admin(update): return
    uid = int(context.args[0]) if context.args and context.args[0].isdigit() else (
        update.message.reply_to_message.from_user.id if update.message.reply_to_message else 0)
    if not uid: return await update.message.reply_text("Usage: /unauth USER_ID or reply.")
    await local_auth_remove(update.effective_chat.id,uid)
    await update.message.reply_text("✅ Local auth removed.")


async def authlist(update, context):
    if not await can_manage(update): return
    ids = await local_auth_list(update.effective_chat.id)
    await update.message.reply_text("🔐 LOCAL AUTH\n\n" + ("\n".join(map(str,ids)) if ids else "None"))


async def gauth(update, context):
    if update.effective_user.id != OWNER_ID: return await update.message.reply_text("❌ Owner only.")
    uid = int(context.args[0]) if context.args and context.args[0].isdigit() else (
        update.message.reply_to_message.from_user.id if update.message.reply_to_message else 0)
    if not uid: return await update.message.reply_text("Usage: /gauth USER_ID")
    await global_auth_add(uid)
    await update.message.reply_text("🌐 Global auth added.")


async def gunauth(update, context):
    if update.effective_user.id != OWNER_ID: return await update.message.reply_text("❌ Owner only.")
    if not context.args or not context.args[0].isdigit(): return await update.message.reply_text("Usage: /gunauth USER_ID")
    await global_auth_remove(int(context.args[0]))
    await update.message.reply_text("🌐 Global auth removed.")


async def gauthlist(update, context):
    if update.effective_user.id != OWNER_ID: return await update.message.reply_text("❌ Owner only.")
    ids=await global_auth_list()
    await update.message.reply_text("🌐 GLOBAL AUTH\n\n" + ("\n".join(map(str,ids)) if ids else "None"))


async def broadcast(update, context):
    if update.effective_user.id != OWNER_ID: return await update.message.reply_text("❌ Owner only.")
    await update.message.reply_text("📢 Broadcast is disabled in this build.")




async def on_message(update, context):
    m=update.effective_message
    if not m or not update.effective_chat or not update.effective_user: return
    if await can_manage(update,m.from_user.id): return
    s=await get_settings(m.chat.id)

    if s[0] and (m.text or m.caption) and LINK_RE.search(m.text or m.caption):
        try: await m.delete()
        except Exception: pass
        return

    if s[6] and (m.photo or m.video or m.animation or m.document or m.audio or m.voice):
        try: await m.delete()
        except Exception: pass
        return

    if s[7] and (m.text or m.caption):
        words=set(re.findall(r"[a-z0-9\u0900-\u097f]+",(m.text or m.caption).lower()))
        if words & ABUSE_WORDS:
            try: await m.delete()
            except Exception: pass
            return

    if s[1]:
        now=time.monotonic()
        q=flood_cache[m.chat.id][m.from_user.id]
        q.append(now)
        while q and now-q[0]>8: q.popleft()
        if len(q)>=6:
            try:
                await m.delete()
                await m.chat.restrict_member(m.from_user.id,ChatPermissions(can_send_messages=False),
                                              until_date=int(time.time())+60)
                q.clear()
            except Exception: pass


async def edited_message(update, context):
    m=update.edited_message
    if not m or not m.from_user: return
    if await can_manage(update,m.from_user.id): return
    s=await get_settings(m.chat.id)
    if s[4]:
        try: await m.delete()
        except Exception: pass


async def error_handler(update, context):
    log.exception("Update error", exc_info=context.error)


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN is missing. Set it in Heroku Config Vars.")

    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("help",help_cmd))
    app.add_handler(CommandHandler("settings",settings_cmd))

    for cmd,func in [
        ("antilink",lambda u,c: toggle(u,c,"links","antilink")),
        ("antispam",lambda u,c: toggle(u,c,"flood","antispam")),
        ("editdelete",lambda u,c: toggle(u,c,"edit_delete","editdelete")),
        ("biolink",lambda u,c: toggle(u,c,"bio_link","biolink")),
        ("mediadel",lambda u,c: toggle(u,c,"media_del","mediadel")),
        ("noabuse",lambda u,c: toggle(u,c,"no_abuse","noabuse")),
    ]:
        app.add_handler(CommandHandler(cmd,func))

    for cmd,func in [
        ("warn",warn),("warnings",warnings),("unwarn",unwarn),("ban",ban),
        ("unban",unban),("kick",kick),("mute",mute),("unmute",unmute),("purge",purge),
        ("auth",auth),("unauth",unauth),("authlist",authlist),
        ("gauth",gauth),("gunauth",gunauth),("gauthlist",gauthlist),
        ("broadcast",broadcast)
    ]:
        app.add_handler(CommandHandler(cmd,func))

    app.add_handler(CallbackQueryHandler(help_callback,pattern=r"^help_"))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE,edited_message))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND,on_message))
    app.add_error_handler(error_handler)

    async def post_init(application):
        await init_db()
    async def post_shutdown(application):
        await close_db()
    app.post_init=post_init
    app.post_shutdown=post_shutdown

    log.info("Shield Guard started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__=="__main__":
    main()
