import asyncio
import random
import sqlite3
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart

TOKEN = "8681189714:AAFrrp6KxNGdlvOaTcjxlcKTfzSUGhw5IEU"

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

pending_bets = {}
active_mines = {}    # user_id -> dict state
active_joker = {}    # user_id -> dict state

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("casino_roulette.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 5000,
            last_bonus TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_user(user_id: int, username: str = "Игрок"):
    conn = sqlite3.connect("casino_roulette.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance, last_bonus FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    if res is None:
        cursor.execute("INSERT INTO users (user_id, username, balance) VALUES (?, ?, 5000)", (user_id, username))
        conn.commit()
        res = (5000, None)
    else:
        cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
        conn.commit()
    conn.close()
    return res

def update_balance(user_id: int, amount: int):
    conn = sqlite3.connect("casino_roulette.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def set_bonus_time(user_id: int):
    conn = sqlite3.connect("casino_roulette.db")
    cursor = conn.cursor()
    now_str = datetime.now().isoformat()
    cursor.execute("UPDATE users SET last_bonus = ? WHERE user_id = ?", (now_str, user_id))
    conn.commit()
    conn.close()

def user_link(user_id: int, name: str) -> str:
    safe_name = name.replace("<", "&lt;").replace(">", "&gt;")
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'

def is_bonus_available(last_bonus: str) -> bool:
    if not last_bonus:
        return True
    last_time = datetime.fromisoformat(last_bonus)
    return datetime.now() - last_time >= timedelta(hours=24)

# --- КЛАВИАТУРА ДЛЯ БАЛАНСА ---
def get_bonus_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Забрать бонус", callback_data="claim_bonus")]
    ])

# --- ПРИВЕТСТВИЕ ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    get_user(message.from_user.id, message.from_user.first_name)
    bot_info = await bot.get_me()
    bot_name = bot_info.first_name
    u_link = user_link(message.from_user.id, message.from_user.first_name)

    text = (
        f"👋 <b>Добро пожаловать, {u_link}!</b>\n\n"
        f"<b>{bot_name}</b> — развлекательный бот для личных сообщений и групп:\n\n"
        f"• 🎰 Рулетка: <code>1000 к</code>, <code>500 15</code>, затем <b>го</b>\n"
        f"• 💣 Мины: <code>мины 500</code>\n"
        f"• 🃏 Джокер: <code>джокер 300</code>\n"
        f"• 💰 Баланс: <b>б</b>\n"
        f"• 🛑 Отмена ставки рулетки: <b>отмена</b>\n\n"
        f"Вам начислено <b>5 000 ₽</b> стартового баланса!"
    )
    await message.answer(text)

# --- ПРОВЕРКА БАЛАНСА ---
@dp.message(F.text.lower() == "б")
async def check_balance_text(message: Message):
    user_id = message.from_user.id
    name = message.from_user.first_name
    balance, last_bonus = get_user(user_id, name)
    formatted_balance = f"{balance:,}".replace(",", " ")
    u_link = user_link(user_id, name)
    
    text = f"👤 {u_link}\n💰 Баланс: <b>{formatted_balance} ₽</b>"
    
    if is_bonus_available(last_bonus):
        await message.answer(text, reply_markup=get_bonus_keyboard())
    else:
        await message.answer(text)

# --- ЕЖЕДНЕВНЫЙ БОНУС ---
@dp.callback_query(F.data == "claim_bonus")
async def process_bonus(callback: CallbackQuery):
    user_id = callback.from_user.id
    name = callback.from_user.first_name
    balance, last_bonus = get_user(user_id, name)

    if not is_bonus_available(last_bonus):
        last_time = datetime.fromisoformat(last_bonus)
        remaining = timedelta(hours=24) - (datetime.now() - last_time)
        hours, remainder = divmod(remaining.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        await callback.answer(f"⏳ Бонус можно взять через {hours}ч {minutes}мин!", show_alert=True)
        return

    bonus_amount = 2500
    update_balance(user_id, bonus_amount)
    set_bonus_time(user_id)
    
    new_balance = balance + bonus_amount
    formatted_balance = f"{new_balance:,}".replace(",", " ")
    u_link = user_link(user_id, name)
    
    await callback.answer("🎉 Вы успешно получили бонус!", show_alert=True)
    await callback.message.edit_text(f"👤 {u_link}\n💰 Баланс: <b>{formatted_balance} ₽</b>")

# --- ВПОМОГАТЕЛЬНЫЕ ФУНКЦИИ МИН ---
def build_mines_keyboard(user_id: int):
    game = active_mines[user_id]
    grid = game["grid"]
    revealed = game["revealed"]
    
    kb = []
    for r in range(5):
        row_btns = []
        for c in range(5):
            idx = r * 5 + c
            if idx in revealed:
                cell_type = grid[idx]
                if cell_type == "bomb":
                    btn_text = "💣"
                elif cell_type == "x5":
                    btn_text = "💎 x5"
                else:
                    btn_text = "✅"
            else:
                btn_text = "❓"
            row_btns.append(InlineKeyboardButton(text=btn_text, callback_data=f"mine_{user_id}_{idx}"))
        kb.append(row_btns)
    
    if len(revealed) > 0 and not game["over"]:
        cur_win = int(game["bet"] * game["multiplier"])
        kb.append([InlineKeyboardButton(text=f"💰 Забрать {cur_win:,} ₽", callback_data=f"mine_cashout_{user_id}")])
        
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ВПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЖОКЕРА ---
def build_joker_keyboard(user_id: int):
    game = active_joker[user_id]
    kb = []
    if not game["over"]:
        row = [InlineKeyboardButton(text=f"🎴 Карта {i+1}", callback_data=f"joker_pick_{user_id}_{i}") for i in range(3)]
        kb.append(row)
        if game["step"] > 0:
            cur_win = int(game["bet"] * game["multiplier"])
            kb.append([InlineKeyboardButton(text=f"💰 Забрать {cur_win:,} ₽", callback_data=f"joker_cashout_{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- ОБРАБОТКА ИГРОВЫХ СООБЩЕНИЙ ---
@dp.message()
async def handle_game_messages(message: Message):
    if not message.text:
        return

    raw_text = message.text.strip()
    text = raw_text.lower()
    user_id = message.from_user.id
    name = message.from_user.first_name
    u_link = user_link(user_id, name)

    # --- СТАРТ ИГРЫ МИНЫ ---
    if text.startswith("мины"):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer(f"❌ Формат команды: <code>мины [ставка]</code> (например: <code>мины 500</code>)")
            return

        bet = int(parts[1])
        balance, _ = get_user(user_id, name)
        if bet <= 0:
            await message.answer("❌ Ставка должна быть больше 0 ₽.")
            return
        if balance < bet:
            await message.answer(f"❌ Недостаточно средств! Баланс: <b>{balance:,} ₽</b>.")
            return

        update_balance(user_id, -bet)

        # Генерация поля 5x5 (25 элементов: 5 бомб, 1 x5, 19 обычных)
        items = ["bomb"] * 5 + ["x5"] + ["safe"] * 19
        random.shuffle(items)

        active_mines[user_id] = {
            "bet": bet,
            "grid": items,
            "revealed": set(),
            "multiplier": 1.0,
            "over": False
        }

        fmt_bet = f"{bet:,}".replace(",", " ")
        msg = (
            f"{u_link}, вы начали игру <b>Минное поле</b>!\n"
            f"💰 Ставка: <b>{fmt_bet} ₽</b> (5 бомб, 1 ячейка X5, рост коэффициента при открытии)\n"
            f"Открывайте ячейки:"
        )
        await message.answer(msg, reply_markup=build_mines_keyboard(user_id))
        return

    # --- СТАРТ ИГРЫ ДЖОКЕР ---
    if text.startswith("джокер"):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            await message.answer(f"❌ Формат команды: <code>джокер [ставка]</code> (например: <code>джокер 300</code>)")
            return

        bet = int(parts[1])
        balance, _ = get_user(user_id, name)
        if bet <= 0:
            await message.answer("❌ Ставка должна быть больше 0 ₽.")
            return
        if balance < bet:
            await message.answer(f"❌ Недостаточно средств! Баланс: <b>{balance:,} ₽</b>.")
            return

        update_balance(user_id, -bet)

        active_joker[user_id] = {
            "bet": bet,
            "multiplier": 1.0,
            "step": 0,
            "over": False
        }

        fmt_bet = f"{bet:,}".replace(",", " ")
        msg = (
            f"{u_link}, вы начали игру <b>Джокер</b>!\n"
            f"💰 Ставка: <b>{fmt_bet} ₽</b>\n"
            f"На столе 3 карты: 2 выигрышные (удваивают куш) и 1 Смерть ☠️.\n"
            f"Выберите карту:"
        )
        await message.answer(msg, reply_markup=build_joker_keyboard(user_id))
        return

    # --- ОТМЕНА СТАВКИ РУЛЕТКИ ---
    if text in ["отмена", "отменить", "отмена ставки", "отменить ставку"]:
        if user_id not in pending_bets:
            await message.answer(f"❌ У вас нет активных ставок для отмены, {u_link}.")
            return

        bet = pending_bets.pop(user_id)
        update_balance(user_id, bet["amount"])
        await message.answer(f"Ставки отменены {u_link}")
        return

    # --- ЗАПУСК РУЛЕТКИ ("го") ---
    if text == "го":
        if user_id not in pending_bets:
            await message.answer("❌ У вас нет активной ставки! Сначала сделайте ставку.")
            return

        bet = pending_bets[user_id]
        time_passed = (datetime.now() - bet["time"]).total_seconds()
        if time_passed < 10:
            left_seconds = int(10 - time_passed)
            await message.answer(f"⏳ Подождите еще {left_seconds} сек. перед запуском!")
            return

        del pending_bets[user_id]

        await message.answer_dice(emoji="🎰")
        await asyncio.sleep(3)

        winning_num = random.randint(0, 39)
        if winning_num == 0:
            winning_color = "зеленое"
            color_emoji = "🟢"
        elif winning_num % 2 == 0:
            winning_color = "к"
            color_emoji = "🔴"
        else:
            winning_color = "ч"
            color_emoji = "⚫️"

        is_win = False
        win_amount = 0

        if bet["type"] == "color" and bet["target"] == winning_color:
            is_win = True
            win_amount = bet["amount"] * 2
        elif bet["type"] == "number" and bet["target"] == winning_num:
            is_win = True
            win_amount = bet["amount"] * 36

        formatted_bet = f"{bet['amount']:,}".replace(",", " ")
        
        if is_win:
            update_balance(user_id, win_amount)
            formatted_win = f"{win_amount:,}".replace(",", " ")
            result_text = (
                f"Рулетка: {winning_num}{color_emoji}\n"
                f"{u_link} <b>{formatted_bet} ₽</b> на {bet['desc_target']}\n\n"
                f"{u_link} ставка <b>{formatted_bet} ₽</b> выиграл <b>{formatted_win} ₽</b> на {bet['desc_target']}"
            )
        else:
            result_text = (
                f"Рулетка: {winning_num}{color_emoji}\n"
                f"{u_link} <b>{formatted_bet} ₽</b> на {bet['desc_target']}"
            )

        await message.answer(result_text)
        return

    # --- ПРИЕМ СТАВОК РУЛЕТКИ ---
    clean_text = re.sub(r'(грам|gram|руб|рублей|р|₽)', '', text)
    parts = clean_text.split()

    if len(parts) >= 2 and parts[0].isdigit():
        amount = int(parts[0])
        choice = parts[-1]

        balance, _ = get_user(user_id, name)

        if amount <= 0:
            await message.answer("❌ Ставка должна быть больше 0 ₽.")
            return

        if balance < amount:
            formatted_balance = f"{balance:,}".replace(",", " ")
            await message.answer(f"❌ Недостаточно средств! Баланс: <b>{formatted_balance} ₽</b>.")
            return

        bet_type = None
        target = None
        desc_target = ""

        if choice in ["к", "красное", "red"]:
            bet_type = "color"
            target = "к"
            desc_target = "RED"
        elif choice in ["ч", "черное", "black"]:
            bet_type = "color"
            target = "ч"
            desc_target = "BLACK"
        elif choice.isdigit() and 0 <= int(choice) <= 39:
            bet_type = "number"
            target = int(choice)
            desc_target = str(target)
        else:
            return

        if user_id in pending_bets:
            update_balance(user_id, pending_bets[user_id]["amount"])

        update_balance(user_id, -amount)
        pending_bets[user_id] = {
            "amount": amount,
            "type": bet_type,
            "target": target,
            "desc_target": desc_target,
            "time": datetime.now()
        }

        formatted_amount = f"{amount:,}".replace(",", " ")
        await message.answer(f"Ставка принята: {u_link} <b>{formatted_amount} ₽</b> на {desc_target}")

# --- CALLBACKS МИНЫ ---
@dp.callback_query(F.data.startswith("mine_"))
async def handle_mines_cb(callback: CallbackQuery):
    data_parts = callback.data.split("_")
    user_id = callback.from_user.id
    u_link = user_link(user_id, callback.from_user.first_name)

    if data_parts[1] == "cashout":
        target_uid = int(data_parts[2])
        if user_id != target_uid:
            await callback.answer("❌ Это не ваша игра!", show_alert=True)
            return

        if user_id not in active_mines or active_mines[user_id]["over"]:
            await callback.answer("Игра уже завершена.")
            return

        game = active_mines[user_id]
        win_amount = int(game["bet"] * game["multiplier"])
        update_balance(user_id, win_amount)
        game["over"] = True

        fmt_win = f"{win_amount:,}".replace(",", " ")
        await callback.message.edit_text(
            f"🎉 {u_link} забрал выигрыш в Минах: <b>{fmt_win} ₽</b>! (Коэффициент: x{game['multiplier']:.2f})",
            reply_markup=build_mines_keyboard(user_id)
        )
        del active_mines[user_id]
        return

    target_uid = int(data_parts[1])
    idx = int(data_parts[2])

    if user_id != target_uid:
        await callback.answer("❌ Это не ваша игра!", show_alert=True)
        return

    if user_id not in active_mines or active_mines[user_id]["over"]:
        await callback.answer("Игра уже завершена.")
        return

    game = active_mines[user_id]
    if idx in game["revealed"]:
        await callback.answer("Ячейка уже открыта!")
        return

    game["revealed"].add(idx)
    cell_type = game["grid"][idx]

    if cell_type == "bomb":
        game["over"] = True
        # Открываем все бомбы
        for i, val in enumerate(game["grid"]):
            if val == "bomb":
                game["revealed"].add(i)
        
        await callback.message.edit_text(
            f"💥 <b>БУМ!</b> {u_link} наступил на мину и потерял <b>{game['bet']:,} ₽</b>!",
            reply_markup=build_mines_keyboard(user_id)
        )
        del active_mines[user_id]
    else:
        if cell_type == "x5":
            game["multiplier"] *= 5.0
            step_msg = "💎 <b>УМНОЖИТЕЛЬ X5!</b>"
        else:
            game["multiplier"] += 0.25
            step_msg = "✅ Безопасно!"

        cur_win = int(game["bet"] * game["multiplier"])
        fmt_win = f"{cur_win:,}".replace(",", " ")
        
        await callback.message.edit_text(
            f"{step_msg}\n👤 {u_link}\n📈 Коэффициент: <b>x{game['multiplier']:.2f}</b>\n💰 Текущий выигрыш: <b>{fmt_win} ₽</b>",
            reply_markup=build_mines_keyboard(user_id)
        )

# --- CALLBACKS ДЖОКЕР ---
@dp.callback_query(F.data.startswith("joker_"))
async def handle_joker_cb(callback: CallbackQuery):
    data_parts = callback.data.split("_")
    user_id = callback.from_user.id
    u_link = user_link(user_id, callback.from_user.first_name)

    if data_parts[1] == "cashout":
        target_uid = int(data_parts[2])
        if user_id != target_uid:
            await callback.answer("❌ Это не ваша игра!", show_alert=True)
            return

        if user_id not in active_joker or active_joker[user_id]["over"]:
            await callback.answer("Игра завершена.")
            return

        game = active_joker[user_id]
        win_amount = int(game["bet"] * game["multiplier"])
        update_balance(user_id, win_amount)
        game["over"] = True

        fmt_win = f"{win_amount:,}".replace(",", " ")
        await callback.message.edit_text(
            f"🎉 {u_link} забирает куш в Джокере: <b>{fmt_win} ₽</b>! (Пройдено раундов: {game['step']})"
        )
        del active_joker[user_id]
        return

    target_uid = int(data_parts[2])
    card_idx = int(data_parts[3])

    if user_id != target_uid:
        await callback.answer("❌ Это не ваша игра!", show_alert=True)
        return

    if user_id not in active_joker or active_joker[user_id]["over"]:
        await callback.answer("Игра завершена.")
        return

    game = active_joker[user_id]
    
    # Распределение: 0, 1 или 2 — позиция Смерти
    death_card = random.randint(0, 2)

    if card_idx == death_card:
        game["over"] = True
        await callback.message.edit_text(
            f"☠️ <b>СМЕРТЬ!</b> {u_link} вытянул карту Смерти и потерял <b>{game['bet']:,} ₽</b>!"
        )
        del active_joker[user_id]
    else:
        game["step"] += 1
        game["multiplier"] *= 2.0
        cur_win = int(game["bet"] * game["multiplier"])
        fmt_win = f"{cur_win:,}".replace(",", " ")

        await callback.message.edit_text(
            f"🃏 <b>Успех!</b> Вы угадали карту!\n\n"
            f"👤 {u_link}\n"
            f"🔥 Раунд: <b>{game['step']}</b>\n"
            f"📈 Коэффициент: <b>x{int(game['multiplier'])}</b>\n"
            f"💰 Текущий куш: <b>{fmt_win} ₽</b>\n\n"
            f"Продолжайте или заберите деньги:",
            reply_markup=build_joker_keyboard(user_id)
        )

# --- ЗАПУСК ---
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())