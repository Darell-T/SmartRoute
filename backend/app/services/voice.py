from elevenlabs.client import ElevenLabs
import os

client = ElevenLabs(
    api_key = os.getenv("ELEVENLABS_API_KEY")
)


def generate_speech(text: str) -> bytes:
    chunks = client.text_to_speech.stream(
        text=text,
        voice_id="tNXCufOhVV3Bk4Mac1E1",
        model_id="eleven_turbo_v2_5",
        output_format="mp3_44100_128",
    )
    return b"".join(chunks)
