# 3x-ui demo Telegram bot

The bot grants a single demo subscription to regular Telegram users. Administrators can inspect panel status, inbounds, clients, groups, and the active demo configuration.

## Setup

1. Create a Telegram bot through BotFather and obtain its token.
2. Copy `.env.example` to `.env` and set all values.
3. `THREEXUI_INBOUND_IDS` contains comma-separated 3x-ui inbound IDs to which demo clients will be attached.
4. Install dependencies: `py -m pip install -r requirements.txt`
5. Start: `py bot.py`

`ADMIN_IDS` is a comma-separated list of numeric Telegram user IDs. Use `@userinfobot` or a similar bot to obtain the ID.

## Docker

1. Copy `.env.example` to `.env` and fill in the real values. Do not put credentials in `Dockerfile` or `compose.yaml`.
2. Build and start the bot:

   ```powershell
   docker compose -f docker/compose.yaml up --build -d
   ```

3. Inspect logs:

   ```powershell
   docker compose -f docker/compose.yaml logs -f bot
   ```

4. Stop the bot:

   ```powershell
   docker compose -f docker/compose.yaml down
   ```

After modifying Python files, rebuild with `docker compose -f docker/compose.yaml up --build -d`. After modifying only `.env`, use `docker compose -f docker/compose.yaml up -d --force-recreate`.

## Behaviour

- A regular user sees one button: **Request demo access**.
- The bot asks for the preferred form of address, creates a client in each configured inbound, adds it to `DEMO_GROUP`, and returns the subscription links.
- The 3x-ui client comment stores the Telegram identity and preferred name.
- An administrator is notified of each successful demo creation and has an in-chat dashboard via `/start`.

The bot never persists API credentials in source control. Rotate any credential that was exposed in a chat before production use.
