import asyncio
import logging
import aiosqlite
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8849587958:AAGD3YKBqTzKB3co00dVZqNot75nQblxYTM"  
ADMIN_ID = 706754876

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)

dp = Dispatcher()
router = Router()
dp.include_router(router)

# ================= БАЗА ДАННЫХ =================
async def init_db():
    async with aiosqlite.connect('beauty_bot.db') as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS services 
                            (id INTEGER PRIMARY KEY, name TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS schedule 
                            (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, time TEXT, is_booked BOOLEAN DEFAULT 0)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS bookings 
                            (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, 
                             phone TEXT, service_id INTEGER, schedule_id INTEGER, created_at TEXT)''')
        
        # Очищаем старые услуги и добавляем актуальные
        await db.execute("DELETE FROM services")
        services = [("Кератин/Ботокс",), ("Холодное восстановление",), ("Пилинг кожи головы",)]
        await db.executemany("INSERT INTO services (name) VALUES (?)", services)
        await db.commit()

# ================= СОСТОЯНИЯ (FSM) =================
class ClientBooking(StatesGroup):
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    sending_phone = State()

class AdminSchedule(StatesGroup):
    adding_date = State()
    adding_times = State()

# ================= ГЛАВНОЕ МЕНЮ =================
def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Записаться на процедуру", callback_data="book_start")
    kb.button(text="📋 Мои записи", callback_data="my_bookings")
    kb.button(text="📞 Связь с мастером", callback_data="contacts")
    kb.adjust(1)
    return kb.as_markup()

async def show_main_menu(callback_or_message, state: FSMContext, first_name=""):
    await state.clear()
    text = f"Здравствуйте, {first_name}! ✨\nЯ бот мастера красоты. Помогу записаться на процедуры или посмотреть свои записи."
    if isinstance(callback_or_message, CallbackQuery):
        await callback_or_message.message.edit_text(text, reply_markup=main_menu_kb())
        await callback_or_message.answer()
    else:
        await callback_or_message.answer(text, reply_markup=main_menu_kb())

# ================= КЛИЕНТСКАЯ ЧАСТЬ =================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await show_main_menu(message, state, message.from_user.first_name)

@router.callback_query(F.data == "book_start")
async def process_book_start(callback: CallbackQuery, state: FSMContext):
    async with aiosqlite.connect('beauty_bot.db') as db:
        cursor = await db.execute("SELECT id, name FROM services")
        services = await cursor.fetchall()
    
    kb = InlineKeyboardBuilder()
    for s_id, s_name in services:
        kb.button(text=s_name, callback_data=f"service_{s_id}")
    kb.button(text="✅ Готово (выбрать дату)", callback_data="service_done")
    kb.button(text="❌ Отмена", callback_data="cancel_to_menu")
    kb.adjust(1)
    await callback.message.edit_text(
        "Выберите процедуру (можно выбрать несколько):\n\n"
        "💡 Нажмите на нужные услуги, затем 'Готово'",
        reply_markup=kb.as_markup()
    )
    await state.set_state(ClientBooking.choosing_service)
    await state.update_data(selected_services=[])
    await callback.answer()

@router.callback_query(F.data.startswith("service_"), StateFilter(ClientBooking.choosing_service))
async def process_service(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.split("_")[1])
    data = await state.get_data()
    selected = data.get("selected_services", [])
    
    if service_id in selected:
        selected.remove(service_id)
    else:
        selected.append(service_id)
    
    await state.update_data(selected_services=selected)
    
    async with aiosqlite.connect('beauty_bot.db') as db:
        cursor = await db.execute("SELECT id, name FROM services")
        services = await cursor.fetchall()
    
    kb = InlineKeyboardBuilder()
    for s_id, s_name in services:
        mark = "✅ " if s_id in selected else ""
        kb.button(text=f"{mark}{s_name}", callback_data=f"service_{s_id}")
    kb.button(text="✅ Готово (выбрать дату)", callback_data="service_done")
    kb.button(text="❌ Отмена", callback_data="cancel_to_menu")
    kb.adjust(1)
    
    selected_names = [name for sid, name in services if sid in selected]
    if selected_names:
        text = f"Выбрано: {', '.join(selected_names)}\n\nМожете выбрать ещё или нажать 'Готово'"
    else:
        text = "Выберите процедуру (можно выбрать несколько):\n\n💡 Нажмите на нужные услуги, затем 'Готово'"
    
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data == "service_done", StateFilter(ClientBooking.choosing_service))
async def service_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_services", [])
    
    if not selected:
        await callback.answer("⚠️ Выберите хотя бы одну услугу!", show_alert=True)
        return
    
    async with aiosqlite.connect('beauty_bot.db') as db:
        cursor = await db.execute("SELECT DISTINCT date FROM schedule WHERE is_booked = 0 AND date >= date('now') ORDER BY date")
        dates = await cursor.fetchall()
    
    if not dates:
        await callback.message.edit_text("К сожалению, свободных окошек пока нет. Нажмите 'Связь с мастером' для индивидуальной записи!")
        await state.clear()
        return

    kb = InlineKeyboardBuilder()
    for (date_str,) in dates:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        kb.button(text=dt.strftime("%d.%m (%a)"), callback_data=f"date_{date_str}")
    kb.button(text="❌ Отмена", callback_data="cancel_to_menu")
    kb.adjust(2)
    await callback.message.edit_text("Выберите удобную дату:", reply_markup=kb.as_markup())
    await state.set_state(ClientBooking.choosing_date)
    await callback.answer()

@router.callback_query(F.data.startswith("date_"), StateFilter(ClientBooking.choosing_date))
async def process_date(callback: CallbackQuery, state: FSMContext):
    date_str = callback.data.split("_")[1]
    await state.update_data(date_str=date_str)
    
    async with aiosqlite.connect('beauty_bot.db') as db:
        cursor = await db.execute("SELECT id, time FROM schedule WHERE date = ? AND is_booked = 0", (date_str,))
        times = await cursor.fetchall()
    
    kb = InlineKeyboardBuilder()
    for t_id, t_time in times:
        kb.button(text=t_time, callback_data=f"time_{t_id}")
    kb.button(text="️ Назад к услугам", callback_data="book_start")
    kb.button(text="❌ Отмена", callback_data="cancel_to_menu")
    kb.adjust(3)
    await callback.message.edit_text(f"Выберите время на {date_str}:", reply_markup=kb.as_markup())
    await state.set_state(ClientBooking.choosing_time)
    await callback.answer()

@router.callback_query(F.data.startswith("time_"), StateFilter(ClientBooking.choosing_time))
async def process_time(callback: CallbackQuery, state: FSMContext):
    schedule_id = int(callback.data.split("_")[1])
    await state.update_data(schedule_id=schedule_id)
    
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Отправить контакт", request_contact=True)]], resize_keyboard=True)
    await callback.message.edit_text("Отлично! Теперь поделитесь вашим номером телефона для связи, нажав на кнопку ниже.", reply_markup=kb)
    await state.set_state(ClientBooking.sending_phone)
    await callback.answer()

@router.message(F.contact, StateFilter(ClientBooking.sending_phone))
async def process_phone(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    phone = message.contact.phone_number
    username = message.from_user.username or message.from_user.first_name
    
    async with aiosqlite.connect('beauty_bot.db') as db:
        for service_id in data['selected_services']:
            await db.execute("UPDATE schedule SET is_booked = 1 WHERE id = ?", (data['schedule_id'],))
            await db.execute(
                "INSERT INTO bookings (user_id, username, phone, service_id, schedule_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (message.from_user.id, username, phone, service_id, data['schedule_id'], datetime.now().isoformat())
            )
        
        cursor = await db.execute("SELECT date, time FROM schedule WHERE id = ?", (data['schedule_id'],))
        date_str, time_str = await cursor.fetchone()
        await db.commit()

    # Собираем список услуг
    services_text = ""
    async with aiosqlite.connect('beauty_bot.db') as db:
        for service_id in data['selected_services']:
            cursor = await db.execute("SELECT name FROM services WHERE id = ?", (service_id,))
            name = (await cursor.fetchone())[0]
            services_text += f"• {name}\n"

    # Уведомление мастеру
    await bot.send_message(ADMIN_ID, 
        f"🔔 <b>НОВАЯ ЗАПИСЬ!</b>\n\n"
        f"👤 Клиент: @{username} ({phone})\n"
        f"💇‍♀️ Услуги:\n{services_text}\n"
        f"📅 Дата: {date_str}\n"
        f"⏰ Время: {time_str}")
    
    # Подтверждение клиенту
    await message.answer(
        f"Вы успешно записаны! ✨\n"
        f"💇‍♀️ Услуги:\n{services_text}\n"
        f"📅 {date_str} в {time_str}\n\n"
        f"Жду вас! За 24 часа я пришлю вам напоминание. 💖",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.clear()

@router.callback_query(F.data == "my_bookings")
async def my_bookings_handler(callback: CallbackQuery):
    async with aiosqlite.connect('beauty_bot.db') as db:
        cursor = await db.execute('''
            SELECT s.name, sch.date, sch.time 
            FROM bookings b
            JOIN services s ON b.service_id = s.id
            JOIN schedule sch ON b.schedule_id = sch.id
            WHERE b.user_id = ? AND sch.is_booked = 1
        ''', (callback.from_user.id,))
        bookings = await cursor.fetchall()
    
    if not bookings:
        text = "У вас пока нет активных записей. 📅"
    else:
        text = "📋 <b>Ваши записи:</b>\n\n"
        for name, date, time in bookings:
            text += f"💇‍♀️ {name}\n📅 {date} в {time}\n\n"
    
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В главное меню", callback_data="start_back")
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data == "contacts")
async def contacts_handler(callback: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В главное меню", callback_data="start_back")
    await callback.message.edit_text(
        "📞 Связь с мастером:\n\n"
        "Telegram: @soresssa\n"
        "VK: https://vk.ru/savchenko_ss\n"
        "Анастасия",
        reply_markup=kb.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "start_back")
async def start_back_handler(callback: CallbackQuery, state: FSMContext):
    await show_main_menu(callback, state, callback.from_user.first_name)

@router.callback_query(F.data == "cancel_to_menu")
async def cancel_to_menu(callback: CallbackQuery, state: FSMContext):
    await show_main_menu(callback, state, callback.from_user.first_name)

# ================= АДМИН-ПАНЕЛЬ =================
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить окошки", callback_data="admin_add_schedule")
    kb.button(text=" Все записи на сегодня", callback_data="admin_today")
    kb.button(text="🗑 Удалить все записи", callback_data="admin_clear")
    kb.adjust(1)
    await message.answer("🔐 Админ-панель:", reply_markup=kb.as_markup())

@router.callback_query(F.data == "admin_add_schedule", F.from_user.id == ADMIN_ID)
async def admin_add_schedule(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Введите дату в формате ГГГГ-ММ-ДД (например, 2026-07-25):")
    await state.set_state(AdminSchedule.adding_date)
    await callback.answer()

@router.message(StateFilter(AdminSchedule.adding_date), F.from_user.id == ADMIN_ID)
async def admin_process_date(message: Message, state: FSMContext):
    try:
        datetime.strptime(message.text, "%Y-%m-%d")
        await state.update_data(date_str=message.text)
        await message.answer("Теперь введите время через запятую (например: 10:00, 12:00, 15:30):")
        await state.set_state(AdminSchedule.adding_times)
    except ValueError:
        await message.answer("Неверный формат. Попробуйте еще раз (ГГГГ-ММ-ДД).")

@router.message(StateFilter(AdminSchedule.adding_times), F.from_user.id == ADMIN_ID)
async def admin_process_times(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    times = [t.strip() for t in message.text.split(",")]
    
    async with aiosqlite.connect('beauty_bot.db') as db:
        for t in times:
            await db.execute("INSERT INTO schedule (date, time) VALUES (?, ?)", (data['date_str'], t))
        await db.commit()
        
    await message.answer(f"✅ Окошки на {data['date_str']} добавлены: {', '.join(times)}")
    await state.clear()

@router.callback_query(F.data == "admin_today", F.from_user.id == ADMIN_ID)
async def admin_today_handler(callback: CallbackQuery):
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect('beauty_bot.db') as db:
        cursor = await db.execute('''
            SELECT b.username, b.phone, s.name, sch.time 
            FROM bookings b
            JOIN services s ON b.service_id = s.id
            JOIN schedule sch ON b.schedule_id = sch.id
            WHERE sch.date = ? AND sch.is_booked = 1
        ''', (today,))
        bookings = await cursor.fetchall()
    
    if not bookings:
        text = f"📊 На сегодня ({today}) записей нет."
    else:
        text = f"📊 <b>Записи на сегодня ({today}):</b>\n\n"
        for username, phone, name, time in bookings:
            text += f"⏰ {time} | {name}\n👤 {username} ({phone})\n\n"
            
    kb = InlineKeyboardBuilder()
    kb.button(text="️ В главное меню", callback_data="start_back")
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data == "admin_clear", F.from_user.id == ADMIN_ID)
async def admin_clear_handler(callback: CallbackQuery):
    async with aiosqlite.connect('beauty_bot.db') as db:
        await db.execute("DELETE FROM bookings")
        await db.execute("UPDATE schedule SET is_booked = 0")
        await db.commit()
    await callback.message.edit_text("🗑 Все записи удалены, окошки снова свободны.")
    await callback.answer()

# ================= НАПОМИНАНИЯ =================
async def check_reminders(bot: Bot):
    now = datetime.now()
    reminder_time = now + timedelta(hours=24)
    target_date = reminder_time.strftime("%Y-%m-%d")
    target_time = reminder_time.strftime("%H:%M")
    
    async with aiosqlite.connect('beauty_bot.db') as db:
        cursor = await db.execute('''
            SELECT b.user_id, s.name, sch.date, sch.time 
            FROM bookings b
            JOIN services s ON b.service_id = s.id
            JOIN schedule sch ON b.schedule_id = sch.id
            WHERE sch.date = ? AND sch.time = ? AND sch.is_booked = 1
        ''', (target_date, target_time))
        upcoming = await cursor.fetchall()
        
    for user_id, service_name, date, time in upcoming:
        try:
            await bot.send_message(user_id, 
                f"🔔 <b>Напоминание!</b>\n\n"
                f"Завтра, {date} в {time}, я жду вас на процедуру:\n"
                f"💇‍♀️ {service_name}\n\n"
                f"Если у вас изменились планы, пожалуйста, предупредите меня заранее! 💖")
        except Exception as e:
            logging.error(f"Не удалось отправить напоминание {user_id}: {e}")

# ================= ЗАПУСК =================
async def on_startup():
    await init_db()
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(check_reminders, 'cron', hour='*', args=[bot])
    scheduler.start()
    logging.info("✅ Бот успешно запущен и готов к работе!")

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
