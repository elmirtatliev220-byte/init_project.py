from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings

# Инициализация асинхронного движка базы данных
engine = create_async_engine(settings.DATABASE_URL, echo=False)

# Фабрика асинхронных сессий
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)