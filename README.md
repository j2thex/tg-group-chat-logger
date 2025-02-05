# Telegram Chat Logger Bot

A Telegram bot that logs chat messages and maintains chat history, marking messages as either live or backfilled.

## Features

- Logs all messages from group chats
- Marks messages as either [LIVE] or [BACKFILL]
- Maintains chat history in text files
- Handles bot restarts and maintains message continuity
- Deduplicates messages to prevent double-logging

## Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/telegram-chat-logger.git
cd telegram-chat-logger
```

2. Install requirements:
```bash
pip install -r requirements.txt
```

3. Create .env file:
```bash
cp .env.example .env
```

4. Edit .env and add your Telegram Bot Token

5. Run the bot:
```bash
python bot.py
```

## Usage

1. Add the bot to a Telegram group
2. The bot will automatically:
   - Start logging new messages
   - Attempt to backfill recent message history
   - Save all messages to `chat_history/[group_name]/messages.txt`

## Message Format

Messages are saved in the following format:
```
[STATUS] [TIMESTAMP] USERNAME: MESSAGE
```

Where:
- STATUS is either LIVE or BACKFILL
- TIMESTAMP is in YYYY-MM-DD HH:MM:SS format
- USERNAME is the sender's username or first name
- MESSAGE is the message content

## File Structure

- `chat_history/` - Directory containing all chat logs
  - `[group_name]/` - Subdirectory for each group
    - `messages.txt` - Log file containing all messages

## Requirements

- Python 3.7+
- python-telegram-bot
- python-dotenv

## License

MIT