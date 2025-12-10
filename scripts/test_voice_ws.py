"""Test script for voice WebSocket endpoint.

Usage:
    python scripts/test_voice_ws.py [audio_file.wav]
"""
import asyncio
import websockets
import json
import sys
from pathlib import Path


async def test_voice_websocket(audio_file: str = None):
    """Test voice WebSocket with audio file or dummy data."""
    uri = "ws://localhost:8000/ws/call/audio"
    
    print(f"Connecting to {uri}...")
    
    async with websockets.connect(uri) as ws:
        # Wait for connection confirmation
        response = await ws.recv()
        print(f"Connected: {response}")
        
        # Send audio data
        if audio_file and Path(audio_file).exists():
            print(f"Sending audio file: {audio_file}")
            with open(audio_file, "rb") as f:
                audio_data = f.read()
                await ws.send(audio_data)
                print(f"Sent {len(audio_data)} bytes")
        else:
            # Send dummy audio (will likely fail transcription but tests connection)
            print("Sending dummy audio data...")
            dummy_audio = b'\x00' * 16000  # 1 second of silence at 16kHz
            await ws.send(dummy_audio)
            print(f"Sent {len(dummy_audio)} bytes")
        
        # Wait for responses (timeout after 30s)
        try:
            while True:
                response = await asyncio.wait_for(ws.recv(), timeout=30.0)
                
                if isinstance(response, bytes):
                    print(f"Received audio response: {len(response)} bytes")
                    # Save to file
                    output_file = "test_response.mp3"
                    with open(output_file, "wb") as f:
                        f.write(response)
                    print(f"Saved audio to {output_file}")
                else:
                    data = json.loads(response)
                    print(f"Received JSON: {data}")
                    
                    if data.get("type") == "error":
                        print(f"Error from server: {data.get('message')}")
                        break
                    elif data.get("type") == "audio_ready":
                        print("Audio synthesis complete!")
                        # Wait a bit more for the audio bytes
                        await asyncio.sleep(1)
                        break
                        
        except asyncio.TimeoutError:
            print("Timeout waiting for response")
        
        print("Test complete")


if __name__ == "__main__":
    audio_file = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(test_voice_websocket(audio_file))
