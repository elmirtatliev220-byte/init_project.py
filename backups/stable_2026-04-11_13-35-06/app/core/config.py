import os
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

class Settings:
    """
    Класс для хранения всех настроек проекта.
    Возвращаем структуру, где настройки находятся внутри класса.
    """
    PROJECT_NAME: str = "Telecore"
    VERSION: str = "1.0.0"
    BASE_URL: str = "https://felicidad-selfsame-veritably.ngrok-free.dev"
    BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
    ADMIN_ID: int = int(os.environ.get("ADMIN_ID", 0))
    GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY")
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./telecore.db")

settings = Settings()