import os
import subprocess
import time

audio_path = "/data/data/com.termux/files/usr/tmp/voice.wav"
clean_wav = "/data/data/com.termux/files/usr/tmp/voice_clean.wav"

def start_recording():
    # Останавливаем предыдущую запись (если висит)
    subprocess.run(["termux-microphone-record", "-q"])
    # Удаляем старые файлы
    for f in (audio_path, clean_wav):
        if os.path.exists(f):
            os.remove(f)
    print(" Включаю микрофон...")
    subprocess.Popen([
        "termux-microphone-record",
        "-f", audio_path,
        "-l", "0",
        "-r", "16000",
        "-b", "16",
        "-c", "1"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def stop_recording_and_get_text():
    print(" Останавливаю запись...")
    subprocess.run(["termux-microphone-record", "-q"])
    time.sleep(0.2)   # даём файлу дописаться

    if not os.path.exists(audio_path):
        print("WAV-файл не найден!")
        return ""
    wav_size = os.path.getsize(audio_path)
    print(f"Размер WAV: {wav_size} байт")
    if wav_size < 1000:
        print("Файл слишком мал – звука нет.")
        return ""

    print("Конвертация WAV...")
    conv = subprocess.run([
        "ffmpeg", "-y", "-i", audio_path,
        "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", clean_wav
    ], capture_output=True, text=True)
    if not os.path.exists(clean_wav) or os.path.getsize(clean_wav) < 100:
        print("Ошибка конвертации ffmpeg:", conv.stderr[:200])
        return ""

    print("Расшифровка...")
    whisper_bin = "/data/data/com.termux/files/home/my_app/whisper.cpp/build/bin/whisper-cli"
    model_path = "/data/data/com.termux/files/home/my_app/whisper.cpp/models/ggml-base.bin"
    result = subprocess.run([
        whisper_bin,
        "-m", model_path,
        "-f", clean_wav,
        "--no-timestamps",
        "--language", "ru"
    ], capture_output=True, text=True)
    print(":", result.stdout[:200])

    for f in (audio_path, clean_wav):
        try:
            os.remove(f)
        except:
            pass
    return result.stdout.strip()
