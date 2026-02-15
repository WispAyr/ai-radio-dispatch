"""
Radio Dispatch Operator System
==============================
Entry point — starts the dispatch pipeline and web server.

Usage:
    python3 main.py

Then open http://localhost:8080 in your browser.
"""

import asyncio
import threading
import signal
import sys
import time
import queue
from dispatch import DispatchSystem
from server import DispatchServer
from config import SERVER_HOST, SERVER_PORT


# Thread-safe queue for events from audio thread → async server
event_queue = queue.Queue()


def main():
    print("=" * 50)
    print("  RADIO DISPATCH OPERATOR")
    print("  Whisper STT → Ollama LLM → Piper TTS")
    print("=" * 50)
    print()

    # Create the dispatch system
    dispatch = DispatchSystem()

    # Load models
    print("[Main] Loading models (first run may download Whisper model)...")
    dispatch.load_models()
    print()

    # Create the web server
    server = DispatchServer(dispatch, host=SERVER_HOST, port=SERVER_PORT)

    # Set up thread-safe callbacks that push to the event queue
    def on_state_change(state):
        event_queue.put(("state", state))

    def on_log_entry(entry):
        event_queue.put(("log", entry))

    _last_level = [0.0]

    def on_level_update(level):
        now = time.time()
        if now - _last_level[0] < 0.1:
            return
        _last_level[0] = now
        event_queue.put(("level", level))

    dispatch.set_callbacks(
        on_state_change=on_state_change,
        on_log_entry=on_log_entry,
        on_level_update=on_level_update
    )

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print("\n[Main] Shutting down...")
        dispatch.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    print(f"[Main] Dashboard: http://localhost:{SERVER_PORT}")
    print("[Main] Press Ctrl+C to quit")
    print()

    # Background task to drain the event queue and broadcast via WebSocket
    async def event_drainer():
        while True:
            try:
                while not event_queue.empty():
                    event_type, data = event_queue.get_nowait()
                    if event_type == "state":
                        await server.broadcast_state(data)
                    elif event_type == "log":
                        await server.broadcast_log_entry(data)
                    elif event_type == "level":
                        await server.broadcast_level(data)
            except Exception as e:
                print(f"[Main] Event drainer error: {e}")
            await asyncio.sleep(0.05)  # 20 Hz poll

    async def run():
        from aiohttp import web
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, SERVER_HOST, SERVER_PORT)
        await site.start()
        print(f"[Main] Server running on http://{SERVER_HOST}:{SERVER_PORT}")

        # Start the event drainer
        asyncio.create_task(event_drainer())

        # Keep running
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await runner.cleanup()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[Main] Shutting down...")
        dispatch.stop()


if __name__ == "__main__":
    main()
