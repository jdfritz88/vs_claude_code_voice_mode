# Plan: Voice Mode Streaming Stall Detection & Graceful Recovery

## Context

The `speak` MCP tool in `claude_code_voice_mode_mcp_server.py` freezes every time it's called. The verified root cause: `while stream.active` (line 324) is an infinite loop because `sd.RawOutputStream` without a callback never sets `active=False` — only `stop()` does, but `stop()` is on line 329, after the loop. AllTalk TTS itself works perfectly (1-5 second generation times per the user's logs). The hang is purely in the drain loop code.

The user does NOT want arbitrary timeouts. Instead: detect the stall using calculated remaining playback time (physics — bytes written, sample rate, device latency), then gracefully fall back to non-streaming mode, notify the user, and offer to help fix the issue.

**This plan has two phases:**
- **Phase A** — Execute immediately (steps 9, 10, 11): log findings, prevent auto-start, add diagnostic logging
- **Phase B** — Feature plan (steps 1-8): stall detection, fallback, user interaction, voice mode shutdown

---

## Phase A: Execute Immediately

### A1. Append conversation findings to log (User's Step 11)

**File:** `F:\Apps\freedom_system\REPO_claude_code_voice_mode\logs\claude_code_voice_mode.log`

Append a dated summary entry containing:
- Root cause: `while stream.active` infinite loop in `speak_text_streaming()` line 324
- Why: `sd.RawOutputStream` without callback — `active` only becomes `False` via `stop()`, which is after the loop
- Why AllTalk is innocent: AllTalk logs show 1-5s generation times, never stalls
- Why `stream.write()` matters: it blocks until data is consumed, so almost all audio has played by the time the iter loop exits
- Three plans were analyzed (Freedom, Claude, Kobold) — contradictions identified, merger approach chosen
- No arbitrary timeouts — use calculated remaining playback time

---

### A2. Prevent auto-start via health-check gate (User's Step 9)

**Approach chosen:** Health-check gate (user approved). Server starts but is dormant when AllTalk/Whisper aren't running.

**File:** `claude_code_voice_mode_mcp_server.py`

#### A2a. Add global flag (after line 58):
```python
_services_available: bool = False
```

#### A2b. Add startup health check in `main()` (after line 604, before `async with`):
```python
# Health check — are voice services actually running?
alltalk_ok = False
whisper_ok = False
try:
    r = requests.get(f"{ALLTALK_URL}/api/ready", timeout=2)
    alltalk_ok = r.status_code == 200
except Exception:
    pass
try:
    r = requests.get(f"{WHISPER_URL}/health", timeout=2)
    whisper_ok = r.status_code == 200
except Exception:
    pass

_services_available = alltalk_ok and whisper_ok

if _services_available:
    logger.info("Services detected: AllTalk OK, Whisper OK. Voice mode ACTIVE.")
else:
    logger.warning(
        f"Services not ready (AllTalk={'OK' if alltalk_ok else 'OFFLINE'}, "
        f"Whisper={'OK' if whisper_ok else 'OFFLINE'}). "
        f"Voice tools will return 'not available'. Start services with start_claude_code_voice_mode.bat"
    )
```

Note: `_services_available` must be declared `global` inside `main()`.

#### A2c. Add service gate at top of `call_tool()` (after line 537):
```python
# voice_status always allowed (reports service state for diagnostics)
if name != "voice_status" and not _services_available:
    # Re-check (services may have started since server launch)
    alltalk_ok = whisper_ok = False
    try:
        r = requests.get(f"{ALLTALK_URL}/api/ready", timeout=2)
        alltalk_ok = r.status_code == 200
    except Exception:
        pass
    try:
        r = requests.get(f"{WHISPER_URL}/health", timeout=2)
        whisper_ok = r.status_code == 200
    except Exception:
        pass

    if alltalk_ok and whisper_ok:
        _services_available = True
        logger.info("Services now available. Voice mode activated.")
    else:
        return [TextContent(type="text", text=json.dumps({
            "status": "unavailable",
            "message": "Voice services not running. Start them with start_claude_code_voice_mode.bat",
            "alltalk": "OK" if alltalk_ok else "offline",
            "whisper": "OK" if whisper_ok else "offline",
        }))]
```

Note: `_services_available` must be declared `global` inside `call_tool()`.

#### A2d. Remove from global config:

**File:** `C:\Users\jespe\.claude.json`

Remove the `claude_code_voice_mode_mcp_server` entry from the top-level `mcpServers` section (around line 682). This prevents the server from starting in every project. Keep the two project-specific entries (for `F:/Apps/freedom_system` and `F:/Apps/freedom_system/REPO_claude_code_voice_mode`) where voice mode is relevant.

---

### A3. Add diagnostic logging (User's Step 10)

**File:** `claude_code_voice_mode_mcp_server.py`

#### A3a. Add timestamps to log format (line 42-49):

Change:
```python
format="[CLAUDE_CODE_VOICE_MODE] [%(levelname)s] %(message)s",
```
To:
```python
format="[CLAUDE_CODE_VOICE_MODE] [%(asctime)s] [%(levelname)s] %(message)s",
datefmt="%Y-%m-%d %H:%M:%S",
```

#### A3b. Add tracking variables in `speak_text_streaming()` (after line 295 `remainder = b""`):
```python
total_bytes_written = 0
chunk_count = 0
first_chunk_logged = False
t_start = time.monotonic()
t_first_write = None
exit_reason = "exhausted"  # "exhausted", "paused", "write_error", "stall"
```

#### A3c. Location A — After `stream.start()` (after line 291):
```python
logger.info(
    f"[STREAM-A] RawOutputStream opened: device={sd.default.device[1]}, "
    f"sr={sr}, ch={ch}, dtype={dtype}, latency={stream.latency}"
)
```

#### A3d. Location B — First chunk + tracking (replace line 311 `stream.write(data[:usable])`):
```python
try:
    stream.write(data[:usable])
    total_bytes_written += usable
    chunk_count += 1
    if not first_chunk_logged:
        t_first_write = time.monotonic()
        first_chunk_logged = True
        logger.info(
            f"[STREAM-B] First chunk written: {usable} bytes "
            f"(time_to_first_audio={t_first_write - t_start:.3f}s)"
        )
except sd.PortAudioError as e:
    logger.error(
        f"[STREAM-E] stream.write() failed: {e} "
        f"(after {total_bytes_written} bytes, {chunk_count} chunks)"
    )
    exit_reason = "write_error"
    break
```

Also update the final remainder write (lines 315-320) to track bytes and handle errors:
```python
if remainder and not is_tts_paused():
    pad = frame_size - (len(remainder) % frame_size)
    if pad < frame_size:
        remainder += b"\x00" * pad
    try:
        stream.write(remainder)
        total_bytes_written += len(remainder)
    except sd.PortAudioError as e:
        logger.error(f"[STREAM-E] Final stream.write() failed: {e}")
```

#### A3e. Location C — After for-loop exits (after the `if is_tts_paused()` break, update exit_reason):

When the `is_tts_paused()` break fires (line 303-305), set `exit_reason = "paused"`.

After the remainder write and before `finally`:
```python
t_loop_end = time.monotonic()
logger.info(
    f"[STREAM-C] Iter loop done: {chunk_count} chunks, {total_bytes_written} bytes, "
    f"reason={exit_reason}, loop_duration={t_loop_end - (t_first_write or t_start):.3f}s"
)
```

#### A3f. Location D — In the `finally` block (replace lines 322-331):

This is where the drain loop currently lives. For Phase A (logging only), add log points around the existing drain loop. The drain loop itself gets replaced in Phase B.

For now, add before the drain loop:
```python
logger.info(f"[STREAM-D] Entering drain: stream.active={stream.active}")
```
And after `stream.close()`:
```python
t_end = time.monotonic()
logger.info(
    f"[STREAM-D] Stream stopped and closed. "
    f"Total: {total_bytes_written} bytes, {chunk_count} chunks, "
    f"wall_time={t_end - t_start:.3f}s, "
    f"audio_duration={total_bytes_written / (sr * frame_size) if sr and frame_size else 0:.3f}s"
)
```

#### A3g. Add fallback logging in `speak_text()`:

Before Method 2 (before line 365):
```python
logger.info("[FALLBACK] Trying OpenAI endpoint (Method 2)")
```

After Method 2 success (after line 377):
```python
logger.info(f"[FALLBACK] Method 2 success: {len(response.content)} bytes")
```

Before Method 3 (before line 383):
```python
logger.info("[FALLBACK] Trying native endpoint (Method 3)")
```

After Method 3 success (after line 412):
```python
logger.info(f"[FALLBACK] Method 3 success: url={audio_url}")
```

After all methods fail (before line 417):
```python
logger.error("[FALLBACK] All TTS methods failed")
```

---

## Phase B: Feature Plan (Implement Later)

### B1. Stall detection in drain loop (User's Steps 1-2)

**File:** `claude_code_voice_mode_mcp_server.py` — `speak_text_streaming()`, lines 322-331

Replace the `while stream.active` infinite loop with a calculated remaining playback drain.

#### The math (why this is NOT a timeout):

`stream.write()` is a blocking call — it doesn't return until the audio device has consumed the data buffer. So by the time the `for chunk in response.iter_content()` loop exits:
- All audio data has been pushed to the device via `stream.write()`
- Each `write()` blocked until its data was consumed
- The only unplayed audio is whatever remains in the device's output buffer

The device output buffer size is reported by PortAudio as `stream.latency` (in seconds). This is a physical property of the audio hardware, typically 0.05-0.2 seconds on Windows.

**Calculated remaining playback:**
```python
output_latency = stream.latency  # seconds, from PortAudio
# If latency is a tuple (input, output), take output
if isinstance(output_latency, tuple):
    output_latency = output_latency[1]

# Wait for the output buffer to finish playing, plus a small margin
# for OS thread scheduling jitter (not a timeout — a scheduling allowance)
remaining_playback = output_latency + 0.5
```

**Why 0.5s margin:** This is NOT a timeout. It accounts for OS thread scheduling — `time.sleep(0.05)` on Windows can oversleep by up to ~15ms per call, and there may be brief scheduling delays. 0.5s covers ~30 polling cycles of jitter. The audio itself is done in `output_latency` seconds.

#### Replacement drain code:

```python
finally:
    # Calculate remaining playback from device output latency
    # stream.write() blocks until consumed, so the output buffer
    # holds at most stream.latency seconds of unplayed audio
    try:
        output_latency = stream.latency
        if isinstance(output_latency, tuple):
            output_latency = output_latency[1]  # (input_latency, output_latency)
    except Exception:
        output_latency = 0.2  # safe fallback for Windows audio

    remaining_playback = output_latency + 0.5

    logger.info(
        f"[STREAM-D] Drain: device_latency={output_latency:.3f}s, "
        f"calculated_wait={remaining_playback:.3f}s"
    )

    # Wait the calculated time, checking for pause every 50ms
    drain_end = time.monotonic() + remaining_playback
    stall_detected = False
    while time.monotonic() < drain_end:
        if is_tts_paused():
            logger.info("[STREAM-D] Drain interrupted: TTS paused")
            break
        time.sleep(0.05)

    # After calculated wait, check if stream is still active
    # If it is, that's expected (RawOutputStream.active is always True
    # until stop() is called). But if we've waited the full calculated
    # time and we're here, audio playback is complete.
    # The stall detection is: did the iter_content loop exit abnormally?
    if exit_reason == "stall":
        stall_detected = True
        logger.warning("[STREAM-D] Stall confirmed: streaming will be disabled")

    stream.stop()
    stream.close()
    response.close()

    t_end = time.monotonic()
    logger.info(
        f"[STREAM-D] Complete: {total_bytes_written} bytes, {chunk_count} chunks, "
        f"wall={t_end - t_start:.3f}s, "
        f"audio_dur={total_bytes_written / (sr * frame_size) if sr and frame_size else 0:.3f}s"
    )

    if stall_detected:
        raise StreamingStallError(
            f"Streaming stalled after {chunk_count} chunks, "
            f"{total_bytes_written} bytes"
        )
```

#### Add iter_content stall detection inside the for loop (line 302):

Track when the last chunk arrived. If the loop has been waiting too long for the next chunk, AllTalk may have stalled mid-generation. Since AllTalk normally generates in 1-5 seconds total, and chunks arrive continuously during generation, a long gap between chunks indicates a stall.

```python
last_chunk_time = time.monotonic()

for chunk in response.iter_content(chunk_size=STREAMING_CHUNK_SIZE):
    now = time.monotonic()
    chunk_gap = now - last_chunk_time
    last_chunk_time = now

    # AllTalk delivers chunks continuously. A gap this long means
    # the generation pipeline has stalled. This threshold is based on
    # observed AllTalk behavior: 1-5s total generation, chunks every ~10ms.
    # A 10s gap is far beyond normal and indicates a real problem.
    if chunk_gap > 10.0 and chunk_count > 0:
        logger.warning(
            f"[STREAM-STALL] No chunk for {chunk_gap:.1f}s "
            f"(after {chunk_count} chunks, {total_bytes_written} bytes). "
            f"Treating as stall."
        )
        exit_reason = "stall"
        break

    if is_tts_paused():
        ...
```

Note: The `chunk_gap > 10.0` check only applies after the first chunk (`chunk_count > 0`) because the first chunk can take longer due to AllTalk's initial generation latency. The 10-second threshold is derived from the user's AllTalk logs showing max 5.07s total generation time — a 10s gap between chunks (mid-stream, after generation has started) is far beyond normal.

#### New exception class (add before `speak_text_streaming`):

```python
class StreamingStallError(Exception):
    """Raised when streaming TTS stalls beyond calculated limits."""
    pass
```

---

### B2. Non-streaming fallback function (User's Step 3)

**File:** `claude_code_voice_mode_mcp_server.py`

Extract Methods 2 and 3 from `speak_text()` into a standalone function so they can be called by the recovery handler without re-entering the streaming path.

Add after `speak_text_streaming()`, before `speak_text()`:

```python
def speak_text_nonstreaming(text: str, voice: Optional[str] = None) -> dict:
    """Speak using non-streaming methods only (Methods 2/3). Used for recovery."""
    voice = voice or current_voice

    # Method 2: OpenAI-compatible endpoint
    try:
        logger.info("[NONSTREAM] Trying OpenAI endpoint (Method 2)")
        response = requests.post(
            f"{ALLTALK_URL}/v1/audio/speech",
            json={"input": text, "voice": voice, "model": "tts-1", "response_format": "wav"},
            timeout=30,
        )
        if response.status_code == 200:
            play_audio_bytes(response.content)
            logger.info(f"[NONSTREAM] Method 2 success: {len(response.content)} bytes")
            return {"status": "spoken", "voice": voice, "length": len(text)}
    except Exception as e:
        logger.warning(f"[NONSTREAM] Method 2 failed: {e}")

    # Method 3: AllTalk native endpoint
    try:
        logger.info("[NONSTREAM] Trying native endpoint (Method 3)")
        payload = {
            "text_input": text,
            "text_filtering": "standard",
            "character_voice_gen": voice,
            "narrator_enabled": "false",
            "narrator_voice_gen": "",
            "text_not_inside": "character",
            "language": "en",
            "output_file_name": "claude_code_voice_mode",
            "output_file_timestamp": "true",
            "autoplay": "false",
            "autoplay_volume": "0.8",
            "speed": "1.0",
            "pitch": "1.0",
            "temperature": "0.75",
            "repetition_penalty": "1.0",
        }
        response = requests.post(f"{ALLTALK_URL}/api/tts-generate", data=payload, timeout=30)
        if response.status_code == 200:
            result = response.json()
            audio_url = result.get("output_file_url", "")
            if audio_url:
                if not audio_url.startswith("http"):
                    audio_url = f"{ALLTALK_URL}{audio_url}"
                play_audio_from_url(audio_url)
                logger.info(f"[NONSTREAM] Method 3 success: url={audio_url}")
                return {"status": "spoken", "voice": voice, "length": len(text)}
    except Exception as e:
        logger.error(f"[NONSTREAM] Method 3 failed: {e}")

    return {"status": "error", "message": "All non-streaming TTS methods failed"}
```

---

### B3. User notification and confirmation (User's Steps 4-5)

**File:** `claude_code_voice_mode_mcp_server.py`

New function `_handle_streaming_failure()` — called when `speak_text()` catches `StreamingStallError`.

```python
def _handle_streaming_failure(original_text: str, voice: str) -> dict:
    """Handle streaming failure: notify user via non-streaming, verify audio, offer fix."""
    global _voice_mode_disabled

    logger.info("[RECOVERY] Starting streaming failure recovery")

    # Step 4: Inform user via TTS using non-streaming mode
    notify_result = speak_text_nonstreaming(
        "Streaming audio had a problem. I've switched to regular mode. Can you hear me?",
        voice,
    )

    if notify_result.get("status") != "spoken":
        # Non-streaming also failed — total audio failure
        logger.error("[RECOVERY] Non-streaming notification also failed")
        _voice_mode_disabled = True
        return {
            "status": "error",
            "voice_mode_disabled": True,
            "message": "Audio playback completely failed (both streaming and non-streaming). Voice mode disabled.",
            "action": "BEGIN_DMAIC_TROUBLESHOOTING",
            "instruction": (
                "TOTAL AUDIO FAILURE — both streaming and non-streaming playback failed. "
                "Voice mode disabled. Communicate via text only. BEGIN DMAIC troubleshooting "
                "NOW. Read the DMAIC process at "
                "F:/Apps/freedom_system/standards/DMAIC_coding_process_tool.md "
                "and follow it systematically. Check AllTalk TTS service, audio output "
                "device, sounddevice configuration, and speaker connections."
            ),
        }

    # Step 5: Listen for user response
    logger.info("[RECOVERY] Listening for user confirmation...")
    try:
        audio = record_audio(duration=8.0, silence_timeout=3.0)
        if len(audio) > 0:
            response_text = transcribe_audio(audio).strip().lower()
            logger.info(f"[RECOVERY] User response: '{response_text}'")

            # Step 6: If user confirms hearing — offer to fix streaming
            yes_words = ["yes", "yeah", "yep", "yup", "hear", "ok", "okay", "can", "working"]
            if any(word in response_text for word in yes_words):
                logger.info("[RECOVERY] User confirmed audio works. Speaking original text.")
                speak_text_nonstreaming(original_text, voice)
                speak_text_nonstreaming(
                    "Good, you can hear me. I'm going to start troubleshooting the streaming issue now.",
                    voice,
                )
                return {
                    "status": "spoken_with_recovery",
                    "streaming_disabled": True,
                    "user_confirmed_audio": True,
                    "length": len(original_text),
                    "action": "BEGIN_DMAIC_TROUBLESHOOTING",
                    "instruction": (
                        "Streaming audio stalled but non-streaming audio works. "
                        "The user confirmed they can hear. BEGIN DMAIC troubleshooting "
                        "of the streaming TTS issue NOW. Read the DMAIC process at "
                        "F:/Apps/freedom_system/standards/DMAIC_coding_process_tool.md "
                        "and follow it systematically. The known root cause is the "
                        "infinite drain loop in speak_text_streaming() — "
                        "while stream.active never becomes False for sd.RawOutputStream "
                        "without a callback. Notify the user via speak that you are "
                        "beginning DMAIC troubleshooting."
                    ),
                }

            # Step 7: If user says no — disable voice mode
            no_words = ["no", "nope", "can't", "cannot", "nothing", "don't"]
            if any(word in response_text for word in no_words):
                logger.warning("[RECOVERY] User cannot hear audio. Disabling voice mode.")
                _voice_mode_disabled = True
                return {
                    "status": "error",
                    "voice_mode_disabled": True,
                    "message": "User confirmed they cannot hear audio. Voice mode disabled.",
                    "action": "BEGIN_DMAIC_TROUBLESHOOTING",
                    "instruction": (
                        "The user CANNOT hear any audio (streaming or non-streaming). "
                        "Voice mode is now disabled — communicate via text only. "
                        "BEGIN DMAIC troubleshooting NOW. Read the DMAIC process at "
                        "F:/Apps/freedom_system/standards/DMAIC_coding_process_tool.md "
                        "and follow it systematically. The problem is broader than "
                        "just the streaming drain loop — non-streaming audio also "
                        "failed. Investigate audio output device, AllTalk TTS service, "
                        "and sounddevice configuration."
                    ),
                }

        # No response or unclear response
        logger.info("[RECOVERY] No clear response from user")
    except Exception as e:
        logger.error(f"[RECOVERY] Listen/transcribe failed: {e}")

    # Unclear response — speak original text anyway (in case they CAN hear)
    # and return with unclear status. Still start DMAIC either way.
    speak_text_nonstreaming(original_text, voice)
    return {
        "status": "spoken_with_recovery",
        "streaming_disabled": True,
        "user_response_unclear": True,
        "length": len(original_text),
        "action": "BEGIN_DMAIC_TROUBLESHOOTING",
        "instruction": (
            "Streaming failed. Switched to regular mode and spoke the message, "
            "but could not confirm the user heard it. BEGIN DMAIC troubleshooting "
            "NOW. Read the DMAIC process at "
            "F:/Apps/freedom_system/standards/DMAIC_coding_process_tool.md "
            "and follow it systematically. First ask the user (via text) if they "
            "can hear, then proceed with troubleshooting."
        ),
    }
```

---

### B4. Modify `speak_text()` to catch `StreamingStallError` (User's Steps 6, 8)

**File:** `claude_code_voice_mode_mcp_server.py` — `speak_text()` function

Add `_voice_mode_disabled` check and `StreamingStallError` handling:

```python
def speak_text(text: str, voice: Optional[str] = None) -> dict:
    global _streaming_available, _voice_mode_disabled

    # If voice mode was disabled by recovery handler, return immediately
    if _voice_mode_disabled:
        return {
            "status": "error",
            "voice_mode_disabled": True,
            "message": "Voice mode is disabled because audio was not working. Communicate via text.",
        }

    if is_tts_paused():
        logger.info("TTS is paused, skipping speech")
        return {"status": "paused", "message": "TTS is currently paused via mic panel"}

    voice = voice or current_voice
    logger.info(f"Speaking: '{text[:80]}...' with voice={voice}")

    # Method 1: Streaming
    if _streaming_available is not False:
        try:
            return speak_text_streaming(text, voice)
        except StreamingStallError as e:
            logger.error(f"Streaming stalled: {e}")
            _streaming_available = False
            return _handle_streaming_failure(text, voice)
        except Exception as e:
            if _streaming_available is None:
                logger.info(f"Streaming not available, disabling: {e}")
                _streaming_available = False
            else:
                logger.warning(f"Streaming failed (transient), falling back: {e}")

    # Methods 2/3
    return speak_text_nonstreaming(text, voice)
```

---

### B5. Voice mode disabled gate in `call_tool()` (User's Step 7)

Add after the `_services_available` gate:

```python
if name in ("speak", "listen", "converse") and _voice_mode_disabled:
    return [TextContent(type="text", text=json.dumps({
        "status": "error",
        "voice_mode_disabled": True,
        "message": "Voice mode is disabled because the user could not hear audio. Communicate via text only. Offer to investigate the audio problem.",
    }))]
```

`voice_status` and `set_voice` remain accessible for diagnostics.

---

### B6. Add `_voice_mode_disabled` to `voice_status` output (User's Step 8)

Modify the `voice_status` handler (line 576) to include the disabled flag:

```python
status = {
    "alltalk": "unknown",
    "whisper": "unknown",
    "voice": current_voice,
    "voices": [],
    "tts_paused": is_tts_paused(),
    "streaming_available": _streaming_available,
    "voice_mode_disabled": _voice_mode_disabled,  # NEW
}
```

---

## Files Modified

| File | Phase | Changes |
|------|-------|---------|
| `REPO_claude_code_voice_mode\claude_code_voice_mode_mcp_server.py` | A+B | All code changes (logging, health gate, stall detection, recovery) |
| `REPO_claude_code_voice_mode\logs\claude_code_voice_mode.log` | A | Append conversation findings |
| `C:\Users\jespe\.claude.json` | A | Remove `claude_code_voice_mode_mcp_server` from global `mcpServers` |

**NOT modified:**
- `tts_hook.py` — uses `sd.play()` (callback-based), its drain loop works correctly
- `start_claude_code_voice_mode.bat` — no changes needed; health-check gate handles the auto-start prevention
- `mic_panel.py` — no changes

---

## Verification

### Phase A verification:
1. Start Claude Code WITHOUT running the batch file. Call `voice_status` — should return service state. Call `speak` — should return `{"status": "unavailable", "message": "Voice services not running..."}` immediately (no 30s timeout).
2. Run the batch file, start Claude Code. Call `speak` with short text — should work (streaming attempt, then drain). Check log for `[STREAM-A]`, `[STREAM-B]`, `[STREAM-C]`, `[STREAM-D]` entries with timestamps.
3. Check log for startup health check messages.

### Phase B verification:
1. Call `speak` — streaming stalls in drain → stall detected → recovery handler fires → non-streaming notification plays → user hears "Can you hear me?" → responds "yes" → original text plays via non-streaming → user hears "I'm going to start troubleshooting the streaming issue now" → tool returns `spoken_with_recovery` with `action: BEGIN_DMAIC_TROUBLESHOOTING` → Claude reads `F:\Apps\freedom_system\standards\DMAIC_coding_process_tool.md` and begins systematic DMAIC troubleshooting of the streaming bug, notifying the user via speak that it's starting.
2. Check log for `[RECOVERY]` entries showing the full flow.
3. Subsequent `speak` calls should skip streaming (`_streaming_available = False`) and go directly to non-streaming methods.
4. If user responds "no" → `_voice_mode_disabled = True` → subsequent speak/listen/converse calls return error immediately → tool returns with `action: BEGIN_DMAIC_TROUBLESHOOTING` so Claude still begins troubleshooting (via text since voice is disabled).
5. `voice_status` still works when disabled.
6. TTS pause still works in non-streaming mode.

---

## Implementation Order

```
Phase A (immediate):
  A1 → Append findings to log
  A2 → Health-check gate + remove global config entry
  A3 → Diagnostic logging (timestamps, locations A-E, fallback logging)

Phase B (later, after Phase A is verified):
  B1 → StreamingStallError class + iter-loop stall detection + drain loop replacement
  B2 → speak_text_nonstreaming() function
  B3 → _handle_streaming_failure() function
  B4 → Modify speak_text() to catch StreamingStallError
  B5 → _voice_mode_disabled gate in call_tool()
  B6 → Add disabled flag to voice_status output
```
