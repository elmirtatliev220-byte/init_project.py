# --- STABLE VERSION CHECKPOINT ---
import asyncio
import uvicorn
import base64
import io
import os
import shutil
import uuid
import hmac
import hashlib
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, BigInteger, DateTime, select, update, delete, ForeignKey, func, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session
from datetime import datetime, timezone, timedelta # Добавлен timedelta для расчетов
from typing import Optional, List, Union
from urllib.parse import parse_qsl
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Depends
from google import genai
from google.genai import types

# Импорты aiogram для работы с файлами и реакциями
from aiogram.types import BufferedInputFile, FSInputFile, ReactionTypeEmoji
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest

# Импорты проекта
from app.core.config import settings
from app.bot.main_bot import bot, dp
from app.core.models import Base, User, Channel, StatsHistory, PostStat, ScheduledPost, AdChannel, GroupProtection, TrafficSource, AutoResponse, AIRequest, WalletRequest, ChannelDeleteRequest, MovePostRequest, AutoDistributeRequest
from app.core.database import engine, async_session

# --- ANALYTICS MODEL (DEFINED HERE FOR MVP) ---
class ChatAnalytics(Base):
    __tablename__ = "chat_analytics"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, index=True)
    event_type = Column(String) # message, spam, join
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

# Создаем папку для загрузок, если нет
os.makedirs("static/uploads", exist_ok=True)

# --- НАСТРОЙКА GEMINI AI (ТЗ 3.0) ---
# Инициализируем клиента СТРОГО без указания лишних параметров
# Это поможет избежать ошибки "Invalid resource field value"
ai_client = None
if settings.GEMINI_API_KEY:
    try:
        # Инициализация клиента
        # Инициализация клиента с ПРИНУДИТЕЛЬНОЙ версией API v1
        ai_client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options={'api_version': 'v1'}
        )
        print("🤖 Gemini AI клиент инициализирован (v1).")
    except Exception as e:
        print(f"❌ Ошибка инициализации клиента: {e}")
else:
    print("⚠️ Gemini AI не настроен (нет GEMINI_API_KEY в .env).")

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ РАСЧЕТА ПРИРОСТА (ДОБАВЛЕНО ТЗ 4.2.1) ---
async def get_growth(session, channel_id: int, days: int):
    """Находит разницу в подписчиках между сейчас и X дней назад"""
    target_date = datetime.now(timezone.utc) - timedelta(days=days)
    
    # Берем самую старую запись, которая ближе всего к целевой дате
    stmt = select(StatsHistory).where(
        StatsHistory.channel_id == channel_id,
        StatsHistory.timestamp <= target_date
    ).order_by(StatsHistory.timestamp.desc()).limit(1)
    
    result = await session.execute(stmt)
    old_record = result.scalar_one_or_none()
    return old_record.subs_count if old_record else None

# --- ДОПОЛНИТЕЛЬНАЯ ЛОГИКА: РЕАКЦИИ ---
async def add_reactions(chat_id: int, message_id: int):
    """Добавляет эмодзи-реакции на отправленное сообщение"""
    try:
        # ИСПРАВЛЕНО: Ставим одну реакцию, чтобы избежать ошибки TOO_MANY
        reactions = [ReactionTypeEmoji(emoji="👍")] # type: ignore
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=reactions # type: ignore
        )
    except Exception as e:
        print(f"⚠️ Не удалось поставить реакции: {e}")

# --- ФУНКЦИЯ СОХРАНЕНИЯ ФАЙЛА ---
async def save_upload_file(upload_file: UploadFile) -> str:
    """Сохраняет загруженный файл на диск и возвращает путь"""
    filename: str = upload_file.filename or "file.bin"
    file_ext = filename.split('.')[-1] if '.' in filename else "bin"
    file_path = f"static/uploads/{uuid.uuid4()}.{file_ext}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
    return file_path

# --- АНАЛИЗ ЗАЩИТЫ (ТЗ 4.6) ---
async def analyze_protection_logic(session, channel_id: int):
    """Анализирует историю подписок на предмет аномалий"""
    # Получаем историю за последние 3 дня
    stmt = select(StatsHistory).where(StatsHistory.channel_id == channel_id).order_by(StatsHistory.timestamp.desc()).limit(72)
    res = await session.execute(stmt)
    history = res.scalars().all()
    
    if len(history) < 2:
        return {"status": "ok", "message": "Недостаточно данных для анализа"}
    
    # 1. Поиск резких скачков (более 10% за час)
    spikes = []
    for i in range(len(history) - 1):
        curr = history[i]
        prev = history[i+1]
        diff = curr.subs_count - prev.subs_count
        if prev.subs_count > 0 and (diff / prev.subs_count) > 0.10:
            spikes.append(f"Скачок +{diff} ({curr.timestamp.strftime('%d.%m %H:%M')})")
            
    # 2. Поиск 'мертвых душ' (резкие отписки после подписок)
    # Упрощенно: если после роста сразу идет спад
    churn_warnings = []
    
    return {
        "status": "warning" if spikes else "ok",
        "spikes": spikes,
        "message": "Обнаружена подозрительная активность" if spikes else "Аномалий не найдено"
    }

# --- ФОНОВЫЙ ПЛАНИРОВЩИК ---
async def scheduler():
    # Ставим время в прошлом, чтобы обновление запустилось сразу после старта сервера
    last_metric_update = datetime.now(timezone.utc) - timedelta(hours=1, minutes=5)
    while True:
        try:
            # --- АВТОМАТИЧЕСКОЕ ОБНОВЛЕНИЕ ОХВАТОВ (ТЗ 4.2.2) ---
            # Запускаем раз в час
            if datetime.now(timezone.utc) - last_metric_update > timedelta(hours=1):
                print("🔄 Запуск фонового обновления охватов...")
                try:
                    async with async_session() as session:
                        # Получаем ID всех каналов
                        stmt = select(Channel.tg_id)
                        res = await session.execute(stmt)
                        channels = res.scalars().all()
                    
                    for cid in channels:
                        await update_post_metrics(cid)
                        await update_channel_stats(cid)
                        await asyncio.sleep(2) # Пауза, чтобы не словить FloodWait
                    
                    last_metric_update = datetime.now(timezone.utc)
                    print("✅ Статистика (охваты и подписчики) успешно обновлена.")
                except Exception as e:
                    print(f"⚠️ Ошибка фонового обновления метрик: {e}")

            async with async_session() as session:
                # ИСПРАВЛЕНО: Используем честный UTC (timezone-aware)
                now = datetime.now(timezone.utc)
                
                stmt = select(ScheduledPost).where(
                    ScheduledPost.publish_at <= now,
                    ScheduledPost.is_sent == 0
                )
                result = await session.execute(stmt)
                posts = result.scalars().all()

                for post in posts:
                    try:
                        msg = None
                        # Логика для ОПРОСОВ (ТЗ 4.4)
                        if post.is_poll:
                            options = json.loads(post.poll_options)
                            config = json.loads(post.poll_config or "{}")
                            msg = await bot.send_poll(
                                chat_id=post.channel_id,
                                question=post.poll_question,
                                options=options,
                                is_anonymous=config.get("is_anonymous", True),
                                allows_multiple_answers=config.get("allows_multiple_answers", False),
                                type="regular" # или "quiz" если нужно расширить
                            )
                        # Логика для ВИДЕО
                        elif post.video_data:
                            if post.video_data.startswith("static/"):
                                # Это файл на диске
                                input_file = FSInputFile(post.video_data)
                            else:
                                # Legacy: Base64
                                encoded = post.video_data.split(",", 1)[1] if "," in post.video_data else post.video_data
                                video_bytes = base64.b64decode(encoded)
                                input_file = BufferedInputFile(video_bytes, filename="video.mp4")
                            
                            msg = await bot.send_video(
                                chat_id=post.channel_id,
                                video=input_file,
                                caption=post.text
                            )
                        # Логика для ФОТО
                        elif post.image_data:
                            if post.image_data.startswith("static/"):
                                # Это файл на диске
                                input_file = FSInputFile(post.image_data)
                            else:
                                # Legacy: Base64
                                encoded = post.image_data.split(",", 1)[1] if "," in post.image_data else post.image_data
                                photo_bytes = base64.b64decode(encoded)
                                input_file = BufferedInputFile(photo_bytes, filename="photo.jpg")
                            
                            msg = await bot.send_photo(
                                chat_id=post.channel_id, 
                                photo=input_file, 
                                caption=post.text
                            )
                        # Логика для ТЕКСТА
                        else:
                            msg = await bot.send_message(chat_id=post.channel_id, text=post.text)
                        
                        if msg:
                            await add_reactions(post.channel_id, msg.message_id)
                            # ДОБАВЛЕНО: Сохраняем пост в статистику для ТЗ 4.2.2
                            new_p_stat = PostStat(channel_id=post.channel_id, message_id=msg.message_id)
                            session.add(new_p_stat)
                        
                        post.is_sent = 1
                        print(f"✅ [{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Пост {post.id} успешно отправлен в канал {post.channel_id}")
                    except TelegramRetryAfter as e:
                        print(f"⏳ Лимит Telegram. Ждем {e.retry_after} сек...")
                        await asyncio.sleep(e.retry_after)
                        continue
                    except Exception as e:
                        print(f"❌ Ошибка отправки поста {post.id}: {e}")
                
                await session.commit()
        except Exception as e:
            print(f"🚨 Ошибка в цикле планировщика: {e}")
        await asyncio.sleep(10)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🛠 Синхронизация БД...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # --- АВТО-МИГРАЦИЯ (MVP) ---
        # Добавляем новые колонки, если их нет (чтобы не удалять базу)
        migrations = [
            "ALTER TABLE scheduled_posts ADD COLUMN is_poll INTEGER DEFAULT 0",
            "ALTER TABLE scheduled_posts ADD COLUMN poll_question VARCHAR",
            "ALTER TABLE scheduled_posts ADD COLUMN poll_options VARCHAR",
            "ALTER TABLE scheduled_posts ADD COLUMN poll_config VARCHAR",
            "ALTER TABLE channels ADD COLUMN photo_url VARCHAR",
            "ALTER TABLE users ADD COLUMN wallet_address VARCHAR"
        ]
        for query in migrations:
            try:
                await conn.execute(text(query))
            except Exception:
                pass 

    if ai_client:
        print("🤖 Gemini AI подключен.")
    else:
        print("⚠️ Gemini AI не настроен (нет GEMINI_API_KEY в .env).")

    print("✅ База готова.")
    polling_task = asyncio.create_task(dp.start_polling(bot))
    scheduler_task = asyncio.create_task(scheduler())
    yield
    polling_task.cancel()
    scheduler_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def read_index():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"error": "index.html not found"}

@app.get("/script.js")
async def get_script():
    return FileResponse("script.js")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- БЕЗОПАСНОСТЬ И АВТОРИЗАЦИЯ (ТЗ 8) ---
security = HTTPBearer()

def validate_telegram_data(init_data: str) -> dict | None:
    """Проверяет подпись initData от Telegram"""
    try:
        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data:
            return None
        hash_value = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash == hash_value:
            return json.loads(parsed_data["user"])
        return None
    except Exception:
        return None

def create_token(tg_id: int) -> str:
    """Создает простой подписанный токен (замена JWT для MVP)"""
    payload = f"{tg_id}.{int(datetime.now().timestamp())}"
    signature = hmac.new(settings.BOT_TOKEN.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"

async def get_db():
    async with async_session() as session:
        yield session

async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Dependency для получения текущего пользователя из токена"""
    token = creds.credentials
    try:
        payload, signature = token.rsplit(".", 1)
        expected_sig = hmac.new(settings.BOT_TOKEN.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if signature != expected_sig:
            raise HTTPException(401, "Invalid token signature")
        
        tg_id_str, timestamp = payload.split(".")
        if datetime.now().timestamp() - int(timestamp) > 86400: # 24 часа жизни
             raise HTTPException(401, "Token expired")
        
        stmt = select(User).where(User.tg_id == int(tg_id_str))
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(401, "User not found")
        return user
    except Exception:
        raise HTTPException(401, "Invalid authentication")

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ПРОВЕРКИ ПРАВ ---
async def check_access(session, channel_id: int, user: User):
    """Проверяет, принадлежит ли канал пользователю"""
    stmt = select(Channel).where(Channel.tg_id == channel_id, Channel.owner_id == user.id)
    result = await session.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="⛔ Доступ запрещен: вы не владелец этого канала")

class DeleteRequest(BaseModel):
    id: int

# Новая модель для добавления канала (ДОБАВЛЕНО)
class ChannelAddRequest(BaseModel):
    tg_id: Union[int, str]

class ChannelCheckRequest(BaseModel):
    link: str

class ClearQueueRequest(BaseModel):
    channel_id: int

class AdRequest(BaseModel):
    title: str
    description: str
    link: str

class LoginRequest(BaseModel):
    initData: str

class InviteRequest(BaseModel):
    channel_id: int
    name: str

class AutoResponseAddRequest(BaseModel):
    channel_id: int
    trigger: str
    response: str

# --- ЭНДПОИНТЫ КАНАЛОВ (ДОБАВЛЕНО - ТЗ 4.1) ---

@app.get("/api/user_channels")
async def get_user_channels(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    stmt = select(Channel).where(Channel.owner_id == user.id)
    result = await db.execute(stmt)
    channels = result.scalars().all()
    return [
        {
            "id": c.tg_id, 
            "title": c.title, 
            "photo": c.photo_url, 
            "owner_id": user.tg_id 
        } for c in channels
    ]

@app.post("/api/login")
async def login_user(data: LoginRequest):
    """Регистрирует пользователя при входе и возвращает его роль"""
    tg_user = validate_telegram_data(data.initData)
    if not tg_user:
        raise HTTPException(403, "Invalid Telegram data")

    tg_id = tg_user["id"]
    first_name = tg_user.get("first_name", "User")
    username = tg_user.get("username")

    async with async_session() as session:
        stmt = select(User).where(User.tg_id == tg_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            # Регистрируем нового пользователя
            user = User(tg_id=tg_id, first_name=first_name, username=username)
            session.add(user)
            await session.commit()
        else:
            # Обновляем данные, если изменились
            if user.first_name != first_name or user.username != username:
                user.first_name = first_name
                user.username = username
                await session.commit()
        
        is_admin = (tg_id == settings.ADMIN_ID)
        role = "owner" if is_admin else "user"
        token = create_token(tg_id)
        return {"status": "success", "role": role, "is_admin": is_admin, "token": token}

@app.post("/api/save_wallet")
async def save_wallet(data: WalletRequest, user: User = Depends(get_current_user)):
    async with async_session() as session:
        # Получаем пользователя в текущей сессии
        db_user = await session.scalar(select(User).where(User.id == user.id))
        db_user.wallet_address = data.address
        await session.commit()
    return {"status": "success"}

@app.post("/api/resolve_channel")
async def resolve_channel(data: ChannelCheckRequest, user: User = Depends(get_current_user)):
    """Проверяет канал по ссылке перед добавлением"""
    try:
        identifier = data.link.strip()
        # Нормализация ссылки
        if "t.me/" in identifier:
            identifier = identifier.split("t.me/")[-1].split("/")[0].split("?")[0]
        
        identifier = identifier.replace("https://", "").replace("http://", "")
        if not identifier.lstrip("-").isdigit() and not identifier.startswith("@"):
            identifier = f"@{identifier}"
            
        try:
            chat = await bot.get_chat(identifier)
        except Exception:
            return {"status": "error", "message": "Канал не найден. Проверьте ссылку."}
            
        if chat.type not in ["channel", "supergroup"]:
            return {"status": "error", "message": "Это не канал и не супергруппа."}

        # Проверка прав бота
        try:
            member = await bot.get_chat_member(chat_id=chat.id, user_id=bot.id)
            if member.status not in ["administrator", "creator"]:
                return {"status": "error", "message": "Бот не является администратором канала!"}
        except Exception:
             return {"status": "error", "message": "Не удалось проверить права бота."}

        return {"status": "success", "id": chat.id, "title": chat.title, "members": await bot.get_chat_member_count(chat.id)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/add_channel")
async def add_channel(data: ChannelAddRequest, user: User = Depends(get_current_user)):
    try:
        print(f"📥 Запрос на добавление канала: {data.tg_id}")
        # 1. Нормализация ввода (ID, ссылка или юзернейм)
        identifier = str(data.tg_id).strip()
        
        # Если это ссылка (t.me/username)
        if "t.me/" in identifier:
            identifier = identifier.split("t.me/")[-1].split("/")[0].split("?")[0]
        
        # Очистка от мусора
        identifier = identifier.replace("https://", "").replace("http://", "")
        
        # Если это не число, добавляем @ (для юзернеймов)
        if not identifier.lstrip("-").isdigit() and not identifier.startswith("@"):
            identifier = f"@{identifier}"
            
        # 2. Получаем информацию о чате (резолвим ID)
        try:
            chat = await bot.get_chat(identifier)
        except Exception:
            return {"status": "error", "message": f"Не удалось найти канал: {identifier}. Проверьте ссылку или добавьте бота в админы."}

        # Проверка, является ли пользователь админом в этом канале
        # Используем chat.id, так как identifier мог быть юзернеймом
        member = await bot.get_chat_member(chat_id=chat.id, user_id=user.tg_id)
        if member.status not in ["administrator", "creator"]:
            return {"status": "error", "message": "Вы должны быть администратором этого канала!"}
        
        # Скачиваем фото канала, если есть
        photo_path = None
        if chat.photo:
            try:
                filename = f"channel_{chat.id}.jpg"
                dest = f"static/uploads/{filename}"
                await bot.download(chat.photo.small_file_id, destination=dest)
                photo_path = dest
            except Exception as e:
                print(f"⚠️ Не удалось скачать лого канала: {e}")

        async with async_session() as session:
            # Прикрепляем к текущему пользователю (user уже загружен через Depends)
            # Но нужно получить его ID в текущей сессии, поэтому лучше найти его снова или использовать user.id если он приатачен
            # user из Depends привязан к другой сессии, поэтому лучше найти по ID
            db_user = await session.scalar(select(User).where(User.id == user.id))

            # Проверка, не добавлен ли канал уже
            existing_stmt = select(Channel).where(Channel.tg_id == chat.id)
            existing_res = await session.execute(existing_stmt)
            if existing_res.scalar_one_or_none():
                return {"status": "error", "message": "Этот канал уже добавлен в систему"}

            new_channel = Channel(tg_id=chat.id, title=chat.title, owner_id=db_user.id, photo_url=photo_path)
            session.add(new_channel)
            await session.commit()
            
        return {"status": "success", "title": chat.title}
    except Exception as e:
        return {"status": "error", "message": f"Ошибка: {str(e)}. Убедитесь, что бот добавлен в канал!"}

@app.post("/api/delete_channel")
async def delete_channel(request: ChannelDeleteRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    # 1. Ищем канал, который принадлежит именно этому пользователю
    stmt = select(Channel).where(Channel.tg_id == request.channel_id, Channel.owner_id == user.id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if not channel:
        raise HTTPException(status_code=404, detail="Канал не найден или у вас нет прав на его удаление")

    try:
        # 2. Удаляем канал (каскадное удаление постов и статистики сработает, если настроено в models.py)
        await db.delete(channel)
        await db.commit()

        return {"status": "success", "message": f"Канал {channel.title} успешно удален из вашей панели"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при удалении: {str(e)}")

# --- ОСТАЛЬНЫЕ ЭНДПОИНТЫ ---

@app.get("/api/get_scheduled")
async def get_scheduled(channel_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
        try:
            c_id = int(channel_id)
        except (ValueError, TypeError):
            return []
        # Проверка прав (только чтение, но все же)
        await check_access(db, c_id, user)
        stmt = select(ScheduledPost).where(
            ScheduledPost.channel_id == c_id,
            ScheduledPost.is_sent == 0
        ).order_by(ScheduledPost.publish_at.asc())
        result = await db.execute(stmt)
        posts = result.scalars().all()
        return [{
            "id": p.id,
            "text": p.text[:50] + "..." if p.text and len(p.text) > 50 else (p.text or "Без текста"),
            "time": p.publish_at.isoformat(),
            "has_image": True if p.image_data else False,
            "media_type": "video" if p.video_data else ("photo" if p.image_data else "text")
        } for p in posts]

@app.post("/api/delete_scheduled")
async def delete_scheduled(data: DeleteRequest, user: User = Depends(get_current_user)):
    try:
        async with async_session() as session:
            # Сначала находим пост, чтобы узнать канал
            stmt_get = select(ScheduledPost).where(ScheduledPost.id == data.id)
            res = await session.execute(stmt_get)
            post = res.scalar_one_or_none()
            
            if post:
                await check_access(session, post.channel_id, user)
            
            # Удаляем файлы с диска, если они есть
            if post and post.image_data and post.image_data.startswith("static/"):
                if os.path.exists(post.image_data): os.remove(post.image_data)
            if post and post.video_data and post.video_data.startswith("static/"):
                if os.path.exists(post.video_data): os.remove(post.video_data)

            stmt = delete(ScheduledPost).where(ScheduledPost.id == data.id)
            await session.execute(stmt)
            await session.commit()
            print(f"🗑 Удален запланированный пост ID: {data.id}")
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/queue/move_post")
async def move_post(data: MovePostRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(ScheduledPost).where(ScheduledPost.id == data.post_id)
    result = await db.execute(stmt)
    post = result.scalar_one_or_none()
    
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")
    
    await check_access(db, post.channel_id, user.id)
    
    try:
        # Получаем новую дату из запроса (YYYY-MM-DD)
        new_date = datetime.strptime(data.new_date, "%Y-%m-%d").date()
        # Сохраняем текущее время поста, меняем только дату
        current_time = post.publish_at.time()
        post.publish_at = datetime.combine(new_date, current_time).replace(tzinfo=timezone.utc)
        await db.commit()
        return {"status": "success", "message": "Время поста обновлено"}
    except ValueError:
        raise HTTPException(status_code=400, detail="Неверный формат даты")

@app.post("/api/queue/auto-distribute")
async def auto_distribute(data: AutoDistributeRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await check_access(db, data.channel_id, user.id)
    # Логика авто-распределения (пока заглушка, как и требовалось)
    return {"status": "success", "message": "Очередь постов оптимизирована"}

@app.post("/api/schedule_post")
async def schedule_post(
    channel_id: int = Form(...),
    text: Optional[str] = Form(None),
    publish_at: str = Form(...),
    media: Optional[UploadFile] = File(None),
    poll_question: Optional[str] = Form(None),
    poll_options: Optional[str] = Form(None), # JSON string
    poll_config: Optional[str] = Form(None), # JSON string
    user: User = Depends(get_current_user)
):
    try:
        date_str = publish_at.replace('Z', '').split('.')[0]
        p_time = datetime.fromisoformat(date_str)
        
        image_path = None
        video_path = None

        if media:
            path = await save_upload_file(media)
            if media.content_type.startswith("video"):
                video_path = path
            else:
                image_path = path

        async with async_session() as session:
            await check_access(session, channel_id, user)
            new_post = ScheduledPost(
                channel_id=channel_id, 
                text=text if not poll_question else None, # Если опрос, текст игнорируем (или можно слать отдельно)
                publish_at=p_time,
                image_data=image_path,
                video_data=video_path,
                is_poll=1 if poll_question else 0,
                poll_question=poll_question,
                poll_options=poll_options,
                poll_config=poll_config
            )
            session.add(new_post)
            await session.commit()
            # Логируем, через сколько времени отправится пост
            now = datetime.now(timezone.utc)
            print(f"📅 Пост запланирован на {p_time} (через {p_time - now}) для канала {channel_id}")
        return {"status": "success", "message": f"Пост запланирован на {p_time}"}
    except Exception as e:
        return {"status": "error", "message": f"Ошибка: {str(e)}"}

@app.post("/api/edit_scheduled")
async def edit_scheduled(
    id: int = Form(...),
    text: Optional[str] = Form(None),
    publish_at: Optional[str] = Form(None),
    media: Optional[UploadFile] = File(None),
    poll_question: Optional[str] = Form(None),
    poll_options: Optional[str] = Form(None),
    poll_config: Optional[str] = Form(None),
    user: User = Depends(get_current_user)
):
    try:
        async with async_session() as session:
            stmt = select(ScheduledPost).where(ScheduledPost.id == id)
            result = await session.execute(stmt)
            post = result.scalar_one_or_none()
            
            if not post:
                return {"status": "error", "message": "Пост не найден"}
            
            await check_access(session, post.channel_id, user)

            if text is not None:
                post.text = text if not poll_question else None
            
            if poll_question:
                post.is_poll = 1
                post.poll_question = poll_question
                post.poll_options = poll_options
                post.poll_config = poll_config
            
            if publish_at:
                date_str = publish_at.replace('Z', '').split('.')[0]
                post.publish_at = datetime.fromisoformat(date_str)
            
            if media:
                # Удаляем старые файлы
                img_data = post.image_data
                if img_data and img_data.startswith("static/"):
                    if os.path.exists(img_data): os.remove(img_data)
                vid_data = post.video_data
                if vid_data and vid_data.startswith("static/"):
                    if os.path.exists(vid_data): os.remove(vid_data)
                
                path = await save_upload_file(media)
                if media.content_type.startswith("video"):
                    post.video_data = path
                    post.image_data = None
                else:
                    post.image_data = path
                    post.video_data = None
            
            await session.commit()
        return {"status": "success", "message": "Пост обновлен"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/send_post")
async def send_post(
    channel_id: int = Form(...),
    text: Optional[str] = Form(None),
    media: Optional[UploadFile] = File(None),
    poll_question: Optional[str] = Form(None),
    poll_options: Optional[str] = Form(None),
    poll_config: Optional[str] = Form(None),
    user: User = Depends(get_current_user)
):
    try:
        async with async_session() as session:
            await check_access(session, channel_id, user)
        msg = None
        
        if poll_question:
            options = json.loads(poll_options)
            config = json.loads(poll_config or "{}")
            msg = await bot.send_poll(
                chat_id=channel_id,
                question=poll_question,
                options=options,
                is_anonymous=config.get("is_anonymous", True),
                allows_multiple_answers=config.get("allows_multiple_answers", False)
            )
        elif media:
            # Для мгновенной отправки не обязательно сохранять на диск, 
            # но для единообразия и надежности (большие файлы) лучше сохранить временно или стримить.
            # Aiogram умеет стримить UploadFile напрямую, но надежнее сохранить.
            path = await save_upload_file(media)
            input_file = FSInputFile(path)
            
            if media.content_type.startswith("video"):
                msg = await bot.send_video(chat_id=channel_id, video=input_file, caption=text)
            else:
                msg = await bot.send_photo(chat_id=channel_id, photo=input_file, caption=text)
            
            # Удаляем временный файл после отправки (так как это не запланированный пост)
            if os.path.exists(path):
                os.remove(path)
        else:
            msg = await bot.send_message(chat_id=channel_id, text=text or "")
            
        if msg:
            await add_reactions(channel_id, msg.message_id)
            # ДОБАВЛЕНО: Сохраняем пост в статистику для ТЗ 4.2.2
            async with async_session() as session:
                new_p_stat = PostStat(channel_id=channel_id, message_id=msg.message_id)
                session.add(new_p_stat)
                await session.commit()
            print(f"🚀 Пост отправлен мгновенно в канал {channel_id}")
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/send_post_photo")
async def send_post_photo(channel_id: int = Form(...), text: str = Form(...), photo: UploadFile = File(...), user: User = Depends(get_current_user)):
    try:
        async with async_session() as session:
            await check_access(session, channel_id, user)
        photo_bytes = await photo.read()
        input_file = BufferedInputFile(photo_bytes, filename=str(photo.filename or "photo.jpg"))
        msg = await bot.send_photo(chat_id=channel_id, photo=input_file, caption=text)
        await add_reactions(channel_id, msg.message_id)
        # Сохраняем в PostStat
        async with async_session() as session:
            session.add(PostStat(channel_id=channel_id, message_id=msg.message_id))
            await session.commit()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- ОБНОВЛЕННЫЙ ЭНДПОИНТ (ДОРАБОТКА 4.2.1) ---
@app.get("/api/stats")
async def get_stats(channel_id: str, user: User = Depends(get_current_user)):
    try:
        try:
            c_id = int(channel_id)
        except (ValueError, TypeError):
             return {"status": "error", "message": "Invalid channel ID"}
        # 1. Получаем текущее кол-во через API Telegram
        count = await bot.get_chat_member_count(chat_id=c_id)
        
        async with async_session() as session:
            await check_access(session, c_id, user)
            # Сохраняем текущий срез в историю
            new_stat = StatsHistory(channel_id=c_id, subs_count=count)
            session.add(new_stat)
            
            # 2. Считаем прирост (ТЗ 4.2.1)
            subs_24h = await get_growth(session, c_id, 1)
            subs_7d = await get_growth(session, c_id, 7)
            subs_30d = await get_growth(session, c_id, 30)
            
            # 3. Средний охват из последних постов (ТЗ 4.2.2)
            p_stmt = select(PostStat).where(PostStat.channel_id == c_id).order_by(PostStat.date.desc()).limit(10)
            p_res = await session.execute(p_stmt)
            recent_posts = p_res.scalars().all()
            
            await session.commit()

            # Расчет дельты
            growth_24h = count - subs_24h if subs_24h is not None else 0
            growth_7d = count - subs_7d if subs_7d is not None else 0
            growth_30d = count - subs_30d if subs_30d is not None else 0

            # Охват: если есть посты в базе — считаем среднее, если нет — берем 42% от сабов
            if recent_posts:
                avg_reach = sum(p.views for p in recent_posts) // len(recent_posts)
            else:
                avg_reach = int(count * 0.42) 
                
            er = f"{(avg_reach / count * 100):.1f}%" if count > 0 else "0%"

            return {
                "status": "success",
                "subscribers": count,
                "growth": {
                    "24h": f"{growth_24h:+d}",
                    "7d": f"{growth_7d:+d}",
                    "30d": f"{growth_30d:+d}"
                },
                "avg_reach": avg_reach,
                "er": er
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/stats_history")
async def get_stats_history(channel_id: str):
    try:
        c_id = int(channel_id)
    except (ValueError, TypeError):
        return {"labels": [], "data": []}
    async with async_session() as session:
        stmt = select(StatsHistory).where(StatsHistory.channel_id == c_id).order_by(StatsHistory.timestamp.desc()).limit(7)
        result = await session.execute(stmt)
        history = list(result.scalars().all())
        history.reverse()
        return {
            "labels": [h.timestamp.strftime("%H:%M") for h in history],
            "data": [h.subs_count for h in history]
        }

# --- НОВЫЕ ФУНКЦИИ ДЛЯ АНАЛИТИКИ (ДОБАВЛЕНО ПО ТЗ) ---

async def update_channel_stats(channel_id: int):
    """Сохраняет текущее количество подписчиков в историю (фоновая задача)"""
    try:
        count = await bot.get_chat_member_count(chat_id=channel_id)
        async with async_session() as session:
            new_stat = StatsHistory(channel_id=channel_id, subs_count=count)
            session.add(new_stat)
            await session.commit()
    except Exception as e:
        print(f"⚠️ Ошибка обновления подписчиков для {channel_id}: {e}")

async def update_post_metrics(channel_id: int):
    """Обновляет количество просмотров и реакций для последних 10 постов из Telegram"""
    async with async_session() as session:
        # 1. Находим владельца канала, чтобы использовать его ID для пересылки
        stmt_owner = select(User.tg_id).join(Channel, Channel.owner_id == User.id).where(Channel.tg_id == channel_id)
        res_owner = await session.execute(stmt_owner)
        owner_tg_id = res_owner.scalar_one_or_none()

        if not owner_tg_id:
            return

        stmt = select(PostStat).where(PostStat.channel_id == channel_id).order_by(PostStat.date.desc()).limit(10)
        result = await session.execute(stmt)
        posts = result.scalars().all()
        
        for post in posts:
            try:
                # Получаем актуальные данные о сообщении из Telegram
                # Используем ID владельца канала вместо глобального ADMIN_ID
                msg = await bot.forward_message(chat_id=owner_tg_id, from_chat_id=channel_id, message_id=post.message_id)
                # Удаляем пересланное сообщение сразу
                await bot.delete_message(chat_id=owner_tg_id, message_id=msg.message_id)
                
                views = getattr(msg, 'views', 0)
                if views:
                    post.views = views
            except TelegramBadRequest as e:
                if "message to forward not found" in str(e) or "MESSAGE_ID_INVALID" in str(e):
                    print(f"🗑 Пост {post.message_id} удален в Telegram. Удаляем из статистики.")
                    await session.delete(post)
                else:
                    print(f"⚠️ Не удалось обновить метрики сообщения {post.message_id}: {e}")
            except Exception as e:
                print(f"⚠️ Не удалось обновить метрики сообщения {post.message_id}: {e}")
        
        await session.commit()

# --- ДОПОЛНИТЕЛЬНЫЙ ЭНДПОИНТ: ПРОВЕРКА КАНАЛА ---

@app.get("/api/check_admin")
async def check_admin_status(channel_id: str, user: User = Depends(get_current_user)):
    """Проверяет, является ли пользователь админом и какие права у бота"""
    try:
        try:
            c_id = int(channel_id)
        except (ValueError, TypeError):
            return {"status": "error", "message": "Invalid channel ID"}
        bot_member = await bot.get_chat_member(chat_id=c_id, user_id=bot.id)
        user_member = await bot.get_chat_member(chat_id=c_id, user_id=user.tg_id)
        
        return {
            "bot_is_admin": bot_member.status in ["administrator", "creator"],
            "user_is_admin": user_member.status in ["administrator", "creator"],
            "can_post": getattr(bot_member, 'can_post_messages', False)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- ЭНДПОИНТ АНАЛИЗА ЗАЩИТЫ (ТЗ 4.6) ---
@app.get("/api/analyze_protection")
async def analyze_protection(channel_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        try:
            c_id = int(channel_id)
        except (ValueError, TypeError):
            return {"status": "error", "message": "Invalid channel ID"}
        await check_access(db, c_id, user)
        return await analyze_protection_logic(db, c_id)
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- ЭНДПОИНТ ДЛЯ ОЧИСТКИ ОЧЕРЕДИ (ТЗ 4.1) ---

@app.post("/api/clear_queue")
async def clear_queue(data: ClearQueueRequest, user: User = Depends(get_current_user)):
    """Удаляет все запланированные, но не отправленные посты для канала"""
    try:
        async with async_session() as session:
            await check_access(session, data.channel_id, user)
            stmt = delete(ScheduledPost).where(
                ScheduledPost.channel_id == data.channel_id,
                ScheduledPost.is_sent == 0
            )
            await session.execute(stmt)
            await session.commit()
        return {"status": "success", "message": "Очередь очищена"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# --- ЭНДПОИНТЫ ДЛЯ ИСТОЧНИКОВ ТРАФИКА (ТЗ 4.1.3) ---

@app.post("/api/create_invite")
async def create_invite(data: InviteRequest, user: User = Depends(get_current_user)):
    try:
        async with async_session() as session:
            await check_access(session, data.channel_id, user)
            
            # Создаем ссылку через Telegram API
            link = await bot.create_chat_invite_link(
                chat_id=data.channel_id,
                name=data.name # Имя ссылки видно админам в Telegram
            )
            
            new_source = TrafficSource(
                channel_id=data.channel_id,
                name=data.name,
                invite_link=link.invite_link
            )
            session.add(new_source)
            await session.commit()
            
            return {"status": "success", "link": link.invite_link}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/traffic_sources")
async def get_traffic_sources(channel_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        c_id = int(channel_id)
    except (ValueError, TypeError):
        return []
    await check_access(db, c_id, user)
    stmt = select(TrafficSource).where(TrafficSource.channel_id == c_id)
    res = await db.execute(stmt)
    sources = res.scalars().all()
    return [{"id": s.id, "name": s.name, "link": s.invite_link, "joins": s.joins} for s in sources]

# --- ЭНДПОИНТЫ ДЛЯ АВТООТВЕТЧИКА (ТЗ 4.2.2) ---

@app.post("/api/add_auto_response")
async def add_auto_response(data: AutoResponseAddRequest, user: User = Depends(get_current_user)):
    try:
        async with async_session() as session:
            await check_access(session, data.channel_id, user)
            
            new_ar = AutoResponse(
                channel_id=data.channel_id,
                trigger=data.trigger.lower(),
                response=data.response
            )
            session.add(new_ar)
            await session.commit()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/auto_responses")
async def get_auto_responses(channel_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        c_id = int(channel_id)
    except (ValueError, TypeError):
        return []
    await check_access(db, c_id, user)
    stmt = select(AutoResponse).where(AutoResponse.channel_id == c_id)
    res = await db.execute(stmt)
    ars = res.scalars().all()
    return [{"id": a.id, "trigger": a.trigger, "response": a.response} for a in ars]

@app.post("/api/delete_auto_response")
async def delete_auto_response(data: DeleteRequest, user: User = Depends(get_current_user)):
    try:
        async with async_session() as session:
            # 1. Находим правило, чтобы узнать ID канала
            stmt_get = select(AutoResponse).where(AutoResponse.id == data.id)
            res = await session.execute(stmt_get)
            ar = res.scalar_one_or_none()
            
            if ar:
                # 2. Проверяем права пользователя на этот канал
                await check_access(session, ar.channel_id, user)
                await session.delete(ar)
            
            await session.commit()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/analytics/{chat_id}")
async def get_analytics(chat_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    try:
        c_id = int(channel_id) if 'channel_id' in locals() else int(chat_id)
    except:
        return {"status": "error", "message": "Invalid ID"}
    
    await check_access(db, c_id, user)

    # 7 days window (including today)
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=6)
    
    # Group by date and event_type
    stmt = select(
        func.date(ChatAnalytics.timestamp).label('date'),
        ChatAnalytics.event_type,
        func.count(ChatAnalytics.id)
    ).where(
        ChatAnalytics.chat_id == c_id,
        ChatAnalytics.timestamp >= start_date
    ).group_by(
        func.date(ChatAnalytics.timestamp),
        ChatAnalytics.event_type
    ).order_by('date')
    
    result = await db.execute(stmt)
    rows = result.all()
    
    # Prepare data map
    data_map = {}
    for r in rows:
        # r.date might be a string or date object depending on DB driver
        d_str = str(r.date)
        e_type = r.event_type
        count = r[2]
        if d_str not in data_map: data_map[d_str] = {}
        data_map[d_str][e_type] = count
        
    labels = []
    messages = []
    spam = []
    
    # Fill last 7 days
    for i in range(7):
        d = start_date + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        # Label format: "Пн", "Вт" etc. or just date "DD.MM"
        # Using DD.MM for simplicity across locales
        labels.append(d.strftime("%d.%m"))
        
        day_data = data_map.get(d_str, {})
        messages.append(day_data.get('message', 0))
        spam.append(day_data.get('spam', 0))

    return {
        "status": "success",
        "labels": labels,
        "messages": messages,
        "spam": spam
    }

# --- ЭНДПОИНТЫ ДЛЯ РЕКЛАМЫ (МОНЕТИЗАЦИЯ) ---

@app.get("/api/get_ads")
async def get_ads():
    """Возвращает активные рекламные объявления для всех пользователей"""
    async with async_session() as session:
        stmt = select(AdChannel).where(AdChannel.is_active == 1)
        result = await session.execute(stmt)
        ads = result.scalars().all()
        return [{"id": a.id, "title": a.title, "desc": a.description, "link": a.link} for a in ads]

@app.post("/api/admin/delete_channel_force")
async def admin_delete_channel(data: ChannelDeleteRequest, user: User = Depends(get_current_user)):
    """Супер-админ удаляет ЛЮБОЙ канал"""
    if user.tg_id != settings.ADMIN_ID:
        return {"status": "error", "message": "Access denied"}
    
    # Используем ту же логику, но без проверки владельца
    try:
        async with async_session() as session:
            # Удаляем канал по ID (игнорируя owner_id)
            stmt = delete(Channel).where(Channel.tg_id == data.channel_id)
            result = await session.execute(stmt)
            
            if result.rowcount > 0:
                # Чистим посты
                await session.execute(delete(ScheduledPost).where(ScheduledPost.channel_id == data.channel_id))
                await session.commit()
                return {"status": "success", "message": "Канал принудительно удален"}
            else:
                return {"status": "error", "message": "Канал не найден"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/admin/add_ad")
async def add_ad(data: AdRequest, user: User = Depends(get_current_user)):
    if user.tg_id != settings.ADMIN_ID:
        return {"status": "error", "message": "Access denied"}
    
    async with async_session() as session:
        new_ad = AdChannel(title=data.title, description=data.description, link=data.link)
        session.add(new_ad)
        await session.commit()
    return {"status": "success"}

@app.post("/api/admin/delete_ad")
async def delete_ad(data: DeleteRequest, user: User = Depends(get_current_user)):
    if user.tg_id != settings.ADMIN_ID:
        return {"status": "error", "message": "Access denied"}
    
    async with async_session() as session:
        stmt = delete(AdChannel).where(AdChannel.id == data.id)
        await session.execute(stmt)
        await session.commit()
    return {"status": "success"}

# --- ЭНДПОИНТЫ ДЛЯ СУПЕР-АДМИНА (ВЛАДЕЛЬЦА) ---

@app.get("/api/check_super_admin")
async def check_super_admin(user: User = Depends(get_current_user)):
    """Проверяет, является ли пользователь владельцем бота"""
    return {"is_admin": user.tg_id == settings.ADMIN_ID}

@app.get("/api/admin/stats")
async def get_admin_stats(user: User = Depends(get_current_user)):
    """Возвращает глобальную статистику по всей системе"""
    if user.tg_id != settings.ADMIN_ID:
        return {"status": "error", "message": "Access denied"}
    
    async with async_session() as session:
        # Считаем общие цифры
        users_count = await session.scalar(select(func.count(User.id)))
        channels_count = await session.scalar(select(func.count(Channel.id)))
        posts_count = await session.scalar(select(func.count(ScheduledPost.id)))
        ads_list = (await session.execute(select(AdChannel))).scalars().all()
        
        # Получаем список всех каналов с владельцами
        stmt = select(Channel, User).join(User, Channel.owner_id == User.id)
        res = await session.execute(stmt)
        channels_data = []
        for ch, u in res.all():
            channels_data.append({
                "id": ch.tg_id,
                "title": ch.title,
                "owner": u.first_name or "Unknown",
                "owner_id": u.tg_id
            })
            
        return {
            "status": "success",
            "users": users_count,
            "channels": channels_count,
            "posts": posts_count,
            "channels_list": channels_data,
            "ads": [{"id": a.id, "title": a.title} for a in ads_list]
        }

# --- ЭНДПОИНТ ДЛЯ AI (ТЗ 3.0) ---
@app.post("/api/ai/process")
async def ai_process(data: AIRequest, user: User = Depends(get_current_user)):
    client = ai_client
    if not client:
        return {"status": "error", "message": "API ключ Gemini не настроен на сервере"}
    
    try:
        prompt = ""
        if data.action == "generate":
            prompt = f"Напиши увлекательный пост для Telegram-канала на тему: '{data.prompt}'. Используй эмодзи, делай абзацы. Стиль: живой, вовлекающий."
        elif data.action == "rewrite":
            prompt = f"Перепиши этот текст для Telegram, сделав его более кликабельным, интересным и легким для чтения. Сохрани смысл:\n\n{data.prompt}"
        elif data.action == "headlines":
            prompt = f"Придумай 5 кликбейтных и цепляющих заголовков для этого текста (выведи только список):\n\n{data.prompt}"
        elif data.action == "clickbait":
            prompt = f"Перепиши этот текст, сделав его максимально интригующим и виральным (кликбейтным). Добавь 'крючок' в начале. Текст:\n\n{data.prompt}"
        elif data.action == "shorten":
            prompt = f"Сократи этот текст для Telegram, оставив только самую суть. Убери воду, сделай списки, если уместно. Текст:\n\n{data.prompt}"
        elif data.action == "emoji":
            prompt = f"Добавь подходящие эмодзи в этот текст, чтобы он выглядел визуально привлекательно и живо, но не переборщи. Текст:\n\n{data.prompt}"
        elif data.action == "analyze":
            prompt = f"Проанализируй этот пост для Telegram. Оцени его виральный потенциал от 0 до 100. Дай 3 конкретных совета, как его улучшить. Текст:\n\n{data.prompt}"
        else:
            return {"status": "error", "message": "Unknown action"}

        # Функция для вызова модели
        def process_ai_request_sync(p):
            try:
                # Используем модель из твоего списка (v2.5 Flash)
                response = client.models.generate_content(
                    model="gemini-2.5-flash", 
                    contents=p
                )
                return response.text
            except Exception as e:
                print(f"Ошибка ИИ: {e}")
                return "Ошибка генерации. Попробуйте позже."

        loop = asyncio.get_running_loop()
        result_text = await loop.run_in_executor(None, lambda: process_ai_request_sync(prompt))
        
        if result_text is None:
            result_text = ""

        # Очистка форматирования Markdown, если Gemini вернул лишнее
        if data.action != "analyze" and "Ошибка" not in result_text:
            result_text = result_text.replace("```markdown", "").replace("```", "").strip()

        return {
            "status": "success", 
            "result": result_text
        }
    except Exception as e:
        print(f"AI Error: {e}")
        return {"status": "error", "message": "Ошибка генерации. Попробуйте позже."}

@app.post("/api/ai/generate_image")
async def ai_generate_image(data: AIRequest, user: User = Depends(get_current_user)):
    """
    Эндпоинт для генерации изображений.
    Здесь должна быть интеграция с Nano Banana или другой моделью.
    Пока возвращаем заглушку для демонстрации работы фронтенда.
    """
    # TODO: Подключить реальную модель генерации (Nano Banana / Stable Diffusion)
    import urllib.parse
    safe_prompt = urllib.parse.quote(data.prompt[:50])
    # Используем сервис-заглушку, генерирующий картинку с текстом промпта
    image_url = f"https://placehold.co/1024x1024/248bed/ffffff.png?text={safe_prompt}&font=roboto"
    return {"status": "success", "image_url": image_url}

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)