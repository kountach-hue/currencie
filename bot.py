import os
import logging
import requests
import xml.etree.ElementTree as ET
import sqlite3
import csv
import io
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, PreCheckoutQueryHandler, InlineQueryHandler

# === НАСТРОЙКИ ===
TELEGRAM_TOKEN = os.environ.get("8782691704:AAGuLDboqWVTbTGT-SHNOVJaQCCA3_TAzqU")
CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
DONATION_OPTIONS = [5, 10, 20, 50, 100, 250, 500]

logging.basicConfig(level=logging.INFO)

# === КЭШ ДЛЯ КУРСОВ ===
cached_rates = None
cached_time = None

# === БАЗА ДАННЫХ (ИСТОРИЯ + ИЗБРАННОЕ) ===
def init_db():
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            from_cur TEXT,
            to_cur TEXT,
            amount REAL,
            result REAL,
            timestamp TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER,
            currency_code TEXT,
            PRIMARY KEY (user_id, currency_code)
        )
    ''')
    conn.commit()
    conn.close()

def save_history(user_id, from_cur, to_cur, amount, result):
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO history (user_id, from_cur, to_cur, amount, result, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, from_cur, to_cur, amount, result, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_history(user_id):
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT from_cur, to_cur, amount, result, timestamp
        FROM history
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT 20
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def export_history_csv(user_id):
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT from_cur, to_cur, amount, result, timestamp
        FROM history
        WHERE user_id = ?
        ORDER BY timestamp DESC
    ''', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return None
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Из валюты', 'В валюту', 'Сумма', 'Результат', 'Дата и время'])
    for row in rows:
        writer.writerow(row)
    return output.getvalue()

def add_favorite(user_id, currency_code):
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO favorites (user_id, currency_code) VALUES (?, ?)', (user_id, currency_code))
    conn.commit()
    conn.close()

def remove_favorite(user_id, currency_code):
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM favorites WHERE user_id = ? AND currency_code = ?', (user_id, currency_code))
    conn.commit()
    conn.close()

def get_favorites(user_id):
    conn = sqlite3.connect('user_data.db')
    cursor = conn.cursor()
    cursor.execute('SELECT currency_code FROM favorites WHERE user_id = ?', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

# === ВАЛЮТЫ С ФЛАГАМИ ===
CURRENCIES = {
    "USD": "🇺🇸 Доллар США",
    "EUR": "🇪🇺 Евро",
    "CNY": "🇨🇳 Юань",
    "GBP": "🇬🇧 Фунт",
    "TRY": "🇹🇷 Лира",
    "KZT": "🇰🇿 Тенге",
    "BYN": "🇧🇾 Бел.рубль",
    "JPY": "🇯🇵 Йена",
    "CHF": "🇨🇭 Франк",
    "CAD": "🇨🇦 Канадский доллар",
    "AED": "🇦🇪 Дирхам",
    "UZS": "🇺🇿 Сум",
    "AMD": "🇦🇲 Драм",
    "GEL": "🇬🇪 Лари",
    "KGS": "🇰🇬 Сом",
    "TJS": "🇹🇯 Сомони",
}

# === ПОЛУЧЕНИЕ КУРСОВ ===
def get_rates():
    global cached_rates, cached_time
    if cached_rates and cached_time and (datetime.now() - cached_time).seconds < 3600:
        return cached_rates
    try:
        response = requests.get(CBR_URL, timeout=5)
        if response.status_code != 200:
            return cached_rates
        root = ET.fromstring(response.content)
        rates = {'RUB': 1.0}
        for valute in root.findall('Valute'):
            code = valute.find('CharCode').text
            if code in CURRENCIES:
                value = float(valute.find('Value').text.replace(',', '.'))
                nominal = int(valute.find('Nominal').text)
                rates[code] = value / nominal
        cached_rates = rates
        cached_time = datetime.now()
        return rates
    except:
        return cached_rates

# === КЛАВИАТУРЫ ===
def main_menu():
    keyboard = [
        [InlineKeyboardButton("💎 RUB → ВАЛЮТА", callback_data='rub_to')],
        [InlineKeyboardButton("💎 ВАЛЮТА → RUB", callback_data='to_rub')],
        [InlineKeyboardButton("🔄 ОБМЕН ВАЛЮТ", callback_data='exchange')],
        [InlineKeyboardButton("⭐ ИЗБРАННОЕ", callback_data='favorites_menu')],
        [InlineKeyboardButton("📜 ИСТОРИЯ", callback_data='history')],
        [InlineKeyboardButton("📤 ЭКСПОРТ CSV", callback_data='export_csv')],
        [InlineKeyboardButton("⭐ ПОДДЕРЖАТЬ", callback_data='donate')],
        [InlineKeyboardButton("✨ ИНЛАЙН-РЕЖИМ", callback_data='inline_help')],
        [InlineKeyboardButton("❓ ПОМОЩЬ", callback_data='help_main')]
    ]
    return InlineKeyboardMarkup(keyboard)

def currency_keyboard(prefix, user_id=None, show_favorites=False):
    keyboard = []
    currencies_list = list(CURRENCIES.items())
    
    if show_favorites and user_id:
        favorites = get_favorites(user_id)
        if favorites:
            fav_currencies = [(code, CURRENCIES[code]) for code in favorites if code in CURRENCIES]
            other_currencies = [(code, name) for code, name in currencies_list if code not in favorites]
            currencies_list = fav_currencies + other_currencies
    
    row = []
    for code, name in currencies_list:
        row.append(InlineKeyboardButton(name, callback_data=f'{prefix}_{code}'))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')])
    return InlineKeyboardMarkup(keyboard)

def donate_keyboard():
    keyboard = []
    row = []
    for stars in DONATION_OPTIONS:
        row.append(InlineKeyboardButton(f"⭐ {stars} Stars", callback_data=f'donate_{stars}'))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("✏️ СВОЯ СУММА", callback_data='donate_custom')])
    keyboard.append([InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')])
    return InlineKeyboardMarkup(keyboard)

def favorites_menu(user_id):
    favorites = get_favorites(user_id)
    keyboard = []
    for code in favorites:
        name = CURRENCIES.get(code, code)
        keyboard.append([InlineKeyboardButton(f"❌ {name}", callback_data=f'fav_remove_{code}')])
    keyboard.append([InlineKeyboardButton("➕ ДОБАВИТЬ ВАЛЮТУ", callback_data='fav_add')])
    keyboard.append([InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')])
    
    if not favorites:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ ДОБАВИТЬ ВАЛЮТУ", callback_data='fav_add')],
            [InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]
        ])
    return InlineKeyboardMarkup(keyboard)

def help_menu():
    """Клавиатура для подробной помощи"""
    keyboard = [
        [InlineKeyboardButton("💎 RUB → ВАЛЮТА", callback_data='help_rub_to')],
        [InlineKeyboardButton("💎 ВАЛЮТА → RUB", callback_data='help_to_rub')],
        [InlineKeyboardButton("🔄 ОБМЕН ВАЛЮТ", callback_data='help_exchange')],
        [InlineKeyboardButton("✨ ИНЛАЙН-РЕЖИМ", callback_data='help_inline')],
        [InlineKeyboardButton("⭐ ИЗБРАННОЕ", callback_data='help_favorites')],
        [InlineKeyboardButton("📜 ИСТОРИЯ", callback_data='help_history')],
        [InlineKeyboardButton("📤 ЭКСПОРТ CSV", callback_data='help_export')],
        [InlineKeyboardButton("⭐ ПОДДЕРЖКА", callback_data='help_donate')],
        [InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

def share_button(from_cur, to_cur, amount, result):
    query_text = f"{amount:.2f} {from_cur} в {to_cur}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 ОТПРАВИТЬ В ЧАТ", switch_inline_query=query_text)],
        [InlineKeyboardButton("🔄 НОВАЯ КОНВЕРТАЦИЯ", callback_data='rub_to')],
        [InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]
    ])

# === ИНЛАЙН-РЕЖИМ ===
async def inline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    if not query:
        await update.inline_query.answer([
            InlineQueryResultArticle(
                id="help",
                title="💱 ВАЛЮТНЫЙ КОНВЕРТЕР",
                description="Пример: 100 USD в RUB",
                input_message_content=InputTextMessageContent(
                    "💎 <b>Валютный конвертер</b>\n\n📝 <b>Примеры:</b>\n• 100 USD в RUB\n• 50 RUB в EUR",
                    parse_mode='HTML'
                )
            )
        ], cache_time=300)
        return
    parts = query.split()
    if len(parts) < 4 or parts[2] not in ['в', 'to']:
        await update.inline_query.answer([
            InlineQueryResultArticle(
                id="error",
                title="❌ НЕПРАВИЛЬНЫЙ ФОРМАТ",
                description="Пример: 100 USD в RUB",
                input_message_content=InputTextMessageContent("❌ Используйте: 100 USD в RUB", parse_mode='HTML')
            )
        ], cache_time=300)
        return
    try:
        amount = float(parts[0].replace(',', '.'))
        from_cur = parts[1].upper()
        to_cur = parts[3].upper()
        rates = get_rates()
        if not rates:
            raise Exception("Нет курсов")
        if from_cur == 'RUB':
            converted = amount / rates[to_cur]
            text = f"💰 {amount:.2f} RUB = {converted:.2f} {to_cur}"
            rate_text = f"📊 1 {to_cur} = {1/rates[to_cur]:.4f} RUB"
        elif to_cur == 'RUB':
            converted = amount * rates[from_cur]
            text = f"💰 {amount:.2f} {from_cur} = {converted:.2f} RUB"
            rate_text = f"📊 1 {from_cur} = {rates[from_cur]:.4f} RUB"
        else:
            rub = amount * rates[from_cur]
            converted = rub / rates[to_cur]
            text = f"🔄 {amount:.2f} {from_cur} = {converted:.2f} {to_cur}"
            rate_text = f"📊 1 {from_cur} ≈ {converted/amount:.4f} {to_cur}"
        await update.inline_query.answer([
            InlineQueryResultArticle(
                id="result",
                title=text,
                description=rate_text,
                input_message_content=InputTextMessageContent(f"💎 <b>Валютный конвертер</b>\n\n{text}\n\n{rate_text}", parse_mode='HTML')
            )
        ], cache_time=60)
    except Exception as e:
        await update.inline_query.answer([
            InlineQueryResultArticle(
                id="error",
                title="❌ ОШИБКА",
                description=str(e),
                input_message_content=InputTextMessageContent(f"❌ {e}", parse_mode='HTML')
            )
        ], cache_time=60)

# === КОМАНДЫ БОТА ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    bot = context.bot.username
    await update.message.reply_text(
        f"💎 <b>Валютный конвертер</b> 💎\n\nПривет, {user}! 👋\n\n🇷🇺 <b>Курсы ЦБ РФ</b>\n🕐 Обновление ~15:00 МСК\n\n✨ <b>Быстрый доступ:</b>\n<code>@{bot} 100 USD в RUB</code>\n\n👇 <b>Выберите действие:</b>",
        reply_markup=main_menu(),
        parse_mode='HTML'
    )

async def rub_to_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "💎 <b>RUB → ВАЛЮТА</b>\n\n🇷🇺 Переводим рубли в выбранную валюту\n\n👇 <b>Выберите валюту:</b>",
        reply_markup=currency_keyboard('rub', user_id, show_favorites=True),
        parse_mode='HTML'
    )

async def to_rub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "💎 <b>ВАЛЮТА → RUB</b>\n\n🇷🇺 Переводим валюту в рубли\n\n👇 <b>Выберите валюту:</b>",
        reply_markup=currency_keyboard('to', user_id, show_favorites=True),
        parse_mode='HTML'
    )

async def exchange_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['step'] = 'from'
    user_id = update.effective_user.id
    await update.message.reply_text(
        "🔄 <b>ОБМЕН ВАЛЮТ</b>\n\nКонвертация между любыми валютами\n\n👇 <b>Выберите первую валюту:</b>",
        reply_markup=currency_keyboard('ex', user_id, show_favorites=True),
        parse_mode='HTML'
    )

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_history(update.effective_user.id)
    if not rows:
        await update.message.reply_text("📜 <b>ИСТОРИЯ ПУСТА</b>\n\nСделайте первую конвертацию!", parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]]))
        return
    text = "📜 <b>ПОСЛЕДНИЕ 20 ОПЕРАЦИЙ</b>\n\n"
    for i, row in enumerate(rows, 1):
        text += f"<b>{i}.</b> {row[2]:.2f} {row[0]} → {row[3]:.2f} {row[1]}\n   🕐 {row[4][:16]}\n\n"
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]]))

async def export_csv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    csv_data = export_history_csv(user_id)
    if not csv_data:
        await update.message.reply_text("📤 <b>НЕТ ДАННЫХ ДЛЯ ЭКСПОРТА</b>\n\nСделайте хотя бы одну конвертацию!", parse_mode='HTML')
        return
    await update.message.reply_document(
        document=io.BytesIO(csv_data.encode('utf-8')),
        filename=f"history_{user_id}_{datetime.now().strftime('%Y%m%d')}.csv",
        caption="📊 Ваша история конвертаций"
    )

async def donate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⭐ <b>ПОДДЕРЖАТЬ БОТА</b> ⭐\n\n❤️ Спасибо за поддержку!\n\n👇 <b>Выберите сумму:</b>", reply_markup=donate_keyboard(), parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>ПОМОЩЬ</b>\n\nВыберите раздел, чтобы узнать подробности:",
        reply_markup=help_menu(),
        parse_mode='HTML'
    )

# === ПЛАТЕЖИ ===
async def send_invoice(update, stars):
    try:
        await update.effective_message.reply_invoice(
            title="⭐ ПОДДЕРЖКА БОТА",
            description=f"Сумма: {stars} Stars",
            payload=f"donation_{update.effective_user.id}_{stars}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("Поддержка", stars)],
            start_parameter="donate"
        )
    except:
        await update.effective_message.reply_text("❌ Ошибка платежа")

async def pre_checkout(update, context):
    await update.pre_checkout_query.answer(ok=True)

async def success_payment(update, context):
    stars = update.message.successful_payment.total_amount
    await update.message.reply_text(f"⭐ <b>СПАСИБО ЗА ПОДДЕРЖКУ!</b> ⭐\n\nВы перевели <b>{stars} Stars</b>!\n\n❤️ Ваша помощь помогает развивать бота!", parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]]))

# === ОБРАБОТКА КНОПОК ===
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    bot = context.bot.username
    user_id = update.effective_user.id

    if data == 'menu':
        await query.edit_message_text("👇 <b>Выберите действие:</b>", reply_markup=main_menu(), parse_mode='HTML')
        return

    # === ГЛАВНОЕ МЕНЮ ПОМОЩИ ===
    if data == 'help_main':
        await query.edit_message_text(
            "📖 <b>ПОМОЩЬ</b>\n\nВыберите раздел, чтобы узнать подробности:",
            reply_markup=help_menu(),
            parse_mode='HTML'
        )
        return

    # === ПОДРОБНЫЕ ИНСТРУКЦИИ ===
    if data == 'help_rub_to':
        await query.edit_message_text(
            "💎 <b>ИНСТРУКЦИЯ: RUB → ВАЛЮТА</b>\n\n"
            "📌 <b>Что делает:</b>\n"
            "Переводит рубли в любую другую валюту\n\n"
            "📝 <b>Как использовать:</b>\n"
            "1️⃣ Нажмите на кнопку <b>«RUB → ВАЛЮТА»</b>\n"
            "2️⃣ Выберите валюту из списка (например, 🇺🇸 Доллар США)\n"
            "3️⃣ Введите сумму в рублях (например, 5000)\n"
            "4️⃣ Бот покажет результат\n\n"
            "💡 <b>Пример:</b>\n"
            "Ввели: 5000 рублей, выбрали USD\n"
            "Результат: 5000 RUB = 53.76 USD\n\n"
            "✨ <b>Совет:</b> После результата есть кнопка «ОТПРАВИТЬ В ЧАТ» — можно поделиться!",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД К ПОМОЩИ", callback_data='help_main')]])
        )
        return

    if data == 'help_to_rub':
        await query.edit_message_text(
            "💎 <b>ИНСТРУКЦИЯ: ВАЛЮТА → RUB</b>\n\n"
            "📌 <b>Что делает:</b>\n"
            "Переводит любую валюту в рубли\n\n"
            "📝 <b>Как использовать:</b>\n"
            "1️⃣ Нажмите на кнопку <b>«ВАЛЮТА → RUB»</b>\n"
            "2️⃣ Выберите валюту из списка (например, 🇺🇸 Доллар США)\n"
            "3️⃣ Введите сумму в выбранной валюте (например, 100)\n"
            "4️⃣ Бот покажет результат в рублях\n\n"
            "💡 <b>Пример:</b>\n"
            "Ввели: 100 USD\n"
            "Результат: 100 USD = 9295 RUB\n\n"
            "✨ <b>Совет:</b> Курс обновляется раз в день от ЦБ РФ.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД К ПОМОЩИ", callback_data='help_main')]])
        )
        return

    if data == 'help_exchange':
        await query.edit_message_text(
            "🔄 <b>ИНСТРУКЦИЯ: ОБМЕН ВАЛЮТ</b>\n\n"
            "📌 <b>Что делает:</b>\n"
            "Переводит из любой валюты в любую (минуя рубли)\n\n"
            "📝 <b>Как использовать:</b>\n"
            "1️⃣ Нажмите на кнопку <b>«ОБМЕН ВАЛЮТ»</b>\n"
            "2️⃣ Выберите <b>из какой</b> валюты переводим\n"
            "3️⃣ Выберите <b>в какую</b> валюту переводим\n"
            "4️⃣ Введите сумму\n"
            "5️⃣ Бот покажет результат\n\n"
            "💡 <b>Пример:</b>\n"
            "Выбрали: USD → EUR, ввели 100\n"
            "Результат: 100 USD = 92.50 EUR\n\n"
            "✨ <b>Совет:</b> Удобно, когда нужно узнать курс евро к доллару!",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД К ПОМОЩИ", callback_data='help_main')]])
        )
        return

    if data == 'help_inline':
        await query.edit_message_text(
            f"✨ <b>ИНСТРУКЦИЯ: ИНЛАЙН-РЕЖИМ</b>\n\n"
            f"📌 <b>Что делает:</b>\n"
            f"Позволяет конвертировать валюту в <b>любом чате</b>, даже не открывая бота!\n\n"
            f"📝 <b>Как использовать:</b>\n"
            f"1️⃣ Откройте <b>любой чат</b> (с собой, с другом, группу)\n"
            f"2️⃣ Начните вводить:\n"
            f"<code>@{bot} 100 USD в RUB</code>\n"
            f"3️⃣ Нажмите на появившийся результат\n"
            f"4️⃣ Результат отправится в чат\n\n"
            f"📋 <b>Примеры запросов:</b>\n"
            f"• <code>@{bot} 100 USD в RUB</code> — доллары → рубли\n"
            f"• <code>@{bot} 50 RUB в EUR</code> — рубли → евро\n"
            f"• <code>@{bot} 2000 RUB в TRY</code> — рубли → лиры\n"
            f"• <code>@{bot} 10 EUR в USD</code> — евро → доллары\n\n"
            f"💡 <b>Совет:</b> Попробуйте прямо сейчас в этом чате!",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД К ПОМОЩИ", callback_data='help_main')]])
        )
        return

    if data == 'help_favorites':
        await query.edit_message_text(
            "⭐ <b>ИНСТРУКЦИЯ: ИЗБРАННОЕ</b>\n\n"
            "📌 <b>Что делает:</b>\n"
            "Позволяет отметить любимые валюты — они будут показываться первыми в списках\n\n"
            "📝 <b>Как использовать:</b>\n"
            "1️⃣ Нажмите на кнопку <b>«ИЗБРАННОЕ»</b>\n"
            "2️⃣ Нажмите <b>«ДОБАВИТЬ ВАЛЮТУ»</b>\n"
            "3️⃣ Выберите валюту из списка\n"
            "4️⃣ Готово! Теперь эта валюта будет первой при конвертации\n\n"
            "❌ <b>Как удалить:</b>\n"
            "В меню избранного нажмите на ❌ рядом с валютой\n\n"
            "💡 <b>Совет:</b> Добавьте USD, EUR, TRY — самые популярные валюты!",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД К ПОМОЩИ", callback_data='help_main')]])
        )
        return

    if data == 'help_history':
        await query.edit_message_text(
            "📜 <b>ИНСТРУКЦИЯ: ИСТОРИЯ</b>\n\n"
            "📌 <b>Что делает:</b>\n"
            "Сохраняет все ваши конвертации и показывает последние 20\n\n"
            "📝 <b>Как использовать:</b>\n"
            "1️⃣ Нажмите на кнопку <b>«ИСТОРИЯ»</b>\n"
            "2️⃣ Бот покажет список последних конвертаций\n\n"
            "📋 <b>Что сохраняется:</b>\n"
            "• Какая сумма была\n"
            "• Из какой валюты в какую\n"
            "• Результат конвертации\n"
            "• Дата и время операции\n\n"
            "💡 <b>Совет:</b> История сохраняется автоматически, ничего настраивать не нужно!",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД К ПОМОЩИ", callback_data='help_main')]])
        )
        return

    if data == 'help_export':
        await query.edit_message_text(
            "📤 <b>ИНСТРУКЦИЯ: ЭКСПОРТ CSV</b>\n\n"
            "📌 <b>Что делает:</b>\n"
            "Выгружает ВСЮ историю конвертаций в файл Excel/CSV\n\n"
            "📝 <b>Как использовать:</b>\n"
            "1️⃣ Нажмите на кнопку <b>«ЭКСПОРТ CSV»</b>\n"
            "2️⃣ Бот пришлёт файл с историей\n"
            "3️⃣ Откройте файл в Excel, Google Sheets или любом редакторе\n\n"
            "📋 <b>Что будет в файле:</b>\n"
            "• Из валюты\n"
            "• В валюту\n"
            "• Сумма\n"
            "• Результат\n"
            "• Дата и время каждой операции\n\n"
            "💡 <b>Совет:</b> Удобно для учёта расходов или отчёта!",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД К ПОМОЩИ", callback_data='help_main')]])
        )
        return

    if data == 'help_donate':
        await query.edit_message_text(
            "⭐ <b>ИНСТРУКЦИЯ: ПОДДЕРЖКА БОТА</b>\n\n"
            "📌 <b>Что делает:</b>\n"
            "Позволяет поддержать разработку бота через Telegram Stars\n\n"
            "📝 <b>Как использовать:</b>\n"
            "1️⃣ Нажмите на кнопку <b>«ПОДДЕРЖАТЬ»</b>\n"
            "2️⃣ Выберите сумму доната (5, 10, 20, 50, 100, 250, 500 или свою)\n"
            "3️⃣ Подтвердите оплату в Telegram\n"
            "4️⃣ Готово! Спасибо за поддержку ❤️\n\n"
            "💰 <b>Что такое Stars:</b>\n"
            "• 1 Star ≈ 1 рубль\n"
            "• Покупаются внутри Telegram\n"
            "• Средства поступают разработчику\n\n"
            "💡 <b>Совет:</b> Все донаты идут на улучшение бота и новые функции!",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД К ПОМОЩИ", callback_data='help_main')]])
        )
        return

    # === ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ===
    if data == 'inline_help':
        await query.edit_message_text(
            f"✨ <b>ИНЛАЙН-РЕЖИМ</b>\n\n💡 Как использовать:\n1️⃣ Откройте любой чат\n2️⃣ Напишите: <code>@{bot} 100 USD в RUB</code>\n3️⃣ Нажмите на результат\n\n📋 Примеры:\n• <code>@{bot} 100 USD в RUB</code>\n• <code>@{bot} 50 RUB в EUR</code>",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]])
        )
        return

    if data == 'history':
        rows = get_history(user_id)
        if not rows:
            await query.edit_message_text("📜 ИСТОРИЯ ПУСТА", parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]]))
            return
        text = "📜 <b>ПОСЛЕДНИЕ 20 ОПЕРАЦИЙ</b>\n\n"
        for i, row in enumerate(rows, 1):
            text += f"<b>{i}.</b> {row[2]:.2f} {row[0]} → {row[3]:.2f} {row[1]}\n   🕐 {row[4][:16]}\n\n"
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]]))
        return

    if data == 'export_csv':
        csv_data = export_history_csv(user_id)
        if not csv_data:
            await query.edit_message_text("📤 НЕТ ДАННЫХ ДЛЯ ЭКСПОРТА", parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]]))
            return
        await query.message.reply_document(
            document=io.BytesIO(csv_data.encode('utf-8')),
            filename=f"history_{user_id}_{datetime.now().strftime('%Y%m%d')}.csv",
            caption="📊 Ваша история конвертаций"
        )
        await query.delete_message()
        return

    if data == 'donate':
        await query.edit_message_text("⭐ <b>ВЫБЕРИТЕ СУММУ:</b>", reply_markup=donate_keyboard(), parse_mode='HTML')
        return

    if data == 'donate_custom':
        context.user_data['mode'] = 'donate_custom'
        await query.edit_message_text("✏️ <b>ВВЕДИТЕ СУММУ</b>\n\nОтправьте число от 1 до 2500:", parse_mode='HTML')
        return

    if data.startswith('donate_'):
        stars = int(data.split('_')[1])
        await send_invoice(update, stars)
        return

    # === ИЗБРАННОЕ ===
    if data == 'favorites_menu':
        await query.edit_message_text("⭐ <b>ИЗБРАННЫЕ ВАЛЮТЫ</b>\n\nНажмите ❌ чтобы удалить валюту из избранного:", reply_markup=favorites_menu(user_id), parse_mode='HTML')
        return

    if data == 'fav_add':
        await query.edit_message_text(
            "➕ <b>ДОБАВИТЬ В ИЗБРАННОЕ</b>\n\n👇 Выберите валюту, которую хотите добавить:",
            reply_markup=currency_keyboard('fav_add', user_id, show_favorites=False),
            parse_mode='HTML'
        )
        return

    if data.startswith('fav_add_'):
        code = data.split('_')[2]
        add_favorite(user_id, code)
        name = CURRENCIES.get(code, code)
        await query.edit_message_text(f"✅ <b>{name}</b> добавлена в избранное!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⭐ В ИЗБРАННОЕ", callback_data='favorites_menu'), InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]]), parse_mode='HTML')
        return

    if data.startswith('fav_remove_'):
        code = data.split('_')[2]
        remove_favorite(user_id, code)
        name = CURRENCIES.get(code, code)
        await query.edit_message_text(f"❌ <b>{name}</b> удалена из избранного!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⭐ В ИЗБРАННОЕ", callback_data='favorites_menu'), InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]]), parse_mode='HTML')
        return

    # === ОСНОВНЫЕ КНОПКИ ===
    if data == 'rub_to':
        await query.edit_message_text("💎 <b>RUB → ВАЛЮТА</b>\n\n👇 Выберите валюту:", reply_markup=currency_keyboard('rub', user_id, show_favorites=True), parse_mode='HTML')
        return

    if data.startswith('rub_'):
        code = data.split('_')[1]
        context.user_data['mode'] = f'rub_{code}'
        await query.edit_message_text(f"💎 <b>{CURRENCIES[code]}</b>\n\n🇷🇺 Введите сумму в рублях:", parse_mode='HTML')
        return

    if data == 'to_rub':
        await query.edit_message_text("💎 <b>ВАЛЮТА → RUB</b>\n\n👇 Выберите валюту:", reply_markup=currency_keyboard('to', user_id, show_favorites=True), parse_mode='HTML')
        return

    if data.startswith('to_'):
        code = data.split('_')[1]
        context.user_data['mode'] = f'to_{code}'
        await query.edit_message_text(f"💎 <b>{CURRENCIES[code]}</b>\n\nВведите сумму в {code}:", parse_mode='HTML')
        return

    if data == 'exchange':
        context.user_data['step'] = 'from'
        await query.edit_message_text("🔄 <b>ОБМЕН ВАЛЮТ</b>\n\n👇 Выберите первую валюту:", reply_markup=currency_keyboard('ex', user_id, show_favorites=True), parse_mode='HTML')
        return

    if data.startswith('ex_'):
        code = data.split('_')[1]
        if context.user_data.get('step') == 'from':
            context.user_data['ex_from'] = code
            context.user_data['step'] = 'to'
            await query.edit_message_text(f"📤 <b>ИЗ:</b> {CURRENCIES[code]}\n\n👇 Выберите вторую валюту:", reply_markup=currency_keyboard('ex', user_id, show_favorites=True), parse_mode='HTML')
        else:
            from_cur = context.user_data.get('ex_from')
            to_cur = code
            context.user_data['mode'] = f'ex_{from_cur}_{to_cur}'
            context.user_data['step'] = None
            await query.edit_message_text(f"🔄 <b>{CURRENCIES[from_cur]} → {CURRENCIES[to_cur]}</b>\n\nВведите сумму в {from_cur}:", parse_mode='HTML')
        return

# === ОБРАБОТКА СУММ ===
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get('mode')
    if not mode:
        await update.message.reply_text("❌ <b>Сначала выберите действие</b>\n\nНажмите /start", parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data='menu')]]))
        return

    if mode == 'donate_custom':
        try:
            stars = int(update.message.text)
            if 1 <= stars <= 2500:
                await send_invoice(update, stars)
                context.user_data['mode'] = None
            else:
                await update.message.reply_text("❌ Введите число от 1 до 2500")
        except:
            await update.message.reply_text("❌ Введите целое число")
        return

    try:
        amount = float(update.message.text.replace(',', '.'))
        if amount <= 0:
            await update.message.reply_text("❌ Введите положительное число!")
            return
    except:
        await update.message.reply_text("❌ <b>Неправильный формат</b>\n\nВведите число, например: 100", parse_mode='HTML')
        return

    rates = get_rates()
    if not rates:
        await update.message.reply_text("⚠️ Ошибка получения курсов. Попробуйте позже.")
        return

    user_id = update.effective_user.id

    if mode.startswith('rub_'):
        to_cur = mode.split('_')[1]
        if to_cur not in rates:
            await update.message.reply_text("❌ Ошибка с валютой")
            return
        converted = amount / rates[to_cur]
        save_history(user_id, "RUB", to_cur, amount, converted)
        await update.message.reply_text(
            f"✅ <b>ГОТОВО!</b>\n\n💎 <b>{amount:.2f} RUB</b> = <b>{converted:.2f} {to_cur}</b>\n\n📊 1 {to_cur} = {1/rates[to_cur]:.4f} RUB\n\n👇 <b>Выберите действие:</b>",
            reply_markup=share_button("RUB", to_cur, amount, converted),
            parse_mode='HTML'
        )

    elif mode.startswith('to_'):
        from_cur = mode.split('_')[1]
        if from_cur not in rates:
            await update.message.reply_text("❌ Ошибка с валютой")
            return
        converted = amount * rates[from_cur]
        save_history(user_id, from_cur, "RUB", amount, converted)
        await update.message.reply_text(
            f"✅ <b>ГОТОВО!</b>\n\n💎 <b>{amount:.2f} {from_cur}</b> = <b>{converted:.2f} RUB</b>\n\n📊 1 {from_cur} = {rates[from_cur]:.4f} RUB\n\n👇 <b>Выберите действие:</b>",
            reply_markup=share_button(from_cur, "RUB", amount, converted),
            parse_mode='HTML'
        )

    elif mode.startswith('ex_'):
        parts = mode.split('_')
        from_cur = parts[1]
        to_cur = parts[2]
        if from_cur not in rates or to_cur not in rates:
            await update.message.reply_text("❌ Ошибка с валютами")
            return
        rub = amount * rates[from_cur]
        converted = rub / rates[to_cur]
        save_history(user_id, from_cur, to_cur, amount, converted)
        await update.message.reply_text(
            f"✅ <b>ГОТОВО!</b>\n\n💎 <b>{amount:.2f} {from_cur}</b> = <b>{converted:.2f} {to_cur}</b>\n\n📊 1 {from_cur} ≈ {converted/amount:.4f} {to_cur}\n\n👇 <b>Выберите действие:</b>",
            reply_markup=share_button(from_cur, to_cur, amount, converted),
            parse_mode='HTML'
        )

    else:
        await update.message.reply_text("❌ Ошибка. Начните заново: /start")

    context.user_data['mode'] = None

# === ЗАПУСК ===
def main():
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rub_to", rub_to_command))
    app.add_handler(CommandHandler("to_rub", to_rub_command))
    app.add_handler(CommandHandler("exchange", exchange_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("donate", donate_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(InlineQueryHandler(inline_handler))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, success_payment))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    print("💎 БОТ ЗАПУЩЕН! Добавлены: Избранное, Экспорт CSV, Подробная помощь")
    app.run_polling()

if __name__ == '__main__':
    main()
