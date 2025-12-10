"""Real-time streaming STT using Google Speech-to-Text Streaming API."""
import asyncio
import logging
from typing import AsyncGenerator
import queue
import threading
import shutil
import os

try:
    from google.cloud import speech_v1p1beta1 as speech  # type: ignore
except Exception:  # pragma: no cover - dependency optional
    speech = None  # type: ignore

logger = logging.getLogger(__name__)


async def transcribe_streaming(
    audio_chunks: AsyncGenerator[bytes, None],
    language_code: str = "vi-VN",
    sample_rate: int = 48000
) -> AsyncGenerator[dict, None]:
    """
    Stream audio chunks to Google Speech-to-Text and yield results.
    
    Args:
        audio_chunks: Async generator yielding audio bytes
        language_code: Language code (default: vi-VN)
        sample_rate: Audio sample rate (default: 48000 for WebM)
        
    Yields:
        Transcription results with text, is_final, confidence
    """
    if speech is None:
        logger.error("google-cloud-speech not installed; cannot use streaming STT")
        yield {"ok": False, "error": "google-cloud-speech not installed"}
        return

    client = speech.SpeechClient()
    
    # Thread-safe queues
    audio_queue = queue.Queue()
    result_queue = queue.Queue()
    done_event = threading.Event()
    
    # Detect if ffmpeg is available for conversion; if not, we'll send raw chunks
    # Optional: allow disabling ffmpeg conversion (default off for stability on Windows)
    use_ffmpeg = os.getenv("STT_USE_FFMPEG", "false").lower() in {"1", "true", "yes"}
    ffmpeg_path = shutil.which("ffmpeg") if use_ffmpeg else None
    conversion_available = bool(ffmpeg_path)
    if conversion_available:
        logger.info(f"ffmpeg found at {ffmpeg_path} - conversion to LINEAR16 will be used")
    else:
        logger.info("ffmpeg conversion disabled; sending WEBM/OPUS chunks directly to STT")

    # Choose recognition encoding depending on whether we convert to LINEAR16
    if conversion_available:
        recog_encoding = speech.RecognitionConfig.AudioEncoding.LINEAR16
        recog_rate = 16000
    else:
        recog_encoding = speech.RecognitionConfig.AudioEncoding.WEBM_OPUS
        recog_rate = sample_rate

    config = speech.RecognitionConfig(
        encoding=recog_encoding,
        sample_rate_hertz=recog_rate,
        language_code=language_code,
        enable_automatic_punctuation=True,
        model="latest_short",
        use_enhanced=True,
        profanity_filter=False,
        max_alternatives=1,  # Only get top result for speed
    )
    streaming_config = speech.StreamingRecognitionConfig(
        config=config,
        interim_results=True,
        single_utterance=False,
    )
    
    # Generator for Google API (pulls audio from queue)
    def audio_generator():
        logger.debug("🔊 audio_generator started")
        while not done_event.is_set() or not audio_queue.empty():
            try:
                chunk = audio_queue.get(timeout=0.1)
                if chunk is not None:
                    logger.debug(f"📤 Sending audio chunk: {len(chunk)} bytes")
                    yield speech.StreamingRecognizeRequest(audio_content=chunk)
            except queue.Empty:
                continue
        logger.debug("🔊 audio_generator ended")
    
    # Background task: collect audio from async generator
    async def audio_collector():
        try:
            chunk_num = 0
            async for chunk in audio_chunks:
                if chunk is None:
                    break
                chunk_num += 1
                logger.debug(f"📥 Received audio chunk #{chunk_num}: {len(chunk)} bytes")

                if conversion_available:
                    # Convert webm/opus chunk to PCM s16le@16k with normalization + denoise using ffmpeg
                    try:
                        ffmpeg_cmd = [
                            "ffmpeg", "-hide_banner", "-loglevel", "error",
                            "-i", "pipe:0",
                            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,afftdn",
                            "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", "pipe:1"
                        ]
                        proc = await asyncio.create_subprocess_exec(
                            *ffmpeg_cmd,
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        stdout, stderr = await proc.communicate(chunk)
                        if proc.returncode != 0:
                            logger.warning(f"ffmpeg convert failed (rc={proc.returncode}): {stderr.decode(errors='ignore')}")
                            # Fallback: send raw chunk unchanged (may still work with webm/opus if client supports)
                            audio_queue.put(chunk)
                        else:
                            pcm = stdout
                            logger.debug(f"📤 Converted chunk #{chunk_num} -> PCM {len(pcm)} bytes")
                            audio_queue.put(pcm)
                    except FileNotFoundError:
                        logger.warning("ffmpeg not found; sending raw chunk to STT (may reduce accuracy)")
                        audio_queue.put(chunk)
                    except Exception as e:
                        logger.exception(f"Error converting chunk to PCM: {e}")
                        audio_queue.put(chunk)
                else:
                    # No conversion; pass raw WebM/Opus to Google STT which supports it
                    audio_queue.put(chunk)
        except Exception as e:
            logger.exception(f"Error in audio_collector: {e}")
        finally:
            done_event.set()
            logger.debug("✋ Audio collection stopped")
    
    # Background thread: run STT streaming
    def stt_thread_func():
        logger.info("🎤 STT thread started")
        try:
            logger.info("📞 Calling Google Speech-to-Text streaming_recognize...")
            responses = client.streaming_recognize(
                config=streaming_config,
                requests=audio_generator()
            )
            
            response_num = 0
            for response in responses:
                response_num += 1
                logger.debug(f"📞 Response #{response_num} received")
                
                if not response.results:
                    logger.debug("  └─ Empty results")
                    continue
                
                result = response.results[0]
                if not result.alternatives:
                    logger.debug("  └─ No alternatives")
                    continue
                
                alternative = result.alternatives[0]
                transcript = alternative.transcript.strip()
                
                if not transcript:
                    logger.debug("  └─ Empty transcript")
                    continue
                
                # Create result dict
                result_dict = {
                    "text": transcript,
                    "is_final": result.is_final,
                    "confidence": float(alternative.confidence) if result.is_final else 0.0,
                    "ok": True
                }
                
                if result.is_final:
                    logger.info(f"✅ Final: '{transcript}' (conf: {alternative.confidence:.2f})")
                else:
                    logger.info(f"🟡 Partial: '{transcript}'")
                
                result_queue.put(result_dict)
            
            logger.info(f"✋ STT stream closed after {response_num} responses")
            
        except Exception as e:
            logger.exception(f"🔴 STT error: {e}")
            result_queue.put({
                "text": "",
                "is_final": True,
                "confidence": 0.0,
                "ok": False,
                "error": str(e)
            })
        finally:
            result_queue.put(None)  # Signal end
            logger.info("🎤 STT thread ended")
    
    # Start audio collector task
    collector_task = asyncio.create_task(audio_collector())
    
    # Start STT thread
    stt_thread = threading.Thread(target=stt_thread_func, daemon=True)
    stt_thread.start()
    
    try:
        # Yield results as they arrive
        loop = asyncio.get_event_loop()
        while True:
            # Get result from queue (with timeout to avoid blocking forever)
            def blocking_get():
                try:
                    return result_queue.get(timeout=0.5)
                except queue.Empty:
                    return "__EMPTY__"
            
            result = await loop.run_in_executor(None, blocking_get)
            
            if result == "__EMPTY__":
                continue
            elif result is None:
                logger.info("🏁 STT streaming complete")
                break
            else:
                yield result
                
    except Exception as e:
        logger.exception(f"Error in transcribe_streaming: {e}")
    finally:
        # Cleanup - give the collector a bit more time to finish gracefully
        try:
            await asyncio.wait_for(collector_task, timeout=4.0)
        except:
            pass
        done_event.set()
        logger.info("🧹 transcribe_streaming cleaned up")

