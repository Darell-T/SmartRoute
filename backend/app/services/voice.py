from elevenlabs.client import ElevenLabs
import os

# TTS is OFF by default — SmartRoute is a visual, map-first product with no
# spoken narration. Set ENABLE_TTS=1 (or legacy DISABLE_TTS=0) to turn the
# ElevenLabs voice back on. When disabled, generate_speech returns empty bytes,
# which flow through as audio="" and the frontend simply plays nothing.
_enable_tts = os.getenv("ENABLE_TTS", "").lower() in ("1", "true", "yes")
_legacy_disable = os.getenv("DISABLE_TTS", "").lower()
TTS_DISABLED = not _enable_tts and _legacy_disable != "0"

client = ElevenLabs(
    api_key = os.getenv("ELEVENLABS_API_KEY")
)


def generate_speech(text: str) -> bytes:
    if TTS_DISABLED:
        print(f"[voice] TTS disabled, skipping ElevenLabs call for: {text[:60]!r}")
        return b""
    chunks = client.text_to_speech.stream(
        text=text,
        voice_id="wrQ1LVmK2qx0vh7ygiYg",
        model_id="eleven_turbo_v2_5",
        output_format="mp3_44100_128",
    )
    return b"".join(chunks)
