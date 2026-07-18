from flask import Flask, jsonify
import signal
import sys
import time
import subprocess
import requests
import threading
import re
import queue

import encoding
import inputOllama

app = Flask(__name__)

# Состояния
IDLE = "idle"
RECORDING = "recording"
PROCESSING = "processing"

state = IDLE
recording_start_time = 0
processing_thread = None
ollama_process = None
generation_counter = 0          # увеличивается при каждом новом запросе на обработку
generation_counter_lock = threading.Lock()

SHORT_THRESHOLD = 0.5
MAX_RECORD_TIME = 120
TTS_SPEED = "1.2"              # скорость озвучки (можно менять)

speak_queue = queue.Queue()

# ==================== Очередь озвучки ====================
def tts_worker():
    while True:
        text = speak_queue.get()
        if text is None:
            break
        clean = clean_for_speech(text)
        if clean:
            subprocess.run(["termux-tts-speak", "-s", TTS_SPEED, clean])
        speak_queue.task_done()

tts_thread = threading.Thread(target=tts_worker, daemon=True)
tts_thread.start()

def clean_for_speech(text):
    text = re.sub(r'[^\w\s,.!?\-:;()]', '', text, flags=re.UNICODE)
    text = text.replace('**', '').replace('*', '').replace('__', '').replace('_', '')
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def speak_sentence(text):
    if text.strip():
        speak_queue.put(text.strip())

# ==================== Ollama ====================
def start_ollama():
    global ollama_process
    try:
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=1)
        if r.status_code == 200:
            print("Ollama уже работает")
            return
    except:
        pass
    print("Запускаю Ollama...")
    ollama_process = subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    for _ in range(15):
        try:
            r = requests.get("http://127.0.0.1:11434/api/tags", timeout=1)
            if r.status_code == 200:
                print("Ollama готова")
                return
        except:
            time.sleep(0.5)
    print("Ollama не запустилась")

def stop_ollama():
    global ollama_process
    if ollama_process:
        print("Выключаю Ollama...")
        ollama_process.terminate()
        try:
            ollama_process.wait(timeout=5)
        except:
            ollama_process.kill()
        ollama_process = None

# ==================== Фоновая обработка ====================
def process_in_background(user_text, gen_id):
    global state
    try:
        inputOllama.process_prompt(user_text, speak_handle=speak_sentence)
    except Exception as e:
        print(f"Ошибка обработки: {e}")
    finally:
        with generation_counter_lock:
            if generation_counter == gen_id:
                state = IDLE
                print("Готов к новым командам")
            else:
                print("Фоновый поток (устаревший) завершён, состояние не трогаю")

# ==================== Главный toggle ====================
@app.route("/toggle", methods=["POST"])
def toggle():
    global state, recording_start_time, generation_counter

    # --- Если сейчас запись (RECORDING) ---
    if state == RECORDING:
        duration = time.time() - recording_start_time
        user_text = encoding.stop_recording_and_get_text()
        print(f"Запись окончена ({duration:.2f} сек)")

        if duration < SHORT_THRESHOLD:
            graceful_shutdown(None, None)
            return jsonify({"status": "shutdown"}), 200
        if duration > MAX_RECORD_TIME:
            print("Слишком длинная запись, сброс")
            state = IDLE
            return jsonify({"status": "reset"}), 200

        # Запускаем обработку с новым идентификатором
        with generation_counter_lock:
            generation_counter += 1
            gen_id = generation_counter
        state = PROCESSING
        threading.Thread(target=process_in_background, args=(user_text, gen_id)).start()
        return jsonify({"status": "processing"}), 200

    # --- Если идёт генерация (PROCESSING) – ПРЕРЫВАЕМ и СРАЗУ НАЧИНАЕМ ЗАПИСЬ ---
    if state == PROCESSING:
        print("Прерываю речь, начинаю слушать...")
        inputOllama.abort_generation()
        # Очищаем очередь озвучки
        while not speak_queue.empty():
            try:
                speak_queue.get_nowait()
                speak_queue.task_done()
            except queue.Empty:
                break
        # Увеличиваем счётчик, чтобы старый поток не сбросил состояние
        with generation_counter_lock:
            generation_counter += 1
        # Сразу включаем микрофон
        encoding.start_recording()
        recording_start_time = time.time()
        state = RECORDING
        print("Говори...")
        return jsonify({"status": "interrupted_and_recording"}), 200

    # --- Если ничего не делаем (IDLE) – начинаем запись ---
    # (state == IDLE)
    encoding.start_recording()
    recording_start_time = time.time()
    state = RECORDING
    print("ЗАПИСЬ НАЧАТА — говори...")
    return jsonify({"status": "recording_started"}), 200

# ==================== Экстренное прерывание (не обязательно, но оставим) ====================
@app.route("/abort", methods=["POST"])
def abort():
    global state
    if state == PROCESSING:
        inputOllama.abort_generation()
        while not speak_queue.empty():
            try:
                speak_queue.get_nowait()
                speak_queue.task_done()
            except queue.Empty:
                break
        # Старый поток сам завершится и не изменит состояние (счётчик увеличен не будет,
        # но он проверит generation_counter и не сбросит state в IDLE)
        print(" Прервано")
    return jsonify({"status": "aborted"}), 200

# ==================== Завершение работы ====================
def graceful_shutdown(sig, frame):
    print("\nЗавершение...")
    if state == RECORDING:
        encoding.stop_recording_and_get_text()
    if state == PROCESSING:
        inputOllama.abort_generation()
    while not speak_queue.empty():
        try:
            speak_queue.get_nowait()
            speak_queue.task_done()
        except queue.Empty:
            break
    speak_queue.put(None)   # сигнал остановки tts_worker
    stop_ollama()
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)

# ==================== Старт ====================
if __name__ == "__main__":
    start_ollama()
    print(" готов (чёткое прерывание: речь  сразу запись)")
    app.run(host="127.0.0.1", port=9999, debug=False, threaded=True)
