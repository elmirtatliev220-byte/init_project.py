# --- STABLE VERSION CHECKPOINT ---
import asyncio
import uvicorn
import base64
import io
import logging
import os
import shutil
import uuid
import hmac
import hashlib
import json
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
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
from aiogram.filters import Command
from openai import AsyncOpenAI

# Импорты aiogram для работы с файлами и реакциями
from aiogram.types import BufferedInputFile, FSInputFile, ReactionTypeEmoji, Message
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest

import aiohttp
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

# Импорты проекта
from app.core.config import settings
from app.bot.main_bot import bot, dp
from app.core.models import Base, User, Channel, StatsHistory, PostStat, ScheduledPost, AdChannel, GroupProtection, TrafficSource, AutoResponse, ChatAnalytics
from app.core.schemas import AIRequest, WalletRequest, ChannelDeleteRequest, MovePostRequest, AutoDistributeRequest, LoginRequest, ProtectionRequest, DeleteRequest, ChannelAddRequest, ChannelCheckRequest, ClearQueueRequest, AdRequest, InviteRequest, AutoResponseAddRequest
from app.core.database import engine, async_session
from app.core.protection import analyze_protection_logic

# Создаем папку для загрузок, если нет
os.makedirs("static/uploads", exist_ok=True)

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("app.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Telecore")

# --- УТИЛИТА ДЛЯ ФОРМАТИРОВАНИЯ ---
def fix_html_formatting(text: str) -> str:
    """
    Полностью обрабатывает текст для публикации в Telegram, исправляя HTML,
    Markdown и проблемы форматирования от AI/RSS.
    """
    if not text:
        return ""

    # 1. Базовое форматирование Markdown в HTML
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.*?)__', r'<i>\1</i>', text, flags=re.DOTALL)

    # 2. Удаление Markdown-блоков
    text = re.sub(r'```(html|markdown)?\s*[\r\n]*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'```', '', text)

    # 3. Преобразование HTML-списков и блочных тегов
    text = re.sub(r'<li[^>]*>', '\n• ', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|h[1-6]|ul|ol|blockquote)>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<(p|div|h[1-6]|ul|ol|blockquote)[^>]*>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    # 4. Удаление всех остальных "мусорных" HTML-тегов
    text = re.sub(r'</?(?!(b|i|a|code|pre)\b)[a-z0-9]+[^>]*>', '', text, flags=re.IGNORECASE)

    # 5. Нормализация пробелов, буллитов и переносов
    text = re.sub(r'([^\n])\s*•', r'\1\n•', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()

def capitalize_bullets(text: str) -> str:
    """Делает первую букву в пунктах списка заглавной"""
    lines = text.split('\n')
    new_lines = []
    for line in lines:
        # Ищем строки, начинающиеся с буллитов
        if line.strip().startswith(('•', '-', '*')):
            parts = line.split(' ', 1)
            if len(parts) > 1 and parts[1]:
                # Делаем первую букву заглавной
                line = f"{parts[0]} {parts[1][0].upper()}{parts[1][1:]}"
        new_lines.append(line)
    return '\n'.join(new_lines)

# --- GEMINI AI MANAGER (ТЗ: Ротация ключей) ---
class GeminiManager:
    def __init__(self, keys: List[str]):
        self.keys = keys
        self.current_index = 0
        self.client = None
        self._init_client()

    def _init_client(self):
        if not self.keys:
            self.client = None
            return
        
        key = self.keys[self.current_index]
        try:
            # Инициализация клиента с ПРИНУДИТЕЛЬНОЙ версией API v1
            self.client = genai.Client(
                api_key=key,
                http_options={'api_version': 'v1'}
            )
            logger.info("🤖 Gemini AI клиент активирован (ключ %s/%s).", self.current_index + 1, len(self.keys))
        except Exception as e:
            logger.error("❌ Ошибка инициализации клиента с ключом %s...: %s", key[:5], e)
            self.client = None

    def get_client(self):
        return self.client

    def rotate(self):
        if not self.keys or len(self.keys) <= 1:
            logger.warning("⚠️ Ротация API ключа невозможна: всего один ключ или список пуст.")
            return False
        
        self.current_index = (self.current_index + 1) % len(self.keys)
        logger.info("🔄 Ротация API ключа. Новый индекс: %s", self.current_index)
        self._init_client()
        return True

ai_manager = GeminiManager(settings.GEMINI_API_KEY)

# Инициализация клиента DeepSeek
ds_client = None
if settings.DEEPSEEK_API_KEY:
    ds_client = AsyncOpenAI(
        api_key=settings.DEEPSEEK_API_KEY, 
        base_url=settings.DEEPSEEK_BASE_URL
    )
    logger.info("🤖 DeepSeek клиент инициализирован.")

# --- ФУНКЦИЯ FAILOVER (DeepSeek) ---
async def call_deepseek(prompt: str):
    """Вызов DeepSeek API как запасного варианта"""
    global ds_client
    if not ds_client:
        return None
    try:
        response = await ds_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Ты профессиональный SMM-редактор."},
                {"role": "user", "content": prompt},
            ],
            stream=False
        )
        return response.choices[0].message.content
    except Exception as e:
        err_msg = str(e)
        logger.error("❌ DeepSeek Error: %s", e)
        if "402" in err_msg or "Insufficient Balance" in err_msg:
            logger.warning("⚠️ DeepSeek отключен на эту сессию (нет средств).")
            ds_client = None
        return None

# --- НОВЫЙ ОБРАБОТЧИК КОМАНДЫ /ai_status ---
@dp.message(Command("ai_status"))
async def send_ai_status(message: Message):
    """Отправляет админу статус AI провайдеров."""
    if message.from_user.id != settings.ADMIN_ID:
        return

    # --- Gemini Status ---
    gemini_total_keys = len(ai_manager.keys)
    gemini_current_key = ai_manager.current_index + 1
    gemini_status = "✅ Активен" if ai_manager.get_client() else "❌ Отключен"
    gemini_text = (
        f"<b>🤖 Gemini Status</b>\n"
        f"Статус: {gemini_status}\n"
        f"Ключи: {gemini_total_keys} шт.\n"
        f"Активный ключ: №{gemini_current_key}"
    )

    # --- DeepSeek Status ---
    deepseek_status = "✅ Активен (Failover)" if ds_client else "❌ Отключен (Нет баланса?)"
    deepseek_text = (
        f"\n\n<b>🌊 DeepSeek Status</b>\n"
        f"Статус: {deepseek_status}"
    )
    
    full_message = gemini_text + deepseek_text
    await message.answer(full_message, parse_mode="HTML")

# --- МОДЕЛЬ КЭША НОВОСТЕЙ (ТЗ: АВТО-НОВОСТИ) ---
class NewsCache(Base):
    __tablename__ = "news_cache"
    id = Column(Integer, primary_key=True, index=True)
    link = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=func.now())

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
async def add_reactions(chat_id: int, message_id: int, emojis: Optional[List[str]] = None):
    """Добавляет эмодзи-реакции на отправленное сообщение"""
    try:
        # ИСПРАВЛЕНО: Бот может поставить только одну реакцию (Telegram API limit)
        target_emoji = emojis[0] if emojis and emojis else "🔥"
        reactions = [ReactionTypeEmoji(emoji=target_emoji)]
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=reactions
        )
    except Exception as e:
        logger.warning("⚠️ Не удалось поставить реакции: %s", e)

def clean_telegram_link(link: str) -> str:
    """
    BUGFIX: Полностью переработанная функция для надежной очистки.
    Очищает любую ссылку до чистого юзернейма (@username), ID (-100...) или инвайт-хеша (t.me/+...).
    """
    link = str(link).strip()
    
    # Удаляем стандартные префиксы
    for prefix in ["https://t.me/", "http://t.me/", "t.me/", "tg://resolve?domain="]:
        if link.startswith(prefix):
            link = link.replace(prefix, "", 1)
            break # Префикс найден, выходим из цикла
            
    # Убираем параметры типа ?start=
    link = link.split('?')[0]
    
    # Убираем слеш в конце, если есть
    if link.endswith('/'):
        link = link[:-1]

    # Если это публичный канал, но без @, добавляем его
    # Не добавляем @ к ID (-100...), приватным ссылкам (+) и joinchat/
    if not link.startswith(("@", "-", "+")) and not link.startswith("joinchat/"):
        # Проверяем, не является ли это числовым ID без минуса (для старых групп)
        if not link.isdigit():
            link = f"@{link}"
            
    return link

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ ОБРАБОТКИ ФОТО ---
async def get_and_save_logo(chat_id: int) -> Optional[str]:
    """Скачивает логотип и возвращает путь к нему"""
    try:
        chat = await bot.get_chat(chat_id)
        if chat.photo:
            os.makedirs("static/uploads", exist_ok=True)
            file_name = f"logo_{chat_id}.jpg"
            path = f"static/uploads/{file_name}"
            await bot.download(chat.photo.small_file_id, destination=path)
            # Возвращаем относительный путь для корректной работы фронтенда
            return path
    except Exception as e:
        logger.error("Ошибка скачивания лого для чата %s: %s", chat_id, e)
    return None

# --- ФУНКЦИЯ СОХРАНЕНИЯ ФАЙЛА ---
async def save_upload_file(upload_file: UploadFile) -> str:
    """Сохраняет загруженный файл на диск и возвращает путь"""
    os.makedirs("static/uploads", exist_ok=True)
    filename: str = upload_file.filename or "file.bin"
    file_ext = filename.split('.')[-1] if '.' in filename else "bin"
    file_path = f"static/uploads/{uuid.uuid4()}.{file_ext}"
    with open(file_path, "wb") as buffer:
        while content := await upload_file.read(1024 * 1024): # 1MB chunks
            buffer.write(content)
    return file_path

# --- ЛОГИКА АВТО-НОВОСТЕЙ ---
RSS_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml", # BBC World News (более специфично)
    "https://www.reuters.com/arc/outboundfeeds/rss/?outputType=xml", # Reuters Top News
    "https://www.aljazeera.com/xml/rss/all.xml",   # Al Jazeera All News (фокус на Ближнем Востоке)
    # Добавляем проверенные российские источники
    "https://lenta.ru/rss/news",                   # Lenta.ru
    "https://www.kommersant.ru/RSS/news.xml",      # Kommersant
    "https://tass.ru/rss/v2.xml",                  # TASS
    "https://ria.ru/export/rss2/archive/index.xml",# RIA Novosti
    "https://techcrunch.com/feed/",                # Tech
    "https://www.theverge.com/rss/index.xml",      # General Tech/Science
    # --- ИСТОЧНИКИ ДЛЯ UTILITIES (TECH & AI) ---
    "https://habr.com/ru/rss/news/?fl=ru",        # Хабр (Лучшие IT-новости)
    "https://vc.ru/rss",                           # VC.ru (Стартапы, бизнес, технологии)
    "https://forklog.com/feed",                    # ForkLog (Криптовалюта и блокчейн)
    "https://www.ixbt.com/export/news.rss"         # iXBT (Железо, гаджеты, рынок)
]

# Новые ключевые слова для фильтрации новостей по запрошенным темам
NEWS_KEYWORDS = {
    "tech_ai": [
        "artificial intelligence", "neural network", "startup", "innovation", "robotics", "cybersecurity", "metaverse", "chatgpt", "gemini", "openai", "google", "apple", "microsoft", "nvidia", "intel", "amd", "samsung", "huawei", "xiaomi", "crypto", "bitcoin", "blockchain", "technology", "software", "hardware", "processor", "cpu", "gpu", "smartphone", "phone", "messenger", "app", "application", "internet", "wifi", "router", "web", "data", "cloud", "server", "hack",
        "искусственный интеллект", "нейросеть", "стартап", "инновации", "робототехника", "кибербезопасность", "метавселенная", "технологии", "гаджеты", "it", "разработка", "наука", "крипта", "блокчейн", "чатгпт", "ии", "интел", "амд", "самсунг", "хуавей", "сяоми", "процессор", "смартфон", "телефон", "мессенджер", "приложение", "интернет", "вайфай", "роутер", "маршрутизатор", "веб", "взлом", "данные", "облако", "сервер"
    ],
    "iran_usa": [
        "iran", "usa", "america", "tehran", "washington", "conflict", "sanctions", "middle east", "persian gulf", "nuclear deal", "israel", "gaza",
        "иран", "сша", "америка", "тегеран", "вашингтон", "конфликт", "санкции", "ближний восток", "израиль", "газа", "хуситы", "йемен", "цхал"
    ],
    "russia_ukraine": [
        "russia", "ukraine", "kyiv", "moscow", "war", "conflict", "invasion", "donbas", "crimea", "zelensky", "putin", "nato", "eu", "sanctions",
        "россия", "рф", "украина", "киев", "москва", "война", "сво", "донбасс", "крым", "зеленский", "путин", "нато", "ес", "санкции", "всу", "фронт", 
        "брянск", "белгород", "курск", "воронеж", "бпла", "беспилотник", "дрон", "ракет", "взрыв", "обстрел", "мид"
    ],
    "cis_events": [
        "cis", "снг", "kazakhstan", "belarus", "uzbekistan", "armenia", "azerbaijan", "georgia", "moldova", "kyrgyzstan", "tajikistan", "turkmenistan", "central asia", "caucasus", "eurasia", "summit", "cooperation",
        "казахстан", "беларусь", "белоруссия", "узбекистан", "армения", "азербайджан", "грузия", "молдова", "киргизия", "кыргызстан", "таджикистан", "туркмения", "туркменистан", "токаев", "лукашенко", "пашинян", "алиев", "брикс", "шос", "одкаб"
    ]
}

# Маппинг реакций по категориям
REACTION_MAPPING = {
    "iran_usa": ["🔥", "😱", "😠"],
    "russia_ukraine": ["🔥", "😢", "😠"],
    "cis_events": ["👍", "🎉"],
    "tech_ai": ["🤩", "👍", "🔥"],
    "default": ["👍", "🔥", "❤️"]
}

# Список стоп-слов для исключения спортивных и развлекательных новостей
STOP_KEYWORDS = [
    "спорт", "футбол", "хоккей", "фигурное катание", "фигурист", "матч", "турнир", "чемпионат", "кубок", "олимпиада", "тренер", "гол", "счет", "лига", "соревнован", "медаль", "забег", "теннис", "бокс", "ufc", "mma", "боец", "поединок", "bare knuckle", "iba", "кулачные бои", "амбассадор",
    "sport", "football", "soccer", "hockey", "skating", "skater", "match", "tournament", "championship", "cup", "olympics", "coach", "score", "league", "medal", "race", "tennis", "boxing", "fight", "fighter"
]

# Подписи для разных категорий
CATEGORY_SIGNATURES = {
    "tech_ai": "\n\n#Tech #AI #IT #Инновации\n👇 <i>Делитесь мнением в комментариях!</i>",
    "iran_usa": "\n\n#Новости #Политика #Иран #США\n👇 <i>Делитесь мнением в комментариях!</i>",
    "russia_ukraine": "\n\n#Новости #Политика #РФ #Украина\n👇 <i>Делитесь мнением в комментариях!</i>",
    "cis_events": "\n\n#Новости #СНГ #Политика\n👇 <i>Делитесь мнением в комментариях!</i>",
    "default": "\n\n#Новости #Мир\n👇 <i>Делитесь мнением в комментариях!</i>"
}

# Максимальная длительность видео в секундах для публикации из RSS
MAX_VIDEO_DURATION_SECONDS = 120

def is_relevant_news(title: str, description: str) -> bool:
    """Проверяет, содержит ли новость ключевые слова по запрошенным темам."""
    text_to_check = (title + " " + description).lower()
    
    # 1. Сначала проверяем стоп-слова (фильтруем спорт)
    for stop_word in STOP_KEYWORDS:
        # Используем regex для поиска целых слов (исключаем частичные совпадения)
        if re.search(r'(?:^|\W)' + re.escape(stop_word) + r'(?:$|\W)', text_to_check):
            return False
    
    for category, keywords in NEWS_KEYWORDS.items():
        for keyword in keywords:
            # Ищем только полные слова
            if re.search(r'(?:^|\W)' + re.escape(keyword) + r'(?:$|\W)', text_to_check):
                return True
    return False

def get_news_category(title: str, description: str) -> str:
    """Определяет категорию новости для выбора реакций"""
    text_to_check = (title + " " + description).lower()
    for category, keywords in NEWS_KEYWORDS.items():
        for keyword in keywords:
            if re.search(r'(?:^|\W)' + re.escape(keyword) + r'(?:$|\W)', text_to_check):
                return category
    return "default"

def find_media_in_item(item: ET.Element) -> tuple[Optional[str], str, int]:
    """Возвращает (url, media_type, duration_sec). media_type может быть 'photo', 'video' или 'none'. duration_sec = 0 для фото или если не найдено."""
    namespaces = {
        'media': 'http://search.yahoo.com/mrss/',
        'content': 'http://purl.org/rss/1.0/modules/content/'
    }
    
    # Вспомогательная функция проверки расширения
    def is_image_ext(u: str) -> bool:
        if not u: return False
        return u.lower().split('?')[0].endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif'))
    
    # 1. Проверяем media:content (более подробный тег, часто с длительностью)
    # Ищем ВСЕ теги media:content, а не только первый попавшийся
    contents = item.findall('media:content', namespaces)
    for content in contents:
        if 'url' not in content.attrib: continue
        
        url = content.attrib['url']
        medium = content.attrib.get('medium')
        mime = content.attrib.get('type', '')
        
        duration_sec = 0
        if 'duration' in content.attrib:
            try:
                duration_sec = int(content.attrib['duration'])
            except: pass

        if medium == 'video' or mime.startswith('video/'):
            return url, 'video', duration_sec
        # Если medium не указан, но type="image/...", тоже берем
        if medium == 'image' or mime.startswith('image/') or is_image_ext(url):
            return url, 'photo', 0

    # 2. Проверяем media:group (группировка медиа)
    group = item.find('media:group', namespaces)
    if group is not None:
        contents = group.findall('media:content', namespaces)
        for content in contents:
            if 'url' not in content.attrib: continue
            url = content.attrib['url']
            medium = content.attrib.get('medium')
            mime = content.attrib.get('type', '')
            
            if medium == 'video' or mime.startswith('video/'):
                return url, 'video', 0
            if medium == 'image' or mime.startswith('image/') or is_image_ext(url):
                return url, 'photo', 0

    # 3. Проверяем <enclosure> (распространенный тег)
    enclosure = item.find('enclosure')
    if enclosure is not None and 'url' in enclosure.attrib:
        url = enclosure.attrib['url']
        mime = enclosure.attrib.get('type', '')
        if mime.startswith('video'):
            # У <enclosure> обычно нет стандартного тега длительности
            return url, 'video', 0
        if mime.startswith('image') or is_image_ext(url):
            return url, 'photo', 0

    # 4. Проверяем media:thumbnail (всегда фото)
    thumbnail = item.find('media:thumbnail', namespaces)
    if thumbnail is not None and 'url' in thumbnail.attrib:
        return thumbnail.attrib['url'], 'photo', 0

    # Regex для поиска картинок в HTML (улучшенный)
    img_regex = re.compile(r'<img[^>]+src\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)

    # 5. Ищем тег <img> внутри content:encoded (часто там картинки в полном размере)
    content_encoded = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
    if content_encoded is not None and content_encoded.text:
        match = img_regex.search(content_encoded.text)
        if match:
            return match.group(1), 'photo', 0

    # 6. Ищем тег <img> внутри описания (description)
    description_tag = item.find('description')
    if description_tag is not None and description_tag.text:
        match = img_regex.search(description_tag.text)
        if match:
            return match.group(1), 'photo', 0
            
    return None, 'none', 0

async def process_rss_news():
    """Парсит RSS, переписывает через AI и публикует в новостные каналы"""
    logger.info("📰 Запуск проверки RSS лент...")
    async with async_session() as session:
        # 1. Получаем списки каналов по категориям
        stmt_news = select(Channel).where(Channel.category == "News")
        res_news = await session.execute(stmt_news)
        news_channels = res_news.scalars().all()

        stmt_tech = select(Channel).where(Channel.category == "Utilities")
        res_tech = await session.execute(stmt_tech)
        tech_channels = res_tech.scalars().all()
        
        if not news_channels and not tech_channels:
            logger.warning("⚠️ Нет каналов с категориями 'News' или 'Utilities'. Пропуск.")
            return

    # 2. Обрабатываем фиды
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    async with aiohttp.ClientSession(headers=headers) as http_session:
        for feed_url in RSS_FEEDS:
            try:
                # ssl=False исправляет ошибку подключения к ria.ru
                async with http_session.get(feed_url, timeout=30, ssl=False) as response:
                    if response.status != 200: continue
                    xml_data = await response.text()
                    
                    root = ET.fromstring(xml_data)
                    # Берем первые 5 новостей из фида, чтобы увеличить шанс найти релевантные
                    items = root.findall(".//item")[:5]
                    
                    for item in items:
                        # Улучшенное извлечение данных с защитой от None и поддержкой content:encoded
                        title_elem = item.find("title")
                        title = (title_elem.text or "No Title").strip() if title_elem is not None else "No Title"
                        
                        link_elem = item.find("link")
                        link = (link_elem.text or "").strip() if link_elem is not None else ""
                        
                        desc_elem = item.find("description")
                        desc = (desc_elem.text or "").strip() if desc_elem is not None else ""
                        
                        # Пытаемся найти полный текст в content:encoded
                        content_elem = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
                        full_text = (content_elem.text or "").strip() if content_elem is not None else ""
                        
                        # Используем наиболее полный текст для AI и фильтров
                        main_text_for_ai = full_text if len(full_text) > len(desc) else desc
                        
                        if len(main_text_for_ai) < 50:
                            logger.info("  🤏 Пропускаем короткую новость: %s", title)
                            continue
                        
                        # Проверка даты (не старше 24 часов)
                        pub_date_str = item.find("pubDate").text if item.find("pubDate") is not None else ""
                        if pub_date_str:
                            try:
                                pub_date = parsedate_to_datetime(pub_date_str)
                                if pub_date.tzinfo is None:
                                    pub_date = pub_date.replace(tzinfo=timezone.utc)
                                
                                if datetime.now(timezone.utc) - pub_date > timedelta(hours=24):
                                    logger.info("  ⏳ Пропускаем старую новость: %s", title)
                                    continue
                            except Exception:
                                pass
                        
                        # НОВАЯ ЛОГИКА: Ищем медиа (видео или фото)
                        media_url, media_type, duration_sec = find_media_in_item(item)
                        
                        if not link: continue
                        
                        # НОВАЯ ЛОГИКА: Фильтруем видео по длительности (2 минуты = 120 секунд)
                        if media_type == 'video' and duration_sec > 0 and duration_sec > MAX_VIDEO_DURATION_SECONDS:
                            logger.info("  ⏩ Пропускаем слишком длинное видео (%s сек > %s сек): %s", duration_sec, MAX_VIDEO_DURATION_SECONDS, title)
                            continue
                        
                        # НОВАЯ ЛОГИКА: Фильтруем новости по ключевым словам
                        if not is_relevant_news(title, main_text_for_ai):
                            logger.info("  ⏩ Пропускаем нерелевантную новость: %s", title)
                            continue

                        # Проверяем кэш
                        async with async_session() as session:
                            stmt = select(NewsCache).where(NewsCache.link == link)
                            exists = await session.execute(stmt)
                            if exists.scalar():
                                continue # Уже было
                            
                            # Сохраняем в кэш СРАЗУ, чтобы другие потоки не подхватили
                            session.add(NewsCache(link=link))
                            await session.commit()

                        logger.info("🆕 Найдена новость: %s", title)
                        if media_url:
                            logger.info("  📷 Найдено медиа (%s): %s", media_type, media_url)
                        
                        # Генерируем пост через AI
                        # Fallback текст без ссылки (если AI упадет)
                        # FIX: Применяем чистку сразу к описанию, чтобы убрать <img> и не ломать Telegram
                        post_text = f"<b>{title}</b>\n\n{fix_html_formatting(desc)}"
                        
                        # Определяем категорию и реакции для этой новости
                        category = get_news_category(title, main_text_for_ai)
                        reactions_for_post = REACTION_MAPPING.get(category, REACTION_MAPPING["default"])

                        # Определяем целевые каналы
                        target_channels = []
                        if category == "tech_ai":
                            target_channels = tech_channels
                        else: # Все остальные релевантные категории идут в News
                            target_channels = news_channels
                        
                        if not target_channels:
                            logger.warning("  ⚠️ Пропуск: для категории '%s' нет подключенных каналов (News/Utilities).", category)
                            continue

                        # Формируем промпт для AI
                        prompt = (
                            f"Ты — элитный новостной редактор. Перепиши эту новость для Telegram в строгом формате:\n"
                            f"1. <b>Заголовок на русском</b> (обязательно в тегах <b>...</b>). В конце заголовка поставь один эмодзи (например ⚡️).\n"
                            f"2. Сделай отступ (пустую строку).\n"
                            f"3. Напиши 2-3 коротких пункта с сутью новости. Каждый пункт начинай с '• '.\n"
                            f"4. В конце каждого пункта ставь подходящий эмодзи (✅, 🚨, 📉 и т.д.).\n"
                            f"5. Весь текст строго на русском языке. Без вступлений и ссылок.\n\n"
                            f"Исходный текст:\n{title}\n{main_text_for_ai}"
                        )

                        # Логика Retry + Rotation для RSS
                        current_try = 0
                        gemini_success = False
                        while current_try < settings.AI_MAX_RETRIES:
                            client = ai_manager.get_client()
                            if not client:
                                break
                            try:
                                ai_resp = client.models.generate_content(
                                    model="gemini-2.5-flash", contents=prompt
                                )
                                if ai_resp.text:
                                    cleaned_text = fix_html_formatting(ai_resp.text)
                                    cleaned_text = re.sub(r'\[.*?\]', '', cleaned_text).strip()
                                    post_text = cleaned_text
                                    gemini_success = True
                                    break # Успех
                            except Exception as e:
                                logger.error("⚠️ Ошибка AI rewriting (попытка %s): %s", current_try + 1, e)
                                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                                    logger.warning("⏳ Лимит исчерпан. Ротация ключа...")
                                    
                                    # Умная задержка: читаем сколько просит подождать Gemini
                                    wait_time = settings.AI_RETRY_DELAY
                                    match = re.search(r'retry in (\d+\.?\d*)s', str(e))
                                    if match:
                                        wait_time = float(match.group(1)) + 1  # +1 сек запаса
                                        logger.info("   ↳ Gemini просит подождать %.1f сек.", wait_time)
                                    
                                    if ai_manager.rotate():
                                        await asyncio.sleep(wait_time)
                                    else:
                                        await asyncio.sleep(60) # Если ротация не помогла, ждем дольше
                                else:
                                    break # Не retryable ошибка
                            current_try += 1
                        
                        # Failover: DeepSeek (если Gemini не справился)
                        if not gemini_success:
                            if ds_client:
                                logger.warning("🔄 Все ключи Gemini подвели. Пробую DeepSeek...")
                                deepseek_text = await call_deepseek(prompt)
                                if deepseek_text:
                                    cleaned_text = fix_html_formatting(deepseek_text)
                                    post_text = re.sub(r'\[.*?\]', '', cleaned_text).strip()
                            else:
                                logger.info("ℹ️ AI недоступен. Публикую оригинал новости.")
                        
                        # --- ЯЗЫКОВОЙ ФИЛЬТР ---
                        # Если итоговый текст не содержит кириллицы (значит это английский оригинал и перевод не сработал),
                        # мы его пропускаем, чтобы не публиковать контент на иностранном языке.
                        if not re.search(r'[а-яА-Я]', post_text):
                            logger.info("  🇺🇸 Пропуск новости (не удалось перевести на русский): %s", title)
                            continue

                        # Добавляем подпись в зависимости от категории
                        signature = CATEGORY_SIGNATURES.get(category, CATEGORY_SIGNATURES["default"])
                        post_text += signature

                        # Рассылаем по целевым каналам
                        for ch in target_channels:
                            try:
                                msg = None
                                # НОВАЯ ЛОГИКА: Отправляем фото или видео
                                if media_type == 'video' and media_url:
                                    msg = await bot.send_video(
                                        chat_id=ch.tg_id,
                                        video=media_url,
                                        caption=post_text,
                                        parse_mode="HTML"
                                    )
                                elif media_type == 'photo' and media_url:
                                    msg = await bot.send_photo(
                                        chat_id=ch.tg_id,
                                        photo=media_url,
                                        caption=post_text,
                                        parse_mode="HTML"
                                    )
                                else:
                                    msg = await bot.send_message(ch.tg_id, post_text, parse_mode="HTML")
                                # Добавляем в статистику
                                async with async_session() as session:
                                    session.add(PostStat(channel_id=ch.tg_id, message_id=msg.message_id, views=0))
                                    await session.commit()
                                # Ставим тематические реакции
                                await add_reactions(ch.tg_id, msg.message_id, reactions_for_post) # type: ignore
                                logger.info("✅ Новость отправлена в канал: %s", ch.title)
                                await asyncio.sleep(2) # Anti-flood
                            except Exception as e:
                                logger.error("❌ Ошибка отправки новости в %s: %s", ch.title, e)
            except Exception as e:
                logger.error("⚠️ Ошибка парсинга фида %s: %s %s", feed_url, type(e).__name__, e)

async def cleanup_news_cache():
    """Очищает кэш новостей старше 3 дней (запуск раз в 24 часа)"""
    logger.info("🧹 Запуск очистки старого кэша новостей...")
    try:
        async with async_session() as session:
            # Удаляем записи старше 3 дней, чтобы база не пухла
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=3)
            stmt = delete(NewsCache).where(NewsCache.created_at < cutoff_date)
            result = await session.execute(stmt)
            await session.commit()
            if result.rowcount > 0:
                logger.info("✅ Кэш очищен. Удалено %s старых записей.", result.rowcount)
    except Exception as e:
        logger.error("⚠️ Ошибка очистки кэша: %s", e)

# --- ФОНОВЫЙ ПЛАНИРОВЩИК ---
async def scheduler():
    # Ставим время в прошлом, чтобы обновление запустилось сразу после старта сервера
    last_metric_update = datetime.now(timezone.utc) - timedelta(hours=1, minutes=5)
    last_news_update = datetime.now(timezone.utc) - timedelta(minutes=30) # Чтобы запустилось сразу
    last_cleanup = datetime.now(timezone.utc) - timedelta(hours=24) # Чтобы запустилось сразу при старте

    while True:
        try:
            # --- АВТОМАТИЧЕСКОЕ ОБНОВЛЕНИЕ ОХВАТОВ (ТЗ 4.2.2) ---
            # Запускаем раз в час
            if datetime.now(timezone.utc) - last_metric_update > timedelta(hours=1):
                logger.info("🔄 Запуск фонового обновления охватов...")
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
                    logger.info("✅ Статистика (охваты и подписчики) для %s каналов обновлена.", len(channels))
                except Exception as e:
                    logger.error("⚠️ Ошибка фонового обновления метрик: %s", e)

            # --- АВТО-ПУБЛИКАЦИЯ НОВОСТЕЙ (ТЗ: NEW) ---
            if datetime.now(timezone.utc) - last_news_update > timedelta(minutes=30):
                try:
                    await process_rss_news() # type: ignore
                    last_news_update = datetime.now(timezone.utc)
                except Exception as e:
                    logger.error(f"⚠️ Ошибка в модуле новостей: {e}")

            # --- ОЧИСТКА КЭША (ТЗ: CLEANUP) ---
            if datetime.now(timezone.utc) - last_cleanup > timedelta(hours=24):
                await cleanup_news_cache()
                last_cleanup = datetime.now(timezone.utc)

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
                                caption=post.text,
                                parse_mode="HTML"
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
                                caption=post.text,
                                parse_mode="HTML"
                            )
                        # Логика для ТЕКСТА
                        else:
                            msg = await bot.send_message(chat_id=post.channel_id, text=post.text, parse_mode="HTML")
                        
                        if msg:
                            await add_reactions(post.channel_id, msg.message_id)
                            # ДОБАВЛЕНО: Сохраняем пост в статистику для ТЗ 4.2.2
                            new_p_stat = PostStat(channel_id=post.channel_id, message_id=msg.message_id)
                            session.add(new_p_stat)
                        
                        post.is_sent = 1
                        logger.info("✅ Пост %s успешно отправлен в канал %s", post.id, post.channel_id)
                    except TelegramRetryAfter as e:
                        logger.warning("⏳ Лимит Telegram. Ждем %s сек...", e.retry_after)
                        await asyncio.sleep(e.retry_after)
                        continue
                    except Exception as e:
                        logger.error("❌ Ошибка отправки поста %s: %s", post.id, e)
                
                await session.commit()
        except Exception as e:
            logger.critical("🚨 КРИТИЧЕСКАЯ ОШИБКА в цикле планировщика: %s", e)
        await asyncio.sleep(10)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🛠 Синхронизация БД...")
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
            "ALTER TABLE users ADD COLUMN wallet_address VARCHAR",
            "ALTER TABLE channels ADD COLUMN protection_enabled INTEGER DEFAULT 0",
            "ALTER TABLE channels ADD COLUMN category VARCHAR DEFAULT 'General'",
            "ALTER TABLE auto_responses ADD COLUMN category VARCHAR DEFAULT 'General'"
        ]
        for query in migrations:
            try:
                await conn.execute(text(query))
            except Exception:
                pass 

    if ai_manager.get_client():
        logger.info("🤖 Gemini AI подключен.")
    else:
        logger.warning("⚠️ Gemini AI не настроен (нет GEMINI_API_KEY в .env).")

    logger.info("🔐 Bot Token loaded: %s... (Length: %s)", settings.BOT_TOKEN[:5], len(settings.BOT_TOKEN))
    logger.info("✅ База готова.")

    # Вывод списка каналов при старте
    logger.info("\n📋 СПИСОК ПОДКЛЮЧЕННЫХ КАНАЛОВ:")
    async with async_session() as session:
        result = await session.execute(select(Channel))
        channels = result.scalars().all()
        if channels:
            for ch in channels:
                logger.info("   • [%s] %s (ID: %s)", ch.category, ch.title, ch.tg_id)
        else:
            logger.info("   (Нет подключенных каналов)")
    logger.info("—" * 30 + "\n")

    polling_task = asyncio.create_task(dp.start_polling(bot))
    scheduler_task = asyncio.create_task(scheduler())
    yield
    polling_task.cancel()
    scheduler_task.cancel()
    await bot.session.close()

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info("📡 API LOG: %s %s", request.method, request.url)
    response = await call_next(request)
    return response

# --- ОБРАБОТЧИК ОШИБОК ВАЛИДАЦИИ ДЛЯ ЛОГИРОВАНИЯ ---
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Логируем "сырое" тело запроса, которое вызвало ошибку 422
    logger.error("--- 🕵️  ПЕРЕХВАЧЕНО НЕВАЛИДНОЕ ТЕЛО ЗАПРОСА (422) ---")
    try:
        body = await request.body()
        logger.error("URL: %s", request.url)
        logger.error("ТЕЛО ЗАПРОСА: %s", body.decode('utf-8'))
        # Пытаемся декодировать как UTF-8 для читаемости
    except Exception as e:
        logger.error("Не удалось прочитать тело запроса: %s", e)
    logger.error("—" * 55)
    
    # Возвращаем стандартный ответ FastAPI, чтобы клиент получил ту же ошибку
    # Это важно, чтобы не сломать поведение фронтенда
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

@app.get("/")
async def read_index():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"error": "index.html not found"}

@app.get("/script.js")
async def get_script():
    return FileResponse("script.js")

@app.get("/style.css")
async def get_style():
    return FileResponse("style.css")

@app.get("/api/config")
async def get_config():
    """
    Возвращает публичный URL API для фронтенда, чтобы он мог динамически
    настраивать свои запросы. Это убирает необходимость жестко прописывать
    URL в JavaScript коде.
    """
    return {
        "api_url": os.getenv("NGROK_URL") or os.getenv("API_URL"),
        "version": settings.VERSION
    }

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
        if not settings.BOT_TOKEN:
            return None

        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data:
            return None

        hash_to_check = parsed_data.pop("hash")
        
        # Сортируем ключи по алфавиту, как того требует Telegram
        data_check_string = "\n".join([f"{k}={v}" for k, v in sorted(parsed_data.items())])

        # Вычисляем секретный ключ на основе токена бота
        secret_key = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
        
        # Вычисляем финальный хеш
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if calculated_hash == hash_to_check:
            user_data_json = parsed_data.get("user")
            if user_data_json:
                return json.loads(user_data_json)
            return parsed_data
        
        # Логирование для отладки (можно оставить)
        logger.warning("❌ Hash mismatch!")
        logger.warning("Expected: %s", hash_to_check)
        logger.warning("Calculated: %s", calculated_hash)
        
        return None
    except Exception as e:
        logger.error("Verify error: %s", e)
        return None

def create_token(tg_id: int) -> str:
    """Создает простой подписанный токен (замена JWT для MVP)"""
    payload = f"{tg_id}.{int(datetime.now(timezone.utc).timestamp())}"
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
        if datetime.now(timezone.utc).timestamp() - int(timestamp) > 86400: # 24 часа жизни
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

@app.post("/api/login")
async def login_user(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Регистрирует пользователя при входе и возвращает его роль"""
    tg_user = validate_telegram_data(data.initData)

    if not tg_user:
        raise HTTPException(403, "Invalid Telegram data")

    tg_id = tg_user["id"]
    first_name = tg_user.get("first_name", "User")
    username = tg_user.get("username")

    stmt = select(User).where(User.tg_id == tg_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        # Регистрируем нового пользователя
        user = User(tg_id=tg_id, first_name=first_name, username=username)
        db.add(user)
        await db.commit()
    else:
        # Обновляем данные, если изменились
        if user.first_name != first_name or user.username != username:
            user.first_name = first_name
            user.username = username
            await db.commit()
    
    is_admin = (tg_id == settings.ADMIN_ID)
    role = "owner" if is_admin else "user"
    token = create_token(tg_id)
    return {"status": "success", "role": role, "is_admin": is_admin, "token": token}


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

class ChannelAddRequestLocal(BaseModel):
    link: Optional[str] = None
    tg_id: Optional[Union[str, int]] = None
    id: Optional[Union[str, int]] = None
    category: Optional[str] = "General"

@app.post("/api/add_channel")
async def add_channel(data: ChannelAddRequestLocal, user: User = Depends(get_current_user)):
    # Определяем входные данные (поддержка разных форматов фронтенда)
    raw_input = data.link or data.tg_id or data.id
    if not raw_input:
        raise HTTPException(422, "No channel identifier provided (link, tg_id, or id)")

    # BUGFIX: Используем единую, надежную функцию очистки
    target = clean_telegram_link(str(raw_input))
    
    try:
        # 1. Получение данных о канале из Telegram
        chat = await bot.get_chat(target)
        
        # BUGFIX: Повторная проверка прав бота, т.к. этот эндпоинт можно вызвать напрямую
        try:
            bot_member = await bot.get_chat_member(chat.id, bot.id)
            if bot_member.status not in ["administrator", "creator"]:
                # BUGFIX: Информативное сообщение об ошибке
                return {"status": "error", "message": "Ошибка: бот не является администратором в этом канале. Добавьте его с правами администратора."}
        except Exception:
            return {"status": "error", "message": "Не удалось проверить права бота в канале. Убедитесь, что он добавлен."}

        # BUGFIX: Проверка, является ли пользователь, добавляющий канал, его администратором
        try:
            user_member = await bot.get_chat_member(chat.id, user.tg_id)
            if user_member.status not in ["administrator", "creator"]:
                return {"status": "error", "message": "Ошибка: вы не являетесь администратором в этом канале."}
        except Exception:
             return {"status": "error", "message": "Не удалось проверить ваши права в канале."}

        # 2. Получение логотипа
        logo_url = await get_and_save_logo(chat.id)
        
        async with async_session() as session:
            # 3. Проверка на дубликат в базе
            stmt = select(Channel).where(Channel.tg_id == chat.id)
            res = await session.execute(stmt)
            if res.scalar():
                return {"status": "error", "message": "Этот канал уже добавлен в систему."}

            # 4. Сохранение нового канала
            new_channel = Channel(
                tg_id=chat.id,
                title=chat.title,
                owner_id=user.id,
                photo_url=logo_url,
                category=data.category or "General"
            )
            session.add(new_channel)
            await session.commit()
            
            return {"status": "success", "message": f"Канал «{chat.title}» успешно добавлен!", "title": chat.title}
    except TelegramBadRequest as e:
        # BUGFIX: Более детальная обработка ошибок от Telegram
        if "chat not found" in str(e).lower():
            return {"status": "error", "message": f"Канал «{target}» не найден. Проверьте правильность ссылки."}
        return {"status": "error", "message": f"Ошибка Telegram: {e}"}
    except Exception as e:
        logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА /api/add_channel: %s", e)
        return {"status": "error", "message": "Произошла внутренняя ошибка сервера."}

@app.post("/api/delete_channel")
async def delete_channel(request: ChannelDeleteRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    logger.info("🗑 Запрос на удаление канала %s от пользователя %s", request.channel_id, user.id)
    # 1. Ищем канал, который принадлежит именно этому пользователю
    stmt = select(Channel).where(Channel.tg_id == request.channel_id, Channel.owner_id == user.id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()

    if not channel:
        logger.error("❌ Ошибка удаления: Канал %s не найден у юзера %s", request.channel_id, user.id)
        raise HTTPException(status_code=404, detail="Канал не найден или у вас нет прав на его удаление")

    try:
        # 2. Удаляем канал (каскадное удаление постов и статистики сработает, если настроено в models.py)
        # Вручную удаляем связанные данные, чтобы избежать "осиротевших" записей в БД.
        await db.execute(delete(ScheduledPost).where(ScheduledPost.channel_id == request.channel_id))
        await db.execute(delete(StatsHistory).where(StatsHistory.channel_id == request.channel_id))
        await db.execute(delete(PostStat).where(PostStat.channel_id == request.channel_id))
        await db.execute(delete(TrafficSource).where(TrafficSource.channel_id == request.channel_id))
        await db.execute(delete(AutoResponse).where(AutoResponse.channel_id == request.channel_id))
        
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
            logger.info("🗑 Удален запланированный пост ID: %s", data.id)
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
    
    await check_access(db, post.channel_id, user)
    
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
    await check_access(db, data.channel_id, user)
    
    stmt = select(ScheduledPost).where(
        ScheduledPost.channel_id == data.channel_id,
        ScheduledPost.is_sent == 0
    ).order_by(ScheduledPost.publish_at.asc())
    
    result = await db.execute(stmt)
    posts = result.scalars().all()
    
    current_time = datetime.now(timezone.utc) + timedelta(minutes=30)
    for post in posts:
        post.publish_at = current_time
        current_time += timedelta(hours=2)
    
    await db.commit()
    return {"status": "success", "message": f"Оптимизировано {len(posts)} постов"}

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
            # Форматируем текст перед сохранением
            new_post = ScheduledPost(
                channel_id=channel_id, 
                text=fix_html_formatting(text) if not poll_question else None, 
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
            logger.info("📅 Пост запланирован на %s (через %s) для канала %s", p_time, p_time - now, channel_id)
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
                post.text = fix_html_formatting(text) if not poll_question else None
            
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
            path = ""
            try:
                path = await save_upload_file(media)
                input_file = FSInputFile(path)
                
                if media.content_type.startswith("video"):
                    msg = await bot.send_video(chat_id=channel_id, video=input_file, caption=text, parse_mode="HTML")
                else:
                    msg = await bot.send_photo(chat_id=channel_id, photo=input_file, caption=text, parse_mode="HTML")
            finally:
                # Удаляем временный файл после отправки (так как это не запланированный пост)
                if path and os.path.exists(path):
                    os.remove(path)
        else:
            # Чистим текст перед мгновенной отправкой
            clean_text = fix_html_formatting(text or "")
            msg = await bot.send_message(chat_id=channel_id, text=clean_text, parse_mode="HTML")
            
        if msg:
            await add_reactions(channel_id, msg.message_id)
            # ДОБАВЛЕНО: Сохраняем пост в статистику для ТЗ 4.2.2
            async with async_session() as session:
                new_p_stat = PostStat(channel_id=channel_id, message_id=msg.message_id)
                session.add(new_p_stat)
                await session.commit()
            logger.info("🚀 Пост отправлен мгновенно в канал %s", channel_id)
        return {"status": "success"}
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e):
            return {"status": "error", "message": "⚠️ Ошибка HTML: Проверьте теги <b> и <i> (возможно, один не закрыт)."}
        return {"status": "error", "message": f"Ошибка Telegram: {e}"}
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
        logger.error("⚠️ Ошибка обновления подписчиков для %s: %s", channel_id, e)

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
                    logger.info("🗑 Пост %s удален в Telegram. Удаляем из статистики.", post.message_id)
                    await session.delete(post)
                else:
                    logger.warning("⚠️ Не удалось обновить метрики сообщения %s: %s", post.message_id, e)
            except Exception as e:
                logger.warning("⚠️ Не удалось обновить метрики сообщения %s: %s", post.message_id, e)
        
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

@app.post("/api/protection/toggle")
async def toggle_protection(data: ProtectionRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    chat_id = int(data.chat_id)
    await check_access(db, chat_id, user)
    
    # Используем ORM
    stmt = select(Channel).where(Channel.tg_id == chat_id)
    result = await db.execute(stmt)
    channel = result.scalar_one_or_none()
    
    if not channel:
        return {"status": "error", "message": "Channel not found"}

    channel.protection_enabled = 0 if channel.protection_enabled else 1
    await db.commit()
    
    return {
        "status": "success", 
        "is_enabled": bool(channel.protection_enabled),
        "message": "🛡 Защита активирована" if channel.protection_enabled else "🛡 Защита выключена"
    }

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
        c_id = int(chat_id)
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
# Исправление: Определяем модель локально, чтобы гарантировать наличие поля action
class AIRequestModel(BaseModel):
    prompt: str
    action: str = "generate"

@app.post("/api/ai/process")
async def ai_process(data: AIRequestModel, user: User = Depends(get_current_user)):
    system_instruction = (
            "Ты — профессиональный SMM-редактор Telegram. "
            "КРИТИЧЕСКОЕ ПРАВИЛО: НЕ ИСПОЛЬЗУЙ СИМВОЛЫ '**'. "
            "Вместо них для жирного шрифта используй ТОЛЬКО HTML-теги <b> и </b>. "
            "СТРУКТУРА ПОСТА:\n"
            "1. <b>ЗАГОЛОВОК КАПСОМ</b> (с эмодзи)\n"
            "2. Краткое вступление\n"
            "3. Основная суть (используй буллиты • и эмодзи)\n"
            "4. Призыв к действию (CTA)\n"
            "5. Хештеги в конце.\n"
            "ВАЖНО: Текст должен быть живым. ОБЯЗАТЕЛЬНО используй эмодзи, которые СТРОГО СООТВЕТСТВУЮТ ТЕМЕ ПОСТА.\n"
            "ПРИМЕРЫ ЭМОДЗИ:\n"
            "- Новости/Срочно: ⚡, 🔥, 🚨\n"
            "- Бизнес/Финансы: 💰, 📈, 💼, 💸\n"
            "- Технологии/IT: 💻, 🤖, 📱, ⚙️\n"
            "- Лайфстайл/Советы: ✨, 🌱, 💡, 🧘\n"
            "Подбирай их по смыслу к каждому абзацу."
        )

    user_prompt = ""
    if data.action == "generate":
        user_prompt = f"Тема поста: '{data.prompt}'. Напиши яркий пост с тематическими эмодзи, строго следуя структуре: Заголовок, Введение, Список, Вывод, Хештеги."
    elif data.action == "rewrite":
        user_prompt = f"Перепиши этот текст для Telegram, сделав его более кликабельным и читабельным. Добавь HTML теги:\n\n{data.prompt}"
    elif data.action == "headlines":
        user_prompt = f"Придумай 5 цепляющих заголовков для этого текста (списком):\n\n{data.prompt}"
    elif data.action == "clickbait":
        user_prompt = f"Сделай этот текст максимально кликбейтным и виральным:\n\n{data.prompt}"
    elif data.action == "shorten":
        user_prompt = f"Сократи текст до сути, используй списки:\n\n{data.prompt}"
    elif data.action == "emoji":
        user_prompt = f"Расставь подходящие эмодзи в этом тексте:\n\n{data.prompt}"
    elif data.action == "analyze":
        user_prompt = f"Проанализируй пост. Оцени виральность (0-100) и дай советы:\n\n{data.prompt}"
    else:
        return {"status": "error", "message": "Unknown action"}

    full_prompt = f"{system_instruction}\n\nЗАДАЧА:\n{user_prompt}"

    result_text = None
    
    # Цикл повторных попыток с ротацией
    for attempt in range(settings.AI_MAX_RETRIES):
        client = ai_manager.get_client()
        if not client:
            # Если ключей нет, прерываем цикл Gemini и идем к DeepSeek
            break
            
        def process_ai_request_sync(p, c):
            try:
                # Используем модель из твоего списка (v2.5 Flash)
                response = c.models.generate_content(
                    model="gemini-2.5-flash", 
                    contents=p
                )
                return response.text
            except Exception as e:
                raise e

        loop = asyncio.get_running_loop()
        try:
            result_text = await loop.run_in_executor(None, lambda: process_ai_request_sync(full_prompt, client))
            break # Успешное выполнение, выходим из цикла
        except Exception as e:
            error_str = str(e)
            logger.error("⚠️ AI Error (Attempt %s): %s", attempt + 1, e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                ai_manager.rotate()
                
                # Умная задержка для API эндпоинта
                wait_time = settings.AI_RETRY_DELAY
                match = re.search(r'retry in (\d+\.?\d*)s', error_str)
                if match:
                    wait_time = float(match.group(1))
                
                await asyncio.sleep(wait_time)
            else:
                # Если ошибка не связана с лимитами, прерываем попытки
                break

    # Failover: Если Gemini не справился (или ключей нет), пробуем DeepSeek
    if not result_text:
        logger.warning("🔄 Все ключи Gemini подвели (или недоступны). Пробую DeepSeek...")
        result_text = await call_deepseek(full_prompt)

    if not result_text:
        return {"status": "error", "message": "Не удалось сгенерировать ответ. Попробуйте позже."}

    # Очистка форматирования Markdown
    if data.action != "analyze":
        result_text = result_text.replace("```html", "").replace("```markdown", "").replace("```", "").strip()
        result_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', result_text, flags=re.DOTALL)
        result_text = re.sub(r'__(.*?)__', r'<i>\1</i>', result_text, flags=re.DOTALL)
        result_text = capitalize_bullets(result_text)
        if result_text.count("<b>") != result_text.count("</b>"):
            result_text += "</b>"

    return {"status": "success", "result": result_text}

@app.post("/api/ai/generate_image")
async def ai_generate_image(data: AIRequestModel, user: User = Depends(get_current_user)):
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


# --- ДОБАВЛЕНИЕ: ГЕНЕРАЦИЯ КОНТЕНТ-ПЛАНА (3-14 ДНЕЙ) ---
class ContentPlanRequest(BaseModel):
    channel_id: int
    days: int = Field(7, ge=3, le=14)

@app.post("/api/ai/content_plan")
async def generate_content_plan(data: ContentPlanRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await check_access(db, data.channel_id, user)

    # 1. Получаем инфо о канале (чтобы ИИ понимал тематику)
    stmt = select(Channel).where(Channel.tg_id == data.channel_id)
    res = await db.execute(stmt)
    channel = res.scalar_one_or_none()
    
    if not channel:
        raise HTTPException(status_code=404, detail="Канал не найден")

    # 2. Промпт для ИИ
    prompt = (
        f"Ты элитный SMM-менеджер. Составь контент-план на {data.days} дней для Telegram-канала '{channel.title}' "
        f"(Тематика/Категория: {channel.category}).\n"
        f"Напиши {data.days} крутых готовых постов. Каждый пост должен быть увлекательным, содержать эмодзи и использовать HTML-теги <b> и <i>.\n"
        "ОТВЕТ ВЕРНИ СТРОГО В ФОРМАТЕ JSON-МАССИВА СТРОК! Никакого лишнего текста, только массив.\n"
        "Пример: [\"<b>Пост 1</b>...\", \"<b>Пост 2</b>...\"]"
    )

    # --- НОВАЯ ЛОГИКА: RETRY + FAILOVER ---
    result_text = None
    
    # Цикл повторных попыток с ротацией для Gemini
    for attempt in range(settings.AI_MAX_RETRIES):
        client = ai_manager.get_client()
        if not client:
            break # Если Gemini недоступен, переходим к DeepSeek
            
        def process_ai_request_sync(p, c):
            try:
                response = c.models.generate_content(
                    model="gemini-2.5-flash", 
                    contents=p
                )
                return response.text
            except Exception as e:
                raise e

        loop = asyncio.get_running_loop()
        try:
            result_text = await loop.run_in_executor(None, lambda: process_ai_request_sync(prompt, client))
            if result_text:
                break # Успешное выполнение, выходим из цикла
        except Exception as e:
            error_str = str(e)
            logger.error("⚠️ Ошибка генерации контент-плана (попытка %s): %s", attempt + 1, e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                ai_manager.rotate()
                wait_time = settings.AI_RETRY_DELAY
                match = re.search(r'retry in (\d+\.?\d*)s', error_str)
                if match:
                    wait_time = float(match.group(1)) + 1
                
                await asyncio.sleep(wait_time)
            else:
                break

    # Failover: Если Gemini не справился, пробуем DeepSeek
    if not result_text:
        logger.warning("🔄 Gemini не справился с генерацией плана, пробую DeepSeek...")
        result_text = await call_deepseek(prompt)

    if not result_text:
        return {"status": "error", "message": "Не удалось сгенерировать контент-план. AI временно недоступен."}
    
    try:
        # 4. Очищаем ответ от маркдауна и парсим JSON
        text_resp = result_text.replace("```json", "").replace("```", "").strip()
        posts = json.loads(text_resp)

        if not isinstance(posts, list) or not posts:
            raise ValueError("AI вернул некорректный или пустой JSON.")

    except (json.JSONDecodeError, ValueError) as e:
        logger.error("❌ Ошибка парсинга JSON от AI для контент-плана: %s. Ответ AI: %s", e, result_text)
        return {"status": "error", "message": "Не удалось обработать ответ от AI. Попробуйте еще раз."}

    # 5. Планируем посты на N дней вперед (по умолчанию на 12:00 UTC)
    start_time = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=1)
    
    new_posts = []
    for i, post_text in enumerate(posts[:data.days]):
        pub_time = start_time + timedelta(days=i)
        new_post = ScheduledPost(
            channel_id=data.channel_id,
            text=fix_html_formatting(post_text),
            publish_at=pub_time,
            is_poll=0
        )
        db.add(new_post)
        new_posts.append(new_post)

    await db.commit()

    return {"status": "success", "message": f"✨ Магия! {len(new_posts)} постов запланировано."}

app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
