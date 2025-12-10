# Testing Q3+ STT Fixes

## Summary of Changes

### 1. WebSocket Connection Timing Fix (FRONTEND)
**Problem**: "WebSocket not ready: state=undefined" error when clicking Record button
**Fix**: Added WebSocket.OPEN state check in btnRecord.onclick handler
**File**: `frontend/voice_ai.html` (lines 1093-1120)
**Change**: 
- Check `ws.readyState === WebSocket.OPEN` before starting recording
- Show error message if WebSocket not ready: "Kết nối chưa sẵn sàng..."
- Display WebSocket state in debug panel

### 2. Enhanced STT Stream Restart (BACKEND)
**Problem**: Q3 STT not restarting properly after start_audio signal
**File**: `backend/voice_stream.py` (lines 195-252)
**Changes**:
- Better exception handling in start_audio handler - properly wait for old STT task cancellation
- Reset `stt_task = None` to force recreation on next audio chunk
- Enhanced logging to track generator creation and task lifecycle
- Added timeout and error handling for task cancellation

### 3. Enhanced Audio Generator Logging (BACKEND)
**Problem**: Hard to debug if audio_generator stops yielding chunks
**File**: `backend/voice_stream.py` (lines 107-137)
**Changes**:
- Added logging to track chunks yielded vs chunks received
- Log generator object ID for debugging task lifecycle
- Show queue timeouts and queue sizes
- Track session.is_active state in finally block

### 4. Improved Audio Chunk Handler Logging (BACKEND)
**Problem**: Hard to see if first audio creates STT task properly
**File**: `backend/voice_stream.py` (lines 172-194)
**Changes**:
- Show stt_task status before and after creation
- Log generator object for correlation
- Show queue size after adding chunk

### 5. Enhanced btnStop Handler (FRONTEND)
**Problem**: No guarantee end_audio sent when user clicks Stop
**File**: `frontend/voice_ai.html` (lines 1264-1275)
**Changes**:
- Added redundant end_audio send in btnStop.onclick
- Better logging with timestamps
- Fail-safe: even if mediaRecorder.onstop doesn't send, btnStop will

## Test Procedure

### Prerequisites
1. Backend running: `python main.py`
2. Frontend loaded: `http://localhost:8000/static/voice_ai.html`
3. Browser console open (F12)
4. Browser network tab open to see WebSocket connection

### Test Steps

#### Test 1: WebSocket Ready Check
1. Open page
2. Look at debug panel - should show "UNKNOWN" or "CLOSED" initially
3. Click Connect button
4. Observe:
   - Debug panel updates: "🟢 Đã kết nối" (green)
   - WebSocket status shows "OPEN"
   - btnRecord becomes enabled
5. **Expected**: If you manually click Record before Connect button works, should see error "Kết nối chưa sẵn sàng"

#### Test 2: Q1-Q2 Multi-Turn (baseline - should already work)
1. Click Record button
2. Say: "Xin chào" (Hello)
3. Wait for AI response
4. Check console logs for:
   - "✅ FIRST AUDIO RECEIVED"
   - "📤 start_audio signal sent to server"
   - "✅ STT task created"
   - "✅ STT_STREAM FINAL"
   - "📤 end_audio sent immediately on transcript_final"
   - AI responds

5. Click Record again (for Q2)
6. Observe in console:
   - "🎙️ START_AUDIO RECEIVED!"
   - "✅ Old STT task cancelled"
   - "🧹 Audio queue cleared"
   - "✅ FIRST AUDIO RECEIVED" (new STT task created)

#### Test 3: Q1-Q2-Q3 Extended Multi-Turn (tests STT restart)
1. Repeat Test 2 steps for Q1 and Q2
2. **For Q3**, watch console logs carefully:
   - Should see: "🎙️ START_AUDIO RECEIVED!" (from clicking Record)
   - Should see: "🛑 Cancelling old STT task..." 
   - Should see: "✅ Old STT task cancelled"
   - Should see: "🧹 Audio queue cleared"
   - When speaking: "✅ FIRST AUDIO RECEIVED - Starting new STT stream now!"
   - Should see: "📤 Creating new audio_generator()"
   - Should see: "✅ New STT task created"
   - Should see: "✅ STT_STREAM FINAL" with your speech text
   - AI should respond

3. If Q3 fails, check console for:
   - Is `start_audio` being sent? (search for "📤 start_audio signal sent")
   - Is first audio chunk arriving? (search for "✅ FIRST AUDIO RECEIVED")
   - Is new generator created? (search for "📤 Created audio_generator")
   - Is new STT task created? (search for "✅ New STT task created")

#### Test 4: Q4-Q5 (stress test)
1. Repeat Q3 procedure for Q4 and Q5
2. All should work identically to Q3
3. If fails at specific question number, note it in logs

### Key Log Patterns to Look For

**Success Pattern**:
```
📤 start_audio signal sent to server
🎙️ START_AUDIO RECEIVED!
✅ Old STT task cancelled
🧹 Audio queue cleared
[Audio chunk arrives...]
✅ FIRST AUDIO RECEIVED - Starting new STT stream now!
📤 Creating new audio_generator()
✅ STT_STREAM FINAL
📤 end_audio sent
🚀 BACKGROUND_TASK: Started processing
[LLM response + TTS...]
✅ ready_for_input
```

**Failure Pattern (Q3 doesn't hear)**:
```
📤 start_audio signal sent to server
🎙️ START_AUDIO RECEIVED!
[Missing: "✅ FIRST AUDIO RECEIVED"]
[Audio chunks in queue but never processed]
[Nothing sent to STT API]
```

### Debug Variables to Check

In browser console, type:
- `ws.readyState` - should be `1` (OPEN)
- `mediaRecorder.state` - should be `"recording"` or `"inactive"`
- `audioContext.state` - should be `"running"` or `"closed"`
- `audio_queue.qsize()` - shows pending chunks (check backend logs)

### If Q3 Still Fails

1. **Collect full logs**:
   - Copy all console.log output (Ctrl+L in console to select all)
   - Copy backend terminal output
   - Note exact question number where it fails

2. **Check specific timeouts**:
   - Add temporary log point in start_audio handler
   - Check if old task cancellation timeout (2.0s) is being hit

3. **Test audio generation manually**:
   - In browser console: `mediaRecorder.state`
   - In backend logs: search for "AUDIO CHUNK" - count chunks received

4. **Check Google STT API**:
   - Look for "STT error" in logs
   - Check if API credentials are working
   - Try Q1 with different longer text

## Expected Behavior After Fixes

1. ✅ WebSocket connection ready before recording starts
2. ✅ Q1 works (baseline)
3. ✅ Q2 works (multi-turn restart)
4. ✅ Q3+ works (extended multi-turn restart)
5. ✅ Audio chunks properly queued and dequeued
6. ✅ STT generators properly created and destroyed
7. ✅ No hanging or silent failures
8. ✅ Errors properly logged and displayed to user

## Performance Expectations

- Q1 response time: ~2-3 seconds
- Q2 response time: ~2-3 seconds (same as Q1 after restart)
- Q3+ response time: ~2-3 seconds (same pattern)
- No degradation with each question
- No memory leaks (WebSocket stays open)
