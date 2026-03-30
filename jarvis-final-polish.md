# JARVIS — Final Polish

---

## Fix 1: JARVIS Voice Personality

In `ai_advisor.py`, add to the `SYSTEM_PROMPT`:

```
Use punctuation to control TTS pacing. Insert em dashes for 
dramatic pauses before dry observations. Use commas liberally 
to slow the pace. End quips with a period followed by "sir" 
after a comma for a natural pause.

Example delivery style:
- "The F departs in 16 minutes -- no transfers, no drama, sir."
- "The N line is, to put it diplomatically, a mess right now."
- "Total trip is 47 minutes, which the MTA would call efficient."
```

---

## Fix 2: TTS Abbreviation Expansion

In `trips.py`, replace any existing `.replace()` calls for TTS 
text with proper regex word-boundary replacements. Apply ONLY to 
the text sent to `generate_speech()`, not the text returned to 
the frontend.

```python
import re

# Add this function
def expand_abbreviations(text: str) -> str:
    replacements = [
        (r'\bSt\b', 'Street'),
        (r'\bSq\b', 'Square'),
        (r'\bAv\b', 'Avenue'),
        (r'\bAve\b', 'Avenue'),
        (r'\bBlvd\b', 'Boulevard'),
        (r'\bHwy\b', 'Highway'),
        (r'\bPkwy\b', 'Parkway'),
        (r'\bCtr\b', 'Center'),
        (r'\bRd\b', 'Road'),
        (r'\bPl\b', 'Place'),
        (r'\bDr\b', 'Drive'),
        (r'\bLn\b', 'Lane'),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return text

# Use it before TTS:
tts_text = expand_abbreviations(recommendation)
audio_bytes = await asyncio.to_thread(generate_speech, tts_text)
```

Using `\b` (word boundary) ensures "St" matches as a standalone 
word, not inside words like "stalled" or "first".

---

## Fix 3: Intermediate Stop Visibility

Intermediate stop labels along the polylines are too dim to read.

1. Increase label color from `rgba(255,255,255,0.25)` to 
   `rgba(255,255,255,0.45)`
2. Increase font-size from `9px` to `10px`
3. Add a small dot marker (4px circle) at each intermediate stop 
   position, colored the same as the route line at 50% opacity

---

## Fix 4: Bus Intermediate Stops Missing

Intermediate stops only appear on subway segments, not bus segments. 
Debug:

1. Log the `intermediate_stops` array for BUS type steps to see 
   if it is empty or missing entirely
2. The issue is likely that `get_intermediate_stops` in 
   `gtfs_static.py` cannot find bus route IDs in the GTFS data 
   because bus routes use different identifiers (e.g., "B35" vs 
   subway "Q")
3. Check `trips_to_routes` to see if bus route IDs are present
4. If bus routes are not in the subway GTFS data (they use separate 
   bus GTFS files), then skip intermediate stops for bus segments 
   gracefully -- return an empty list instead of erroring
5. The frontend should handle empty `intermediate_stops` without 
   breaking

---

## Fix 5: Console Error

There is still a "1 Issue" red badge in the bottom-left corner. 
Find and fix the console error. Log it and show me what it says.

---

## Do NOT:
- Change any backend pipeline logic
- Break route rendering or audio playback
- Remove existing functionality
