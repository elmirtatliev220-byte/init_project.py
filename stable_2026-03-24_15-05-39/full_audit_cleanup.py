import os
import shutil
import ast
from datetime import datetime

# --- НАСТРОЙКИ СТРУКТУРЫ (2026) ---
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_TO_RESTORE = os.path.join(ROOT_DIR, "backups", "stable_2026-02-20_14-37-44")

# Файлы, которые считаются эталонными
MAIN_FILES = [
    "main.py", "main_bot.py", "config.py", "audit.py", 
    "index.html", "script.js", "style.css", "telecore.db", "models.py"
]

# Полный список игнора для чистоты аудита
IGNORE_LIST = [
    "backups", "old_backups_archive", ".git", ".vscode", 
    "__pycache__", "venv", ".env", "full_audit_cleanup.py", 
    "ngrok.exe", "telecore.db"
]

PROTECTED_EXT = [".db", ".gitignore", ".env"]

# --- ФУНКЦИИ АНАЛИЗА ---

def get_file_stats(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return len(f.readlines())
    except: return 0

def analyze_code_structure(file_path):
    """Вытаскивает названия функций и классов для ИИ."""
    functions = []
    classes = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    functions.append(node.name)
                elif isinstance(node, ast.ClassDef):
                    classes.append(node.name)
        return functions, classes
    except: return [], []

def show_project_tree(startpath):
    """Рисует дерево и помечает зоны ответственности."""
    print("\n🌳 КАРТА ПРОЕКТА (Для понимания структуры):")
    for root, dirs, files in os.walk(startpath):
        dirs[:] = [d for d in dirs if d not in IGNORE_LIST]
        level = root.replace(startpath, '').count(os.sep)
        indent = ' ' * 4 * level
        basename = os.path.basename(root) or "ROOT"
        
        # Пометка для интерфейса
        ui_tag = " 🎨 [FRONTEND INTERFACE]" if basename == "static" else ""
        logic_tag = " ⚙️ [APP LOGIC]" if basename in ["app", "core", "bot"] else ""
        
        print(f'{indent}📂 {basename}/{ui_tag}{logic_tag}')
        
        subindent = ' ' * 4 * (level + 1)
        for f in files:
            if f not in IGNORE_LIST:
                print(f'{subindent}📄 {f}')

# --- ОСНОВНАЯ ЛОГИКА ---

def full_audit():
    if not os.path.exists(ROOT_DIR): return

    print(f"🚀 ЗАПУСК ГЛОБАЛЬНОГО АУДИТА...")
    print("=" * 60)

    # 1. Показываем дерево (ИИ увидит все папки)
    show_project_tree(ROOT_DIR)
    print("-" * 60)

    # 2. Глубокий анализ функционала (чтобы ИИ не писал дубли функций)
    print("🧠 АНАЛИЗ РЕАЛИЗОВАННЫХ ФУНКЦИЙ:")
    for root, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs if d not in IGNORE_LIST]
        for file in files:
            if file.endswith(".py") and file != "full_audit_cleanup.py":
                f_path = os.path.join(root, file)
                rel_path = os.path.relpath(f_path, ROOT_DIR)
                lines = get_file_stats(f_path)
                funcs, classes = analyze_code_structure(f_path)
                
                if funcs or classes:
                    print(f"  • {rel_path} [{lines} строк]")
                    if classes: print(f"    └─ Классы: {', '.join(classes)}")
                    if funcs: print(f"    └─ Функции: {', '.join(funcs)}")
    print("-" * 60)

    # 3. Умная очистка мусора
    print("\n🧹 ПРОВЕРКА ЛИШНИХ ОБЪЕКТОВ...")
    extra_files = []
    for root, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs if d not in IGNORE_LIST]
        
        for file in files:
            file_path = os.path.join(root, file)
            if file in IGNORE_LIST: continue
            
            # Проверяем: если файл в корне, но его копия уже есть в static или app
            is_root_duplicate = False
            if root == ROOT_DIR and file in ["index.html", "script.js", "style.css", "main_bot.py", "config.py"]:
                for sub in ["static", "app/bot", "app/core"]:
                    if os.path.exists(os.path.join(ROOT_DIR, sub, file)):
                        is_root_duplicate = True
                        break

            # ЛОГИКА: Применяем фильтр "MAIN_FILES" только к корню.
            # Файлы внутри подпапок (app/, static/) считаем полезными и не удаляем.
            should_delete = (root == ROOT_DIR) and (file not in MAIN_FILES or is_root_duplicate)

            if should_delete:
                if any(file.endswith(ext) for ext in PROTECTED_EXT):
                    print(f"    🔒 [ЗАЩИЩЕН] -> {os.path.relpath(file_path, ROOT_DIR)}")
                    continue
                extra_files.append(file_path)
    
    if extra_files:
        print(f"  ⚠️ Найдено лишних объектов/дублей: {len(extra_files)}")
        for ef in extra_files:
            print(f"     🗑️ [ЛИШНИЙ] -> {os.path.relpath(ef, ROOT_DIR)}")
        
        confirm = input("\n❓ Удалить лишнее? (y/n): ").lower()
        if confirm == 'y':
            for ef in extra_files:
                try:
                    os.remove(ef)
                    print(f"  [OK] Удалено: {os.path.basename(ef)}")
                except: pass
    else:
        print("  ✅ Лишних файлов не найдено.")

    print("=" * 60)
    print("✨ АУДИТ ЗАВЕРШЕН. ПРОЕКТ ГОТОВ К РАБОТЕ.")
    print(f"🚀 Запуск: uvicorn main:app --port 8080")

if __name__ == "__main__":
    full_audit()