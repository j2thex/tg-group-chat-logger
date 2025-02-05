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
from openai import OpenAI

# Initialize logging with sensitive data protection
class SensitiveFormatter(logging.Formatter):
    """Custom formatter that redacts sensitive information"""
    def __init__(self, fmt: str):
        super().__init__(fmt)
        self.sensitive_patterns = [
            (re.compile(r'bot\d+:[A-Za-z0-9-_]{35}'), 'BOT_TOKEN_REDACTED'),
            (re.compile(r'sk-[A-Za-z0-9]{48}'), 'OPENAI_KEY_REDACTED'),
        ]

    def format(self, record):
        if isinstance(record.msg, str):
            msg = record.msg
            for pattern, replacement in self.sensitive_patterns:
                msg = pattern.sub(replacement, msg)
            record.msg = msg
        return super().format(record)

# Set up logging
log_formatter = SensitiveFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
file_handler = logging.FileHandler("bot.log")
file_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
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

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

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

async def download_file(file, destination):
    """Download a file from Telegram"""
    try:
        await file.download_to_drive(destination)
        return True
    except Exception as e:
        logger.error(f"Error downloading file: {e}", exc_info=True)
        return False

async def process_media(message, chat_dir):
    """Process and save media files from a message"""
    try:
        file_obj = None
        file_name = None
        media_type = None
        caption = message.caption or "No caption"

        if message.photo:
            media_type = 'photo'
            file_obj = await message.photo[-1].get_file()
            file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        elif message.document:
            media_type = 'document'
            file_obj = await message.document.get_file()
            original_name = message.document.file_name
            file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{original_name}"
        elif message.video:
            media_type = 'video'
            file_obj = await message.video.get_file()
            ext = os.path.splitext(message.video.file_name)[1] if message.video.file_name else '.mp4'
            file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        elif message.audio:
            media_type = 'audio'
            file_obj = await message.audio.get_file()
            ext = os.path.splitext(message.audio.file_name)[1] if message.audio.file_name else '.mp3'
            file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
        elif message.voice:
            media_type = 'voice'
            file_obj = await message.voice.get_file()
            file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.ogg"
        
        if file_obj and file_name:
            file_path = chat_dir / file_name
            
            if await download_file(file_obj, str(file_path)):
                logger.info(f"Saved {media_type} to {file_path}")
                return f"[MEDIA] [{message.date.strftime('%Y-%m-%d %H:%M:%S')}] " \
                       f"{message.from_user.username or message.from_user.first_name}: " \
                       f"Sent {media_type} ({file_name}) - Caption: {caption}\n"
    
    except Exception as e:
        logger.error(f"Error processing media: {e}", exc_info=True)
    
    return None

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
                match = re.match(r'\[(LIVE|BACKFILL|MEDIA)\] \[(.*?)\] (.*?): (.*)', line.strip())
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

async def analyze_with_gpt(messages: list) -> str:
    """Analyze conversation using GPT"""
    try:
        if not messages:
            return "There aren't any recent messages to analyze."

        formatted_chat = "\n".join([
            f"{msg['username']}: {msg['content']}" 
            for msg in messages
        ])

        prompt = f"""Here are the last few messages from a group chat:

{formatted_chat}

Please provide a brief, friendly analysis of this conversation. Include:
- What are people discussing?
- Any notable interactions or patterns?
- The overall tone or mood of the conversation

Keep your response concise and conversational, as if you're another member of the chat.
"""

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a friendly chat participant who provides brief, insightful observations about conversations."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"GPT analysis error: {e}", exc_info=True)
        return "I had some trouble analyzing the recent messages."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new messages including media"""
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

        # Handle text messages
        if message.text:
            line = await process_message(message, existing)
            if line:
                with open(file_path, "a", encoding='utf-8') as f:
                    f.write(line)
                logger.info(f"Saved text message from {chat.title}")
        
        # Handle media messages
        if any([message.photo, message.document, message.video, 
                message.audio, message.voice]):
            media_line = await process_media(message, chat_dir)
            if media_line:
                with open(file_path, "a", encoding='utf-8') as f:
                    f.write(media_line)
                logger.info(f"Saved media message from {chat.title}")

    except Exception as e:
        logger.error(f"Message handling error: {e}", exc_info=True)

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
        response = await analyze_with_gpt(recent_messages)
        await update.message.reply_text(response)
        
    except Exception as e:
        logger.error(f"React command error: {e}", exc_info=True)
        await update.message.reply_text("Sorry, I had trouble analyzing the recent messages.")

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
        # Verify OpenAI API key is present
        if not os.getenv('OPENAI_API_KEY'):
            raise ValueError("Missing OPENAI_API_KEY in environment variables")

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
        
        # Run the bot with proper shutdown handling
        application.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)
        
    except KeyboardInterrupt:
        logger.info("Bot stopping...")
        if 'application' in locals():
            application.stop()
        
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()