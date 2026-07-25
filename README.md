# WoTB Replay Bot

A Discord bot for World of Tanks Blitz that parses `.wotbreplay` files and tracks scrim session stats.

## Commands

| Command | Description |
|---|---|
| `/player <name>` | Search for a player by username |
| `/stats <name>` | Show a player's battle count and win rate |
| `/scrim start` | Start a scrim session and begin accepting replays |
| `/scrim end` | End the session and post the results image |

During an active session, upload `.wotbreplay` files (or a `.zip` containing them) to any channel — the bot will automatically parse and track them.

## Scrim Score

Each player receives a score out of 100 based on:
- Average damage
- Penetration %
- Average damage blocked
- Average kills
- Damage ratio (dealt vs received)

## Contributing

1. Clone the repo and install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Create a `.env` file in the project root with the credentials (ask the bot owner):
   ```
   DISCORD_TOKEN=...
   WG_API_KEY=...
   ```
3. Run locally:
   ```bash
   python Replay_Bot.py
   ```
4. Open a pull request with your changes.
