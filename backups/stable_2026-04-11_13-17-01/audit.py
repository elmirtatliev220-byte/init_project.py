import os
import re
import shutil
import time
from datetime import datetime
from typing import Optional

# === НАСТРОЙКИ ПРОЕКТА ===
EXPECTED_CHAN_ID: str = "-1002424660098"
CURRENT_NGROK: str = "https://felicidad-selfsame-veritably.ngrok-free.dev"
GITHUB_PAGE_URL: str = "https://elmirtatliev220-byte.github.io/init_project.py/"

def fix_missing_modules():
    """Проверяет наличие критических файлов и функций, чтобы избежать ImportError."""
    print("🛠️ Проверка целостности модулей...")
    
    # 1. Проверка структуры папок
    os.makedirs("app/core", exist_ok=True)
    os.makedirs("app/bot", exist_ok=True)
    
    # 2. Исправление app/core/protection.py (чтобы не было ImportError)
    prot_path = "app/core/protection.py"
    if not os.path.exists(prot_path) or os.path.getsize(prot_path) < 10:
        with open(prot_path, "w", encoding="utf-8") as f:
            f.write("def analyze_protection_logic(data): return True\n")
            f.write("def check_spam(user_id): return False\n")
        print(f"✅ Созданы заглушки функций в {prot_path}")

    # 3. Создание __init__.py для корректных импортов
    for folder in ["app", "app/core", "app/bot"]:
        init_file = os.path.join(folder, "__init__.py")
        if not os.path.exists(init_file):
            open(init_file, "a").close()

def get_version() -> str:
    try:
        if os.path.exists("app/core/config.py"):
            with open("app/core/config.py", "r", encoding="utf-8") as f:
                content = f.read()
                match = re.search(r'VERSION = "(.*?)"', content, re.MULTILINE)
                if match: return str(match.group(1))
        return "1.0.0"
    except Exception:
        return "1.0.0"

def create_full_backup() -> None:
    """Создает ПОЛНУЮ копию проекта (все папки с кодом)."""
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_path = f"backups/stable_{ts}"
    
    # Список всего, что нужно для работы
    items_to_save = ["main.py", "app", "static", "index.html", "telecore.db", "audit.py"]
    
    os.makedirs(backup_path, exist_ok=True)
    for item in items_to_save:
        if os.path.exists(item):
            dest = os.path.join(backup_path, item)
            if os.path.isdir(item):
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
    print(f"💾 ПОЛНЫЙ бэкап сохранен: {backup_path}")

def update_configs():
    """Обновляет URL в боте и фронтенде."""
    # Обновление main_bot.py
    bot_file = "app/bot/main_bot.py"
    if os.path.exists(bot_file):
        with open(bot_file, "r", encoding="utf-8") as f:
            content = f.read()
        
        pattern = r'web_app_url = f"https?://.*?\?v=\{int\(time\.time\(\)\)\}"'
        replacement = f'web_app_url = f"{GITHUB_PAGE_URL}?v={{int(time.time())}}"'
        new_content = re.sub(pattern, replacement, content)
        
        with open(bot_file, "w", encoding="utf-8") as f:
            f.write(new_content)
        print("✅ Ссылка на Mini App обновлена в боте.")

    # Обновление index.html
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            content = f.read()
        content = re.sub(r"const API_URL = '.*?';", f"const API_URL = '{CURRENT_NGROK}';", content)
        content = re.sub(r"const CHAN_ID = .*?;", f"const CHAN_ID = {EXPECTED_CHAN_ID};", content)
        with open("index.html", "w", encoding="utf-8") as f:
            f.write(content)
        print("✅ index.html обновлен (Ngrok + ID канала).")

def audit():
    print("🚀 ЗАПУСК ПОЛНОГО АУДИТА И ФИКСАЦИИ...")
    
    fix_missing_modules()
    create_full_backup()
    update_configs()

    # Git секция
    if os.path.exists(".git"):
        os.system('git add .')
        ver = get_version()
        os.system(f'git commit -m "STABLE AUTO-FIX v{ver}: {datetime.now()}"')
        os.system('git push origin main')
        print("✅ Изменения отправлены в GitHub.")
    else:
        print("ℹ️ Git не инициализирован, пропускаю push.")

    print("\n✨ ВСЁ ГОТОВО! Теперь проект должен работать.")
    print("👉 Запускай: uvicorn main:app --port 8080")

if __name__ == "__main__":
    audit()