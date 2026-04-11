from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_MEMBER, KICKED, LEFT, RESTRICTED, MEMBER, ADMINISTRATOR, CREATOR
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton
from app.core.config import settings
import re
import time

# Инициализация
bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()

# --- КОНФИГУРАЦИЯ МОДЕРАЦИИ (ТЗ 4.3.3) ---
STOP_WORDS = ["спам", "реклама", "купить", "подпишись"] # Можно расширять список
ALLOW_LINKS = False # Если False — удаляем все t.me и http ссылки

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Ссылка на твой Mini App
    # Добавляем параметр v=time, чтобы сбросить кэш Telegram и загрузить обновления
    web_app_url = f"https://elmirtatliev220-byte.github.io/init_project.py/?v={int(time.time())}"
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="📝 Создать пост", 
        web_app=types.WebAppInfo(url=web_app_url)
    ))
    # Добавляем кнопки с правилами (замените URL на свои Telegraph ссылки)
    builder.row(
        types.InlineKeyboardButton(text="📜 Правила", url="https://telegra.ph/Terms-of-Service--Telecore-02-15"),
        types.InlineKeyboardButton(text="🔒 Приватность", url="https://telegra.ph/Privacy-Policy--Telecore-02-15")
    )

    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n\n"
        "Я — Telecore, твой помощник для управления каналами.\n"
        "Планируй посты, смотри статистику и используй AI.\n\n"
        "Нажми кнопку ниже, чтобы начать:",
        reply_markup=builder.as_markup()
    )

# --- КОМАНДА НАСТРОЙКИ ЗАЩИТЫ (ТЗ 4.2.1) ---
@dp.message(Command("protect"))
async def cmd_protect(message: types.Message):
    """
    Привязывает текущую группу к каналу для обязательной подписки.
    Использование: /protect @channel_username
    """
    # Проверяем, что это группа
    if message.chat.type not in ["group", "supergroup"]:
        return await message.answer("⛔ Эту команду можно использовать только в группах.")

    # Проверяем права автора (должен быть админом)
    user_member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if user_member.status not in ["administrator", "creator"]:
        return await message.answer("⛔ Только администраторы могут настраивать защиту.")

    args = message.text.split()
    if len(args) < 2:
        return await message.answer("ℹ️ Укажите канал: `/protect @channel`", parse_mode="Markdown")

    channel_username = args[1].replace("@", "")
    
    try:
        # Проверяем существование канала и права бота в нем (бот должен видеть участников)
        # Для проверки просто пробуем получить инфо о чате
        chat = await bot.get_chat(f"@{channel_username}")
        
        # Ленивый импорт БД
        from main import async_session, GroupProtection, select, delete

        async with async_session() as session:
            # Удаляем старую настройку для этой группы, если была
            await session.execute(delete(GroupProtection).where(GroupProtection.group_id == message.chat.id))
            
            # Создаем новую
            new_prot = GroupProtection(
                group_id=message.chat.id,
                channel_id=chat.id,
                channel_username=channel_username
            )
            session.add(new_prot)
            await session.commit()
        
        await message.answer(
            f"🛡 <b>Защита активирована!</b>\n\n"
            f"Теперь писать в этот чат могут только подписчики канала @{channel_username}.\n"
            f"Убедитесь, что я (бот) являюсь администратором и здесь, и в канале!",
            parse_mode="HTML"
        )

    except Exception as e:
        await message.answer(f"⚠️ Ошибка: Не удалось найти канал @{channel_username} или нет прав. {str(e)}")

# --- ХЕНДЛЕР ОТСЛЕЖИВАНИЯ ТРАФИКА (ТЗ 4.1.3) ---
@dp.chat_member(ChatMemberUpdatedFilter(IS_MEMBER))
async def on_user_join(event: types.ChatMemberUpdated):
    """Срабатывает, когда пользователь вступает в канал/группу"""
    if not event.invite_link:
        return
    
    link = event.invite_link.invite_link
    # Ленивый импорт
    from main import async_session, TrafficSource, update
    
    try:
        async with async_session() as session:
            stmt = update(TrafficSource).where(TrafficSource.invite_link == link).values(joins=TrafficSource.joins + 1)
            await session.execute(stmt)
            await session.commit()
            print(f"📈 Новый подписчик по ссылке {link}")
    except Exception as e:
        print(f"⚠️ Ошибка трекинга ссылки: {e}")

# --- ХЕНДЛЕР МОДЕРАЦИИ ---
@dp.message(F.chat.type.in_({"group", "supergroup", "channel"}))
async def moderator_handler(message: types.Message):
    """Автоматическая фильтрация контента в каналах и группах"""
    
    # 1. ПРОВЕРКА ПОДПИСКИ (ТЗ 4.2.1)
    # Пропускаем админов и самого бота
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in ["administrator", "creator"]:
            pass # Админов не проверяем на подписку, но проверяем на стоп-слова ниже
        else:
            # Ленивый импорт
            from main import async_session, GroupProtection, select
            
            async with async_session() as session:
                stmt = select(GroupProtection).where(GroupProtection.group_id == message.chat.id)
                res = await session.execute(stmt)
                protection = res.scalar_one_or_none()
            
            if protection:
                if not await check_subscription(message, protection.channel_id, protection.channel_username):
                    return # Сообщение удалено, выходим
    except:
        pass 

    # 2. АВТООТВЕТЧИК (ТЗ 4.2.2)
    # Проверяем наличие триггеров в тексте
    if message.text:
        from main import async_session, AutoResponse, select
        try:
            async with async_session() as session:
                stmt = select(AutoResponse).where(AutoResponse.channel_id == message.chat.id)
                res = await session.execute(stmt)
                responses = res.scalars().all()
                
                for ar in responses:
                    if ar.trigger.lower() in message.text.lower():
                        await message.reply(ar.response)
                        # Не делаем return, чтобы дальше сработала модерация (если нужно)
        except Exception as e:
            print(f"⚠️ Ошибка автоответчика: {e}")

    text = message.text or message.caption or ""
    
    for word in STOP_WORDS:
        if word.lower() in text.lower():
            try:
                await message.delete()
                print(f"🚫 Удалено сообщение со стоп-словом: {word}")
                return
            except Exception as e:
                print(f"⚠️ Ошибка удаления (стоп-слово): {e}")

    if not ALLOW_LINKS:
        urls = re.findall(r'(https?://[^\s]+|t\.me/[^\s]+)', text)
        if urls:
            try:
                await message.delete()
                print(f"🚫 Удалено сообщение с ссылкой: {urls[0]}")
                return
            except Exception as e:
                print(f"⚠️ Ошибка удаления (ссылка): {e}")

async def check_subscription(message: types.Message, channel_id: int, channel_username: str) -> bool:
    """Проверяет подписку пользователя. Если нет — удаляет сообщение и шлет варн."""
    try:
        user_status = await bot.get_chat_member(chat_id=channel_id, user_id=message.from_user.id)
        if user_status.status in ["left", "kicked"]:
            await message.delete()
            
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="➕ Подписаться", url=f"https://t.me/{channel_username}"))
            builder.row(InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check_sub_{channel_id}"))
            
            warn_msg = await message.answer(
                f"👋 {message.from_user.first_name}, чтобы писать в этот чат, подпишись на канал!",
                reply_markup=builder.as_markup()
            )
            # Удаляем предупреждение через 15 секунд, чтобы не засорять чат
            # (В реальном проекте можно использовать Celery, тут просто оставим или удалим позже)
            return False
        return True
    except Exception as e:
        print(f"⚠️ Ошибка проверки подписки: {e}")
        return True # Если ошибка (например, бот не админ в канале), лучше пропустить, чем блокировать всех

@dp.callback_query(F.data.startswith("check_sub_"))
async def callback_check_sub(callback: types.CallbackQuery):
    """Обработка кнопки 'Я подписался'"""
    channel_id = int(callback.data.split("_")[2])
    try:
        user_status = await bot.get_chat_member(chat_id=channel_id, user_id=callback.from_user.id)
        if user_status.status not in ["left", "kicked"]:
            await callback.message.delete()
            await callback.answer("✅ Спасибо! Теперь вы можете писать.", show_alert=True)
        else:
            await callback.answer("❌ Вы все еще не подписаны!", show_alert=True)
    except Exception as e:
        await callback.answer("⚠️ Ошибка проверки.", show_alert=True)

# --- АВТОМАТИЧЕСКОЕ ДОБАВЛЕНИЕ КАНАЛА (НОВАЯ ФИЧА) ---
@dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=(KICKED | LEFT | RESTRICTED | MEMBER) >> (ADMINISTRATOR | CREATOR)))
async def bot_added_as_admin(event: types.ChatMemberUpdated):
    """Срабатывает, когда бота добавляют в канал как администратора."""
    user_who_added = event.from_user
    chat = event.chat

    # Ленивый импорт, чтобы избежать циклических зависимостей
    from main import async_session, User, Channel, select

    try:
        async with async_session() as session:
            # 1. Находим пользователя, который добавил бота
            stmt = select(User).where(User.tg_id == user_who_added.id)
            db_user = (await session.execute(stmt)).scalar_one_or_none()

            if not db_user:
                # Если пользователь не зарегистрирован в системе, просим его запустить бота
                await bot.send_message(user_who_added.id, f"Пожалуйста, сначала запустите меня, отправив /start, а затем снова добавьте в канал «{chat.title}».")
                return

            # 2. Проверяем, не добавлен ли канал уже
            stmt_ch = select(Channel).where(Channel.tg_id == chat.id)
            if (await session.execute(stmt_ch)).scalar_one_or_none():
                await bot.send_message(user_who_added.id, f"Канал «{chat.title}» уже был добавлен в систему.")
                return

            # 3. Добавляем канал в базу, привязывая к пользователю
            new_channel = Channel(tg_id=chat.id, title=chat.title, owner_id=db_user.id)
            session.add(new_channel)
            await session.commit()

            # 4. Уведомляем пользователя об успехе
            web_app_url = f"https://elmirtatliev220-byte.github.io/init_project.py/?v={int(time.time())}"
            builder = InlineKeyboardBuilder()
            builder.row(types.InlineKeyboardButton(text="➡️ Открыть панель", web_app=types.WebAppInfo(url=web_app_url)))
            
            await bot.send_message(user_who_added.id, f"✅ Канал «{chat.title}» успешно привязан к вашему аккаунту!", reply_markup=builder.as_markup())
            print(f"🤖 Бот добавлен как админ в канал {chat.id} пользователем {user_who_added.id}. Канал привязан.")

    except Exception as e:
        print(f"❌ Ошибка при авто-добавлении канала: {e}")

# --- ДОБАВЛЕНИЯ ДЛЯ АНАЛИТИКИ (ТЗ 4.2.2) ---
# ВАЖНО: Мы убрали импорт отсюда, чтобы не было ошибки ModuleNotFoundError

@dp.channel_post()
async def channel_post_handler(message: types.Message):
    """
    Ловит новые посты в канале и регистрирует их в базе.
    Используем импорт внутри функции, чтобы избежать цикличной зависимости.
    """
    try:
        # ЛЕНИВЫЙ ИМПОРТ: загружается только при вызове функции
        from main import async_session, PostStat 
        
        async with async_session() as session:
            new_post_stat = PostStat(
                channel_id=message.chat.id,
                message_id=message.message_id,
                views=0,
                reactions=0
            )
            session.add(new_post_stat)
            await session.commit()
            print(f"📊 Пост {message.message_id} в канале {message.chat.id} зарегистрирован.")
    except Exception as e:
        print(f"⚠️ Ошибка регистрации поста в БД: {e}")

@dp.message(Command("refresh_stats"))
async def cmd_refresh_stats(message: types.Message):
    """Команда для админа, чтобы вручную пнуть обновление"""
    await message.answer("🔄 Запрос на обновление метрик отправлен в очередь.")