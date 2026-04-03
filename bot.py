import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, CallbackQueryHandler
import pandas as pd
from datetime import datetime

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Временное хранилище данных (в реальном проекте используйте БД: SQLite/PostgreSQL)
user_data = {}

# Класс для управления финансами пользователя
class FinanceTracker:
    def __init__(self, user_id):
        self.user_id = user_id
        self.transactions = pd.DataFrame(columns=['Date', 'Category', 'Amount', 'Description'])
    
    def add_transaction(self, category, amount, description):
        new_transaction = {
            'Date': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'Category': category,
            'Amount': float(amount),
            'Description': description
        }
        self.transactions = self.transactions.append(new_transaction, ignore_index=True)
        return "✅ Транзакция добавлена!"

    def get_stats(self):
        if self.transactions.empty:
            return "📊 Пока нет данных о транзакциях."
        stats = self.transactions.groupby('Category')['Amount'].sum().reset_index()
        return stats.to_string(index=False)

# Команда /start
def start(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    user_data[user_id] = FinanceTracker(user_id)
    
    keyboard = [
        [InlineKeyboardButton("➕ Добавить расход", callback_data='add_expense')],
        [InlineKeyboardButton("📊 Статистика", callback_data='get_stats')],
        [InlineKeyboardButton("❓ Помощь", callback_data='help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    update.message.reply_text(
        '💰 **CashFlow Guardian**\n\n'
        'Я помогу вам отслеживать расходы и оптимизировать бюджет!\n'
        'Выберите действие:',
        reply_markup=reply_markup
    )

# Обработка кнопок
def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    user_id = update.effective_user.id
    query.answer()
    
    if query.data == 'add_expense':
        query.edit_message_text(text="📝 Введите расход в формате:\n`<категория> <сумма> <описание>`\n\nПример: `еда 350 обед в кафе`")
        context.user_data['awaiting_expense'] = True
    elif query.data == 'get_stats':
        stats = user_data[user_id].get_stats()
        query.edit_message_text(text=f"📊 **Ваша статистика:**\n```\n{stats}\n```", parse_mode='Markdown')
    elif query.data == 'help':
        query.edit_message_text(text="🆘 **Помощь**\n\n"
                               "Доступные команды:\n"
                               "/start - начать работу\n"
                               "/add - добавить расход\n"
                               "/stats - показать статистику")

# Добавление транзакции
def add_expense(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    text = update.message.text
    
    try:
        parts = text.split(maxsplit=2)
        if len(parts) < 3:
            raise ValueError
        category, amount, description = parts
        response = user_data[user_id].add_transaction(category, amount, description)
    except ValueError:
        response = "❌ Ошибка формата. Используйте: `еда 350 обед в кафе`"
    
    update.message.reply_text(response, parse_mode='Markdown')

# Основная функция
def main() -> None:
    # Замените 'YOUR_BOT_TOKEN' на реальный токен
    updater = Updater("YOUR_BOT_TOKEN", use_context=True)
    dispatcher = updater.dispatcher

    # Обработчики команд
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", start))
    dispatcher.add_handler(CommandHandler("add", add_expense))
    dispatcher.add_handler(CommandHandler("stats", lambda u, c: button_handler(u, c, 'get_stats')))
    
    # Обработчик кнопок
    dispatcher.add_handler(CallbackQueryHandler(button_handler))
    
    # Обработчик текстовых сообщений
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, add_expense))

    # Запуск бота
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()