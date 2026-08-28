# LastCall

Open source Discord bot for voice channel timers and activity tracking, by Team RhythmoSync.

## Features

- Set disconnect timers for users in voice channels
- A warning before the disconnect, with a button to extend
- Track voice channel activity (join/leave/duration)
- View user stats and guild leaderboards
- Custom prefix per guild
- Hybrid commands (prefix + slash)

## Commands

| Command | Description |
|---------|-------------|
| `dc 5m @user` | Set disconnect timer |
| `extend 10m` | Add time to a timer |
| `cancel` | Cancel a timer |
| `timers` | List active timers |
| `prefix !` | Set guild prefix |
| `stats` | View VC stats |
| `top` | Guild leaderboard |

Anyone can set a timer on themselves. `Move Members` is required to set one on
someone else, and you cannot target anyone at or above your own top role.

## Setup

1. Clone the repo
2. Copy `.env.example` to `.env` and fill in values
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the bot:
   ```bash
   python main.py
   ```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Requirements

- Python 3.11+
- MongoDB 4.0+
- Discord Bot Token with these intents:
  - Message Content
  - Server Members
  - Voice States

## Notes

Slash commands sync automatically whenever the command set changes, so restarts
do not burn through the global sync rate limit. Set `SYNC_ON_START=1` to force
one, or use the owner-only `push` command.
