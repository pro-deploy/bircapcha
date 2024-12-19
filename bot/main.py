import os
import sys
import time
import logging
import random
import sqlite3
import traceback
import threading
import telebot
from telebot import types
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
def setup_logging():
    log_dir = '/app/logs'
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'bot.log')

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=10*1024*1024,  
        backupCount=5
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[file_handler, console_handler]
    )

    telebot_logger = logging.getLogger('telebot')
    telebot_logger.setLevel(logging.WARNING)

class DatabaseManager:
    def __init__(self, db_path='/app/data/users_activity.db'):
        self.db_path = db_path
        self.ensure_database()

    def ensure_database(self):
        # Проверяем существование директории
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # Подключаемся к базе данных
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Создаем таблицу пользователей, если она не существует
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER,
                chat_id INTEGER,
                username TEXT,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                captcha_passed INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')

        # Создаем таблицу активности, если она не существует
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                action TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Сохраняем изменения и закрываем соединение
        conn.commit()
        conn.close()

        print(f"База данных инициализирована: {self.db_path}")

class UserActivityTracker:
    def __init__(self, db_path='/app/data/users_activity.db'):
        DatabaseManager(db_path)
        
        self.logger = logging.getLogger(self.__class__.__name__)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.logger.info("База данных подключена")

    def check_user_status(self, user_id, chat_id):
        try:
            self.cursor.execute('''
                SELECT captcha_passed, last_activity 
                FROM users 
                WHERE user_id = ? AND chat_id = ?
            ''', (user_id, chat_id))
            
            result = self.cursor.fetchone()
            
            if result is None:
                return 'new'
            
            captcha_passed, last_activity = result
            
            if captcha_passed == 0:
                return 'not_verified'
            
            return 'verified'
        
        except Exception as e:
            self.logger.error(f"Ошибка проверки статуса пользователя {user_id}: {e}")
            return 'error'

    def add_user(self, user_id, username, chat_id):
        try:
            self.cursor.execute('''
                INSERT OR REPLACE INTO users 
                (user_id, username, chat_id, join_date, last_activity, captcha_passed) 
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)
            ''', (user_id, username, chat_id))
            
            self.cursor.execute('''
                INSERT INTO activity_log 
                (user_id, chat_id, action) 
                VALUES (?, ?, ?)
            ''', (user_id, chat_id, 'user_joined'))
            
            self.conn.commit()
            self.logger.info(f"Пользователь {user_id} добавлен в чат {chat_id}")
        except Exception as e:
            self.logger.error(f"Ошибка добавления пользователя {user_id}: {e}")
            self.conn.rollback()

    def track_activity(self, user_id, chat_id):
        try:
            self.cursor.execute('''
                UPDATE users 
                SET 
                    last_activity = CURRENT_TIMESTAMP, 
                    message_count = message_count + 1 
                WHERE user_id = ? AND chat_id = ?
            ''', (user_id, chat_id))
            
            self.cursor.execute('''
                INSERT INTO activity_log 
                (user_id, chat_id, action) 
                VALUES (?, ?, ?)
            ''', (user_id, chat_id, 'message_sent'))
            
            self.conn.commit()
        except Exception as e:
            self.logger.error(f"Ошибка трекинга активности {user_id}: {e}")
            self.conn.rollback()

    def update_captcha_status(self, user_id, chat_id, status):
        try:
            self.cursor.execute('''
                UPDATE users 
                SET captcha_passed = ? 
                WHERE user_id = ? AND chat_id = ?
            ''', (1 if status == 'completed' else 0, user_id, chat_id))
            
            self.cursor.execute('''
                INSERT INTO activity_log 
                (user_id, chat_id, action) 
                VALUES (?, ?, ?)
            ''', (user_id, chat_id, f'captcha_{status}'))
            
            self.conn.commit()
        except Exception as e:
            self.logger.error(f"Ошибка обновления статуса капчи {user_id}: {e}")
            self.conn.rollback()

class CaptchaBot:
    def __init__(self):
        setup_logging()
        self.logger = logging.getLogger(self.__class__.__name__)
        
        self.bot_token = os.getenv('BOT_TOKEN')
        if not self.bot_token:
            self.logger.critical("BOT_TOKEN не установлен!")
            sys.exit(1)
        
        self.captcha_config = {
            'objects': ['стол', 'стул', 'ложка', 'вилка', 'нож', 'чашка'],
            'emojis': {
                'стол': ['🪑', '🍽️', '🏠', '🧊', '🚪'],
                'стул': ['🪑', '🛋️', '🏠', '🧊', '📚'],
                'ложка': ['🥄', '🍲', '🥣', '🍽️', '🍵'],
                'вилка': ['🍴', '🥘', '🍽️', '🥣', '🍲'],
                'нож': ['🔪', '🍽️', '🥩', '🥒', '🥕'],
                'чашка': ['☕', '🍵', '🥤', '🍺', '🥛']
            },
            'difficulty_levels': {
                'easy': {'objects_count': 5, 'time_limit': 60},
                'medium': {'objects_count': 10, 'time_limit': 45},
                'hard': {'objects_count': 15, 'time_limit': 30}
            }
        }
        
        self.bot = telebot.TeleBot(self.bot_token)
        self.activity_tracker = UserActivityTracker()
        self.user_captcha = {}

        self.register_handlers()

    def register_handlers(self):
        @self.bot.message_handler(content_types=['new_chat_members'])
        def welcome(message):
            for new_member in message.new_chat_members:
                self.handle_new_member(message, new_member)

        @self.bot.message_handler(func=lambda message: True)
        def track_activity(message):
            self.activity_tracker.track_activity(
                message.from_user.id, 
                message.chat.id
            )

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('captcha_'))
        def captcha_callback(call):
            self.handle_captcha_response(call)

        @self.bot.message_handler(commands=['remove_captcha'])
        def remove_captcha(message):
            chat_member = self.bot.get_chat_member(message.chat.id, message.from_user.id)
            
            if chat_member.status in ['administrator', 'creator']:
                if message.reply_to_message:
                    user_id = message.reply_to_message.from_user.id
                    chat_id = message.chat.id
                elif len(message.text.split()) > 1:
                    try:
                        user_id = int(message.text.split()[1])
                        chat_id = message.chat.id
                    except ValueError:
                        self.bot.reply_to(message, "Некорректный ID пользователя")
                        return
                else:
                    self.bot.reply_to(message, "Используйте реплай или укажите ID")
                    return

                self.activity_tracker.update_captcha_status(user_id, chat_id, 'completed')
                
                self.bot.reply_to(
                    message, 
                    f"Капча для пользователя {user_id} принудительно снята администратором."
                )
            else:
                self.bot.reply_to(
                    message, 
                    "У вас недостаточно прав для этой команды"
                )

    def handle_new_member(self, message, new_member):
        # Проверяем статус пользователя
        user_status = self.activity_tracker.check_user_status(
            new_member.id, 
            message.chat.id
        )
        
        self.logger.info(f"Статус пользователя {new_member.id}: {user_status}")
        
        # Добавляем пользователя в базу данных
        self.activity_tracker.add_user(
            new_member.id, 
            new_member.username or str(new_member.id),
            message.chat.id
        )
        
        # В зависимости от статуса выбираем действие
        if user_status in ['new', 'not_verified']:
            # Генерируем капчу
            captcha_data = self.generate_captcha()
            
            markup = types.InlineKeyboardMarkup(row_width=3)
            buttons = [
                types.InlineKeyboardButton(emoji, callback_data=f'captcha_{emoji}') 
                for emoji in captcha_data['emojis']
            ]
            markup.add(*buttons)

            captcha_message = self.bot.send_message(
                message.chat.id, 
                f"{new_member.first_name}, для входа в группу выберите {captcha_data['object']}!",
                reply_markup=markup
            )

            self.user_captcha[new_member.id] = {
                'object': captcha_data['object'],
                'correct_emoji': captcha_data['correct_emoji'],
                'message_id': captcha_message.message_id,
                'chat_id': message.chat.id,
                'user_id': new_member.id,
                'timestamp': time.time()
            }
        
        elif user_status == 'verified':
            # Пользователь уже verified, просто приветствуем
            self.bot.send_message(
                message.chat.id, 
                f"👋 {new_member.first_name}, добро пожаловать обратно!"
            )
        
        else:
            # Обработка ошибки
            self.bot.send_message(
                message.chat.id, 
                f"❌ Ошибка при проверке пользователя {new_member.first_name}"
            )

    def generate_captcha(self):
        difficulty = os.getenv('DIFFICULTY_LEVEL', 'medium')
        objects = self.captcha_config['objects']
        
        target_object = random.choice(objects)
        object_emojis = self.captcha_config['emojis'].get(target_object, [])
        
        random.shuffle(object_emojis)
        
        return {
            'object': target_object,
            'emojis': object_emojis,
            'correct_emoji': object_emojis[0]
        }

    def handle_captcha_response(self, call):
        user_id = call.from_user.id
        chat_id = call.message.chat.id
        
        # Находим капчу для этого пользователя в этом чате
        captcha_info = next(
            (info for info in self.user_captcha.values() 
             if info['user_id'] == user_id and info['chat_id'] == chat_id), 
            None
        )
        
        if not captcha_info:
            return
        
        selected_emoji = call.data.split('_')[1]
        
        if selected_emoji == captcha_info['correct_emoji']:
            self.bot.delete_message(
                captcha_info['chat_id'], 
                captcha_info['message_id']
            )
            
            self.activity_tracker.update_captcha_status(
                user_id, 
                chat_id, 
                'completed'
            )
            
            self.bot.send_message(
                captcha_info['chat_id'], 
                f"✅ {call.from_user.first_name} прошел проверку!"
            )
            
            # Удаляем капчу для этого пользователя
            self.user_captcha = {
                k: v for k, v in self.user_captcha.items() 
                if v['user_id'] != user_id or v['chat_id'] != chat_id
            }
        else:
            self.bot.answer_callback_query(
                call.id, 
                f"Это не {captcha_info['object']}!"
            )

    def start(self):
        self.logger.info("Запуск бота")
        
        def check_captcha_timeout():
            while True:
                current_time = time.time()
                max_captcha_time = int(os.getenv('MAX_CAPTCHA_TIME', 300))
                
                for captcha_info in list(self.user_captcha.values()):
                    if current_time - captcha_info['timestamp'] > max_captcha_time:
                        try:
                            self.bot.delete_message(
                                captcha_info['chat_id'], 
                                captcha_info['message_id']
                            )
                            self.bot.kick_chat_member(
                                captcha_info['chat_id'], 
                                captcha_info['user_id']
                            )
                            
                            # Удаляем капчу
                            self.user_captcha = {
                                k: v for k, v in self.user_captcha.items() 
                                if v['user_id'] != captcha_info['user_id'] 
                                or v['chat_id'] != captcha_info['chat_id']
                            }
                        except Exception as e:
                            self.logger.error(f"Ошибка удаления капчи: {e}")
                
                time.sleep(60)  # Проверка каждую минуту

        timeout_thread = threading.Thread(target=check_captcha_timeout, daemon=True)
        timeout_thread.start()

        while True:
            try:
                self.bot.polling(none_stop=True)
            except Exception as e:
                self.logger.error(f"Ошибка polling: {e}")
                time.sleep(15)

def main():
    bot = CaptchaBot()
    bot.start()

if __name__ == '__main__':
    main()