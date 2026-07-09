#!/usr/bin/env python3
"""
The Cabinet: Live Brain (Multi-Threaded Production Agent)
Location: D:\TheCraftersHub_DataLab\scripts\live_brain.py

This is the production-grade Agent loop. It features:
1. Asynchronous audio listening (Agent is never deaf).
2. Native LLM Tool Calling (Gemma-4 E4B decides when to use the camera).
3. Zero-latency threaded RTSP camera buffer.
"""

import os
import time
import queue
import json
import threading
import requests
import pyaudio
import numpy as np
import sounddevice as sd
import soundfile as sf
import psycopg2
from pathlib import Path
from dotenv import load_dotenv

# Load Environment Variables
load_dotenv(Path(__file__).parent.parent / ".env")

# ─── Configuration ────────────────────────────────────────────────────────────
OLLAMA_ENDPOINT = os.getenv("GEMMA_ENDPOINT", "http://localhost:11434/api/chat")
MODEL_NAME = "gemma4:e4b"

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "crafters_hub")
DB_USER = os.getenv("CRAFTER_ADMIN_USER", "crafter_admin")
DB_PASS = os.getenv("CRAFTER_ADMIN_PASSWORD", "")

CAMERA_IP = os.getenv("IMOU_CAMERA_IP", "")
CAMERA_CODE = os.getenv("IMOU_SAFETY_CODE", "")

# Audio Config
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
CHUNK = 1280

# The queue allows the audio thread to pass recorded commands to the Agent thread
audio_task_queue = queue.Queue()

# ─── Global Vision Stream ───────────────────────────────────────────────────
camera_stream = None
try:
    from imou_vision import ImouCameraStream
    if CAMERA_IP and CAMERA_CODE:
        camera_stream = ImouCameraStream(name="Workshop_Main", ip=CAMERA_IP, safety_code=CAMERA_CODE)
        camera_stream.start()
except ImportError:
    print("Warning: imou_vision.py not found.")

# ─── Tools (Callable by Gemma-4) ────────────────────────────────────────────
def get_cabinet_snapshot() -> str:
    """Tool: Database Query"""
    print("🔧 Agent called tool: get_cabinet_snapshot")
    try:
        conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(amount_paid), 0)::int FROM registrations WHERE registration_date >= current_date - interval '30 days' AND status = 'CONFIRMED'")
        revenue = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM students WHERE created_at >= current_date - interval '30 days'")
        new_students = cur.fetchone()[0]
        conn.close()
        return f"Database Snapshot: 30-day Revenue EGP {revenue:,}, 30-day New Students {new_students}"
    except Exception as e:
        return "Database offline."

def grab_camera_frame() -> dict:
    """Tool: Vision Capture. Returns a dict that will be embedded into the next prompt."""
    print("🔧 Agent called tool: grab_camera_frame")
    if camera_stream:
        b64 = camera_stream.grab_frame_base64()
        if b64:
            return {"text": "Camera captured.", "image": b64}
    return {"text": "Camera offline or disconnected.", "image": None}

OLLAMA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "grab_camera_frame",
            "description": "Takes a live picture from the workshop camera so you can see what is happening.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_cabinet_snapshot",
            "description": "Queries the database for live revenue and student statistics.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

# ─── Agent Loop & Tool Execution ──────────────────────────────────────────

def execute_agent_loop(transcribed_text: str):
    """Passes user command to Gemma-4, handles tool calls, and returns final text."""
    messages = [
        {"role": "system", "content": "You are HAMADA, the AI brain of The Crafters Hub workshop. Answer concisely for voice output."},
        {"role": "user", "content": transcribed_text}
    ]

    print("🧠 Gemma-4 E4B is thinking...")
    
    # First LLM Call (Let Gemma decide if it needs tools)
    try:
        response = requests.post(OLLAMA_ENDPOINT, json={"model": MODEL_NAME, "messages": messages, "tools": OLLAMA_TOOLS, "stream": False}, timeout=30)
        result = response.json().get('message', {})
    except Exception as e:
        return f"Brain connection failed: {e}"

    # Check for Tool Calls
    tool_calls = result.get('tool_calls', [])
    if tool_calls:
        # Append the assistant's tool call request to the message history
        messages.append(result)
        
        for tool in tool_calls:
            func_name = tool['function']['name']
            
            if func_name == "get_cabinet_snapshot":
                tool_result = get_cabinet_snapshot()
                messages.append({"role": "tool", "content": tool_result})
                
            elif func_name == "grab_camera_frame":
                cam_res = grab_camera_frame()
                msg = {"role": "tool", "content": cam_res["text"]}
                if cam_res["image"]:
                    # Ollama accepts images attached to the tool response
                    msg["images"] = [cam_res["image"]]
                messages.append(msg)

        # Second LLM Call (Gemma generates final answer using the tool data)
        print("🧠 Gemma-4 E4B is analyzing tool results...")
        try:
            response = requests.post(OLLAMA_ENDPOINT, json={"model": MODEL_NAME, "messages": messages, "stream": False}, timeout=30)
            return response.json().get('message', {}).get('content', "Error generating response.")
        except Exception as e:
            return f"Brain connection failed on second pass: {e}"

    # If no tools were called, just return the text
    return result.get('content', "I have nothing to say.")


# ─── Speech Synthesis (Piper TTS) ──────────────────────────────────────────

def speak(text: str):
    print(f"💬 HAMADA Speaks: {text}")
    os.system(f'echo "{text}" | piper --model en_US-lessac-medium.onnx --output_file /tmp/response.wav 2>/dev/null')
    try:
        data, fs = sf.read('/tmp/response.wav', dtype='float32')
        sd.play(data, fs)
        sd.wait()
    except Exception as e:
        print(f"TTS Audio playback failed: {e}")

# ─── Thread 1: Always-On Listening ─────────────────────────────────────────

def audio_listener_thread():
    """Continuously listens for wake word and pushes audio to the queue without blocking."""
    import openwakeword
    from openwakeword.model import Model
    
    openwakeword.utils.download_models()
    oww_model = Model(wakeword_models=["alexa"]) # Train custom later

    audio = pyaudio.PyAudio()
    mic_stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

    print("\n👂 Audio Listener Thread ONLINE.")
    
    while True:
        try:
            pcm = mic_stream.read(CHUNK, exception_on_overflow=False)
            audio_data = np.frombuffer(pcm, dtype=np.int16)

            prediction = oww_model.predict(audio_data)
            
            if any(score > 0.5 for score in prediction.values()):
                print("\n🚨 WAKE WORD DETECTED! Recording...")
                # We could use `speak` here, but it blocks. Instead, just print or play a tiny beep.
                os.system('echo -e "\a"') 
                
                command_frames = []
                for _ in range(0, int(RATE / CHUNK * 5)):
                    command_frames.append(mic_stream.read(CHUNK, exception_on_overflow=False))
                
                audio_task_queue.put(b''.join(command_frames))
                print("✅ Audio captured. Pushed to Agent Queue.")
        except Exception as e:
            print(f"Audio thread error: {e}")
            time.sleep(1)

# ─── Main Thread: Task Processor ───────────────────────────────────────────

def start_live_brain():
    from faster_whisper import WhisperModel
    print("==================================================")
    print(" 🧠 The Cabinet Live Brain (Multi-Threaded)")
    print("==================================================")

    print("📝 Loading local Speech-to-Text (Whisper)...")
    whisper_model = WhisperModel("tiny.en", device="cpu", compute_type="int8")

    # Start the background listener
    listener = threading.Thread(target=audio_listener_thread, daemon=True)
    listener.start()

    print("\n✅ System fully armed and waiting for commands...")
    
    try:
        while True:
            # Block until audio is available in the queue
            raw_audio = audio_task_queue.get()
            
            print("⏳ Transcribing audio command...")
            command_audio = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) / 32768.0
            segments, _ = whisper_model.transcribe(command_audio, beam_size=1)
            command_text = " ".join([segment.text for segment in segments]).strip()
            
            print(f"🗣️ You said: {command_text}")
            
            if command_text:
                final_response = execute_agent_loop(command_text)
                speak(final_response)
                
            audio_task_queue.task_done()

    except KeyboardInterrupt:
        print("\n👋 Live Brain shutting down.")
    finally:
        if camera_stream:
            camera_stream.stop()

if __name__ == "__main__":
    start_live_brain()
