import os
import shutil

DATA_DIR = "data"

def clean_previous_lab():
    if not os.path.exists(DATA_DIR):
        print(f"Папка {DATA_DIR} не найдена. Нечего чистить.")
        return

    print(f"Внимание! Вы собираетесь удалить все файлы из папки {DATA_DIR}.")
    confirm = input("Вы уверены? (y/n): ")
    
    if confirm.lower() == 'y':
        # Проходимся по содержимому data и удаляем всё, кроме папки screenshots
        for item in os.listdir(DATA_DIR):
            item_path = os.path.join(DATA_DIR, item)
            if item == "screenshots":
                # Очищаем папку со скриншотами, но не удаляем её саму
                for file in os.listdir(item_path):
                    os.remove(os.path.join(item_path, file))
                print(f"Очищена папка: {item_path}")
            elif os.path.isfile(item_path):
                os.remove(item_path)
                print(f"Удален файл: {item_path}")
        print("Готово! Можно приступать к новой лабе.")
    else:
        print("Очистка отменена.")

if __name__ == "__main__":
    clean_previous_lab()