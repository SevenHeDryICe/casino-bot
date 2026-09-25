import asyncio
import os
import aiosqlite
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command

# --- Токен из переменных окружения BotHost ---
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден. Укажите токен в настройках бота на BotHost.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Путь к базе данных (в контейнере BotHost) ---
DB_PATH = "casino.db"

# --- Награды за выигрышные комбинации 🎰 ---
# ВАЖНО: 64 = 777 (джекпот), 1 = BAR BAR BAR, 22 = вишни, 43 = лимоны
WIN_REWARDS = {
    64: 3.0,  # 7️⃣7️⃣7️⃣ — джекпот
    1: 2.0,   # BAR BAR BAR
    22: 1.0,  # 🍒🍒🍒 — три вишни
    43: 1.0,  # 🍋🍋🍋 — три лимона
}

LOSS_PENALTY = 0  # Проигрыш — ничего не отнимаем


# --- Инициализация базы данных ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                points REAL DEFAULT 0,
                lose_streak INTEGER DEFAULT 0,
                last_bonus TEXT
            )
        """)
        await db.commit()


# --- Работа с очками ---
async def update_points(user_id: int, username: str, delta: float) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, points, lose_streak) VALUES (?, ?, 0, 0)",
            (user_id, username)
        )
        await db.execute(
            "UPDATE users SET username = ?, points = points + ? WHERE user_id = ?",
            (username, delta, user_id)
        )
        await db.commit()
        async with db.execute(
            "SELECT points FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0]


# --- Команда /start ---
@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer(
        "Привет! 🎰 Отправь эмодзи 🎰, чтобы сыграть.\n"
        "Выигрыш: 777 → +3, BAR → +2, вишни/лимоны → +1.\n"
        "Проигрыш: 0.\n\n"
        "Команды: /top /me /bonus /help"
    )


# --- Команда /help ---
@dp.message(Command("help"))
async def help_handler(message: types.Message):
    await message.answer(
        "🎰 <b>Команды бота-рулетки</b>\n\n"
        "Отправь эмодзи 🎰 — сыграть в рулетку.\n"
        "Выигрыш: 777 → +3, BAR → +2, вишни/лимоны → +1.\n"
        "Проигрыш: 0 (ничего не теряешь).\n\n"
        "<b>Команды:</b>\n"
        "/top — таблица лидеров\n"
        "/me — твой баланс\n"
        "/bonus — ежедневный бонус +1\n"
        "/help — эта справка",
        parse_mode="HTML"
    )


# --- Команда /top ---
@dp.message(Command("top"))
async def top_handler(message: types.Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT username, points FROM users ORDER BY points DESC LIMIT 10"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await message.answer("Пока никто не играл. Отправь 🎰, чтобы начать!")
        return

    lines = ["🏆 <b>Таблица лидеров</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (username, points) in enumerate(rows, start=1):
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{prefix} {username} — <b>{points:g}</b>")

    await message.answer("\n".join(lines), parse_mode="HTML")


# --- Команда /me ---
@dp.message(Command("me"))
async def me_handler(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "Игрок"

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT points, lose_streak FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        points, streak = 0.0, 0
    else:
        points, streak = row

    await message.answer(
        f"👤 <b>{username}</b>\n"
        f"💼 Баланс: <b>{points:g}</b>\n"
        f"😢 Серия проигрышей: {streak}",
        parse_mode="HTML"
    )


# --- Команда /bonus ---
@dp.message(Command("bonus"))
async def bonus_handler(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "Игрок"

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT last_bonus FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()

    now = datetime.utcnow()
    if row and row[0]:
        last = datetime.fromisoformat(row[0])
        if now - last < timedelta(hours=24):
            remaining = timedelta(hours=24) - (now - last)
            hours = remaining.seconds // 3600
            minutes = (remaining.seconds % 3600) // 60
            await message.answer(
                f"⏳ Бонус будет доступен через {hours} ч {minutes} мин."
            )
            return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, points, lose_streak) VALUES (?, ?, 0, 0)",
            (user_id, username)
        )
        await db.execute(
            "UPDATE users SET points = points + 1, last_bonus = ?, username = ? WHERE user_id = ?",
            (now.isoformat(), username, user_id)
        )
        await db.commit()
        async with db.execute(
            "SELECT points FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            new_balance = (await cursor.fetchone())[0]

    await message.answer(
        f"🎁 Ежедневный бонус: <b>+1 балл</b>\n"
        f"💼 Ваш баланс: <b>{new_balance:g}</b>",
        parse_mode="HTML"
    )


# --- Игра: рулетка ---
@dp.message(lambda message: message.dice is not None and message.dice.emoji == "🎰")
async def casino_handler(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "Игрок"

    dice_value = message.dice.value

    # Получаем текущую серию проигрышей
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT lose_streak FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            lose_streak = row[0] if row else 0

    if dice_value in WIN_REWARDS:
        delta = WIN_REWARDS[dice_value]
        if dice_value == 64:
            combo = "7️⃣7️⃣7️⃣ ДЖЕКПОТ!"
        elif dice_value == 1:
            combo = "BAR BAR BAR"
        elif dice_value == 22:
            combo = "🍒🍒🍒 Три вишни"
        else:
            combo = "🍋🍋🍋 Три лимона"
        result = f"🎉 {combo} +{delta:g} балла"
        new_streak = 0
    else:
        lose_streak += 1
        delta = LOSS_PENALTY
        result = f"😢 Мимо. {delta:g} балла (серия: {lose_streak})"
        new_streak = lose_streak

    # Обновляем баланс и серию
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, points, lose_streak) VALUES (?, ?, 0, 0)",
            (user_id, username)
        )
        await db.execute(
            "UPDATE users SET username = ?, points = points + ?, lose_streak = ? WHERE user_id = ?",
            (username, delta, new_streak, user_id)
        )
        await db.commit()
        async with db.execute(
            "SELECT points FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            new_balance = (await cursor.fetchone())[0]

    await message.reply(
        f"{result}\n"
        f"💼 Ваш баланс: <b>{new_balance:g}</b>",
        parse_mode="HTML"
    )


# --- Запуск ---
async def main():
    await init_db()
    print("Бот запущен...")
    while True:
        try:
            await dp.start_polling(bot)
        except Exception as e:
            print(f"Ошибка: {e}. Перезапуск через 5 секунд...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())