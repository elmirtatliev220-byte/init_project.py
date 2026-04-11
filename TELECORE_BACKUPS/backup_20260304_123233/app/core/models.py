from sqlalchemy import Column, Integer, String, BigInteger, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    tg_id = Column(BigInteger, unique=True, index=True)
    first_name = Column(String)
    username = Column(String, nullable=True)
    wallet_address = Column(String, nullable=True)
    # Связь с каналами
    channels = relationship("Channel", back_populates="owner")

class Channel(Base):
    __tablename__ = "channels"
    id = Column(Integer, primary_key=True, index=True)
    tg_id = Column(BigInteger, unique=True, index=True)
    title = Column(String)
    owner_id = Column(Integer, ForeignKey("users.id"))
    owner = relationship("User", back_populates="channels")
    photo_url = Column(String, nullable=True)
    protection_enabled = Column(Integer, default=0)

class StatsHistory(Base):
    __tablename__ = "stats_history"
    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(BigInteger)
    subs_count = Column(Integer)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class ChatAnalytics(Base):
    __tablename__ = "chat_analytics"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, index=True)
    event_type = Column(String) # message, spam, join
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

# НОВАЯ МОДЕЛЬ ДЛЯ АНАЛИТИКИ ПОСТОВ (ТЗ 4.2.2)
class PostStat(Base):
    __tablename__ = "post_stats"
    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(BigInteger, index=True)
    message_id = Column(BigInteger)
    views = Column(Integer, default=0)
    reactions = Column(Integer, default=0)
    date = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"
    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(BigInteger)
    text = Column(String, nullable=True)
    image_data = Column(String, nullable=True) 
    video_data = Column(String, nullable=True) # Добавлено для видео
    # Поля для опросов (ТЗ 4.4)
    is_poll = Column(Integer, default=0)
    poll_question = Column(String, nullable=True)
    poll_options = Column(String, nullable=True) # JSON string ["Option 1", "Option 2"]
    poll_config = Column(String, nullable=True) # JSON string {"is_anonymous": true, "allows_multiple_answers": false}
    publish_at = Column(DateTime)
    is_sent = Column(Integer, default=0)

# МОДЕЛЬ ДЛЯ РЕКЛАМЫ (МОНЕТИЗАЦИЯ)
class AdChannel(Base):
    __tablename__ = "ad_channels"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    description = Column(String)
    link = Column(String)
    is_active = Column(Integer, default=1)

# МОДЕЛЬ ДЛЯ ЗАЩИТЫ ЧАТОВ (ТЗ 4.2.1)
class GroupProtection(Base):
    __tablename__ = "group_protection"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(BigInteger, unique=True, index=True) # ID группы, где проверяем
    channel_id = Column(BigInteger) # ID канала, на который надо подписаться
    channel_username = Column(String) # Юзернейм канала (для ссылки)

# МОДЕЛЬ ДЛЯ ИСТОЧНИКОВ ТРАФИКА (ТЗ 4.1.3)
class TrafficSource(Base):
    __tablename__ = "traffic_sources"
    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(BigInteger, index=True)
    name = Column(String) # Название метки (например, "Instagram Bio")
    invite_link = Column(String) # Сама ссылка t.me/+...
    joins = Column(Integer, default=0) # Количество вступивших

# МОДЕЛЬ ДЛЯ АВТООТВЕТЧИКА (ТЗ 4.2.2)
class AutoResponse(Base):
    __tablename__ = "auto_responses"
    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(BigInteger, index=True)
    trigger = Column(String) # Ключевое слово
    response = Column(String) # Ответ бота