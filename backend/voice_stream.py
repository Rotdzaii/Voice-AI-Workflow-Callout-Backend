"""Voice streaming WebSocket endpoint for realtime audio communication.

Handles bidirectional audio streaming:
- Receives audio chunks from client (microphone) in realtime
- Transcribes via streaming STT (partial + final results)
- Generates response via LLM when detecting end of sentence
- Synthesizes via TTS
- Sends audio back to client
"""
from fastapi import WebSocket, WebSocketDisconnect
from typing import Optional
import asyncio
import json
import logging
import time
import shutil
import os
import re
import contextlib

try:
    import webrtcvad
except Exception:
    webrtcvad = None


async def decode_to_pcm_bytes(input_bytes: bytes, sample_rate: int = 16000) -> Optional[bytes]:
    """Decode arbitrary input audio bytes (webm/opus, etc.) to 16kHz s16le PCM using ffmpeg.

    Returns raw PCM bytes or None on failure. Requires `ffmpeg` on PATH.
    """
    if not input_bytes:
        return None

    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "pipe:1",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate(input_bytes)
        if proc.returncode != 0:
            logger = logging.getLogger(__name__)
            logger.warning(f"ffmpeg decode failed (rc={proc.returncode}): {stderr.decode(errors='ignore')}")
            return None
        return stdout
    except FileNotFoundError:
        logging.getLogger(__name__).warning("ffmpeg not found on PATH; cannot decode audio for VAD")
        return None
    except Exception as e:
        logging.getLogger(__name__).exception(f"Exception while decoding audio via ffmpeg: {e}")
        return None


def check_vad_on_pcm(pcm_bytes: bytes, aggressiveness: int = 2, frame_ms: int = 30, sample_rate: int = 16000, min_speech_frames: int = 1) -> bool:
    """Run webrtcvad on raw PCM bytes and return True if speech is detected.

    pcm_bytes should be s16le mono at `sample_rate`.
    """
    if webrtcvad is None:
        raise RuntimeError("webrtcvad is not available")

    vad = webrtcvad.Vad(aggressiveness)
    bytes_per_sample = 2
    frame_size = int(sample_rate * (frame_ms / 1000.0) * bytes_per_sample)
    if frame_size <= 0:
        raise ValueError("invalid frame size")

    # iterate frames and count speech frames
    speech_frames = 0
    for start in range(0, len(pcm_bytes), frame_size):
        frame = pcm_bytes[start:start + frame_size]
        if len(frame) < frame_size:
            break
        try:
            if vad.is_speech(frame, sample_rate):
                speech_frames += 1
                if speech_frames >= min_speech_frames:
                    return True
        except Exception:
            continue

    return False

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Force INFO level for debugging

# Simple cache for vectorstore search results (avoid repeated searches)
_search_cache = {}
_cache_max_size = 50
SILENCE_DB_THRESHOLD = -35.0
# Require a longer stable low-dB window before treating as silence
SILENCE_DB_MIN_DURATION = 1.0

# filler words (Vietnamese common fillers) to filter out when deciding if user said something meaningful
FILLERS = {"ạ", "ừ", "ừm", "à", "ờ", "hmm", "ưm", "ừ...", "ờm", "ạ...", "ờ...", "ồ", "hơ"}


def is_sentence_end(text: str) -> bool:
    """Heuristic to decide if a final transcript likely ends a sentence.

    Uses punctuation and minimum word count to avoid cutting mid-sentence.
    """
    if not text:
        return False
    stripped = text.strip()
    # If ends with '?' then cut immediately (questions are usually short)
    if re.search(r"\?\s*$", stripped):
        return True
    # Otherwise require some content to avoid short fillers
    if len(stripped.split()) < 4:
        return False
    return bool(re.search(r"[\.!?…]+\s*$", stripped))


def remove_fillers_and_normalize(text: str) -> str:
    """Remove common filler words and normalize whitespace/punctuation."""
    if not text:
        return ""
    # Lowercase and remove punctuation except unicode letters and spaces
    txt = text.lower()
    # Replace punctuation with spaces
    # remove punctuation/symbols
    txt = re.sub(r"[^\w\s]", " ", txt, flags=re.UNICODE)
    # simple tokenization by whitespace
    tokens = [t.strip() for t in txt.split() if t.strip()]
    # filter fillers
    tokens = [t for t in tokens if t not in FILLERS]
    return " ".join(tokens).strip()


class VoiceSession:
    """Manages a single voice call session with conversation context."""
    
    def __init__(self, websocket: WebSocket, call_id: Optional[str] = None):
        self.websocket = websocket
        self.call_id = call_id
        self.conversation_history = []
        self.audio_buffer = bytearray()
        self.last_confidence = 0.0
        self.is_active = True
        self.current_transcript = ""  # Accumulate partial transcripts
        self.accumulated_final_text = ""  # Store final transcript for end_audio trigger
        self.is_processing_response = False  # Flag to prevent overlapping responses
        self.pending_final_text = ""  # Queue next user text if it arrives while processing
        self.history_loaded = False  # Track if we've loaded previous messages
        self.current_listening_start = None  # Timestamp when current question began
        self.silence_db_low_since = None
        self.silence_db_triggered = False
        self.silence_db_waiting = False
        # accumulate raw webm/opus bytes for VAD checks when needed
        self.audio_buffer = bytearray()
        
    async def load_previous_messages(self, db_pool):
        """Load previous conversation messages from database."""
        if not self.call_id or self.history_loaded:
            return
        
        try:
            async with db_pool.acquire() as conn:
                # Load last 10 messages from conversation_logs
                rows = await conn.fetch(
                    "SELECT speaker, text FROM conversation_logs WHERE call_id=$1 ORDER BY created_at DESC LIMIT 10",
                    self.call_id
                )
                # Reverse to get chronological order
                for row in reversed(rows):
                    self.conversation_history.append({"role": row["speaker"], "text": row["text"]})
                
                if rows:
                    logger.info(f"📚 Loaded {len(rows)} previous messages from database")
            
            self.history_loaded = True
        except Exception as e:
            logger.warning(f"⚠️ Failed to load previous messages: {e}")
            self.history_loaded = True  # Don't retry
        
    async def send_json(self, data: dict):
        """Send JSON message to client."""
        try:
            if self.websocket.client_state.name != "CONNECTED":
                logger.error(f"❌ SEND_JSON FAILED: WebSocket not connected (state={self.websocket.client_state.name})")
                logger.error(f"❌ Message that failed to send: {data}")
                return
            logger.debug(f"📤 send_json: {data.get('type', 'unknown')} - {str(data)[:100]}")
            await self.websocket.send_json(data)
            logger.debug(f"✅ send_json: {data.get('type', 'unknown')} sent successfully")
        except Exception as e:
            logger.error(f"❌ SEND_JSON EXCEPTION: {e}")
            logger.error(f"❌ Message that failed: {data}")
            self.is_active = False
    
    async def send_audio(self, audio_bytes: bytes):
        """Send audio chunk to client."""
        try:
            if self.websocket.client_state.name != "CONNECTED":
                logger.warning(f"⚠️ WebSocket not connected (state={self.websocket.client_state.name}), skipping audio")
                return
            await self.websocket.send_bytes(audio_bytes)
        except Exception as e:
            logger.debug(f"Failed to send audio: {e}")
            self.is_active = False
    
    def add_to_history(self, role: str, text: str):
        """Add message to conversation history."""
        self.conversation_history.append({"role": role, "text": text})
        # Keep last 20 messages in memory for context (more than before since we have DB)
        if len(self.conversation_history) > 20:
            self.conversation_history = self.conversation_history[-20:]


async def _run_llm_task(session: VoiceSession, text_to_process: str, reason_label: str):
    try:
        logger.info(f"🚀 BACKGROUND_TASK ({reason_label}): Started processing '{text_to_process}'")
        await process_llm_and_tts(session, text_to_process)
        logger.info("✅ BACKGROUND_TASK: Completed successfully")
    except asyncio.CancelledError:
        logger.info("⏸️ BACKGROUND_TASK: Cancelled")
        raise
    except Exception as e:
        logger.exception(f"❌ BACKGROUND_TASK: Error - {e}")
        session.is_processing_response = False
        try:
            await session.send_json({
                "type": "error",
                "message": f"Lỗi xử lý: {str(e)}"
            })
        except:
            logger.debug("Could not send error to client")


async def initiate_processing(session: VoiceSession, reason_label: str):
    text_to_process = session.accumulated_final_text
    if text_to_process and not session.is_processing_response:
        logger.info(f"✅ INITIATE_PROCESSING ({reason_label}): Will process '{text_to_process}'")
        session.is_processing_response = True
        session.silence_db_waiting = False
        await session.send_json({
            "type": "processing_started",
            "message": "🧠 Đang suy nghĩ..."
        })
        asyncio.create_task(_run_llm_task(session, text_to_process, reason_label))
        return True
    if reason_label == "silence_db":
        session.silence_db_waiting = True
    return False


async def handle_audio_stream(session: VoiceSession):
    """Process streaming audio: STT -> LLM -> TTS in realtime."""
    from . import stt_streaming, rag_mitek, db
    
    logger.info("="*60)
    logger.info("🎙️ HANDLE_AUDIO_STREAM STARTED")
    logger.info("="*60)
    
    # Create async queue for audio chunks
    audio_queue = asyncio.Queue()
    stt_task = None
    audio_chunk_count = 0
    first_audio_received = False  # Track if first audio chunk arrived
    last_audio_time = time.time()
    
    async def audio_generator():
        """Generate audio chunks from queue."""
        nonlocal audio_chunk_count
        logger.info("📥 AUDIO_GENERATOR: Started")
        logger.info(f"📥 AUDIO_GENERATOR: Generator object id={id(audio_generator)}")
        chunk_received = 0
        try:
            while session.is_active:
                try:
                    logger.debug(f"📥 AUDIO_GENERATOR: Waiting for chunk (queue_size={audio_queue.qsize()})...")
                    chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.5)
                    if chunk is None:  # Sentinel to end STT stream
                        logger.info("📥 AUDIO_GENERATOR: Received sentinel (None), ending")
                        break
                    audio_chunk_count += 1
                    chunk_received += 1
                    logger.debug(f"📥 AUDIO_GENERATOR: Yielding chunk #{audio_chunk_count} ({len(chunk)} bytes)")
                    yield chunk
                except asyncio.TimeoutError:
                    logger.debug(f"📥 AUDIO_GENERATOR: Queue timeout (queue_size={audio_queue.qsize()}), continuing...")
                    continue
        except Exception as e:
            logger.exception(f"❌ AUDIO_GENERATOR: Exception: {e}")
            raise
        finally:
            logger.info(f"📥 AUDIO_GENERATOR: Ended after {chunk_received} chunks yielded, {audio_chunk_count} total")
            logger.info(f"📥 AUDIO_GENERATOR: session.is_active={session.is_active}, queue_size={audio_queue.qsize()}")
    
    def restart_stt_stream():
        """Restart STT stream for next question."""
        nonlocal stt_task
        logger.info("🔄 RESTART_STT_STREAM: Starting new STT stream...")
        if stt_task:
            try:
                stt_task.cancel()
                logger.info("🔄 RESTART_STT_STREAM: Cancelled old STT task")
            except:
                pass
        # Create new generator and task
        stt_task = asyncio.create_task(process_stt_stream(session, audio_generator()))
        logger.info("🔄 RESTART_STT_STREAM: New STT task created")
    
    # DON'T start STT yet - wait for first audio chunk to arrive
    # This prevents Google STT timeout when client hasn't started recording yet
    logger.info("🎙️ HANDLE_AUDIO_STREAM: Waiting for first audio chunk before starting STT...")
    
    try:
        # Main loop: receive audio chunks from client
        logger.info("🎙️ HANDLE_AUDIO_STREAM: Entering main receive loop (waiting for first audio chunk)")
        logger.info(f"🔍 DEBUG: session.is_active={session.is_active}, first_audio_received={first_audio_received}")
        message_count = 0
        last_message_time = time.time()
        while session.is_active:
            try:
                logger.info(f"🔄 LOOP #{message_count+1}: Waiting for WebSocket message... (first_audio_received={first_audio_received})")
                message = await asyncio.wait_for(session.websocket.receive(), timeout=30.0)
                message_count += 1
                current_time = time.time()
                time_since_last = current_time - last_message_time
                last_message_time = current_time
                
                logger.info(f"📬 MESSAGE #{message_count}: Received after {time_since_last:.2f}s")
                logger.info(f"📬 MESSAGE #{message_count} keys: {list(message.keys())}")
                logger.info(f"📬 MESSAGE #{message_count} type: {'BYTES' if 'bytes' in message else 'TEXT' if 'text' in message else 'OTHER'}")
                
                if "bytes" in message:
                    # Audio chunk received - add to queue for STT
                    audio_data = message["bytes"]
                    last_audio_time = time.time()
                    # Append to session-level buffer for possible VAD checks later
                    try:
                        session.audio_buffer.extend(audio_data)
                    except Exception:
                        # if extend fails, reset buffer
                        session.audio_buffer = bytearray(audio_data)
                    logger.info(f"✅ AUDIO CHUNK #{message_count}: {len(audio_data)} bytes received! (first_audio_received={first_audio_received}, stt_task={stt_task}, stt_done={stt_task.done() if stt_task else 'N/A'})")

                    if session.current_listening_start is None:
                        session.current_listening_start = time.time()
                        logger.info(f"🎧 Recorded listening start time: {session.current_listening_start}")
                    
                    # Start STT on first audio chunk OR if previous task is done (auto-restart)
                    if not first_audio_received or (stt_task and stt_task.done()):
                        if stt_task and stt_task.done():
                            logger.warning("⚠️ STT task was done but receiving audio - auto-restarting!")
                            first_audio_received = False  # Force reset
                        
                        logger.info("="*60)
                        logger.info("✅ FIRST AUDIO RECEIVED - Starting new STT stream now!")
                        logger.info("="*60)
                        first_audio_received = True
                        logger.info("📤 Creating new audio_generator()")
                        gen = audio_generator()
                        logger.info(f"📤 Created audio_generator: {gen}")
                        stt_task = asyncio.create_task(process_stt_stream(session, gen))
                        logger.info(f"✅ New STT task created: {stt_task}")
                    else:
                        logger.info(f"🔄 Audio chunk added to queue (STT already running, task_id={stt_task.get_name() if stt_task else 'N/A'})")
                    
                    await audio_queue.put(audio_data)
                    logger.info(f"✅ AUDIO CHUNK #{message_count}: Added to queue (queue_size={audio_queue.qsize()})")
                    
                elif "text" in message:
                    # JSON command
                    try:
                        data = json.loads(message["text"])
                        cmd = data.get("type")
                        logger.info(f"📝 JSON COMMAND: {message['text']}")
                        logger.info(f"🎯 COMMAND TYPE: '{cmd}'")
                        
                        if cmd == "start_audio":
                            # Client is about to start new audio stream
                            logger.info("="*80)
                            logger.info("🎙️ START_AUDIO RECEIVED!")
                            logger.info(f"🔍 Current state: first_audio_received={first_audio_received}, stt_task={'exists' if stt_task else 'None'}, stt_task.done()={stt_task.done() if stt_task else 'N/A'}")
                            logger.info("🔄 Resetting first_audio_received to False")
                            first_audio_received = False
                            
                            # Cancel old STT task if exists - WAIT PROPERLY for it to complete
                            if stt_task and not stt_task.done():
                                logger.info("🛑 Cancelling old STT task...")
                                stt_task.cancel()
                                try:
                                    # Wait a bit longer for the STT task to shutdown cleanly
                                    result = await asyncio.wait_for(stt_task, timeout=4.0)
                                    logger.info(f"✅ Old STT task finished: {result}")
                                except asyncio.CancelledError:
                                    logger.info("✅ Old STT task cancelled (CancelledError)")
                                except asyncio.TimeoutError:
                                    logger.warning("⚠️ Old STT task did not respond to cancel in 4s - may still be running")
                                except Exception as e:
                                    logger.warning(f"⚠️ Old STT task exception: {type(e).__name__}: {e}")
                            else:
                                logger.info(f"ℹ️ No active STT task to cancel (stt_task={stt_task}, done={stt_task.done() if stt_task else 'N/A'})")
                            
                            # Clear audio queue to prevent old audio from being processed
                            logger.info("🧹 Clearing audio queue...")
                            cleared_count = 0
                            queue_size = audio_queue.qsize()
                            while not audio_queue.empty():
                                try:
                                    audio_queue.get_nowait()
                                    cleared_count += 1
                                except asyncio.QueueEmpty:
                                    break
                            logger.info(f"✅ Audio queue cleared: {cleared_count} items removed (queue_size_before={queue_size})")
                            
                            # Reset accumulated text for new question
                            session.accumulated_final_text = ""
                            logger.info("🔄 Reset accumulated_final_text for new question")

                            # Reset any pending text
                            session.pending_final_text = ""
                            logger.info("🔄 Reset pending_final_text for new question")
                            
                            # Reset is_processing_response flag
                            session.is_processing_response = False
                            logger.info("🔄 Reset is_processing_response flag")
                            session.current_listening_start = None
                            logger.info("🔄 Reset listening timer for new question")
                            session.silence_db_low_since = None
                            session.silence_db_triggered = False
                            session.silence_db_waiting = False
                            
                            # Reset stt_task to None so audio_chunk handler will create new one
                            stt_task = None
                            logger.info("🔄 Reset stt_task to None (will be recreated on first audio chunk)")
                            
                            logger.info("🎉 Ready for new audio stream!")
                            logger.info("="*80)
                            
                        elif cmd == "end_audio":
                            # Client stopped speaking - trigger processing with grace + filtering
                            logger.info("🎙️ END_AUDIO: Received end_audio signal")
                            logger.info(f"🎙️ END_AUDIO: accumulated_final_text='{session.accumulated_final_text}', is_processing={session.is_processing_response}")

                            # Check if connection is still active
                            if not session.is_active:
                                logger.info("🎙️ END_AUDIO: Session already inactive, ignoring")
                                continue

                            # Cancel any pending silence_task (we're ending manually, not waiting for silence)
                            if hasattr(session, 'silence_task') and session.silence_task and not session.silence_task.done():
                                logger.info("🎙️ END_AUDIO: Cancelling silence_task to trigger processing now")
                                session.silence_task.cancel()
                            session.silence_db_waiting = False

                            # Grace delay to allow late partials/finals to arrive (avoid cutting short)
                            grace_seconds = 0.5
                            logger.info(f"🎙️ END_AUDIO: Waiting {grace_seconds}s grace for late partials")
                            try:
                                await asyncio.sleep(grace_seconds)
                            except asyncio.CancelledError:
                                logger.info("🎙️ END_AUDIO: Grace sleep cancelled")

                            # If we already have final transcript text, apply filler/confidence checks
                            if session.accumulated_final_text:
                                text = session.accumulated_final_text.strip()
                                confidence = getattr(session, "last_confidence", 0.0) or 0.0
                                clean = remove_fillers_and_normalize(text)
                                word_count = len(clean.split()) if clean else 0
                                logger.info(f"🎙️ END_AUDIO: Post-grace text='{text}' clean='{clean}' words={word_count} confidence={confidence:.2f}")

                                # Thresholds
                                # Tighten thresholds to avoid reacting to short fillers / low-confidence transcriptions
                                MIN_WORDS = 3
                                MIN_CONFIDENCE = 0.45

                                if word_count < MIN_WORDS or confidence < MIN_CONFIDENCE:
                                    logger.info("ℹ️ END_AUDIO: Detected non-meaningful or low-confidence speech -> request restart")
                                    # Tell client to restart mic and clear buffers
                                    session.audio_buffer = bytearray()
                                    session.accumulated_final_text = ""
                                    session.pending_final_text = ""
                                    try:
                                        await session.send_json({"type": "ready_for_input", "message": "Nghe chưa rõ, mời bạn nói tiếp."})
                                    except Exception:
                                        logger.debug("Could not send ready_for_input to client")
                                else:
                                    # Accept and initiate processing
                                    if not await initiate_processing(session, "end_audio"):
                                        logger.warning(f"⚠️ END_AUDIO: Cannot process (accumulated='{session.accumulated_final_text}', processing={session.is_processing_response})")
                            else:
                                # No final STT text yet: perform server-side VAD check on accumulated audio
                                vad_detected = False
                                try:
                                    if webrtcvad is None:
                                        logger.warning("webrtcvad not installed; skipping server-side VAD check")
                                    else:
                                        # Decode accumulated webm/opus bytes to PCM s16le 16kHz mono using ffmpeg
                                        pcm = None
                                        try:
                                            pcm = await decode_to_pcm_bytes(bytes(session.audio_buffer or b''))
                                        except Exception as e:
                                            logger.warning(f"Could not decode audio for VAD: {e}")
                                        if pcm:
                                            try:
                                                vad_detected = check_vad_on_pcm(pcm, aggressiveness=1, min_speech_frames=2)
                                                logger.info(f"webrtcvad: speech_detected={vad_detected}")
                                            except Exception as e:
                                                logger.warning(f"VAD check error: {e}")
                                except Exception as e:
                                    logger.exception(f"Unexpected error during server-side VAD: {e}")

                                if vad_detected:
                                    # treat like normal end_audio with speech
                                    if not await initiate_processing(session, "end_audio_vad"):
                                        logger.warning("⚠️ END_AUDIO(VAD): initiate_processing returned False")
                                else:
                                    # No speech detected -> do not call LLM; reset and tell client to restart mic
                                    logger.info("ℹ️ END_AUDIO: No speech detected by VAD -> sending ready_for_input to client")
                                    session.audio_buffer = bytearray()
                                    try:
                                        await session.send_json({"type": "ready_for_input", "message": "Tôi đang lắng nghe..."})
                                    except Exception:
                                        logger.debug("Could not send ready_for_input to client")
                        elif cmd == "audio_level":
                            level = data.get("level")
                            if level is None:
                                continue
                            current_time = time.time()
                            if level < SILENCE_DB_THRESHOLD:
                                if session.silence_db_low_since is None:
                                    session.silence_db_low_since = current_time
                                elif not session.silence_db_triggered and (current_time - session.silence_db_low_since) >= SILENCE_DB_MIN_DURATION:
                                    session.silence_db_triggered = True
                                    logger.info(f"🤫 AUDIO_LEVEL: Low dB for {SILENCE_DB_MIN_DURATION}s, triggering silence_processing (level={level})")
                                    await initiate_processing(session, "silence_db")
                            else:
                                session.silence_db_low_since = None
                                session.silence_db_triggered = False
                                session.silence_db_waiting = False

                        elif session.accumulated_final_text and session.is_processing_response:
                            # Already processing; queue the new text to handle right after current response
                            session.pending_final_text = session.accumulated_final_text
                            logger.info(f"⏳ END_AUDIO: Queued pending text: '{session.pending_final_text}' (processing in progress)")
                            
                        elif cmd == "ping":
                            await session.send_json({"type": "pong"})
                            
                    except json.JSONDecodeError:
                        pass
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ TIMEOUT waiting for message (first_audio_received={first_audio_received})")
                await session.send_json({"type": "keepalive"})

                # Inactivity watchdog: if we have been receiving audio before and now
                # no audio for a while, decide how to proceed to avoid hanging sessions.
                now = time.time()
                if first_audio_received and (now - last_audio_time) >= 1.0 and not session.is_processing_response:
                    logger.info("🤫 INACTIVITY: >1s without audio after speech; triggering fallback")
                    if session.accumulated_final_text:
                        # Process what we have
                        await initiate_processing(session, "inactivity_silence")
                    else:
                        # Nothing to process: reset and prompt client to speak again
                        session.audio_buffer = bytearray()
                        session.accumulated_final_text = ""
                        session.pending_final_text = ""
                        try:
                            await session.send_json({"type": "ready_for_input", "message": "Không nghe thấy gì, mời bạn nói lại."})
                        except Exception:
                            logger.debug("Could not send ready_for_input on inactivity")
                        # Reset listening state so next audio restarts STT cleanly
                        first_audio_received = False
                        if stt_task and not stt_task.done():
                            stt_task.cancel()
                    last_audio_time = now
                continue
                
    finally:
        # Cleanup - only signal end when connection is truly closing
        session.is_active = False
        try:
            await audio_queue.put(None)
            if stt_task:
                await asyncio.wait_for(stt_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("STT task did not finish in time")
        except asyncio.CancelledError:
            logger.info("STT task was cancelled")
        except Exception as e:
            logger.exception(f"Error waiting for STT task: {e}")


async def process_stt_stream(session: VoiceSession, audio_generator):
    """Process streaming STT results and trigger LLM when appropriate."""
    from . import stt_streaming, rag_mitek, db
    
    logger.info("-"*60)
    logger.info("🎤 PROCESS_STT_STREAM: Started")
    logger.info("-"*60)
    
    last_final_time = asyncio.get_event_loop().time()
    silence_threshold = 0.3  # Chờ 0.3 giây sau final transcript
    chunk_count = 0
    final_count = 0
    silence_task = None  # Track silence detection task
    last_result_time = asyncio.get_event_loop().time()
    last_partial_text = ""

    # Inactivity watchdog: if no new results (partial or final) for 2s, endpoint with best available text
    async def inactivity_watch():
        nonlocal last_result_time, last_partial_text
        try:
            while session.is_active:
                await asyncio.sleep(0.5)
                if session.is_processing_response:
                    continue
                now = asyncio.get_event_loop().time()
                if now - last_result_time >= 2.0:
                    logger.info("⏰ STT inactivity >=2s, auto-ending stream")
                    if session.accumulated_final_text:
                        await initiate_processing(session, "inactivity_final")
                    elif last_partial_text:
                        # Promote last partial to final to avoid losing user speech
                        session.accumulated_final_text = last_partial_text
                        await initiate_processing(session, "inactivity_partial")
                    else:
                        try:
                            await session.send_json({"type": "ready_for_input", "message": "Không nghe thấy gì, mời bạn nói lại."})
                        except Exception:
                            logger.debug("Could not send ready_for_input on inactivity")
                    last_result_time = now
        except asyncio.CancelledError:
            logger.debug("Inactivity watchdog cancelled")

    watchdog_task = asyncio.create_task(inactivity_watch())
    
    try:
        logger.info("🎤 PROCESS_STT_STREAM: Calling transcribe_streaming...")
        async for result in stt_streaming.transcribe_streaming(audio_generator):
            chunk_count += 1
            last_result_time = asyncio.get_event_loop().time()
            if not result.get("ok"):
                logger.error(f"❌ STT_STREAM: Error result: {result}")
                await session.send_json({"type": "error", "message": result.get("error", "STT failed")})
                continue
            
            text = result.get("text", "").strip()
            is_final = result.get("is_final", False)
            if not is_final and text:
                last_partial_text = text
            
            if not text:
                logger.debug(f"🎤 STT_STREAM: Empty text, skipping")
                continue
            
            current_time = asyncio.get_event_loop().time()
            
            if is_final:
                final_count += 1
                # Final result: store in session
                session.accumulated_final_text = text
                # store last confidence if provided by STT
                try:
                    session.last_confidence = float(result.get("confidence", 0.0) or 0.0)
                except Exception:
                    session.last_confidence = 0.0
                last_final_time = current_time
                
                if session.silence_db_waiting and not session.is_processing_response:
                    await initiate_processing(session, "silence_db_final")

                # Context-based end-of-sentence trigger: if the final text ends with punctuation
                # and we are not already processing, start immediately without waiting for end_audio.
                if not session.is_processing_response and is_sentence_end(text):
                    logger.info("✂️ CONTEXT END: Detected sentence-ending punctuation, triggering processing")
                    if not await initiate_processing(session, "context_end"):
                        logger.warning("⚠️ CONTEXT END: initiate_processing returned False")

                logger.info(f"✅ STT_STREAM FINAL #{final_count}: '{text}'")
                
                # Send final transcript to client
                duration_ms = 0
                if session.current_listening_start:
                    duration_ms = int((time.time() - session.current_listening_start) * 1000)
                await session.send_json({
                    "type": "transcript_final",
                    "text": text,
                    "is_final": True,
                    "listening_duration_ms": duration_ms
                })

                session.current_listening_start = None
                
                # Cancel old silence task if exists
                if silence_task and not silence_task.done():
                    silence_task.cancel()
                    try:
                        await silence_task
                    except asyncio.CancelledError:
                        pass
                
                # Create new silence detection task (BACKUP only if end_audio doesn't come)
                async def auto_trigger_processing():
                    """Auto-trigger processing if no end_audio after 1.5s (backup mechanism)"""
                    try:
                        await asyncio.sleep(1.5)  # Wait 1.5s for end_audio from frontend
                        # If still not processing and text hasn't changed, trigger now
                        if session.accumulated_final_text == text and not session.is_processing_response:
                            logger.warning(f"⏰ BACKUP-TRIGGER: No end_audio received after 1.0s, auto-processing now")
                            session.is_processing_response = True
                            
                            await session.send_json({
                                "type": "processing_started",
                                "message": "🧠 Đang suy nghĩ..."
                            })
                            
                            # Create safe wrapper
                            async def safe_process():
                                try:
                                    await process_llm_and_tts(session, text)
                                except Exception as e:
                                    logger.exception(f"Error in backup trigger: {e}")
                                    session.is_processing_response = False
                            
                            asyncio.create_task(safe_process())
                        else:
                            logger.debug(f"⏰ BACKUP-TRIGGER: Cancelled (already processing or text changed)")
                    except asyncio.CancelledError:
                        logger.debug("⏰ BACKUP-TRIGGER: Cancelled (end_audio received)")
                
                silence_task = asyncio.create_task(auto_trigger_processing())
                logger.info(f"⏰ STT_STREAM: Backup trigger scheduled (1.0s) - waiting for end_audio from frontend")
                
            else:
                # Partial result: send to client for display
                logger.debug(f"🟡 STT_STREAM PARTIAL: '{text}'")
                await session.send_json({
                    "type": "transcript_partial",
                    "text": text,
                    "is_final": False
                })
        
        logger.info(f"🎤 STT_STREAM: Generator ended after {chunk_count} chunks ({final_count} final)")
                
    except asyncio.CancelledError:
        logger.info(f"🎤 STT_STREAM: Task cancelled (normal restart)")
        raise
    except Exception as e:
        logger.exception("❌ STT_STREAM: Exception occurred")
        await session.send_json({"type": "error", "message": str(e)})
    finally:
        try:
            watchdog_task.cancel()
            with contextlib.suppress(Exception):
                await watchdog_task
        except Exception:
            pass
        logger.info(f"🎤 STT_STREAM: Finally block - accumulated_text='{session.accumulated_final_text}', is_processing={session.is_processing_response}")
        # When stream ends, process any accumulated text if not already processing
        if session.accumulated_final_text and not session.is_processing_response:
            logger.info(f"🎤 STT_STREAM: Stream ended, triggering LLM for: '{session.accumulated_final_text}'")
            session.is_processing_response = True
            await process_llm_and_tts(session, session.accumulated_final_text)
        else:
            logger.info(f"🎤 STT_STREAM: Not processing (accumulated={bool(session.accumulated_final_text)}, processing={session.is_processing_response})")


async def check_silence_and_respond(session: VoiceSession, text: str, timestamp: float, threshold: float):
    """Check if user has stopped speaking and trigger response."""
    logger.info(f"⏱️ CHECK_SILENCE: Started for text='{text}' threshold={threshold}s")
    try:
        # Wait for silence threshold
        logger.info(f"⏱️ CHECK_SILENCE: Sleeping for {threshold}s...")
        await asyncio.sleep(threshold)
        
        # Check if no new final results came in (user stopped speaking)
        current_time = asyncio.get_event_loop().time()
        time_since = current_time - timestamp
        
        logger.info(f"⏱️ CHECK_SILENCE: Woke up, time_since_last_final={time_since:.2f}s, threshold={threshold}s")
        
        if current_time - timestamp >= threshold and not session.is_processing_response:
            # User has been silent, process the accumulated text
            logger.info(f"✅ CHECK_SILENCE: Silence confirmed! Processing: '{text}'")
            
            # Notify client that processing started
            await session.send_json({
                "type": "processing_started",
                "message": "🧠 Đang suy nghĩ..."
            })
            
            session.is_processing_response = True
            await process_llm_and_tts(session, text)
        else:
            logger.info(f"❌ CHECK_SILENCE: Not processing (is_processing={session.is_processing_response})")
    except asyncio.CancelledError:
        # Task was cancelled because new speech started
        logger.info("⏱️ CHECK_SILENCE: Task cancelled (new speech detected)")
        raise
    except Exception as e:
        logger.exception(f"❌ CHECK_SILENCE: Error occurred: {e}")


async def process_llm_and_tts(session: VoiceSession, user_text: str):
    """Process user text through LLM and TTS."""
    from . import rag_mitek, db
    import time
    
    logger.info("*"*60)
    logger.info(f"🚀 PROCESS_LLM_AND_TTS: STARTED for user_text='{user_text}'")
    logger.info("*"*60)
    
    try:
        start_time = time.time()
        
        # Add to conversation history
        logger.info(f"📝 PROCESS_LLM_AND_TTS: Adding to history")
        session.add_to_history("user", user_text)
        
        # Generate LLM response
        # Try to initialize Gemini LLM if available; otherwise fall back to a simple responder
        llm = None
        try:
            logger.info("🔄 PROCESS_LLM_AND_TTS: Initializing Gemini LLM...")
            import google.generativeai as genai
            from .config import GEMINI_API_KEY

            genai.configure(api_key=GEMINI_API_KEY)
            llm = genai.GenerativeModel(
                model_name="gemini-2.0-flash-exp",
                generation_config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_output_tokens": 120,
                    "top_k": 40,
                }
            )
            logger.info("✅ Gemini LLM initialized")
        except ModuleNotFoundError:
            logger.warning("⚠️ Gemini client library not installed; using local fallback for LLM responses")
            llm = None
        except Exception as e:
            logger.warning(f"⚠️ LLM init failed: {e}; using fallback")
            llm = None

        logger.info("📚 LLM: Free conversation mode with optional RAG...")

        # Try to use RAG if available (safe fallback if fails)
        context_text = ""
        is_rag_used = False
        try:
            logger.info("🔍 Attempting RAG search for context...")
            from .rag_api import search_rag_context

            # Allow a bit more time for RAG (vectorstore load) but stay bounded
            rag_result = await asyncio.wait_for(
                asyncio.to_thread(search_rag_context, user_text, top_k=3),
                timeout=4.0
            )

            if rag_result and rag_result.strip():
                context_text = rag_result
                is_rag_used = True
                logger.info(f"✅ RAG context found: {context_text[:120]}...")
            else:
                logger.info("📄 No relevant RAG context, using general knowledge")
        except asyncio.TimeoutError:
            logger.warning("⏱️ RAG search timeout (>4.0s), using general knowledge")
        except Exception as e:
            logger.warning(f"⚠️ RAG search failed: {e}, using general knowledge")

        # Build conversation context from history
        history_items = session.conversation_history[-2:] if session.conversation_history else []
        context_msgs = "\n".join([f"{m['role']}: {m['text']}" for m in history_items])

        # Generate prompt - can chat freely, RAG context is optional enhancement
        system_role = "Bạn là một trợ lý AI thân thiện, trả lời mọi câu hỏi của người dùng một cách tự nhiên và hữu ích."

        # Build prompt from context (avoid complex triple-quoted f-strings)
        parts = [system_role]
        if context_text:
            parts.append("Thông tin tham khảo: " + context_text)
        if context_msgs:
            parts.append("Lịch sử hội thoại:\n" + context_msgs)
        parts.append("Câu hỏi: " + user_text)
        parts.append("Hãy trả lời ngắn gọn, thân thiện (1-2 câu).")
        prompt = "\n\n".join(parts)

        # Attempt to generate using the configured Gemini model; if not available use a lightweight fallback
        if llm is not None:
            try:
                # Some Gemini client libraries expose different call signatures; attempt a common one
                response = llm.generate(prompt=prompt, max_output_tokens=120)
                ai_text = ""
                if hasattr(response, 'candidates') and response.candidates:
                    candidate = response.candidates[0]
                    ai_text = getattr(candidate, 'content', None) or getattr(candidate, 'output', None) or str(candidate)
                elif hasattr(response, 'text'):
                    ai_text = response.text.strip()
                else:
                    ai_text = str(response).strip()

                if not ai_text:
                    ai_text = "Xin lỗi, tôi không nhận được câu trả lời từ LLM."
            except Exception as e:
                logger.warning(f"⚠️ LLM generation failed at runtime: {e}")
                # If RAG context is available, still return it instead of a generic apology
                if context_text:
                    snippet = context_text.strip()
                    if len(snippet) > 240:
                        snippet = snippet[:237] + "..."
                    ai_text = f"Theo tài liệu: {snippet}"
                else:
                    ai_text = f"Xin lỗi, tôi không thể trả lời ngay bây giờ. Bạn vừa hỏi: {user_text}"
        else:
            # Fallback without external LLM: use RAG context if available, otherwise echo
            if context_text:
                snippet = context_text.strip()
                if len(snippet) > 240:
                    snippet = snippet[:237] + "..."
                ai_text = f"Theo tài liệu: {snippet}"
            else:
                short = user_text.strip()
                if len(short) > 120:
                    short = short[:117] + "..."
                ai_text = f"Mình nghe được: '{short}'. Mình sẽ trả lời sau nếu cần chi tiết."

        logger.info(f"✅ LLM: Generated response: {ai_text}")

        # Send context info to client for debugging (non-fatal)
        try:
            await session.send_json({
                "type": "debug_info",
                "context_preview": context_text[:100] if context_text else "General knowledge",
                "is_rag_used": is_rag_used
            })
        except Exception:
            logger.debug("⚠️ Failed to send debug_info to client (socket may be closed)")
            
        except Exception as e:
            logger.exception("❌ LLM failed, using fallback")
            ai_text = f"Xin lỗi, tôi không hiểu câu hỏi: {user_text}"
        
        # Send response text to client (protect against disconnected websockets)
        logger.info(f"📤 SENDING RESPONSE TO CLIENT: '{ai_text[:100]}...'")
        try:
            logger.info(f"📤 WebSocket state: {getattr(session.websocket, 'client_state', 'UNKNOWN')}")
            await session.send_json({"type": "response", "text": ai_text, "speaker": "agent"})
        except Exception:
            logger.warning("❌ Could not send response to client (socket closed)")
        logger.info("✅ Response sent successfully")
        
        # Add to conversation history
        session.add_to_history("agent", ai_text)
        
        # TTS: Synthesize speech
        try:
            logger.info("🔊 TTS: Synthesizing speech...")
            audio_bytes, audio_path, latency = rag_mitek.synthesize_speech(ai_text)
            if audio_bytes:
                logger.info(f"✅ TTS: Audio synthesized ({latency}ms)")
                # Send audio back to client
                await session.send_audio(audio_bytes)
                await session.send_json({"type": "audio_ready", "latency": latency})
            else:
                logger.error("❌ TTS: No audio bytes generated")
                await session.send_json({"type": "error", "message": "TTS unavailable"})
        except Exception as e:
            logger.exception("❌ TTS failed")
            await session.send_json({"type": "error", "message": f"TTS error: {e}"})
        
        # Log to database if call_id exists
        if session.call_id:
            try:
                pool = await db.get_pool()
                async with pool.acquire() as conn:
                    # Log user message
                    await conn.execute(
                        "INSERT INTO conversation_logs(call_id, speaker, text, created_at) VALUES($1, $2, $3, NOW())",
                        session.call_id, "user", user_text
                    )
                    # Log agent response
                    await conn.execute(
                        "INSERT INTO conversation_logs(call_id, speaker, text, created_at) VALUES($1, $2, $3, NOW())",
                        session.call_id, "agent", ai_text
                    )
            except Exception as e:
                logger.warning(f"Failed to log conversation: {e}")
        
        elapsed = time.time() - start_time
        logger.info(f"✅ DONE: Full response took {elapsed:.2f}s")
        
        # Don't reset accumulated_final_text here - let finally block check it
                
    except Exception as e:
        logger.exception("❌ Error processing LLM/TTS")
        await session.send_json({"type": "error", "message": str(e)})
    finally:
        session.is_processing_response = False
        logger.info(f"✅ PROCESS_LLM_AND_TTS: Finally block - resetting is_processing_response")
        
        # Reset accumulated text to prevent re-processing
        session.accumulated_final_text = ""
        logger.info(f"🔄 PROCESS_LLM_AND_TTS: Reset accumulated_final_text")

        # If we have queued text, process it immediately (sequentially)
        if session.pending_final_text:
            queued_text = session.pending_final_text
            session.pending_final_text = ""
            logger.info(f"🚀 PROCESS_LLM_AND_TTS: Detected pending text, processing next: '{queued_text}'")
            session.is_processing_response = True
            await process_llm_and_tts(session, queued_text)
            return
        
        # Notify client that AI is ready to listen again
        await session.send_json({"type": "ready_for_input", "message": "Tôi đang lắng nghe..."})


async def handle_audio_chunk(session: VoiceSession, audio_data: bytes):
    """Legacy handler for non-streaming mode (single audio file upload)."""
    from . import stt, rag_mitek, db
    
    try:
        # 1. STT: Transcribe audio
        result = stt.transcribe_bytes(audio_data)
        if not result.get("ok"):
            await session.send_json({"type": "error", "message": "STT failed"})
            return
        
        user_text = result.get("text", "").strip()
        if not user_text:
            return  # Silence or noise
        
        logger.info(f"User said: {user_text}")
        await session.send_json({"type": "transcript", "text": user_text, "speaker": "user"})
        
        # Add to conversation history
        session.add_to_history("user", user_text)
        
        # 2. LLM: Generate response with context
        try:
            from .rag_api import _ensure_rag
            _ensure_rag()
            from .rag_api import _RAG
            
            llm = _RAG["llm"]
            vectorstore = _RAG["vectorstore"]
            
            if not llm or not vectorstore:
                raise RuntimeError("RAG not initialized")
            
            # Build context from history
            context_msgs = "\n".join([f"{m['role']}: {m['text']}" for m in session.conversation_history[-5:]])
            
            # Retrieve relevant docs
            docs = vectorstore.similarity_search(user_text, k=3)
            context_text = "\n\n".join([d.page_content for d in docs])
            
            # Generate prompt
            prompt = f"""Bạn là trợ lý AI của MITEK. Dựa trên thông tin sau:

{context_text}

Lịch sử hội thoại:
{context_msgs}

Câu hỏi mới: {user_text}

Trả lời ngắn gọn, tự nhiên như đang nói chuyện (1-2 câu):"""
            
            response = llm.invoke(prompt)
            ai_text = response.content.strip() if hasattr(response, 'content') else str(response).strip()
            
        except Exception as e:
            logger.exception("LLM failed, using fallback")
            ai_text = f"Xin lỗi, tôi không hiểu câu hỏi: {user_text}"
        
        logger.info(f"AI responds: {ai_text}")
        await session.send_json({"type": "response", "text": ai_text, "speaker": "agent"})
        
        # Add to conversation history
        session.add_to_history("agent", ai_text)
        
        # 3. TTS: Synthesize speech
        try:
            audio_bytes, audio_path, latency = rag_mitek.synthesize_speech(ai_text)
            if audio_bytes:
                # Send audio back to client
                await session.send_audio(audio_bytes)
                await session.send_json({"type": "audio_ready", "latency": latency})
            else:
                await session.send_json({"type": "error", "message": "TTS unavailable"})
        except Exception as e:
            logger.exception("TTS failed")
            await session.send_json({"type": "error", "message": f"TTS error: {e}"})
        
        # 4. Log to database if call_id exists
        if session.call_id:
            try:
                pool = await db.get_pool()
                async with pool.acquire() as conn:
                    # Log user message
                    await conn.execute(
                        "INSERT INTO conversation_logs(call_id, speaker, text, created_at) VALUES($1, $2, $3, NOW())",
                        session.call_id, "user", user_text
                    )
                    # Log agent response
                    await conn.execute(
                        "INSERT INTO conversation_logs(call_id, speaker, text, created_at) VALUES($1, $2, $3, NOW())",
                        session.call_id, "agent", ai_text
                    )
            except Exception as e:
                logger.warning(f"Failed to log conversation: {e}")
                
    except Exception as e:
        logger.exception("Error processing audio chunk")
        await session.send_json({"type": "error", "message": str(e)})


async def send_greeting(session: VoiceSession):
    """Send automatic greeting when connection is established."""
    try:
        logger.info("🎤 Sending automatic greeting...")
        
        greeting_text = "Xin chào! Tôi là trợ lý AI của bạn. Hãy nói câu hỏi của bạn."
        
        # Add to conversation history
        session.add_to_history("agent", greeting_text)
        
        # Send greeting text IMMEDIATELY (don't wait for TTS)
        await session.send_json({"type": "response", "text": greeting_text, "speaker": "agent"})
        logger.info("📤 Greeting text sent immediately")
        
        # Notify client ready to listen immediately
        await session.send_json({"type": "ready_for_input", "message": "Hãy nói câu hỏi của bạn...", "is_greeting": True})
        logger.info("📤 Ready for input sent")
        
        # TTS: Synthesize greeting in background (non-blocking)
        async def synthesize_greeting_background():
            try:
                logger.info("🔊 TTS: Synthesizing greeting (background)...")
                from . import rag_mitek
                import concurrent.futures
                loop = asyncio.get_event_loop()
                
                # Run blocking TTS in executor
                audio_bytes, audio_path, latency = await loop.run_in_executor(
                    None,
                    rag_mitek.synthesize_speech,
                    greeting_text
                )
                
                if audio_bytes:
                    logger.info(f"✅ TTS: Greeting synthesized ({latency}ms)")
                    # Send audio back to client
                    await session.send_audio(audio_bytes)
                    await session.send_json({"type": "audio_ready", "latency": latency, "is_greeting": True})
                else:
                    logger.error("❌ TTS: No audio bytes for greeting")
            except Exception as e:
                logger.exception("❌ TTS greeting failed")
        
        # Create background task for TTS
        asyncio.create_task(synthesize_greeting_background())
        logger.info("✅ Greeting sent successfully (TTS in background)")
        
    except Exception as e:
        logger.exception(f"Error sending greeting: {e}")


async def voice_stream_handler(websocket: WebSocket, call_id: Optional[str] = None):
    """Main WebSocket handler for voice streaming."""
    from . import db
    
    await websocket.accept()
    session = VoiceSession(websocket, call_id)
    
    try:
        await session.send_json({"type": "connected", "call_id": call_id, "mode": "streaming"})
        logger.info(f"Voice session started (STREAMING): call_id={call_id}")
        
        # Load previous messages from database if call_id exists
        if call_id:
            try:
                pool = await db.get_pool()
                await session.load_previous_messages(pool)
            except Exception as e:
                logger.warning(f"⚠️ Could not load previous messages: {e}")
        
        # Send greeting message first
        await send_greeting(session)
        
        # Use streaming mode
        await handle_audio_stream(session)
        
    except WebSocketDisconnect:
        logger.info(f"Client disconnected: call_id={call_id}")
    except Exception as e:
        logger.exception(f"Voice session error: {e}")
        try:
            await session.send_json({"type": "error", "message": str(e)})
        except:
            pass
    finally:
        session.is_active = False
        logger.info(f"Voice session ended: call_id={call_id}")
