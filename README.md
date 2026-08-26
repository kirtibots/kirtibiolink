# Shield Guard — Telegram Security Bot

A clean, self-hosted Telegram group moderation bot inspired by common Shield-style security bots. It is an independent implementation, not the source code of @ShieldronBot.

## Features
- Anti-link
- Anti-flood
- Warning system with automatic ban
- Ban / unban / kick
- Temporary mute / unmute
- Purge
- Admin-only controls
- SQLite persistence
- Optional moderation log chat

## Setup
1. Create a bot with Telegram @BotFather.
2. Add it to your group as an administrator.
3. Give it permission to delete messages, ban users and restrict members.
4. Copy `.env.example` to `.env` and set `BOT_TOKEN`.
5. Install dependencies:
   `pip install -r requirements.txt`
6. Run:
   `python bot.py`

## Commands
- `/settings`
- `/antilink on|off`
- `/antispam on|off`
- `/warn` (reply)
- `/warnings` (reply)
- `/unwarn` (reply)
- `/ban` (reply)
- `/unban USER_ID`
- `/kick` (reply)
- `/mute [minutes]` (reply)
- `/unmute` (reply)
- `/purge [count]`

## Important
For message moderation, configure the bot's Telegram privacy/admin permissions appropriately. Do not put your real bot token into source control.


## Heroku deployment
1. Create a new Heroku app.
2. Deploy this folder/repository using Heroku Git or GitHub deployment.
3. In **Settings → Config Vars**, set `BOT_TOKEN` to your BotFather token.
4. Optionally set `LOG_CHAT_ID`.
5. In **Resources**, enable the `worker` dyno.
6. Add the bot to your Telegram group as an administrator with permission to delete messages, ban users, and restrict members.

The bot uses polling, so the worker process should be enabled.
