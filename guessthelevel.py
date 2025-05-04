import discord,logging,random,sqlite3,os,json,asyncio
from discord.ext import commands
from discord import app_commands

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен и название гдпса (ОБЯЗАТЕЛЬНО ДОБАВЬТЕ ТОКЕН, ИНАЧЕ НЕ БУДЕТ РАБОТАТЬ!!!)
# (Чтобы добавлять уровни, создайте отдельную роль и назовите ёё, как она записана в строчке)
GDPS_NAME = "GDPS"
required_role_name = "addlevel"
TOKEN = ""

# Создаем бота с префиксом команды "/"
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# CommandTree для работы со слэш-командами
tree = bot.tree

# Подключение к базе данных SQLite3
DB_FILE = "leaderboard.db"
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# Создание таблицы лидеров, если она не существует
cursor.execute("""
CREATE TABLE IF NOT EXISTS leaderboard (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    points INTEGER DEFAULT 0
)
""")
conn.commit()

# Загрузка данных об уровнях из JSON файла
LEVELS_FILE = "levels.json"
if not os.path.exists(LEVELS_FILE):
    with open(LEVELS_FILE, "w") as f:
        json.dump([], f)
with open(LEVELS_FILE, "r") as f:
    levels_data = json.load(f)  # Загружаем данные об уровнях

# Словарь для хранения текущих игр
active_games = {}

# Словарь для хранения временно заблокированных уровней
used_levels = {}  # {channel_id: {"blocked": [level_names], "counter": count}}

# Функция для проверки, что команда выполняется не в личных сообщениях
def is_guild_channel(interaction: discord.Interaction):
    if isinstance(interaction.channel, discord.DMChannel):
        return False
    return True

# Логика игры
async def guess_level_logic(interaction: discord.Interaction):
    # Проверяем, что команда выполняется не в личных сообщениях
    if not is_guild_channel(interaction):
        await interaction.response.send_message("Эта команда недоступна в личных сообщениях!", ephemeral=True)
        return

    # Проверяем, не запущена ли уже игра
    if interaction.channel.id in active_games:
        await interaction.response.send_message("Игра уже запущена в этом канале!")
        return

    # Получаем список заблокированных уровней для текущего канала
    blocked_levels = used_levels.get(interaction.channel.id, {}).get("blocked", [])

    # Фильтруем доступные уровни, исключая заблокированные
    available_levels = [
        level for level in levels_data
        if level["name"].lower() not in blocked_levels
    ]

    if not available_levels:
        # Если все уровни заблокированы, сбрасываем блокировку
        used_levels[interaction.channel.id] = {"blocked": [], "counter": 0}
        available_levels = levels_data

    # Выбираем случайный уровень
    level = random.choice(available_levels)
    level_name = level["name"]
    level_image_url = level["image_url"]

    # Сохраняем последний выбранный уровень
    active_games[interaction.channel.id] = {
        "answer": level_name.lower(),
        "last_level": level_name.lower()
    }

    # Блокируем выбранный уровень
    if interaction.channel.id not in used_levels:
        used_levels[interaction.channel.id] = {"blocked": [], "counter": 0}
    used_levels[interaction.channel.id]["blocked"].append(level_name.lower())
    used_levels[interaction.channel.id]["counter"] += 1

    # Если счетчик достигает 5, сбрасываем блокировку
    if used_levels[interaction.channel.id]["counter"] >= 5:
        used_levels[interaction.channel.id] = {"blocked": [], "counter": 0}

    # Запускаем фоновую задачу
    asyncio.create_task(game_task(interaction, level_name, level_image_url))

# Команда для запуска игры
@tree.command(name="guess", description="Запустить игру 'Угадай уровень'")
async def guess_level(interaction: discord.Interaction):
    # Проверяем, что команда выполняется не в личных сообщениях
    if not is_guild_channel(interaction):
        await interaction.response.send_message("Эта команда недоступна в личных сообщениях!", ephemeral=True)
        return

    await guess_level_logic(interaction)

# Фоновая задача для игры
async def game_task(interaction: discord.Interaction, level_name: str, level_image_url: str):
    # Проверяем, что команда выполняется не в личных сообщениях
    if not is_guild_channel(interaction):
        return
    # Отправляем изображение уровня
    global GDPS_NAME
    embed = discord.Embed(
        title="Угадай уровень",
        description=f"Уровень есть на {GDPS_NAME}",
        color=0x6b6b6b
        )
    embed.set_image(url=level_image_url)
    try:
        # Попытка отправить сообщение через followup
        await interaction.followup.send(embed=embed)
    except discord.errors.NotFound:
        # Если followup недоступен, отправляем сообщение через канал
        logger.error("Followup send failed: Unknown Webhook")
        await interaction.channel.send(embed=embed)

    # Ждем ответа от пользователей
    def check(message):
        return message.channel == interaction.channel and message.content.lower() == level_name.lower()

    try:
        # Ожидаем сообщение в течение 20 секунд
        msg = await bot.wait_for("message", timeout=20.0, check=check)
        user = msg.author

        # Начисляем очки за угаданный уровень
        points = 10  # Фиксированное количество очков за любой уровень
        cursor.execute("""
        INSERT INTO leaderboard (user_id, username, points)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
        points = points + excluded.points,
        username = excluded.username
        """, (user.id, user.name, points))
        conn.commit()

        # Получаем обновленные очки пользователя
        cursor.execute("SELECT points FROM leaderboard WHERE user_id = ?", (user.id,))
        total_points = cursor.fetchone()[0]

        try:
            # Создаем embed для сообщения об угадывании уровня
            embed = discord.Embed(
                title="🎉 Уровень угадан!",
                description=f"{user.mention} угадал уровень! +{points} очков.",
                color=0x00ff00  # Зеленый цвет для успеха
            )
            embed.add_field(name="Всего очков:", value=str(total_points), inline=False)
            
            # Отправляем embed через followup
            await interaction.followup.send(embed=embed, view=GameEndView())
        except discord.errors.NotFound:
            # Если followup недоступен, отправляем embed через канал
            logger.error("Followup send failed: Unknown Webhook")
            await interaction.channel.send(embed=embed, view=GameEndView())

    except asyncio.TimeoutError:
        try:
            # Создаем embed для сообщения о завершении времени
            embed = discord.Embed(
                title="⏰ Время вышло!",
                description="Никто не угадал этот уровень.",
                color=0xff0000  # Красный цвет для обозначения завершения
            )
            
            # Отправляем embed через followup
            await interaction.followup.send(embed=embed, view=GameEndView())
        except discord.errors.NotFound:
            # Если followup недоступен, отправляем embed через канал
            logger.error("Followup send failed: Unknown Webhook")
            await interaction.channel.send(embed=embed, view=GameEndView())
    finally:
        # Очищаем игру
        del active_games[interaction.channel.id]

# Класс для кнопок после завершения игры
class GameEndView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Сыграть снова", style=discord.ButtonStyle.blurple)
    async def play_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Проверяем, что команда выполняется не в личных сообщениях
        if not is_guild_channel(interaction):
            await interaction.response.send_message("Эта команда недоступна в личных сообщениях!", ephemeral=True)
            return

        # Запускаем игру с обновленной сложностью
        await interaction.response.defer()  # Откладываем ответ
        await guess_level_logic(interaction)
        self.stop()

# Команда для просмотра таблицы лидеров
@tree.command(name="leaderboard", description="Показать таблицу лидеров")
async def leaderboard(interaction: discord.Interaction):
    # Проверяем, что команда выполняется не в личных сообщениях
    if not is_guild_channel(interaction):
        await interaction.response.send_message("Эта команда недоступна в личных сообщениях!", ephemeral=True)
        return

    # Получаем топ-10 игроков
    cursor.execute("SELECT user_id, username, points FROM leaderboard ORDER BY points DESC LIMIT 10")
    top_players = cursor.fetchall()

    # Создаем строку для топ-10
    leaderboard_text = ""
    for idx, (user_id, username, points) in enumerate(top_players, start=1):
        try:
            # Пытаемся получить объект пользователя по user_id
            user = interaction.guild.get_member(user_id)
            if user is None:
                # Если пользователь не на сервере, пробуем получить его через fetch_user
                user = await interaction.client.fetch_user(user_id)
            mention = user.mention  # Упоминание пользователя
        except discord.errors.NotFound:
            # Если пользователь не найден, используем сохраненный username
            mention = f"@{username}"
        except Exception as e:
            # Логируем другие ошибки
            logger.error(f"Ошибка при получении пользователя с ID {user_id}: {e}")
            mention = f"@{username}"

        leaderboard_text += f"#{idx} {mention}: {points} очков\n"

    # Получаем место и очки пользователя
    cursor.execute("SELECT points FROM leaderboard WHERE user_id = ?", (interaction.user.id,))
    user_data = cursor.fetchone()
    if user_data:
        user_points = user_data[0]
        cursor.execute("SELECT COUNT(*) + 1 FROM leaderboard WHERE points > ?", (user_points,))
        user_rank = cursor.fetchone()[0]
        user_info = f"\nВаше место: #{user_rank}, Очки: {user_points}"
    else:
        user_info = "\nВы еще не играли!"

    # Отправляем таблицу лидеров
    embed = discord.Embed(title="Таблица лидеров", description=leaderboard_text + user_info, color=0x6b6b6b)
    await interaction.response.send_message(embed=embed)

# Команда для добавления уровня в базу данных
@tree.command(name="addlevel", description="Добавить новый уровень в базу данных")
@app_commands.describe(
    name="Название уровня",
    image_url="Ссылка на изображение уровня"
)
async def add_level(interaction: discord.Interaction, name: str, image_url: str):
    # Проверяем, что команда выполняется не в личных сообщениях
    if not is_guild_channel(interaction):
        await interaction.response.send_message("Эта команда недоступна в личных сообщениях!", ephemeral=True)
        return

    # Проверяем, есть ли у пользователя роль
    global required_role_name
    has_permission = any(role.name == required_role_name for role in interaction.user.roles)
    if not has_permission:
        await interaction.response.send_message(
            f"У вас нет роли '{required_role_name}' для выполнения этой команды!",
            ephemeral=True
        )
        return

    # Добавляем уровень в базу данных
    levels_data.append({
        "name": name.lower(),
        "image_url": image_url
    })
    with open(LEVELS_FILE, "w") as f:
        json.dump(levels_data, f)  # Сохраняем данные обратно в файл

    await interaction.response.send_message(f'Уровень "{name}" успешно добавлен!')

# Синхронизация команд при запуске бота
@bot.event
async def on_ready():
    print(f"Бот {bot.user} запущен!")
    await tree.sync()  # Синхронизируем слэш-команды с Discord
    print("Слэш-команды синхронизированы!")
# Запуск бота
bot.run(TOKEN)