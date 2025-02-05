import os
import re
import logging
from datetime import datetime
from pathlib import Path
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

# Initialize logging first
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Set up base directory and load environment variables
BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / '.env'
DATA_DIR = BASE_DIR / "chat_history"

# Try to load .env file
load_dotenv(ENV_FILE)

# Constants
MAX_HISTORY = 1000  # Max messages to backfill
PROCESSED_MESSAGES = set()
BOT_START_TIME = datetime.now()

def get_bot_token():
    """Get bot token from environment with detailed error handling"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables")
        logger.info(f"Current working directory: {os.getcwd()}")
        logger.info(f"Environment variables available: {list(os.environ.keys())}")
        raise ValueError(
            "TELEGRAM_BOT_TOKEN not found. Please ensure it is set in your environment "
            f"or .env file. Current working directory: {os.getcwd()}"
        )
    return token

def sanitize_name(name):
    """Sanitize chat name for filesystem use"""
    if not name:
        return "unnamed_chat"
    return re.sub(r'[^\w_ -]', '', name).strip().replace(' ', '_')

def format_message(timestamp, username, content, message_time):
    """Format message with live/backfill flag"""
    is_live = message_time > BOT_START_TIME
    flag = "[LIVE]" if is_live else "[BACKFILL]"
    return f"{flag} [{timestamp}] {username}: {content}\n"

def get_message_id(message):
    """Create a unique identifier for a message"""
    return f"{message.chat.id}_{message.message_id}"

async def get_recent_messages(chat_title: str, num_messages: int = 5) -> list:
    """Retrieve the last n messages from the chat history"""
    try:
        chat_dir = DATA_DIR / sanitize_name(chat_title)
        file_path = chat_dir / "messages.txt"
        
        if not file_path.exists():
            logger.warning(f"No message history found for chat: {chat_title}")
            return []
            
        recent_messages = []
        with open(file_path, "r", encoding='utf-8') as f:
            lines = f.readlines()
            for line in lines[-num_messages:]:
                match = re.match(r'\[(LIVE|BACKFILL)\] \[(.*?)\] (.*?): (.*)', line.strip())
                if match:
                    _, timestamp, username, content = match.groups()
                    recent_messages.append({
                        'timestamp': timestamp,
                        'username': username,
                        'content': content
                    })
        return recent_messages
    except Exception as e:
        logger.error(f"Error getting recent messages: {e}", exc_info=True)
        return []

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
            
            message_time = datetime.fromtimestamp(message.date.timestamp())
            line = format_message(timestamp, username, content, message_time)
            
            if line not in existing:
                return line
    return None

def analyze_conversation(messages: list) -> str:
    """Analyze the conversation and generate a contextual response"""
    try:
        if not messages:
            return "There aren't any recent messages to analyze."
            
        # Count messages per user
        user_messages = {}
        topics = set()
        question_count = 0
        
        for msg in messages:
            user = msg['username']
            user_messages[user] = user_messages.get(user, 0) + 1
            
            content = msg['content'].lower()
            
            if '?' in content:
                question_count += 1
                
            words = [word for word in re.findall(r'\w+', content) if len(word) > 4]
            topics.update(words)
        
        response_parts = []
        
        participants = len(user_messages)
        if participants > 1:
            response_parts.append(f"I see a conversation between {participants} people!")
        
        if question_count > 0:
            response_parts.append(f"There {'was' if question_count == 1 else 'were'} {question_count} question{'s' if question_count > 1 else ''} asked.")
        
        if user_messages:
            most_active = max(user_messages.items(), key=lambda x: x[1])
            if most_active[1] > 1:
                response_parts.append(f"{most_active[0]} has been quite active!")
        
        relevant_topics = [topic for topic in topics if len(topic) > 4][:2]
        if relevant_topics:
            topics_str = " and ".join(relevant_topics)
            response_parts.append(f"The conversation seems to be about {topics_str}.")
        
        if not response_parts:
            return "I can see the recent messages, but I'm not quite sure what to make of them yet!"
            
        return " ".join(response_parts)
        
    except Exception as e:
        logger.error(f"Error analyzing conversation: {e}", exc_info=True)
        return "I had some trouble analyzing the recent messages."

async def handle_react_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /react command"""
    try:
        chat = update.effective_chat
        if not chat:
            logger.error("No chat found in update")
            return
            
        if chat.type not in [Chat.GROUP, Chat.SUPERGROUP]:
            await update.message.reply_text("This command only works in group chats!")
            return
            
        logger.info(f"Processing /react command in chat: {chat.title}")
        
        recent_messages = await get_recent_messages(chat.title)
        response = analyze_conversation(recent_messages)
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f"React command error: {e}", exc_info=True)
        await update.message.reply_text("Sorry, I had trouble analyzing the recent messages.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new messages"""
    try:
        chat = update.effective_chat
        message = update.effective_message
        
        if chat.type not in [Chat.GROUP, Chat.SUPERGROUP]:
            return

        chat_title = sanitize_name(chat.title)
        chat_dir = DATA_DIR / chat_title
        chat_dir.mkdir(parents=True, exist_ok=True)
        file_path = chat_dir / "messages.txt"

        existing = set()
        if file_path.exists():
            with open(file_path, "r", encoding='utf-8') as f:
                existing = set(f.readlines())

        line = await process_message(message, existing)
        if line:
            with open(file_path, "a", encoding='utf-8') as f:
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

async def backfill_history(chat: Chat, context: ContextTypes.DEFAULT_TYPE):
    """Backfill previous messages for a chat"""
    try:
        chat_title = sanitize_name(chat.title)
        chat_dir = DATA_DIR / chat_title
        chat_dir.mkdir(parents=True, exist_ok=True)
        file_path = chat_dir / "messages.txt"

        existing = set()
        if file_path.exists():
            with open(file_path, "r", encoding='utf-8') as f:
                existing = set(f.readlines())

        new_messages = []
        offset = 0
        total_fetched = 0

        while total_fetched < MAX_HISTORY:
            updates = await context.bot.get_updates(offset=offset, limit=100, timeout=30)
            if not updates:
                break

            for update in updates:
                offset = update.update_id + 1
                
                if update.message and update.message.chat.id == chat.id:
                    line = await process_message(update.message, existing)
                    if line:
                        new_messages.append(line)
                        total_fetched += 1

            if len(updates) < 100:
                break

        if new_messages:
            with open(file_path, "a", encoding='utf-8') as f:
                f.writelines(reversed(new_messages))
            
            logger.info(f"Backfilled {len(new_messages)} messages for {chat.title}")

    except Exception as e:
        logger.error(f"Backfill error: {e}", exc_info=True)

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
    try:
        # Get bot token and create directories
        token = get_bot_token()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        # Log startup information
        logger.info("Starting bot...")
        logger.info(f"Working directory: {os.getcwd()}")
        logger.info(f"Base directory: {BASE_DIR}")
        logger.info(f"Data directory: {DATA_DIR}")
        
        # Initialize and start the application
        application = Application.builder().token(token).post_init(post_init).build()

        # Add handlers
        application.add_handler(ChatMemberHandler(handle_new_chat, ChatMemberHandler.MY_CHAT_MEMBER))
        application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
        application.add_handler(CommandHandler("react", handle_react_command))

        logger.info("Bot initialized, starting polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()