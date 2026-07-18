import json
import requests
import subprocess
import threading
import datetime

# Глобальный флаг для прерывания генерации
abort_flag = threading.Event()

def abort_generation():
    abort_flag.set()

# --- ИНСТРУМЕНТЫ (только create_note) ---
def create_note(text: str):
    """Открывает блокнот и вставляет текст (требуется root или настроенный intent)"""
    print(f"[🛠 TOOL] Вызов create_note с текстом: {text}")
    try:
        cmd = [
            "su", "-c",
            f'am start -a android.intent.action.SEND --es android.intent.extra.TEXT "{text}" -t "text/plain"'
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        subprocess.run(["termux-tts-speak", "Заметка успешно создана, хозяйка!"])
        return "Заметка успешно создана"
    except Exception as e:
        print(f"[⚠ TOOL] Ошибка create_note: {e}")
        subprocess.run(["termux-tts-speak", "Не удалось создать заметку"])
        return f"Ошибка: {e}"

AVAILABLE_TOOLS = {
    "create_note": create_note
}

# --- ОПИСАНИЕ ТУЛОВ ---
OLLAMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_note",
            "description": "Используй эту функцию, только если пользователь явно просит сделать заметку, записать что-то или открыть блокнот. Не вызывай для других целей.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Текст заметки, который нужно записать."
                    }
                },
                "required": ["text"]
            }
        }
    }
]

OLLAMA_CHAT_URL = "http://127.0.0.1:11434/api/chat"
MODEL_NAME = "ministral-3:3b"

# --- ДИНАМИЧЕСКИЙ СИСТЕМНЫЙ ПРОМПТ С ДАТОЙ/ВРЕМЕНЕМ ---
def get_system_prompt():
    now = datetime.datetime.now()
    date_str = now.strftime("%d.%m.%Y")
    time_str = now.strftime("%H:%M")
    weekday = now.strftime("%A")
    days_ru = {
        "Monday": "понедельник",
        "Tuesday": "вторник",
        "Wednesday": "среда",
        "Thursday": "четверг",
        "Friday": "пятница",
        "Saturday": "суббота",
        "Sunday": "воскресенье"
    }
    weekday_ru = days_ru.get(weekday, weekday)

    return f"""Ты персональный голосовой ассистент и друг на русском языке. Сейчас {date_str}, {weekday_ru}, время {time_str}. Ты работаешь на смартфоне Nothing Phone.
- Отвечай коротко и по делу, с душой.
- Не используй разметку (звёздочки, подчёркивания).
- Если пользователь спрашивает про время или дату – опирайся на эти актуальные данные.
- Если пользователь просит создать заметку, записать что-то, открой блокнот, используя функцию create_note.
- В остальных случаях просто дай ответ текстом, не вызывая никаких других функций.
- Будь жизнерадостным, но не переигрывай. Если не знаешь ответа, честно скажи об этом, но предложи помощь.
- Не придумывай функции, кроме create_note.
- Если тебя спрашивают «кто ты», «расскажи о себе», ответь в том же тёплом стиле."""

def speak_sentence(text, handle=None):
    """Отправляет текст на озвучку через переданную функцию handle (из engine)"""
    if handle:
        handle(text)

def stream_ollama_and_speak(messages, include_tools=True, speak_handle=None):
    global abort_flag
    abort_flag.clear()

    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": True
    }
    if include_tools:
        payload["tools"] = OLLAMA_TOOLS

    response = requests.post(OLLAMA_CHAT_URL, json=payload, stream=True)
    response.raise_for_status()

    sentence_buffer = ""
    full_text = ""
    tool_calls = []

    for line in response.iter_lines():
        if abort_flag.is_set():
            print("Генерация прервана пользователем")
            break
        if not line:
            continue
        chunk = json.loads(line.decode('utf-8'))
        message = chunk.get("message", {})

        if "tool_calls" in message:
            for tool in message["tool_calls"]:
                func_name = tool["function"]["name"]
                if func_name in AVAILABLE_TOOLS:
                    tool_calls.append(tool)
                else:
                    print(f"[⚠] Модель попыталась вызвать неизвестную функцию: {func_name}, игнорирую")

        content = message.get("content", "")
        if content:
            print(content, end="", flush=True)
            full_text += content
            sentence_buffer += content

            if any(c in content for c in ".!?\n") or len(sentence_buffer) > 80:
                speak_sentence(sentence_buffer, handle=speak_handle)
                sentence_buffer = ""

    print()
    if not abort_flag.is_set() and sentence_buffer.strip():
        speak_sentence(sentence_buffer, handle=speak_handle)

    return full_text, tool_calls

def execute_tools(tool_calls):
    """Выполняет список вызовов инструментов и возвращает список словарей результатов"""
    results = []
    for call in tool_calls:
        func_name = call["function"]["name"]
        func_args = call["function"]["arguments"]
        func = AVAILABLE_TOOLS.get(func_name)
        if func:
            result = func(**func_args)
        else:
            result = f"Инструмент {func_name} не найден"
        results.append({
            "role": "tool",
            "tool_call_id": call.get("id", "0"),
            "content": result
        })
    return results

def process_prompt(prompt_text: str, speak_handle=None):
    if not prompt_text:
        return
    print(" Отправка текста в модель...")
    # Вставляем динамический системный промпт с датой/временем
    messages = [
        {"role": "system", "content": get_system_prompt()},
        {"role": "user", "content": prompt_text}
    ]

    final_text, tool_calls = stream_ollama_and_speak(messages, include_tools=True, speak_handle=speak_handle)

    # Фильтрация: оставляем только create_note
    real_tools = [t for t in tool_calls if t["function"]["name"] in AVAILABLE_TOOLS]

    if not final_text and not real_tools:
        # Модель ничего не ответила и не вызвала допустимых инструментов — пробуем без инструментов
        print("[⚠] Модель не дала ответа, пробую ещё раз без инструментов...")
        final_text, _ = stream_ollama_and_speak(messages, include_tools=False, speak_handle=speak_handle)
        return

    if real_tools and not abort_flag.is_set():
        print("Выполняю инструменты...")
        tool_results = execute_tools(real_tools)
        messages.append({
            "role": "assistant",
            "content": final_text if final_text else None,
            "tool_calls": real_tools
        })
        messages.extend(tool_results)
        print("Финальный ответ...")
        stream_ollama_and_speak(messages, include_tools=False, speak_handle=speak_handle)
