#!/usr/bin/env python3
"""
Imou Camera Vision Module (Threaded Production Version)
Location: D:\TheCraftersHub_DataLab\scripts\imou_vision.py

This module connects to Imou WiFi cameras via RTSP using a dedicated
background thread. It continuously clears the FFmpeg buffer and keeps only 
the freshest frame in memory, ensuring 0ms latency when the LLM requests it.
"""

import cv2
import base64
import time
import threading
import os
from typing import Optional
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

class ImouCameraStream:
    def __init__(self, name: str, ip: str, safety_code: str, username: str = "admin"):
        self.name = name
        self.ip = ip
        self.safety_code = safety_code
        self.username = username
        self.rtsp_url = f"rtsp://{self.username}:{self.safety_code}@{self.ip}:554/cam/realmonitor?channel=1&subtype=1"
        
        self.cap = None
        self.latest_frame = None
        self.running = False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._update, daemon=True)

    def start(self):
        """Starts the background capture thread."""
        print(f"📷 [{self.name}] Connecting to RTSP stream...")
        self.cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.cap.isOpened():
            print(f"❌ [{self.name}] Error: Could not connect to camera.")
            return False

        self.running = True
        self.thread.start()
        print(f"✅ [{self.name}] Stream connected and buffering in background.")
        return True

    def _update(self):
        """Background loop that continuously empties the buffer to prevent latency."""
        while self.running:
            if self.cap.isOpened():
                # .grab() is fast and pulls the frame from the network buffer
                ret = self.cap.grab()
                if ret:
                    # .retrieve() decodes the grabbed frame
                    ret, frame = self.cap.retrieve()
                    if ret:
                        with self.lock:
                            self.latest_frame = frame
            else:
                time.sleep(0.1)

    def grab_frame_base64(self) -> Optional[str]:
        """Returns the absolute newest frame instantly (0ms latency)."""
        with self.lock:
            frame = self.latest_frame.copy() if self.latest_frame is not None else None
            
        if frame is not None:
            _, buffer = cv2.imencode('.jpg', frame)
            return base64.b64encode(buffer).decode('utf-8')
        return None

    def stop(self):
        """Stops the thread and releases the camera."""
        self.running = False
        if self.thread.is_alive():
            self.thread.join()
        if self.cap and self.cap.isOpened():
            self.cap.release()
        print(f"🛑 [{self.name}] Stream stopped.")

# ─── Testing ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_ip = input("Enter Camera IP Address: ").strip()
    test_code = input("Enter Safety Code: ").strip()
    
    if test_ip and test_code:
        cam = ImouCameraStream(name="Test_Camera", ip=test_ip, safety_code=test_code)
        if cam.start():
            # Wait a second for the first frame to decode
            time.sleep(1)
            b64_frame = cam.grab_frame_base64()
            if b64_frame:
                print(f"🎉 SUCCESS! Instant frame grabbed ({len(b64_frame)} bytes).")
            cam.stop()
