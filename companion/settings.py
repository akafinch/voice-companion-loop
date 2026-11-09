"""Settings, read from the environment when the bot starts.

Locally, set the six service variables. On LKE the deployment sets all eight.
"""

import os

# The three model services, each reached through its OpenAI-compatible HTTP interface.
CHAT_URL = os.environ.get("COMPANION_CHAT_URL", "")  # vLLM, e.g. http://chat:8000/v1
CHAT_MODEL = os.environ.get("COMPANION_CHAT_MODEL", "")
STT_URL = os.environ.get("COMPANION_STT_URL", "")  # vLLM, e.g. http://transcription:8000/v1
STT_MODEL = os.environ.get("COMPANION_STT_MODEL", "")
TTS_URL = os.environ.get("COMPANION_TTS_URL", "")  # VoiceStudio, e.g. http://speech:3900
TTS_KEY = os.environ.get("COMPANION_TTS_KEY", "")  # VoiceStudio's API key, which only the bot holds

# The TURN relay: STUNner's auth service, and the hostname of the relay's TLS listener on 443.
# Without this pair the bot runs in local mode, and the browser connects to it directly.
TURN_AUTH_URL = os.environ.get("COMPANION_TURN_AUTH_URL", "")
TURN_TLS_HOST = os.environ.get("COMPANION_TURN_TLS_HOST", "")
