#!/usr/bin/env python3
"""
Workshop Voice & Vision Assistant — The Crafters Hub
100% Local Edge Architecture: Gemma-4 E4B (Multimodal)
Location: D:\TheCraftersHub_DataLab\scripts\workshop_voice_assistant.py

Usage:
  python workshop_voice_assistant.py

Requirements:
  pip install pyaudio opencv-python psycopg2-binary requests pynput python-dotenv

Hardware:
  - Microphone connected to The Cabinet PC
  - Webcam connected to The Cabinet PC
  - Speaker/headphones
  - NVIDIA Quadro P1000 (Running local Ollama)
"""

import os
import sys
import json
import base64
import asyncio
import threading
import pyaudio
import cv2
import psycopg2
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from pynput import keyboard

# ─── Config ───────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent.parent / ".env")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "crafters_hub")
DB_USER = os.getenv("CRAFTER_ADMIN_USER", "crafter_admin")
DB_PASS = os.getenv("CRAFTER_ADMIN_PASSWORD", "")

# 100% Local Routing - Zero Cloud Dependency
LOCAL_MODEL_ENDPOINT = os.getenv("GEMMA_ENDPOINT", "http://localhost:11434/api/chat")
LOCAL_MODEL_NAME = "gemma4:e4b"

# Audio constants
AUDIO_IN_SAMPLE_RATE  = 16000
AUDIO_OUT_SAMPLE_RATE = 24000
AUDIO_CHANNELS        = 1
AUDIO_CHUNK           = 1024
AUDIO_FORMAT          = pyaudio.paInt16

# ─── System Prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are HAMADA, the AI assistant for The Crafters Hub workshop in Alexandria, Egypt.
You run 100% locally on The Cabinet using Gemma-4 E4B. You process native voice and vision.
You are speaking with Hosam Elshanawany (CEO) or Mostafa Fahmy (COO).

Your personality:
- Direct and concise — workshop is a busy environment.
- You understand Arabic, English, and Franco-Arabic.
- If you see an image of wood, tools, or a project, describe it and answer the user's question.
- You are data-first: answer based on the Cabinet data provided in your context.

Rules:
- Keep responses short, suitable for spoken audio.
- Never mention that you are an AI.
"""

# ─── Database Queries ─────────────────────────────────────────────────────
def get_cabinet_snapshot() -> str:
    """Pull a quick snapshot from The Cabinet for context injection."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASS, connect_timeout=3
        )
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(amount_paid), 0)::int
            FROM registrations
            WHERE registration_date >= current_date - interval '30 days'
            AND status = 'CONFIRMED'
        """)
        revenue = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM students WHERE created_at >= current_date - interval '30 days'")
        new_students = cur.fetchone()[0]
        conn.close()
        return f"Cabinet snapshot: Revenue 30d = EGP {revenue:,}, New students = {new_students}"
    except Exception as e:
        return f"Cabinet offline: {e}"

# ─── Vision Capture ─────────────────────────────────────────────────────────
def capture_frame():
    """Captures a single frame from the default webcam and returns it as base64."""
    try:
        cap = cv2.VideoCapture(0)
        ret, frame = cap.read()
        cap.release()
        if ret:
            _, buffer = cv2.imencode('.jpg', frame)
            return base64.b64encode(buffer).decode('utf-8')
    except Exception as e:
        print(f"Camera error: {e}")
    return None

# ─── Live Local Session ─────────────────────────────────────────────────────
is_recording = False
audio_frames = []

def on_press(key):
    global is_recording, audio_frames
    if key == keyboard.Key.space and not is_recording:
        print("\n🎙️  Listening... (Recording voice + capturing image)")
        is_recording = True
        audio_frames = []

def on_release(key):
    global is_recording
    if key == keyboard.Key.space:
        print("⏳ Processing locally with Gemma-4 E4B on Quadro P1000...")
        is_recording = False

async def process_interaction(pya):
    """Sends recorded audio and captured image to local Gemma-4 E4B."""
    global audio_frames
    
    # 1. Capture image exactly when spacebar is released
    b64_image = capture_frame()
    
    # 2. Package audio
    audio_data = b"".join(audio_frames)
    b64_audio = base64.b64encode(audio_data).decode('utf-8')
    
    # 3. Get DB context
    db_context = get_cabinet_snapshot()
    
    # 4. Construct payload for Ollama Multimodal (Voice + Vision)
    messages = [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{db_context}"},
        {
            "role": "user",
            "content": "Answer the voice request based on the image provided.",
            "images": [b64_image] if b64_image else [],
            "audio": [b64_audio] # Assuming Ollama Gemma-4 E4B format supports audio array
        }
    ]
    
    payload = {
        "model": LOCAL_MODEL_NAME,
        "messages": messages,
        "stream": False
    }
    
    try:
        response = requests.post(LOCAL_MODEL_ENDPOINT, json=payload, timeout=60)
        if response.status_code == 200:
            result = response.json()
            message_content = result.get('message', {}).get('content', '')
            print(f"💬 HAMADA: {message_content}")
            
            # Note: For full TTS, if Gemma-4 E4B outputs raw PCM in a specific field, 
            # we would extract and play it here. For now, it outputs text.
        else:
            print(f"❌ API Error: {response.status_code} - {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"❌ Connection Error (Ensure Ollama with Gemma-4 E4B is running!): {e}")

def main():
    pya = pyaudio.PyAudio()
    mic_stream = pya.open(
        format=AUDIO_FORMAT, channels=AUDIO_CHANNELS,
        rate=AUDIO_IN_SAMPLE_RATE, input=True,
        frames_per_buffer=AUDIO_CHUNK
    )

    print(f"\n🪵 HAMADA Workshop Assistant (100% Local - Gemma-4 E4B)")
    print(f"   Connecting to local edge: {LOCAL_MODEL_ENDPOINT}")
    print(f"\n   [PTT] Hold SPACE to speak. A photo is taken when you release SPACE.")
    print(f"   Press Ctrl+C to exit.\n")

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    try:
        while True:
            if is_recording:
                data = mic_stream.read(AUDIO_CHUNK, exception_on_overflow=False)
                audio_frames.append(data)
            else:
                if len(audio_frames) > 0:
                    asyncio.run(process_interaction(pya))
                    audio_frames = []
    except KeyboardInterrupt:
        print("\n👋 HAMADA offline.")
    finally:
        mic_stream.stop_stream()
        mic_stream.close()
        pya.terminate()

if __name__ == "__main__":
    main()
