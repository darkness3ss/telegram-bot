import asyncio
import logging
import aiosqlite
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8849587958:AAGD3YKBqTzKB3co00dVZqNot75nQblxYTM"  
MAIN_ADMIN_ID = 706754876
ADMIN_PASSWORD = "120126"

# Путь к базе данных (используем volume если есть)
DB_PATH = os.environ.get("DB_PATH", "/data/beauty_bot.db")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)

dp = Dispatcher()
router = Router()
dp.include_router(router)

# ================= БАЗА ДАННЫХ =================
async def init_db():
    # Создаём папку для базы данных если её нет
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS services 
                            (id INTEGER PRIMARY KEY, name TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS schedule 
                            (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, time TEXT, is_booked BOOLEAN DEFAULT 0)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS bookings 
                            (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, 
                             phone TEXT, service_id INTEGER, schedule_id INTEGER, created_at TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS admins 
                            (id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, username TEXT, added_at TEXT)''')
        
        # Добавляем услуги только если таблица пустая (НЕ очищаем!)
        cursor = await db.execute("SELECT COUNT(*) FROM services")
        if (await cursor.fetchone())[0] == 0:
            services = [("Кератин/Ботокс",), ("Холодное восстановление",), ("Пилинг кожи головы",)]
            await db.executemany("INSERT INTO services (name) VALUES (?)", services)
        
        # Добавляем главного админа если его нет
        await db.execute("INSERT OR IGNORE INTO admins (user_id, username, added_at) VALUES (?, ?, ?)",
                        (MAIN_ADMIN_ID, "main_admin", datetime.now().isoformat()))
        await db.commit()

async def get_admin_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id FROM admins")
        admins = await cursor.fetchall()
    return [admin[0] for admin in admins]

async def is_admin(user_id: int) -> bool:
    admin_ids = await get_admin_ids()
    return user_id in admin_ids

# ================= СОСТОЯНИЯ (FSM) =================
class ClientBooking(StatesGroup):
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    sending_phone = State()

class AdminAuth(StatesGroup):
    waiting_password = State()

class AdminSchedule(StatesGroup):
    adding_date = State()
    adding_times = State()

class AdminAdd(StatesGroup):
    waiting_admin_id = State()

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

# ================= АДМИН МЕНЮ =================
def admin_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить окошки", callback_data="admin_add_schedule")
    kb.button(text="📊 Все записи на сегодня", callback_data="admin_today")
    kb.button(text="🗑 Удалить все записи", callback_data="admin_clear")
    kb.button(text="👥 Управление админами", callback_data="admin_manage")
    kb.button(text="🚪 Выйти из админки", callback_data="admin_logout")
    kb.adjust(1)
    return kb.as_markup()

async def show_admin_menu(callback_or_message, state: FSMContext):
    if isinstance(callback_or_message, CallbackQuery):
        await callback_or_message.message.edit_text("🔐 Админ-панель:", reply_markup=admin_menu_kb())
        await callback_or_message.answer()
    else:
        await callback_or_message.answer("🔐 Админ-панель:", reply_markup=admin_menu_kb())

# ================= КЛИЕНТСКАЯ ЧАСТЬ =================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state and current_state.startswith("Admin"):
        kb = InlineKeyboardBuilder()
        kb.button(text="Да, выйти", callback_data="admin_logout")
        kb.button(text="Нет, остаться в админке", callback_data="stay_admin")
        await message.answer("⚠️ Вы находитесь в админ-режиме. Выйти в главное меню?", reply_markup=kb.as_markup())
        return
    await show_main_menu(message, state, message.from_user.first_name)

@router.callback_query(F.data == "stay_admin")
async def stay_admin_handler(callback: CallbackQuery, state: FSMContext):
    await show_admin_menu(callback, state)

@router.callback_query(F.data == "book_start")
async def process_book_start(callback: CallbackQuery, state: FSMContext):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, name FROM services")
        services = await cursor.fetchall()
    
    kb = InlineKeyboardBuilder()
    for s_id, s_name in services:
        kb.button(text=s_name, callback_data=f"service_{s_id}")
    kb.button(text="✅ Готово (выбрать дату)", callback_data="service_done")
    kb.button(text=" Отмена", callback_data="cancel_to_menu")
    kb.adjust(1)
    await callback.message.edit_text(
        "Выберите процедуру (можно выбрать несколько):\n\n💡 Нажмите на нужные услуги, затем 'Готово'",
        reply_markup=kb.as_markup()
    )
    await state.set_state(ClientBooking.choosing_service)
    await state.update_data(selected_services=[])
    await callback.answer()

@router.callback_query(F.data.startswith("service_") & ~F.data.endswith("service_done"), StateFilter(ClientBooking.choosing_service))
async def process_service(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.split("_")[1])
    data = await state.get_data()
    selected = data.get("selected_services", [])
    
    if service_id in selected:
        selected.remove(service_id)
    else:
        selected.append(service_id)
    
    await state.update_data(selected_services=selected)
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, name FROM services")
        services = await cursor.fetchall()
    
    kb = InlineKeyboardBuilder()
    for s_id, s_name in services:
        mark = "✅ " if s_id in selected else ""
        kb.button(text=f"{mark}{s_name}", callback_data=f"service_{s_id}")
    kb.button(text="✅ Готово (выбрать дату)", callback_data="service_done")
    kb.button(text=" Отмена", callback_data="cancel_to_menu")
    kb.adjust(1)
    
    selected_names = [name for sid, name in services if sid in selected]
    if selected_names:
        text = f"Выбрано: {', '.join(selected_names)}\n\nМожете выбрать ещё или нажать 'Готово'"
    else:
        text = "Выберите процедуру (можно выбрать несколько):\n\n Нажмите на нужные услуги, затем 'Готово'"
    
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data == "service_done", StateFilter(ClientBooking.choosing_service))
async def service_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("selected_services", [])
    
    if not selected:
        await callback.answer("️ Выберите хотя бы одну услугу!", show_alert=True)
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
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
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, time FROM schedule WHERE date = ? AND is_booked = 0", (date_str,))
        times = await cursor.fetchall()
    
    kb = InlineKeyboardBuilder()
    for t_id, t_time in times:
        kb.button(text=t_time, callback_data=f"time_{t_id}")
    kb.button(text="⬅️ Назад к услугам", callback_data="book_start")
    kb.button(text="❌ Отмена", callback_data="cancel_to_menu")
    kb.adjust(3)
    await callback.message.edit_text(f"Выберите время на {date_str}:", reply_markup=kb.as_markup())
    await state.set_state(ClientBooking.choosing_time)
    await callback.answer()

@router.callback_query(F.data.startswith("time_"), StateFilter(ClientBooking.choosing_time))
async def process_time(callback: CallbackQuery, state: FSMContext):
    schedule_id = int(callback.data.split("_")[1])
    await state.update_data(schedule_id=schedule_id)
    
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=" Отправить контакт", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
    await callback.message.delete()
    await callback.message.answer("Отлично! Теперь нажмите на кнопку ниже, чтобы поделиться номером телефона:", reply_markup=kb)
    await state.set_state(ClientBooking.sending_phone)
    await callback.answer()

@router.message(F.contact, StateFilter(ClientBooking.sending_phone))
async def process_phone(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    phone = message.contact.phone_number
    username = message.from_user.username or message.from_user.first_name
    
    async with aiosqlite.connect(DB_PATH) as db:
        for service_id in data['selected_services']:
            await db.execute("UPDATE schedule SET is_booked = 1 WHERE id = ?", (data['schedule_id'],))
            await db.execute(
                "INSERT INTO bookings (user_id, username, phone, service_id, schedule_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (message.from_user.id, username, phone, service_id, data['schedule_id'], datetime.now().isoformat())
            )
        
        cursor = await db.execute("SELECT date, time FROM schedule WHERE id = ?", (data['schedule_id'],))
        date_str, time_str = await cursor.fetchone()
        await db.commit()

    services_text = ""
    async with aiosqlite.connect(DB_PATH) as db:
        for service_id in data['selected_services']:
            cursor = await db.execute("SELECT name FROM services WHERE id = ?", (service_id,))
            name = (await cursor.fetchone())[0]
            services_text += f"• {name}\n"

    admin_ids = await get_admin_ids()
    notification = (
        f"🔔 НОВАЯ ЗАПИСЬ!\n\n"
        f" Клиент: @{username} ({phone})\n"
        f"💇‍♀️ Услуги:\n{services_text}"
        f" Дата: {date_str}\n"
        f"⏰ Время: {time_str}"
    )
    
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, notification)
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")
    
    await message.answer(
        f"✅ Вы успешно записаны! ✨\n"
        f"💇‍♀️ Услуги:\n{services_text}"
        f"📅 {date_str} в {time_str}\n\n"
        f"Жду вас! За 24 часа я пришлю вам напоминание. 💖",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.clear()

@router.message(F.text, StateFilter(ClientBooking.sending_phone))
async def handle_text_in_phone_state(message: Message):
    await message.answer("⚠️ Пожалуйста, нажмите на кнопку '📱 Отправить контакт' ниже.")

@router.callback_query(F.data == "my_bookings")
async def my_bookings_handler(callback: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
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
        text = "📋 Ваши записи:\n\n"
        for name, date, time in bookings:
            text += f"💇‍♀️ {name}\n {date} в {time}\n\n"
    
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
async def cmd_admin(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        await message.answer("⚠️ У вас нет доступа к админ-панели.")
        return
    
    current_state = await state.get_state()
    if current_state and current_state.startswith("Admin"):
        await show_admin_menu(message, state)
        return
    
    await message.answer("🔐 Введите пароль для доступа к админ-панели:")
    await state.set_state(AdminAuth.waiting_password)

@router.message(StateFilter(AdminAuth.waiting_password))
async def admin_auth_handler(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    
    if message.text == ADMIN_PASSWORD:
        await show_admin_menu(message, state)
    else:
        await message.answer("❌ Неверный пароль.")
        await state.clear()

@router.callback_query(F.data == "admin_add_schedule")
async def admin_add_schedule(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⚠️ У вас нет доступа!", show_alert=True)
        return
    await callback.message.edit_text("Введите дату в формате ГГГГ-ММ-ДД (например, 2026-07-25):")
    await state.set_state(AdminSchedule.adding_date)
    await callback.answer()

@router.message(StateFilter(AdminSchedule.adding_date))
async def admin_process_date(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    try:
        datetime.strptime(message.text, "%Y-%m-%d")
        await state.update_data(date_str=message.text)
        await message.answer("Теперь введите время через запятую (например: 10:00, 12:00, 15:30):")
        await state.set_state(AdminSchedule.adding_times)
    except ValueError:
        await message.answer("❌ Неверный формат.")
        await show_admin_menu(message, state)

@router.message(StateFilter(AdminSchedule.adding_times))
async def admin_process_times(message: Message, state: FSMContext, bot: Bot):
    if not await is_admin(message.from_user.id):
        return
    data = await state.get_data()
    times = [t.strip() for t in message.text.split(",")]
    
    async with aiosqlite.connect(DB_PATH) as db:
        for t in times:
            await db.execute("INSERT INTO schedule (date, time) VALUES (?, ?)", (data['date_str'], t))
        await db.commit()
        
    await message.answer(f"✅ Окошки на {data['date_str']} добавлены: {', '.join(times)}")
    await show_admin_menu(message, state)

@router.callback_query(F.data == "admin_today")
async def admin_today_handler(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⚠️ У вас нет доступа!", show_alert=True)
        return
    
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
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
        text = f"📊 Записи на сегодня ({today}):\n\n"
        for username, phone, name, time in bookings:
            text += f"⏰ {time} | {name}\n👤 {username} ({phone})\n\n"
            
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В админ-меню", callback_data="admin_back")
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data == "admin_clear")
async def admin_clear_handler(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⚠️ У вас нет доступа!", show_alert=True)
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bookings")
        await db.execute("UPDATE schedule SET is_booked = 0")
        await db.commit()
    await callback.message.edit_text("🗑 Все записи удалены.")
    await callback.answer()
    await show_admin_menu(callback, callback)

@router.callback_query(F.data == "admin_back")
async def admin_back_handler(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⚠️ У вас нет доступа!", show_alert=True)
        return
    await show_admin_menu(callback, state)

@router.callback_query(F.data == "admin_manage")
async def admin_manage_handler(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("⚠️ У вас нет доступа!", show_alert=True)
        return
    
    if callback.from_user.id != MAIN_ADMIN_ID:
        await callback.message.edit_text("⚠️ Только главный админ может управлять списком админов.")
        await callback.answer()
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id, username FROM admins")
        admins = await cursor.fetchall()
    
    text = "👥 Список админов:\n\n"
    for admin_id, username in admins:
        role = "👑 Главный" if admin_id == MAIN_ADMIN_ID else " Админ"
        text += f"• ID: {admin_id} ({username}) - {role}\n"
    
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить админа", callback_data="admin_add_admin")
    kb.button(text="🗑 Удалить админа", callback_data="admin_remove_admin")
    kb.button(text="⬅️ В админ-меню", callback_data="admin_back")
    kb.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data == "admin_add_admin")
async def admin_add_admin_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != MAIN_ADMIN_ID:
        await callback.answer("⚠️ Только главный админ!", show_alert=True)
        return
    
    await callback.message.edit_text("Введите ID нового админа (число):\n\n💡 Чтобы узнать ID, попросите человека написать боту /start, затем посмотрите в логах.")
    await state.set_state(AdminAdd.waiting_admin_id)
    await callback.answer()

@router.message(StateFilter(AdminAdd.waiting_admin_id))
async def admin_add_process(message: Message, state: FSMContext):
    if message.from_user.id != MAIN_ADMIN_ID:
        return
    
    try:
        new_admin_id = int(message.text)
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO admins (user_id, username, added_at) VALUES (?, ?, ?)",
                            (new_admin_id, f"admin_{new_admin_id}", datetime.now().isoformat()))
            await db.commit()
        
        await message.answer(f"✅ Админ с ID {new_admin_id} добавлен!")
        await show_admin_menu(message, state)
    except ValueError:
        await message.answer("❌ Неверный ID. Введите число.")
        await state.clear()

@router.callback_query(F.data == "admin_remove_admin")
async def admin_remove_admin_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != MAIN_ADMIN_ID:
        await callback.answer("⚠️ Только главный админ!", show_alert=True)
        return
    
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT user_id, username FROM admins WHERE user_id != ?", (MAIN_ADMIN_ID,))
        admins = await cursor.fetchall()
    
    if not admins:
        await callback.message.edit_text("Нет админов для удаления.")
        await callback.answer()
        return
    
    text = "Выберите админа для удаления:\n\n"
    kb = InlineKeyboardBuilder()
    for admin_id, username in admins:
        kb.button(text=f"❌ {username} (ID: {admin_id})", callback_data=f"remove_admin_{admin_id}")
    kb.button(text="⬅️ Назад", callback_data="admin_manage")
    kb.adjust(1)
    
    await callback.message.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("remove_admin_"))
async def admin_remove_process(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != MAIN_ADMIN_ID:
        await callback.answer("️ Только главный админ!", show_alert=True)
        return
    
    admin_id = int(callback.data.split("_")[2])
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (admin_id,))
        await db.commit()
    
    await callback.message.edit_text(f"✅ Админ с ID {admin_id} удалён.")
    await callback.answer()
    await show_admin_menu(callback, state)

@router.callback_query(F.data == "admin_logout")
async def admin_logout_handler(callback: CallbackQuery, state: FSMContext):
    await show_main_menu(callback, state, callback.from_user.first_name)
    await callback.message.answer("👋 Вы вышли из админ-панели.")

# ================= НАПОМИНАНИЯ =================
async def check_reminders(bot: Bot):
    now = datetime.now()
    reminder_time = now + timedelta(hours=24)
    target_date = reminder_time.strftime("%Y-%m-%d")
    target_time = reminder_time.strftime("%H:%M")
    
    async with aiosqlite.connect(DB_PATH) as db:
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
                f"🔔 Напоминание!\n\n"
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
    logging.info(f"✅ Бот успешно запущен! База данных: {DB_PATH}")

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
