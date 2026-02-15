"""LLM integration via Ollama for dispatch response generation."""

import json
import urllib.request
from config import OLLAMA_URL, OLLAMA_MODEL, OLLAMA_CONTEXT_WINDOW, DISPATCH_SYSTEM_PROMPT


class DispatchLLM:
    """Generates dispatch responses using Ollama."""

    def __init__(self):
        self._history = []

    def generate(self, user_message: str) -> str:
        """
        Generate a dispatch response to an incoming radio message.

        Args:
            user_message: Transcribed text from the radio.

        Returns:
            Dispatch response text.
        """
        self._history.append({"role": "user", "content": user_message})

        # Trim history to context window
        if len(self._history) > OLLAMA_CONTEXT_WINDOW * 2:
            self._history = self._history[-(OLLAMA_CONTEXT_WINDOW * 2):]

        messages = [
            {"role": "system", "content": DISPATCH_SYSTEM_PROMPT},
            *self._history
        ]

        payload = json.dumps({
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
                "num_predict": 150,  # Keep responses short for radio
            }
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                reply = data.get("message", {}).get("content", "").strip()

                self._history.append({"role": "assistant", "content": reply})
                return reply

        except Exception as e:
            error_msg = f"[LLM] Error: {e}"
            print(error_msg)
            return "Dispatch, stand by. Experiencing technical difficulties."

    def clear_history(self):
        """Clear conversation history."""
        self._history = []
