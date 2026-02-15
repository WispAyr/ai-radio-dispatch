"""Web server with WebSocket support for the dispatch dashboard."""

import os
import json
import asyncio
from aiohttp import web
import aiohttp
from dispatch import DispatchSystem


class DispatchServer:
    """HTTP + WebSocket server for the dispatch dashboard."""

    def __init__(self, dispatch: DispatchSystem, host="0.0.0.0", port=8080):
        self.dispatch = dispatch
        self.host = host
        self.port = port
        self.app = web.Application()
        self.websockets = set()
        self._setup_routes()

    def _setup_routes(self):
        """Set up HTTP and WebSocket routes."""
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_get("/ws", self._handle_websocket)
        self.app.router.add_post("/api/start", self._handle_start)
        self.app.router.add_post("/api/stop", self._handle_stop)
        self.app.router.add_post("/api/mute", self._handle_mute)
        self.app.router.add_post("/api/clear", self._handle_clear)
        self.app.router.add_get("/api/status", self._handle_status)
        self.app.router.add_get("/api/devices", self._handle_devices)
        self.app.router.add_post("/api/device", self._handle_set_device)
        self.app.router.add_get("/api/settings", self._handle_get_settings)
        self.app.router.add_post("/api/settings", self._handle_set_settings)

        # Static files
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        if os.path.exists(static_dir):
            self.app.router.add_static("/static", static_dir)

        # Recordings
        recordings_dir = os.path.join(os.path.dirname(__file__), "recordings")
        if os.path.exists(recordings_dir):
            self.app.router.add_static("/recordings", recordings_dir)

    async def _handle_index(self, request):
        """Serve the dashboard HTML."""
        index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
        return web.FileResponse(index_path)

    async def _handle_websocket(self, request):
        """Handle WebSocket connections for real-time updates."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.websockets.add(ws)

        # Send current state
        await ws.send_json({
            "type": "init",
            "state": self.dispatch.state.value,
            "log": [e.to_dict() for e in self.dispatch.log],
            "muted": self.dispatch._muted,
            "devices": self.dispatch.get_input_devices()
        })

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            self.websockets.discard(ws)

        return ws

    async def _handle_start(self, request):
        """Start the dispatch system."""
        self.dispatch.start()
        return web.json_response({"status": "started"})

    async def _handle_stop(self, request):
        """Stop the dispatch system."""
        self.dispatch.stop()
        return web.json_response({"status": "stopped"})

    async def _handle_mute(self, request):
        """Toggle mute."""
        muted = self.dispatch.toggle_mute()
        await self._broadcast({"type": "mute", "muted": muted})
        return web.json_response({"muted": muted})

    async def _handle_clear(self, request):
        """Clear the dispatch log."""
        self.dispatch.clear_log()
        await self._broadcast({"type": "clear"})
        return web.json_response({"status": "cleared"})

    async def _handle_status(self, request):
        """Get current status."""
        return web.json_response({
            "state": self.dispatch.state.value,
            "muted": self.dispatch._muted,
            "log_count": len(self.dispatch.log)
        })

    async def _handle_devices(self, request):
        """List available audio input devices."""
        devices = self.dispatch.get_input_devices()
        return web.json_response({"devices": devices})

    async def _handle_set_device(self, request):
        """Switch audio input device."""
        data = await request.json()
        device_id = data.get("device_id")
        if device_id is not None:
            self.dispatch.set_input_device(int(device_id))
            devices = self.dispatch.get_input_devices()
            await self._broadcast({"type": "devices", "devices": devices})
            return web.json_response({"status": "ok", "device_id": device_id})
        return web.json_response({"error": "device_id required"}, status=400)

    async def _handle_get_settings(self, request):
        """Get current transcription settings."""
        settings = self.dispatch.get_settings()
        return web.json_response(settings)

    async def _handle_set_settings(self, request):
        """Update transcription settings. May trigger model reload."""
        data = await request.json()
        result = self.dispatch.apply_settings(data)
        await self._broadcast({"type": "settings", "settings": self.dispatch.get_settings()})
        return web.json_response(result)

    async def _broadcast(self, data: dict):
        """Broadcast a message to all connected WebSocket clients."""
        dead = set()
        for ws in self.websockets:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self.websockets -= dead

    async def broadcast_state(self, state: str):
        """Broadcast state change."""
        await self._broadcast({"type": "state", "state": state})

    async def broadcast_log_entry(self, entry: dict):
        """Broadcast a new log entry."""
        await self._broadcast({"type": "log", "entry": entry})

    async def broadcast_level(self, level: float):
        """Broadcast audio level."""
        await self._broadcast({"type": "level", "level": level})

    def run(self):
        """Start the web server (blocking)."""
        web.run_app(self.app, host=self.host, port=self.port)
