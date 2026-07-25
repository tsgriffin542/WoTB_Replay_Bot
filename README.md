# WoTB Replay Bot

A Discord bot for World of Tanks Blitz that parses `.wotbreplay` files and tracks scrim session stats.

## Features

- Look up player profiles and win rates via the Wargaming API
- Start and end scrim sessions, accepting replays from multiple players
- Parse individual `.wotbreplay` files or bulk `.zip` archives
- Generate a stats image at the end of a scrim showing damage, kills, pen%, score, and more

## Commands

| Command | Description |
|---|---|
| `/player <name>` | Search for a player by username |
| `/stats <name>` | Show a player's battle count and win rate |
| `/scrim start` | Start a scrim session and begin accepting replays |
| `/scrim end` | End the session and post the results image |

During an active session, just upload `.wotbreplay` files (or a `.zip` containing them) to any channel — the bot will automatically parse and track them.

## Setup

### 1. Create a Discord bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application and add a bot
3. Under **Bot**, enable the **Message Content Intent**
4. Copy your bot token

### 2. Configure environment

Create a `.env` file in the project root:

```
DISCORD_TOKEN=your_bot_token_here
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the bot

```bash
python Replay_Bot.py
```

## Hosting on Railway

Set the `DISCORD_TOKEN` environment variable in your Railway service's **Variables** tab. The bot will pick it up automatically — no `.env` file needed.

## Scrim Score

Each player receives a score out of 100 based on:
- Average damage
- Penetration %
- Average damage blocked
- Average kills
- Damage ratio (dealt vs received)
