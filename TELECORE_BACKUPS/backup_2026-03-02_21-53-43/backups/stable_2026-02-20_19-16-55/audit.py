import os
import re
import shutil
from datetime import datetime
from typing import Optional

EXPECTED_CHAN_ID: str = "-1002424660098"
CURRENT_NGROK: str = "https://felicidad-selfsame-veritably.ngrok-free.dev" 

def get_version() -> str:
    """Извлекает версию из конфига с типизацией для стабильности Pylance."""
    try:
        if os.path.exists("app/core/config.py"):
            with open("app/core/config.py", "r", encoding="utf-8") as f:
                content: str = f.read()
                # Добавлен флаг re.MULTILINE для ускорения поиска
                match: Optional[re.Match] = re.search(r'VERSION = "(.*?)"', content, re.MULTILINE)
                if match:
                    return str(match.group(1))
        return "1.0.0"
    except Exception:
        return "1.0.0"

def create_backup() -> None:
    """Создает локальную резервную копию стабильной версии."""
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = f"backups/stable_{ts}"
    
    files_to_save = [
        "main.py", "audit.py", "index.html", 
        "static/script.js", "static/style.css", 
        "app/bot/main_bot.py"
    ]
    
    for file_path in files_to_save:
        if os.path.exists(file_path):
            dest = os.path.join(backup_path, file_path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(file_path, dest)
            
    print(f"💾 Резервная копия сохранена в: {backup_path}")

def audit() -> None:
    print("🔍 Начинаю фиксацию стабильной версии...")
    
    # 1. Создаем локальный бэкап
    create_backup()
    
    # Создаем .gitignore, чтобы токены не улетали на GitHub
    try:
        with open(".gitignore", "w", encoding="utf-8") as f:
            f.write(".env\n__pycache__/\n*.db\n.vscode/\n") # Добавил .vscode в игнор
        print("✅ Файл .gitignore настроен (токены защищены).")
    except IOError as e:
        print(f"⚠️ Ошибка записи .gitignore: {e}")

    # Проверка index.html
    if os.path.exists("index.html"):
        try:
            with open("index.html", "r", encoding="utf-8") as f:
                content: str = f.read()
            
            # Явное указание типов для переменных контента
            new_content: str = re.sub(r"const API_URL = '.*?';", f"const API_URL = '{CURRENT_NGROK}';", content)
            new_content = re.sub(r"const CHAN_ID = .*?;", f"const CHAN_ID = {EXPECTED_CHAN_ID};", new_content)
            
            with open("index.html", "w", encoding="utf-8") as f:
                f.write(new_content)
            print("✅ index.html обновлен.")
        except Exception as e:
            print(f"⚠️ Ошибка при обновлении index.html: {e}")

    # Удаляем старые секреты из индекса Git
    os.system('git rm --cached .env --ignore-unmatch')
    
    print("\n🚀 Синхронизация с GitHub...")
    ver: str = get_version()
    
    # Рекомендуется использовать f-строки аккуратно с системными вызовами
    os.system('git add .')
    os.system(f'git commit -m "STABLE CHECKPOINT v{ver}: {datetime.now().strftime("%Y-%m-%d %H:%M")}"')
    os.system('git push origin main --force')
    
    print(f"\n📦 Версия приложения: v{ver}")
    print("✨ ГОТОВО! Перезапусти main.py.")

if __name__ == "__main__":
    audit()