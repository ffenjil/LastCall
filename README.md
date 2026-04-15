# LastCall

Open source Discord bot for voice channel timers and activity tracking, by Team RhythmoSync.

## Features

- Set disconnect timers for users in voice channels
- Track voice channel activity (join/leave/duration)
- View user stats and guild leaderboards
- Custom prefix per guild
- Hybrid commands (prefix + slash)

## Commands

| Command | Description |
|---------|-------------|
| `dc @user 5m` | Set disconnect timer |
| `cancel` | Cancel a timer |
| `timers` | List active timers |
| `prefix !` | Set guild prefix |
| `stats` | View VC stats |
| `top` | Guild leaderboard |

## Setup

1. Clone the repo
2. Copy `.env.example` to `.env` and fill in values
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the bot:
   ```bash
   python run.py
   ```

## Requirements

- Python 3.11+
- MongoDB 4.0+
- Discord Bot Token with these intents:
  - Message Content
  - Server Members
  - Voice States
