import os
import re
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, Chat
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    ContextTypes,
    filters
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATA_DIR = "chat_history"
MAX_HISTORY = 1000  # Max messages to backfill

# Track processed messages and bot start time
PROCESSED_MESSAGES = set()
BOT_START_TIME = datetime.now()

def sanitize_name(name):
    return re.sub(r'[^\w_ -]', '', name).strip().replace(' ', '_')

def format_message(timestamp, username, content, message_time):
    """
    Format message with live/backfill flag based on message time
    versus when the bot started
    """
    is_live = message_time > BOT_START_TIME
    flag = "[LIVE]" if is_live else "[BACKFILL]"
    return f"{flag} [{timestamp}] {username}: {content}\n"

def get_message_id(message):
    """Create a unique identifier for a message"""
    return f"{message.chat.id}_{message.message_id}"

async def process_message(message, existing, is_backfill=False):
    """Process a single message and return formatted line if valid"""
    if message and (message.text or message.caption):
        msg_id = get_message_id(message)
        if msg_id not in PROCESSED_MESSAGES:
            PROCESSED_MESSAGES.add(msg_id)
            
            timestamp = message.date.strftime("%Y-%m-%d %H:%M:%S")
            user = message.from_user
            username = user.username or user.first_name or "Unknown"
            content = message.text or message.caption
            
            # Convert message date to datetime for comparison
            message_time = datetime.fromtimestamp(message.date.timestamp())
            line = format_message(timestamp, username, content, message_time)
            
            if line not in existing:
                return line
    return None

async def backfill_history(chat: Chat, context: ContextTypes.DEFAULT_TYPE):
    """Backfill previous messages for a chat"""
    try:
        chat_title = sanitize_name(chat.title)
        chat_dir = os.path.join(DATA_DIR, chat_title)
        os.makedirs(chat_dir, exist_ok=True)
        file_path = os.path.join(chat_dir, "messages.txt")

        # Get existing messages to avoid duplicates
        existing = set()
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                existing = set(f.readlines())

        new_messages = []
        offset = 0
        total_fetched = 0

        # Fetch messages in chunks
        while total_fetched < MAX_HISTORY:
            updates = await context.bot.get_updates(offset=offset, limit=100, timeout=30)
            if not updates:
                break

            # Process each update
            for update in updates:
                offset = update.update_id + 1
                
                if update.message and update.message.chat.id == chat.id:
                    line = await process_message(update.message, existing)
                    if line:
                        new_messages.append(line)
                        total_fetched += 1

            # If we got less than 100 updates, we've reached the end
            if len(updates) < 100:
                break

        # Append new messages in chronological order
        if new_messages:
            with open(file_path, "a") as f:
                f.writelines(reversed(new_messages))
            
            logger.info(f"Backfilled {len(new_messages)} messages for {chat.title}")

    except Exception as e:
        logger.error(f"Backfill error: {e}", exc_info=True)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new messages"""
    try:
        chat = update.effective_chat
        message = update.effective_message
        
        if chat.type not in [Chat.GROUP, Chat.SUPERGROUP]:
            return

        chat_title = sanitize_name(chat.title)
        chat_dir = os.path.join(DATA_DIR, chat_title)
        os.makedirs(chat_dir, exist_ok=True)
        file_path = os.path.join(chat_dir, "messages.txt")

        # Get existing messages to avoid duplicates
        existing = set()
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                existing = set(f.readlines())

        line = await process_message(message, existing)
        if line:
            with open(file_path, "a") as f:
                f.write(line)
            
            logger.info(f"Saved new message from {chat.title}")

    except Exception as e:
        logger.error(f"Message error: {e}", exc_info=True)

async def handle_new_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle being added to a group"""
    try:
        chat = update.effective_chat
        new_status = update.my_chat_member.new_chat_member.status
        
        if new_status == "member" and chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
            logger.info(f"Joined new group: {chat.title}")
            await backfill_history(chat, context)

    except Exception as e:
        logger.error(f"New chat error: {e}", exc_info=True)

async def post_init(application: Application):
    """Backfill history for all known groups on startup"""
    try:
        updates = await application.bot.get_updates(limit=100, timeout=30)
        
        groups = set()
        for update in updates:
            if update.effective_chat and update.effective_chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
                groups.add(update.effective_chat)

        for chat in groups:
            await backfill_history(chat, application)

    except Exception as e:
        logger.error(f"Startup backfill error: {e}", exc_info=True)

def main():
    """Start the bot"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN")

    application = Application.builder().token(token).post_init(post_init).build()

    # Add handlers
    application.add_handler(ChatMemberHandler(handle_new_chat, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()