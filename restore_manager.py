import os
import shutil

# Пути
BASE_DIR = r"E:\init_project.py"
BACKUP_ROOT = os.path.join(BASE_DIR, "TELECORE_BACKUPS")

def get_backups():
    """Находит все папки бэкапов в директории."""
    if not os.path.exists(BACKUP_ROOT):
        print(f"❌ Папка с бэкапами не найдена: {BACKUP_ROOT}")
        return []
    
    # Берем только папки
    backups = [d for d in os.listdir(BACKUP_ROOT) if os.path.isdir(os.path.join(BACKUP_ROOT, d))]
    return sorted(backups, reverse=True) # Самые свежие сверху

def restore_backup(backup_name):
    """Копирует содержимое выбранного бэкапа в корень проекта."""
    source = os.path.join(BACKUP_ROOT, backup_name)
    print(f"\n🔄 Начинаю восстановление из: {backup_name}...")

    # Список того, что обычно нужно копировать (папки и файлы)
    items_to_copy = os.listdir(source)

    for item in items_to_copy:
        src_path = os.path.join(source, item)
        dest_path = os.path.join(BASE_DIR, item)

        try:
            # Удаляем старое, если оно есть, чтобы не было конфликтов
            if os.path.isdir(src_path):
                if os.path.exists(dest_path):
                    shutil.rmtree(dest_path)
                shutil.copytree(src_path, dest_path)
            else:
                shutil.copy2(src_path, dest_path)
            print(f"  ✅ Восстановлено: {item}")
        except Exception as e:
            print(f"  ❌ Ошибка при копировании {item}: {e}")

    print(f"\n✨ ГОТОВО! Проект откатан к версии: {backup_name}")

def main():
    print("=== TELECORE BACKUP MANAGER ===")
    backups = get_backups()

    if not backups:
        print("Бэкапов не найдено. Проверь папку TELECORE_BACKUPS.")
        return

    print("\nДоступные версии для отката:")
    for i, b in enumerate(backups, 1):
        print(f"{i}. {b}")

    try:
        choice = int(input("\nВведите номер версии для отката (или 0 для отмены): "))
        if choice == 0:
            print("Отмена.")
            return
        
        selected_backup = backups[choice - 1]
        confirm = input(f"⚠️ ВНИМАНИЕ: Это заменит файлы в корне на {selected_backup}. Уверен? (y/n): ")
        
        if confirm.lower() == 'y':
            restore_backup(selected_backup)
        else:
            print("Отмена операции.")

    except (ValueError, IndexError):
        print("❌ Неверный выбор. Введите число из списка.")

if __name__ == "__main__":
    main()