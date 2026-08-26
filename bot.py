import os, re, time, sqlite3, logging
from collections import defaultdict, deque
from telegram import Update, ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "shieldbot.db")
LOG_CHAT = os.getenv("LOG_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("shieldbot")

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS settings(
    chat_id INTEGER PRIMARY KEY, links INTEGER DEFAULT 1, flood INTEGER DEFAULT 1,
    welcome INTEGER DEFAULT 0, max_warns INTEGER DEFAULT 3
)""")
db.execute("""CREATE TABLE IF NOT EXISTS warns(
    chat_id INTEGER, user_id INTEGER, count INTEGER DEFAULT 0,
    PRIMARY KEY(chat_id,user_id)
)""")
db.commit()

flood_cache = defaultdict(lambda: defaultdict(deque))
LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/)", re.I)

def settings(chat_id):
    row=db.execute("SELECT links,flood,welcome,max_warns FROM settings WHERE chat_id=?", (chat_id,)).fetchone()
    if not row:
        db.execute("INSERT OR IGNORE INTO settings(chat_id) VALUES(?)", (chat_id,))
        db.commit()
        return (1,1,0,3)
    return row

def is_admin(update: Update):
    return bool(update.effective_chat and update.effective_user and
                update.effective_chat.get_member(update.effective_user.id).status in ("administrator","creator"))

async def admin(update, user_id=None):
    if not update.effective_chat or not update.effective_user:
        return False
    uid=user_id or update.effective_user.id
    try:
        m=await update.effective_chat.get_member(uid)
        return m.status in ("administrator","creator")
    except Exception:
        return False

async def log_action(context, text):
    if LOG_CHAT:
        try: await context.bot.send_message(LOG_CHAT, text)
        except Exception: pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    me = await context.bot.get_me()
    mention = user.mention_html()

    text = f"""
• HELLO, {mention} 🇨🇦 ✨

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
» GIVE ME DELETE MESSAGES
  PERMISSION

• BUILT FOR A SAFER COMMUNITY.
"""

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✚ ADD ME IN YOUR GROUP ✚",
                              url=f"https://t.me/{me.username}?startgroup=true")],
        [InlineKeyboardButton("💬 OWNER", url="https://t.me/YourOwnerUsername"),
         InlineKeyboardButton("🧑‍💼 SUPPORT ↗", url="https://t.me/YourSupportUsername")],
        [InlineKeyboardButton("📚 HELP AND COMMANDS", callback_data="help:1")]
    ])

    try:
        await update.message.reply_photo(
            photo=os.getenv("START_IMAGE_URL", ""),
            caption=text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    page = q.data.split(":")[1] if ":" in q.data else "1"

    if page == "1":
        text = """📖 <u><b>HELP & COMMANDS ~</b></u>

◇ <b>CHOOSE A CATEGORY BELOW TO VIEW
ITS COMMANDS.</b>

⚡ <b>ADD ME IN YOUR GROUP AND GIVE
ME "DELETE MESSAGES" PERMISSION.</b>
✨ <b>KEEP YOUR GROUP CLEAN AND SAFE.</b>"""

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("LINK DEL", callback_data="help:link"),
             InlineKeyboardButton("ADMIN DETECT", callback_data="help:admin")],
            [InlineKeyboardButton("BROADCAST", callback_data="help:broadcast"),
             InlineKeyboardButton("OTHER", callback_data="help:other")],
            [InlineKeyboardButton("BACK ↩", callback_data="home"),
             InlineKeyboardButton("• HOME •", callback_data="home")]
        ])
    elif page == "2":
        text = """📖 <u><b>HELP & COMMANDS ~</b></u>

◇ <b>CHOOSE A CATEGORY BELOW TO VIEW
ITS COMMANDS.</b>"""

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("LOCAL AUTH", callback_data="help:local"),
             InlineKeyboardButton("GLOBAL AUTH", callback_data="help:global")],
            [InlineKeyboardButton("EDIT DELETE", callback_data="help:edit"),
             InlineKeyboardButton("BIO LINK", callback_data="help:bio")],
            [InlineKeyboardButton("MEDIA DEL", callback_data="help:media"),
             InlineKeyboardButton("NO ABUSE", callback_data="help:noabuse")],
            [InlineKeyboardButton("BACK ↩", callback_data="help:1"),
             InlineKeyboardButton("• HOME •", callback_data="home"),
             InlineKeyboardButton("NEXT", callback_data="help:3")]
        ])
    elif page == "3":
        text = """📖 <u><b>HELP & COMMANDS ~</b></u>

◇ <b>MODERATION COMMANDS</b>

/warn — warn a member
/warnings — check warnings
/unwarn — reset warnings
/ban — ban a member
/unban USER_ID — unban
/kick — kick a member
/mute [minutes] — mute
/unmute — unmute
/purge [count] — delete messages
/settings — view settings
/antilink on|off — link protection
/antispam on|off — flood protection"""

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("BACK ↩", callback_data="help:2"),
             InlineKeyboardButton("• HOME •", callback_data="home")]
        ])
    else:
        commands = {
            "link": ("🔗 LINK DEL", "Automatically removes messages containing links.\n\n/antilink on\n/antilink off"),
            "admin": ("🛡️ ADMIN DETECT", "Admin-only moderation controls.\n\nAdmins are protected from moderation actions."),
            "broadcast": ("📢 BROADCAST", "Broadcast category reserved for bot-owner features."),
            "other": ("⚙️ OTHER", "/settings\n/start\n/help"),
            "local": ("🔐 LOCAL AUTH", "Group-level admin protection and moderation permissions."),
            "global": ("🌐 GLOBAL AUTH", "Owner/global controls can be added here."),
            "edit": ("✏️ EDIT DELETE", "Edited messages containing links are removed when anti-link is enabled."),
            "bio": ("🔗 BIO LINK", "Link-protection category for profile/group link checks."),
            "media": ("🧹 MEDIA DEL", "Media auto-delete category reserved for future media filters."),
            "noabuse": ("🛡️ NO ABUSE", "Abuse/profanity protection category reserved for future filters.")
        }
        title, body = commands.get(page, ("HELP", "Choose a category."))
        text = f"<u><b>{title}</b></u>\n\n{body}"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("BACK ↩", callback_data="help:1" if page in ("link","admin","broadcast","other") else "help:2"),
             InlineKeyboardButton("• HOME •", callback_data="home")]
        ])

    try:
        await q.edit_message_caption(caption=text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await q.edit_message_text(text=text, reply_markup=keyboard, parse_mode="HTML")

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if q.data=="home":
        await q.answer()
        user=q.from_user
        me=await context.bot.get_me()
        text=f"""• HELLO, {user.mention_html()} 🇨🇦 ✨

✦ WELCOME TO <b>Shield Guard</b>
<b>PREMIUM ABUSE & GROUP PROTECTION</b>

⚡ <b>BLAZING FAST PROTECTION</b>
🛡️ <b>AUTO ABUSE DETECTION</b>
🔗 <b>BIO & LINK PROTECTION</b>
🧹 <b>MEDIA AUTO-DELETE</b>
⚙️ <b>SMART GROUP MODERATION</b>

» ADD ME TO YOUR GROUP
» GIVE ME DELETE MESSAGES PERMISSION"""
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("✚ ADD ME IN YOUR GROUP ✚",url=f"https://t.me/{me.username}?startgroup=true")],
            [InlineKeyboardButton("💬 OWNER",url="https://t.me/YourOwnerUsername"),
             InlineKeyboardButton("🧑‍💼 SUPPORT ↗",url="https://t.me/YourSupportUsername")],
            [InlineKeyboardButton("📚 HELP AND COMMANDS",callback_data="help:1")]
        ])
        try: await q.edit_message_caption(caption=text,reply_markup=kb,parse_mode="HTML")
        except Exception: await q.edit_message_text(text=text,reply_markup=kb,parse_mode="HTML")
    elif q.data.startswith("help:"):
        await help_menu(update,context)

async def settings_cmd(update, context):
    if not await admin(update): return
    s=settings(update.effective_chat.id)
    await update.message.reply_text(
        f"⚙️ Settings\n\n"
        f"🔗 Anti-link: {'ON' if s[0] else 'OFF'}\n"
        f"🌊 Anti-flood: {'ON' if s[1] else 'OFF'}\n"
        f"👋 Welcome: {'ON' if s[2] else 'OFF'}\n"
        f"⚠️ Max warnings: {s[3]}"
    )

async def toggle(update, context, field, name):
    if not await admin(update): return
    if not context.args or context.args[0].lower() not in ("on","off"):
        await update.message.reply_text(f"Usage: /{name} on|off"); return
    val=1 if context.args[0].lower()=="on" else 0
    db.execute(f"UPDATE settings SET {field}=? WHERE chat_id=?", (val,update.effective_chat.id))
    db.commit()
    await update.message.reply_text(f"✅ {name.title()} {'enabled' if val else 'disabled'}.")

async def antilink(update, context): await toggle(update,context,"links","antilink")
async def antispam(update, context): await toggle(update,context,"flood","antispam")

async def target(update):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user
    if update.message.entities:
        for e in update.message.entities:
            if e.type=="text_mention":
                return e.user
    return None

async def warn(update, context):
    if not await admin(update): return
    u=await target(update)
    if not u:
        await update.message.reply_text("Reply to a user's message and use /warn."); return
    if await admin(update,u.id):
        await update.message.reply_text("❌ I won't warn an admin."); return
    row=db.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?",(update.effective_chat.id,u.id)).fetchone()
    count=(row[0] if row else 0)+1
    db.execute("INSERT OR REPLACE INTO warns VALUES(?,?,?)",(update.effective_chat.id,u.id,count)); db.commit()
    maxw=settings(update.effective_chat.id)[3]
    await update.message.reply_text(f"⚠️ {u.mention_html()} warned. ({count}/{maxw})", parse_mode="HTML")
    if count>=maxw:
        try:
            await update.effective_chat.ban_member(u.id)
            db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?",(update.effective_chat.id,u.id)); db.commit()
            await log_action(context,f"🚫 Auto-ban: {u.full_name} ({u.id}) reached {maxw} warnings.")
        except Exception as e: log.warning(e)

async def warnings(update, context):
    u=await target(update)
    if not u: u=update.effective_user
    row=db.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?",(update.effective_chat.id,u.id)).fetchone()
    await update.message.reply_text(f"⚠️ {u.full_name}: {row[0] if row else 0} warning(s).")

async def unwarn(update, context):
    if not await admin(update): return
    u=await target(update)
    if not u: await update.message.reply_text("Reply to a user's message."); return
    db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?",(update.effective_chat.id,u.id)); db.commit()
    await update.message.reply_text(f"✅ Warnings reset for {u.mention_html()}.", parse_mode="HTML")

async def ban(update, context):
    if not await admin(update): return
    u=await target(update)
    if not u: await update.message.reply_text("Reply to a user's message."); return
    if await admin(update,u.id): return
    try:
        await update.effective_chat.ban_member(u.id)
        await update.message.reply_text(f"🚫 Banned {u.mention_html()}.", parse_mode="HTML")
        await log_action(context,f"🚫 BAN | {u.full_name} | {u.id} | chat {update.effective_chat.id}")
    except Exception as e: await update.message.reply_text(f"❌ Ban failed: {e}")

async def unban(update, context):
    if not await admin(update): return
    if not context.args:
        await update.message.reply_text("Usage: /unban USER_ID"); return
    try:
        await update.effective_chat.unban_member(int(context.args[0]))
        await update.message.reply_text("✅ User unbanned.")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def kick(update, context):
    if not await admin(update): return
    u=await target(update)
    if not u: await update.message.reply_text("Reply to a user's message."); return
    if await admin(update,u.id): return
    try:
        await update.effective_chat.ban_member(u.id)
        await update.effective_chat.unban_member(u.id)
        await update.message.reply_text(f"👢 Kicked {u.mention_html()}.", parse_mode="HTML")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def mute(update, context):
    if not await admin(update): return
    u=await target(update)
    if not u: await update.message.reply_text("Reply to a user's message."); return
    if await admin(update,u.id): return
    minutes=10
    if context.args:
        try: minutes=max(1,min(int(context.args[0]),10080))
        except: pass
    until=int(time.time())+minutes*60
    try:
        await update.effective_chat.restrict_member(u.id, ChatPermissions(can_send_messages=False), until_date=until)
        await update.message.reply_text(f"🔇 Muted {u.mention_html()} for {minutes} min.", parse_mode="HTML")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def unmute(update, context):
    if not await admin(update): return
    u=await target(update)
    if not u: await update.message.reply_text("Reply to a user's message."); return
    try:
        await update.effective_chat.restrict_member(u.id, ChatPermissions(can_send_messages=True,can_send_other_messages=True))
        await update.message.reply_text("🔊 Unmuted.")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def purge(update, context):
    if not await admin(update): return
    count=10
    if context.args:
        try: count=max(1,min(int(context.args[0]),100))
        except: pass
    deleted=0
    msg=update.message
    for i in range(count):
        try:
            await context.bot.delete_message(update.effective_chat.id, msg.message_id-i)
            deleted+=1
        except Exception: pass
    await update.message.reply_text(f"🧹 Deleted {deleted} messages.", quote=True)

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m=update.effective_message
    if not m or not update.effective_chat or not update.effective_user: return
    if await admin(update): return
    links,flood,_,_=settings(update.effective_chat.id)

    if links and (m.text or m.caption) and LINK_RE.search(m.text or m.caption):
        try:
            await m.delete()
            await log_action(context,f"🔗 Deleted link from {update.effective_user.full_name} ({update.effective_user.id})")
        except Exception: pass
        return

    if flood:
        now=time.monotonic()
        q=flood_cache[update.effective_chat.id][update.effective_user.id]
        q.append(now)
        while q and now-q[0]>8: q.popleft()
        if len(q)>=6:
            try:
                await m.delete()
                await update.effective_chat.restrict_member(
                    update.effective_user.id,
                    ChatPermissions(can_send_messages=False),
                    until_date=int(time.time())+60
                )
                q.clear()
                await log_action(context,f"🌊 Flood mute: {update.effective_user.full_name} ({update.effective_user.id})")
            except Exception: pass

async def error_handler(update, context):
    log.exception("Update error", exc_info=context.error)

def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN is missing. Put your bot token in .env.")
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("settings",settings_cmd))
    app.add_handler(CommandHandler("antilink",antilink))
    app.add_handler(CommandHandler("antispam",antispam))
    app.add_handler(CommandHandler("warn",warn))
    app.add_handler(CommandHandler("warnings",warnings))
    app.add_handler(CommandHandler("unwarn",unwarn))
    app.add_handler(CommandHandler("ban",ban))
    app.add_handler(CommandHandler("unban",unban))
    app.add_handler(CommandHandler("kick",kick))
    app.add_handler(CommandHandler("mute",mute))
    app.add_handler(CommandHandler("unmute",unmute))
    app.add_handler(CommandHandler("purge",purge))
    app.add_handler(CallbackQueryHandler(callbacks, pattern=r"^(help:|home)"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, on_message))
    app.add_error_handler(error_handler)
    log.info("Shield Guard started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()
