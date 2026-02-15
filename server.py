"""Web server with WebSocket support for the dispatch dashboard."""

import os
import json
import asyncio
import time
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

        # Global controls
        self.app.router.add_post("/api/start", self._handle_start)
        self.app.router.add_post("/api/stop", self._handle_stop)
        self.app.router.add_post("/api/mute", self._handle_mute)
        self.app.router.add_post("/api/clear", self._handle_clear)
        self.app.router.add_get("/api/status", self._handle_status)
        self.app.router.add_get("/api/devices", self._handle_devices)
        self.app.router.add_post("/api/device", self._handle_set_device)
        self.app.router.add_get("/api/settings", self._handle_get_settings)
        self.app.router.add_post("/api/settings", self._handle_set_settings)

        # Channel management
        self.app.router.add_get("/api/channels", self._handle_get_channels)
        self.app.router.add_post("/api/channels", self._handle_add_channel)
        self.app.router.add_delete("/api/channels/{channel_id}", self._handle_remove_channel)
        self.app.router.add_put("/api/channels/{channel_id}", self._handle_update_channel)
        self.app.router.add_post("/api/channels/{channel_id}/start", self._handle_channel_start)
        self.app.router.add_post("/api/channels/{channel_id}/stop", self._handle_channel_stop)
        self.app.router.add_post("/api/channels/{channel_id}/mute", self._handle_channel_mute)
        self.app.router.add_post("/api/channels/{channel_id}/clear", self._handle_channel_clear)
        self.app.router.add_post("/api/channels/{channel_id}/device", self._handle_channel_device)

        # Search
        self.app.router.add_get("/api/search", self._handle_search)

        # Bookmarks
        self.app.router.add_get("/api/bookmarks", self._handle_get_bookmarks)
        self.app.router.add_post("/api/bookmarks", self._handle_add_bookmark)
        self.app.router.add_delete("/api/bookmarks/{bookmark_id}", self._handle_remove_bookmark)
        self.app.router.add_post("/api/transcripts/{transcript_id}/flag", self._handle_flag_transcript)

        # Callsigns
        self.app.router.add_get("/api/callsigns", self._handle_get_callsigns)
        self.app.router.add_get("/api/callsigns/{callsign}", self._handle_get_callsign)

        # Alerts
        self.app.router.add_get("/api/alerts", self._handle_get_alerts)
        self.app.router.add_post("/api/alerts", self._handle_add_alert)
        self.app.router.add_delete("/api/alerts/{alert_id}", self._handle_remove_alert)

        # Export
        self.app.router.add_get("/api/export", self._handle_export)

        # Stats
        self.app.router.add_get("/api/stats", self._handle_stats)

        # Sessions
        self.app.router.add_get("/api/sessions", self._handle_get_sessions)

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

        # Send current state with channel info
        channels = self.dispatch.get_channels()
        channel_logs = {}
        for ch in channels:
            channel_logs[ch["id"]] = self.dispatch.get_channel_logs(ch["id"])

        await ws.send_json({
            "type": "init",
            "state": self.dispatch.state.value,
            "channels": channels,
            "channel_logs": channel_logs,
            "muted": self.dispatch._muted,
            "devices": self.dispatch.get_input_devices(),
            "stats": self.dispatch.get_stats(),
            "alerts": self.dispatch.alert_engine.get_rules(),
            "callsigns": self.dispatch.callsign_registry.get_active(30),
        })

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            self.websockets.discard(ws)

        return ws

    # ===== Global controls =====

    async def _handle_start(self, request):
        self.dispatch.start()
        return web.json_response({"status": "started"})

    async def _handle_stop(self, request):
        self.dispatch.stop()
        return web.json_response({"status": "stopped"})

    async def _handle_mute(self, request):
        muted = self.dispatch.toggle_mute()
        await self._broadcast({"type": "mute", "muted": muted})
        return web.json_response({"muted": muted})

    async def _handle_clear(self, request):
        self.dispatch.clear_log()
        await self._broadcast({"type": "clear"})
        return web.json_response({"status": "cleared"})

    async def _handle_status(self, request):
        return web.json_response({
            "state": self.dispatch.state.value,
            "muted": self.dispatch._muted,
            "channels": self.dispatch.get_channels(),
            "stats": self.dispatch.get_stats(),
        })

    async def _handle_devices(self, request):
        devices = self.dispatch.get_input_devices()
        return web.json_response({"devices": devices})

    async def _handle_set_device(self, request):
        data = await request.json()
        device_id = data.get("device_id")
        channel_id = data.get("channel_id")
        if device_id is not None and channel_id:
            self.dispatch.set_input_device(int(device_id), channel_id)
            await self._broadcast_channels()
            return web.json_response({"status": "ok"})
        return web.json_response({"error": "device_id and channel_id required"}, status=400)

    async def _handle_get_settings(self, request):
        settings = self.dispatch.get_settings()
        return web.json_response(settings)

    async def _handle_set_settings(self, request):
        data = await request.json()
        result = self.dispatch.apply_settings(data)
        await self._broadcast({"type": "settings", "settings": self.dispatch.get_settings()})
        return web.json_response(result)

    # ===== Channel management =====

    async def _handle_get_channels(self, request):
        return web.json_response({"channels": self.dispatch.get_channels()})

    async def _handle_add_channel(self, request):
        data = await request.json()
        ch = self.dispatch.add_channel(
            name=data.get("name", "New Channel"),
            device_id=data.get("device_id"),
            color=data.get("color")
        )
        await self._broadcast({"type": "channel_added", "channel": ch})
        return web.json_response(ch)

    async def _handle_remove_channel(self, request):
        channel_id = request.match_info["channel_id"]
        if self.dispatch.remove_channel(channel_id):
            await self._broadcast({"type": "channel_removed", "channel_id": channel_id})
            return web.json_response({"status": "removed"})
        return web.json_response({"error": "channel not found"}, status=404)

    async def _handle_update_channel(self, request):
        channel_id = request.match_info["channel_id"]
        data = await request.json()
        ch = self.dispatch.update_channel(
            channel_id, name=data.get("name"),
            device_id=data.get("device_id"), color=data.get("color")
        )
        if ch:
            await self._broadcast({"type": "channel_updated", "channel": ch})
            return web.json_response(ch)
        return web.json_response({"error": "channel not found"}, status=404)

    async def _handle_channel_start(self, request):
        channel_id = request.match_info["channel_id"]
        self.dispatch.start(channel_id)
        await self._broadcast_channels()
        return web.json_response({"status": "started", "channel_id": channel_id})

    async def _handle_channel_stop(self, request):
        channel_id = request.match_info["channel_id"]
        self.dispatch.stop(channel_id)
        await self._broadcast_channels()
        return web.json_response({"status": "stopped", "channel_id": channel_id})

    async def _handle_channel_mute(self, request):
        channel_id = request.match_info["channel_id"]
        muted = self.dispatch.toggle_mute(channel_id)
        await self._broadcast({"type": "channel_mute", "channel_id": channel_id, "muted": muted})
        return web.json_response({"muted": muted, "channel_id": channel_id})

    async def _handle_channel_clear(self, request):
        channel_id = request.match_info["channel_id"]
        self.dispatch.clear_log(channel_id)
        await self._broadcast({"type": "channel_clear", "channel_id": channel_id})
        return web.json_response({"status": "cleared", "channel_id": channel_id})

    async def _handle_channel_device(self, request):
        channel_id = request.match_info["channel_id"]
        data = await request.json()
        device_id = data.get("device_id")
        if device_id is not None:
            self.dispatch.set_input_device(int(device_id), channel_id)
            await self._broadcast_channels()
            return web.json_response({"status": "ok"})
        return web.json_response({"error": "device_id required"}, status=400)

    # ===== Search =====

    async def _handle_search(self, request):
        q = request.query.get("q", "")
        channel_id = request.query.get("channel")
        speaker = request.query.get("speaker")
        from_ts = request.query.get("from")
        to_ts = request.query.get("to")
        flagged = request.query.get("flagged") == "1"
        limit = int(request.query.get("limit", 100))
        offset = int(request.query.get("offset", 0))

        results = self.dispatch.search_transcripts(
            q, channel_id, speaker,
            float(from_ts) if from_ts else None,
            float(to_ts) if to_ts else None,
            flagged, limit, offset
        )
        return web.json_response({"results": results, "count": len(results)})

    # ===== Bookmarks =====

    async def _handle_get_bookmarks(self, request):
        bookmarks = self.dispatch.get_bookmarks()
        return web.json_response({"bookmarks": bookmarks})

    async def _handle_add_bookmark(self, request):
        data = await request.json()
        bm_id = self.dispatch.add_bookmark(
            int(data["transcript_id"]),
            data.get("label", "")
        )
        return web.json_response({"id": bm_id, "status": "created"})

    async def _handle_remove_bookmark(self, request):
        bookmark_id = int(request.match_info["bookmark_id"])
        self.dispatch.remove_bookmark(bookmark_id)
        return web.json_response({"status": "removed"})

    async def _handle_flag_transcript(self, request):
        transcript_id = int(request.match_info["transcript_id"])
        data = await request.json()
        self.dispatch.flag_transcript(
            transcript_id,
            data.get("flagged", True),
            data.get("notes")
        )
        return web.json_response({"status": "ok"})

    # ===== Callsigns =====

    async def _handle_get_callsigns(self, request):
        domain = request.query.get("domain")
        since = request.query.get("since")
        callsigns = self.dispatch.callsign_registry.get_all() if not since \
            else self.dispatch.callsign_registry.get_active(int(since))
        if domain:
            callsigns = [c for c in callsigns if c.get("domain") == domain]
        return web.json_response({"callsigns": callsigns})

    async def _handle_get_callsign(self, request):
        callsign = request.match_info["callsign"]
        result = self.dispatch.callsign_registry.lookup(callsign)
        if result:
            return web.json_response(result)
        return web.json_response({"error": "callsign not found"}, status=404)

    # ===== Alerts =====

    async def _handle_get_alerts(self, request):
        rules = self.dispatch.alert_engine.get_rules()
        return web.json_response({"alerts": rules})

    async def _handle_add_alert(self, request):
        data = await request.json()
        alert_id = self.dispatch.alert_engine.add_keyword_alert(
            name=data.get("name", "Alert"),
            pattern=data["pattern"],
            is_regex=data.get("is_regex", False)
        )
        await self._broadcast({"type": "alerts_updated", "alerts": self.dispatch.alert_engine.get_rules()})
        return web.json_response({"id": alert_id, "status": "created"})

    async def _handle_remove_alert(self, request):
        alert_id = int(request.match_info["alert_id"])
        self.dispatch.alert_engine.remove_keyword_alert(alert_id)
        await self._broadcast({"type": "alerts_updated", "alerts": self.dispatch.alert_engine.get_rules()})
        return web.json_response({"status": "removed"})

    # ===== Export =====

    async def _handle_export(self, request):
        fmt = request.query.get("format", "json")
        channel_id = request.query.get("channel")
        from_ts = request.query.get("from")
        to_ts = request.query.get("to")

        data = self.dispatch.export_transcripts(
            fmt, channel_id,
            float(from_ts) if from_ts else None,
            float(to_ts) if to_ts else None
        )

        content_types = {
            "json": "application/json",
            "csv": "text/csv",
            "txt": "text/plain",
        }
        ct = content_types.get(fmt, "application/json")

        return web.Response(
            text=data,
            content_type=ct,
            headers={
                "Content-Disposition": f'attachment; filename="dispatch_export.{fmt}"'
            }
        )

    # ===== Stats =====

    async def _handle_stats(self, request):
        return web.json_response(self.dispatch.get_stats())

    # ===== Sessions =====

    async def _handle_get_sessions(self, request):
        return web.json_response({"sessions": self.dispatch.get_sessions()})

    # ===== Broadcasting =====

    async def _broadcast(self, data: dict):
        """Broadcast a message to all connected WebSocket clients."""
        dead = set()
        for ws in self.websockets:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self.websockets -= dead

    async def _broadcast_channels(self):
        await self._broadcast({
            "type": "channels",
            "channels": self.dispatch.get_channels()
        })

    async def broadcast_state(self, state: str):
        await self._broadcast({"type": "state", "state": state})

    async def broadcast_log_entry(self, entry: dict):
        await self._broadcast({"type": "log", "entry": entry})

    async def broadcast_level(self, channel_id: str, level: float):
        await self._broadcast({"type": "level", "channel_id": channel_id, "level": level})

    async def broadcast_channel_state(self, channel_id: str, state: str):
        await self._broadcast({"type": "channel_state", "channel_id": channel_id, "state": state})

    async def broadcast_alert(self, alert_data: dict):
        await self._broadcast({"type": "alert", **alert_data})

    async def broadcast_callsign(self, callsigns: list, channel_id: str):
        await self._broadcast({"type": "callsign_detected", "callsigns": callsigns, "channel_id": channel_id})

    def run(self):
        web.run_app(self.app, host=self.host, port=self.port)
