import os
import json
import base64
import re
from openai import OpenAI
from jinja2 import Environment, FileSystemLoader
from pypdf import PdfReader
import httpx
from httpx_socks import SyncProxyTransport
from config import API_KEY, BASE_URL, MODEL_NAME, PROXY_URL, AUTHOR_NAME, AUTHOR_GROUP, AUTHOR_EMAIL, AUTHOR_GENDER

# Создаем транспорт, который точно умеет SOCKS5 с паролем
transport = SyncProxyTransport.from_url(PROXY_URL)

# Передаем этот транспорт в httpx.Client
http_client = httpx.Client(transport=transport)

# Передаем клиент в OpenAI
client = OpenAI(api_key=API_KEY, base_url=BASE_URL, http_client=http_client)

# ==========================================
# 1. КОНВЕРТАЦИЯ PDF В ТЕКСТ
# ==========================================
def pdf_to_text(pdf_path, txt_path):
    print(f"Читаем PDF: {pdf_path}...")
    try:
        reader = PdfReader(pdf_path)
        full_text = ""
        for page in reader.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
                
        # Чистим текст от лишних переносов строк и пробелов (чтобы не тратить токены)
        clean_text = re.sub(r'[ \t]+', ' ', full_text)
        clean_text = re.sub(r'\n+', '\n', clean_text)
        
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(clean_text)
        print(f"Текст успешно сохранен в {txt_path}")
    except Exception as e:
        print(f"Ошибка при чтении PDF: {e}")
        exit(1)

# ==========================================
# 2. АНАЛИЗ СКРИНШОТОВ
# ==========================================
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

import concurrent.futures

import time
import random

def make_request_with_retry(client, model, messages, max_retries=10):
    """Делает запрос с автоматическими повторными попытками при ошибке 429"""
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={ "type": "json_object" }
            )
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                # Пытаемся вытащить время ожидания из текста ошибки
                match = re.search(r'Please retry in ([\d.]+)s', error_str)
                if match:
                    wait_time = float(match.group(1)) + 1.0 # +1 секунда про запаса
                else:
                    wait_time = random.randint(10, 20) # Если не нашли, ждем рандомно
                
                print(f"  [Лимит 429] Ждем {wait_time:.1f} сек. (Попытка {attempt+1}/{max_retries})")
                time.sleep(wait_time)
            else:
                # Если ошибка не связана с лимитами, пробрасываем её дальше
                raise e
    
    # Если все попытки исчерпаны
    raise Exception("Превышено максимальное количество попыток запроса к API.")


def process_single_image(img_info):
    """Вспомогательная функция для обработки одной картинки"""
    idx, img_name, img_path = img_info
    print(f"  Начата обработка {img_name}...")
    
    try:
        with open(img_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            
        # Определяем MIME-тип на основе расширения
        ext = os.path.splitext(img_name)[1].lower()
        mime_type = "image/png" if ext == ".png" else "image/jpeg"

        prompt = """
        Ты — студент, который описывает свои действия для отчета по Linux.
        Опиши ОЧЕНЬ КРАТКО (в 1-2 предложениях), что происходит на этом скриншоте.
        Начни с глагола действия (например: "Получаем полномочия...", "Открываем файл...", "Проверяем статус...").
        Укажи конкретную команду, если она видна.
        Если на скриншоте пустой редактор vim, так и напиши: "Открыт пустой редактор vim".
        
        Верни ответ строго в формате JSON: 
        {"text": "твое краткое описание действия", "caption": "короткая подпись для картинки"}
        """

        
        response = make_request_with_retry(
            client=client,
            model=MODEL_NAME,
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                ]}
            ]
        )
        
        data = json.loads(response.choices[0].message.content)
        print(f"  Успешно обработана {img_name}")
        return {
            "id": f"{idx:03d}",
            "image": img_name,
            "text": data.get("text", "Нет описания"),
            "caption": data.get("caption", "Скриншот")
        }
    except Exception as e:
        print(f"  Ошибка с {img_name}: {e}")
        return {
            "id": f"{idx:03d}",
            "image": img_name,
            "text": "Описание не удалось получить.",
            "caption": "Скриншот"
        }

def analyze_images(folder_path):
    steps = []
    images = sorted([f for f in os.listdir(folder_path) if f.endswith(('.png', '.jpg', '.jpeg'))])
    print(f"Найдено {len(images)} картинок. Начинаем параллельный анализ...")
    
    # Подготавливаем данные для параллельного запуска
    tasks = [(idx, img_name, os.path.join(folder_path, img_name)) for idx, img_name in enumerate(images, start=1)]
    
    # Запускаем максимум 5 запросов одновременно (чтобы не словить ошибку 429 Too Many Requests)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # executor.map сохраняет порядок результатов в соответствии с порядком задач
        results = list(executor.map(process_single_image, tasks))
        
    return results

# ==========================================
# 3. ГЕНЕРАЦИЯ ТЕКСТА ОТЧЕТА
# ==========================================
def generate_report_data(steps, task_path,gender):
    print("Генерация текста отчета...")
    steps_text = "\n".join([f"Шаг {s['id']}: {s['text']}" for s in steps])
    
    try:
        with open(task_path, "r", encoding="utf-8") as f:
            task_text = f.read()
    except FileNotFoundError:
        print("Ошибка: файл task.txt не найден!")
        return None

    task_text = task_text[:25000] 

    prompt = f"""
    Ты — студент. Твой пол: {gender}. Пиши текст отчета от первого лица (Я сделал/сделала, Я проверил/проверила)., пишущий отчет по лабораторной работе. 
    Вот полный текст методички с заданием, теорией и контрольными вопросами:
    ---
    {task_text}
    ---
    
    А вот шаги, которые студент выполнил (судя по скриншотам):
    {steps_text}
    
    Напиши текст для отчета на основе этих данных.
    Верни ответ СТРОГО в формате JSON со следующей структурой (без Markdown-разметки внутри значений, только чистый текст):
    {{
        "lab_num": "8",
        "lab_title": "Планировщики событий",
        "keywords": "cron, at, планировщик",
        "aktualnost": "текст...",
        "goal": "текст...",
        "tasks": ["задача 1", "задача 2", "задача 3"],
        "object_subject": "текст...",
        "methods": "текст...",
        "theory": "текст...",
        "materials_and_methods": "текст...",
        "questions": [
            {{"question": "текст вопроса 1", "answer": "текст ответа 1"}},
            {{"question": "текст вопроса 2", "answer": "текст ответа 2"}}
        ],
        "results": "текст...",
        "discussion": "текст...",
        "conclusion": "текст..."
    }}
    
    Важно: 
    1. Поле "theory" напиши на основе теоретической части из методички.
    2. В поле "questions" обязательно ответь на ВСЕ вопросы, которые найдешь в тексте методички.
    """
    
    try:
        response = make_request_with_retry(
            client=client,
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}]
        )
        data = json.loads(response.choices[0].message.content)
        
        # --- ЖЕСТКО ПРОПИСЫВАЕМ ТО, ЧТО НЕ ДОЛЖНА ГЕНЕРИРОВАТЬ НЕЙРОСЕТЬ ---
        
        # 1. Ссылка на лабу (меняй руками при необходимости)
        data["lab_url"] = "https://esystem.rudn.ru/pluginfile.php/3318698/mod_resource/content/4/009-scheduling.pdf" 
        
        return data
    except Exception as e:
        print(f"Ошибка при генерации текста отчета: {e}")
        return None
#
#   PRESEnTATION
#
def generate_presentation(report_data, steps, gender):
    print("Генерация презентации...")
    
    prompt = f"""
    Ты — студент. Твой пол: {gender}.
    На основе этого отчета сделай структуру для презентации.
    Верни ответ СТРОГО в формате JSON. В ключе "steps_short" верни список объектов, каждый с ключом "short_text".
    {{
        "steps_short": [
            {{"short_text": "короткий текст для слайда 1 (до 10 слов)"}},
            {{"short_text": "короткий текст для слайда 2"}}
        ]
    }}
    Отчет: {json.dumps(report_data, ensure_ascii=False)}
    """
    
    response = make_request_with_retry(
        client=client,
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}]
    )
    pres_data = json.loads(response.choices[0].message.content)
    
    # Объединяем данные
    final_data = report_data
    final_data.update(pres_data)

    final_data['author_name'] = AUTHOR_NAME
    final_data['author_group'] = AUTHOR_GROUP
    final_data['author_email'] = AUTHOR_EMAIL

    # --- УМНАЯ ОБРАБОТКА СПИСКА ---
    short_list = pres_data.get("steps_short", [])
    
    for i, step in enumerate(steps):
        if i < len(short_list):
            item = short_list[i]
            # Если нейросеть вернула словарь (как просили)
            if isinstance(item, dict):
                step["short_text"] = item.get("short_text", item.get("text", str(item)))
            # Если нейросеть вернула просто строку (поленилась)
            elif isinstance(item, str):
                step["short_text"] = item
            else:
                step["short_text"] = step["text"]
        else:
            step["short_text"] = step["text"]
            
    final_data['steps'] = steps
    
    # Рендерим презентацию
    env = Environment(loader=FileSystemLoader('.'))
    template = env.get_template('template_pres.qmd')
    
    # Рендерим один раз в переменную
    output = template.render(**final_data)
    
    # Записываем в файл
    output_path = os.path.join('data', 'presentation.qmd')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output)
        
    print(f"Готово! Файл {output_path} создан.")
# ==========================================
# 4. СБОРКА QMD ФАЙЛА
# ==========================================
def build_qmd(report_data, steps, output_path):
    if not report_data:
        print("Данные отчета пусты, сборка отменена.")
        return

    print("Сборка .qmd файла...")
    env = Environment(loader=FileSystemLoader('.'))
    try:
        template = env.get_template('template_report.qmd')
    except Exception as e:
        print(f"Ошибка загрузки шаблона: {e}")
        return

    # Объединяем данные текста и шаги со скриншотами
    final_data = report_data
    final_data['steps'] = steps

    final_data['author_name'] = AUTHOR_NAME
    final_data['author_group'] = AUTHOR_GROUP
    final_data['author_email'] = AUTHOR_EMAIL

    # Рендерим
    output = template.render(**final_data)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(output) # <--- ИСПОЛЬЗУЕМ ПЕРЕМЕННУЮ
    print("Готово! Файл report.qmd создан.")

# ==========================================
# ГЛАВНЫЙ ЗАПУСК
# ==========================================
if __name__ == "__main__":
    # Все пути теперь ведут в папку data
    DATA_DIR = "data"
    PDF_FILE = os.path.join(DATA_DIR, "lab.pdf")
    TXT_FILE = os.path.join(DATA_DIR, "task.txt")
    IMAGES_FOLDER = os.path.join(DATA_DIR, "screenshots")
    
    # Если папки data нет, создаем её
    os.makedirs(IMAGES_FOLDER, exist_ok=True)

    if not os.path.exists(TXT_FILE):
        if os.path.exists(PDF_FILE):
            pdf_to_text(PDF_FILE, TXT_FILE)
        else:
            print(f"Ошибка: Не найден файл {PDF_FILE}.")
            exit(1)
    else:
        print("Файл task.txt уже существует, используем его.")

    if not os.path.exists(IMAGES_FOLDER):
        print(f"Ошибка: не найдена папка {IMAGES_FOLDER}")
        exit(1)

    parsed_steps = analyze_images(IMAGES_FOLDER)
    if parsed_steps:
        full_report_data = generate_report_data(parsed_steps, TXT_FILE, AUTHOR_GENDER)
        # Сохраняем отчет тоже в data
        build_qmd(full_report_data, parsed_steps, output_path=os.path.join(DATA_DIR, "report.qmd"))
        generate_presentation(full_report_data, parsed_steps, AUTHOR_GENDER)
