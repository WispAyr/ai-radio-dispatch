"""Entry point for the Radio Dispatch system."""

import asyncio
import signal
import sys
from dispatch import DispatchSystem
from server import DispatchServer
from config import SERVER_HOST, SERVER_PORT


async def main():
    dispatch = DispatchSystem()
    server = DispatchServer(dispatch, host=SERVER_HOST, port=SERVER_PORT)

    # Get the event loop for scheduling coroutines from sync callbacks
    loop = asyncio.get_event_loop()

    # ===== Wire callbacks =====

    def on_state_change(state: str):
        asyncio.run_coroutine_threadsafe(
            server.broadcast_state(state), loop
        )

    def on_log_entry(entry: dict):
        asyncio.run_coroutine_threadsafe(
            server.broadcast_log_entry(entry), loop
        )

    def on_level_update(channel_id: str, level: float):
        asyncio.run_coroutine_threadsafe(
            server.broadcast_level(channel_id, level), loop
        )

    def on_channel_state_change(channel_id: str, state: str):
        asyncio.run_coroutine_threadsafe(
            server.broadcast_channel_state(channel_id, state), loop
        )

    def on_callsign_detected(callsigns: list, channel_id: str):
        asyncio.run_coroutine_threadsafe(
            server.broadcast_callsign(callsigns, channel_id), loop
        )

    def on_alert(alert_data: dict):
        asyncio.run_coroutine_threadsafe(
            server.broadcast_alert(alert_data), loop
        )

    dispatch.set_callbacks(
        on_state_change=on_state_change,
        on_log_entry=on_log_entry,
        on_level_update=on_level_update,
        on_channel_state_change=on_channel_state_change,
        on_callsign_detected=on_callsign_detected,
        on_alert=on_alert,
    )

    # Load models
    print("=" * 60)
    print("  Radio Dispatch Console")
    print("=" * 60)
    dispatch.load_models()

    # List channels
    channels = dispatch.get_channels()
    print(f"\nChannels ({len(channels)}):")
    for ch in channels:
        print(f"  [{ch['id']}] {ch['name']} (device={ch['device_id']}, color={ch['color']})")

    # List DB stats
    stats = dispatch.get_stats()
    print(f"\nDatabase: {stats['total_transcripts']} transcripts, "
          f"{stats['total_callsigns']} callsigns, "
          f"{stats['total_bookmarks']} bookmarks")

    print(f"\nDashboard: http://localhost:{SERVER_PORT}")
    print("=" * 60)

    # Handle graceful shutdown
    def handle_signal(sig, frame):
        print("\n[Main] Shutting down...")
        dispatch.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Start the web server
    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, server.host, server.port)
    await site.start()

    # Keep running
    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    from aiohttp import web
    asyncio.run(main())
