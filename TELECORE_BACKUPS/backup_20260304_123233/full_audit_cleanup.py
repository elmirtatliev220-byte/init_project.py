import os
import shutil
import ast
import sys
import logging
from datetime import datetime, timezone

# --- НАСТРОЙКИ СТРУКТУРЫ (2026) ---
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

WHITELIST_FILES = [
    "main.py", "main_bot.py", "database.py", "models.py", 
    "schemas.py", "config.py", "protection.py", "index.html", 
    "script.js", "style.css", ".env", ".gitignore", "fast_backup.py",
    "audit.py", "version_manager.py", "full_audit_cleanup.py"
]

IGNORE_LIST = [
    "backups", "old_backups_archive", ".git", ".vscode", 
    "__pycache__", "venv", ".env", "ngrok.exe", "telecore.db"
]

PROTECTED_EXT = [".db", ".gitignore", ".env", ".pyc", ".log"]
CORE_DIRS = ["app", "static", "core", "bot", "uploads"]

# --- МОДУЛИ ГЛУБОКОЙ ПРОВЕРКИ ---

def check_syntax_errors(file_path):
    """Проверяет Python файл на наличие синтаксических ошибок."""
    if not file_path.endswith(".py"): return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            ast.parse(f.read())
        return "OK"
    except SyntaxError as e:
        return f"КРИТИЧЕСКАЯ ОШИБКА: {e.msg} (строка {e.lineno})"
    except Exception as e:
        return f"Ошибка чтения: {e}"

def analyze_logs():
    """Анализирует лог-файлы на наличие частых ошибок 502 или Bad Request."""
    print("\n📝 АНАЛИЗ СОСТОЯНИЯ ЛОГОВ:")
    found_issues = False
    log_files = [f for f in os.listdir(ROOT_DIR) if f.endswith(".log")]
    
    if not log_files:
        print("  ✅ Активных лог-файлов не найдено (чистый запуск).")
        return

    for log in log_files:
        try:
            with open(os.path.join(ROOT_DIR, log), "r", encoding="utf-8") as f:
                content = f.read()
                if "Bad Request: message can't be forwarded" in content:
                    print(f"  ⚠️ [LOG ERROR] В '{log}' найдены ошибки пересылки Telegram. Решение: проверьте права бота.")
                    found_issues = True
                if "502 Bad Gateway" in content or "TypeError: can't compare offset-naive" in content:
                    print(f"  🔥 [CRITICAL] В '{log}' найдены краши сервера (502/Timezone). Решение: исправьте datetime.now() на UTC.")
                    found_issues = True
        except: pass
    
    if not found_issues:
        print("  ✅ В логах критических аномалий не обнаружено.")

def check_timezone_safety(file_path):
    """Ищет опасное использование datetime без UTC."""
    issues = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for i, line in enumerate(lines):
                if "datetime.now()" in line and "timezone.utc" not in line:
                    issues.append(i + 1)
        return issues
    except: return []

# --- ФУНКЦИИ ВИЗУАЛИЗАЦИИ ---

def get_file_info(file_path):
    size = os.path.getsize(file_path) / 1024
    return f"{size:.1f} KB"

def show_project_tree(startpath):
    print("\n🌳 КАРТА ПРОЕКТА (Структура 2026):")
    for root, dirs, files in os.walk(startpath):
        dirs[:] = [d for d in dirs if d not in IGNORE_LIST]
        level = root.replace(startpath, '').count(os.sep)
        indent = ' ' * 4 * level
        basename = os.path.basename(root) or "ROOT"
        
        ui_tag = " 🎨 [FRONTEND]" if basename == "static" else ""
        logic_tag = " ⚙️ [LOGIC]" if basename in ["app", "core", "bot"] else ""
        
        print(f'{indent}📂 {basename}/{ui_tag}{logic_tag}')
        
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            if f not in IGNORE_LIST:
                f_path = os.path.join(root, f)
                print(f'{subindent}📄 {f} ({get_file_info(f_path)})')

# --- ОСНОВНАЯ ЛОГИКА ---

def full_smart_audit():
    start_time = datetime.now(timezone.utc)
    print(f"🚀 ЗАПУСК ГЛОБАЛЬНОГО SMART-АУДИТА V6.0 (Sentinel)")
    print("=" * 70)

    # 1. Дерево и структура
    show_project_tree(ROOT_DIR)
    print("-" * 70)

    # 2. Анализ зависимостей
    used_imports = set()
    print("🔍 [LOG] СКАНИРОВАНИЕ ИМПОРТОВ И СИНТАКСИСА...")
    for root, dirs, files in os.walk(ROOT_DIR):
        if any(ignore in root for ignore in IGNORE_LIST): continue
        for file in files:
            if file.endswith(".py"):
                f_path = os.path.join(root, file)
                
                # Проверка синтаксиса
                syntax = check_syntax_errors(f_path)
                if syntax != "OK" and syntax is not None:
                    print(f"   ❌ {file}: {syntax}")
                
                # Поиск Timezone-рисков
                tz_issues = check_timezone_safety(f_path)
                if tz_issues:
                    print(f"   ⚠️ {file}: Опасный datetime в строках {tz_issues} (нужен UTC!)")

                try:
                    with open(f_path, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read())
                        for node in ast.walk(tree):
                            if isinstance(node, (ast.Import, ast.ImportFrom)):
                                if isinstance(node, ast.Import):
                                    for alias in node.names: used_modules.add(alias.name.split('.')[0])
                                elif node.module:
                                    used_imports.add(node.module.split('.')[0])
                    print(f"   ∟ OK: {file}")
                except: continue

    # 3. Анализ Логов
    analyze_logs()
    print("-" * 70)

    # 4. Проверка на лишние файлы
    print("\n🧹 ПРОВЕРКА БЕЗОПАСНОСТИ ОБЪЕКТОВ...")
    extra_files = []
    for root, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs if d not in IGNORE_LIST]
        for file in files:
            file_path = os.path.join(root, file)
            if file in IGNORE_LIST: continue
            
            is_safe = False
            reason = ""

            if file in WHITELIST_FILES:
                is_safe = True; reason = "Белый список"
            elif any(file.endswith(ext) for ext in PROTECTED_EXT):
                is_safe = True; reason = "Системный тип"
            elif file.endswith(".py") and file.replace(".py", "") in used_imports:
                is_safe = True; reason = "Активный импорт"

            if is_safe:
                # Печатаем только для важных файлов, чтобы не спамить
                if file.endswith(".py") or root == ROOT_DIR:
                    print(f"  🛡️ [SAFE] {os.path.relpath(file_path, ROOT_DIR)} ({reason})")
                continue

            # Проверка дубликатов
            if root == ROOT_DIR and any(os.path.exists(os.path.join(ROOT_DIR, d, file)) for d in CORE_DIRS):
                extra_files.append(file_path)
            elif root == ROOT_DIR and file not in WHITELIST_FILES:
                extra_files.append(file_path)
    
    if extra_files:
        print(f"\n⚠️ ОБНАРУЖЕНЫ ЛИШНИЕ ОБЪЕКТЫ: {len(extra_files)}")
        for ef in extra_files: print(f"     🗑️ [DELETE] -> {os.path.relpath(ef, ROOT_DIR)}")
        confirm = input("\n❓ Удалить эти объекты? (y/n): ").lower()
        if confirm == 'y':
            for ef in extra_files:
                try: os.remove(ef); print(f"  [OK] Удалено: {os.path.basename(ef)}")
                except: pass
    else:
        print("\n✅ Проект идеально чист.")

    print("=" * 70)
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()
    print(f"✨ АУДИТ ЗАВЕРШЕН ЗА {duration:.2f} сек.")
    print(f"🚀 Команда запуска: uvicorn main:app --port 8080")

if __name__ == "__main__":
    full_smart_audit()