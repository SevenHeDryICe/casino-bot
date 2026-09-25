import asyncio
import os
import random
import asyncpg
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command

# --- Переменные окружения ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
CHAT_ID = os.getenv("CHAT_ID")
OWNER_ID = 5171289253  # твой user_id

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден.")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не найден.")
if not CHAT_ID:
    raise ValueError("CHAT_ID не найден.")

CHAT_ID = int(CHAT_ID)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Награды ---
WIN_REWARDS = {
    64: 3.0,  # 7️⃣7️⃣7️⃣ джекпот
    1: 2.0,   # BAR BAR BAR
    22: 1.0,  # 🍒🍒🍒
    43: 1.0,  # 🍋🍋🍋
}
LOSS_PENALTY = 0

# --- Московское время ---
MSK = timezone(timedelta(hours=3))


# --- Пул соединений с БД ---
db_pool: asyncpg.Pool = None


async def init_db():
    """Создаёт таблицы, если их нет."""
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                points DOUBLE PRECISION DEFAULT 0,
                lose_streak INTEGER DEFAULT 0,
                last_bonus TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)


async def get_last_roll_date() -> str:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT value FROM settings WHERE key = 'last_roll'"
        )
        return row["value"] if row else ""


async def set_last_roll_date(date_str: str):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO settings (key, value) VALUES ('last_roll', $1)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, date_str)


async def update_points(user_id: int, username: str, delta: float) -> float:
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, points, lose_streak)
            VALUES ($1, $2, 0, 0)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id, username)
        await conn.execute("""
            UPDATE users SET username = $1, points = points + $2
            WHERE user_id = $3
        """, username, delta, user_id)
        row = await conn.fetchrow(
            "SELECT points FROM users WHERE user_id = $1", user_id
        )
        return row["points"]


# --- Логика розыгрыша ---
async def do_roll(bot: Bot, announce: bool = True):
    async with db_pool.acquire() as conn:
        players = await conn.fetch(
            "SELECT user_id, username, points FROM users WHERE points > 0"
        )

    if not players:
        if announce:
            await bot.send_message(
                CHAT_ID,
                "🎰 Розыгрыш недели: нет игроков с положительным балансом. "
                "Пусть кто-нибудь сыграет!"
            )
        return

    players_list = list(players)
    random.shuffle(players_list)
    winners = players_list[:3]
    prizes = [10.0, 5.0, 3.0]

    results = []
    for i, player in enumerate(winners):
        prize = prizes[i]
        user_id = player["user_id"]
        username = player["username"]
        new_balance = await update_points(user_id, username, prize)
        mention = f'<a href="tg://user?id={user_id}">{username}</a>'
        results.append(f"{i+1}. {mention} — <b>+{prize:g}</b> (баланс: {new_balance:g})")

    text = (
        "🎉 <b>РОЗЫГРЫШ НЕДЕЛИ!</b> 🎉\n\n"
        "🏆 Победители:\n" + "\n".join(results) + "\n\n"
        "Поздравляем! 🎰\n"
        "Следующий розыгрыш — в следующую субботу в 20:00 МСК."
    )

    if announce:
        await bot.send_message(CHAT_ID, text, parse_mode="HTML")


# --- Планировщик: суббота, 20:00–20:59 МСК ---
async def weekly_roll_scheduler(bot: Bot):
    while True:
        try:
            now = datetime.now(MSK)
            # Суббота = 5. Час — с 20:00 до 20:59
            if now.weekday() == 5 and 20 <= now.hour < 21:
                today_str = now.strftime("%Y-%m-%d")
                last_roll = await get_last_roll_date()
                if last_roll != today_str:
                    await set_last_roll_date(today_str)
                    await do_roll(bot, announce=True)
        except Exception as e:
            print(f"[scheduler] Ошибка: {e}")
        await asyncio.sleep(3600)


# --- Команды ---
@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await message.answer(
        "Привет! 🎰 Отправь эмодзи 🎰, чтобы сыграть.\n"
        "Выигрыш: 777 → +3, BAR → +2, вишни/лимоны → +1.\n"
        "Проигрыш: 0.\n\n"
        "Команды: /top /me /bonus /help\n"
        "Розыгрыш недели: суббота, 20:00 МСК!"
    )


@dp.message(Command("help"))
async def help_handler(message: types.Message):
    await message.answer(
        "🎰 <b>Команды бота-рулетки</b>\n\n"
        "Отправь эмодзи 🎰 — сыграть.\n"
        "Выигрыш: 777 → +3, BAR → +2, вишни/лимоны → +1.\n"
        "Проигрыш: 0.\n\n"
        "<b>Команды:</b>\n"
        "/top — таблица лидеров\n"
        "/me — твой баланс\n"
        "/bonus — ежедневный бонус +1\n"
        "/help — справка\n\n"
        "🎉 <b>Розыгрыш недели:</b> каждую субботу в 20:00 МСК.\n"
        "Топ-3 игрока с балансом > 0 получают +10, +5 и +3.",
        parse_mode="HTML"
    )


@dp.message(Command("top"))
async def top_handler(message: types.Message):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT username, points FROM users ORDER BY points DESC LIMIT 10"
        )

    if not rows:
        await message.answer("Пока никто не играл. Отправь 🎰!")
        return

    lines = ["🏆 <b>Таблица лидеров</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows, start=1):
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{prefix} {row['username']} — <b>{row['points']:g}</b>")

    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("me"))
async def me_handler(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "Игрок"

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT points, lose_streak FROM users WHERE user_id = $1", user_id
        )

    if not row:
        points, streak = 0.0, 0
    else:
        points, streak = row["points"], row["lose_streak"]

    await message.answer(
        f"👤 <b>{username}</b>\n"
        f"💼 Баланс: <b>{points:g}</b>\n"
        f"😢 Серия проигрышей: {streak}",
        parse_mode="HTML"
    )


@dp.message(Command("bonus"))
async def bonus_handler(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "Игрок"

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_bonus FROM users WHERE user_id = $1", user_id
        )

    now = datetime.utcnow()
    if row and row["last_bonus"]:
        last = datetime.fromisoformat(row["last_bonus"])
        if now - last < timedelta(hours=24):
            remaining = timedelta(hours=24) - (now - last)
            hours = remaining.seconds // 3600
            minutes = (remaining.seconds % 3600) // 60
            await message.answer(
                f"⏳ Бонус будет доступен через {hours} ч {minutes} мин."
            )
            return

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, points, lose_streak)
            VALUES ($1, $2, 0, 0)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id, username)
        await conn.execute("""
            UPDATE users SET points = points + 1, last_bonus = $1, username = $2
            WHERE user_id = $3
        """, now.isoformat(), username, user_id)
        row = await conn.fetchrow(
            "SELECT points FROM users WHERE user_id = $1", user_id
        )
        new_balance = row["points"]

    await message.answer(
        f"🎁 Ежедневный бонус: <b>+1 балл</b>\n"
        f"💼 Ваш баланс: <b>{new_balance:g}</b>",
        parse_mode="HTML"
    )


@dp.message(Command("roll_now"))
async def roll_now_handler(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("🚫 Команда доступна только владельцу бота.")
        return
    await message.answer("🎲 Провожу розыгрыш...")
    await do_roll(bot, announce=True)


# --- Игра ---
@dp.message(lambda message: message.dice is not None and message.dice.emoji == "🎰")
async def casino_handler(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "Игрок"

    dice_value = message.dice.value

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT lose_streak FROM users WHERE user_id = $1", user_id
        )
        lose_streak = row["lose_streak"] if row else 0

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

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, points, lose_streak)
            VALUES ($1, $2, 0, 0)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id, username)
        await conn.execute("""
            UPDATE users SET username = $1, points = points + $2, lose_streak = $3
            WHERE user_id = $4
        """, username, delta, new_streak, user_id)
        row = await conn.fetchrow(
            "SELECT points FROM users WHERE user_id = $1", user_id
        )
        new_balance = row["points"]

    await message.reply(
        f"{result}\n"
        f"💼 Ваш баланс: <b>{new_balance:g}</b>",
        parse_mode="HTML"
    )


# --- Запуск ---
async def main():
    await init_db()
    print("Бот запущен...")
    asyncio.create_task(weekly_roll_scheduler(bot))
    while True:
        try:
            await dp.start_polling(bot)
        except Exception as e:
            print(f"Ошибка: {e}. Перезапуск через 5 секунд...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
if __name__ == "__main__":
    asyncio.run(main())
