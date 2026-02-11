# RAEC v3 ACE v2 — Changelog

## What Changed (and Why)

### ace_layer.py — Full Rewrite
The old ACE layer was a simple read/write pass with no awareness of time, mood, 
personality, or relationship depth. The new version implements five layers:

**Layer 1: Temporal Awareness**
- Every prompt now includes current time-of-day, a tonal descriptor ("the hollow hours," 
  "evening descent"), and how long since the user last spoke. Gemini uses this naturally 
  to shift tone — late-night conversations feel different from midday ones, and long 
  absences get acknowledged.

**Layer 2: Personality Kernel**
- On startup, ACE loads `01_Raec_Profile.txt`, `Conversational_Directives.txt`, and 
  `14_Lexicon_Archive.txt` from the knowledge folder and caches them. These get injected 
  into every system prompt so Gemini actually writes in Raec's voice with the correct 
  lexicon and behavioral directives.

**Layer 3: Entity State (Global Mood)**
- New `entity_state` table stores Raec's mood, energy level, and current contemplation.
- `drift_mood()` runs on the ambient loop tick and naturally shifts mood based on time 
  of day and activity level. Heavy conversation drains energy; idle time restores it.
- The mood label (contemplative, withdrawn, vigilant, somber, etc.) gets injected into 
  every prompt so Gemini can color responses accordingly.

**Layer 4: Relationship Echo**  
- New `user_relationship` table tracks per-user interaction count, first/last seen, 
  and a computed depth score (logarithmic scale from interactions + fact density).
- Each prompt includes the relationship standing so Gemini treats a stranger differently 
  from a regular.

**Layer 5: Memory Lifecycle**
- Facts now have `confidence`, `access_count`, `last_accessed`, `active` fields.
- `decay_memories()` runs on startup — old, low-access facts lose confidence over time 
  and get deactivated below threshold. Frequently accessed facts are protected.
- `compact_episodes()` deactivates old episodic entries past a cap per user.
- Episodic retrieval increased from 3 to 5 recent entries.
- Fact retrieval now ordered by confidence (most confident first), capped at 30.

### Raec_v3_ACE.py — Rewritten
- **Ambient pulse**: `@tasks.loop(minutes=12)` that drifts mood, then probabilistically 
  asks Gemini if Raec should speak. Gated by energy, cooldown, and randomness. Messages 
  are contextual (not a static list), logged to prevent repetition.
- **System prompt**: Now includes the full personality kernel, behavioral directives for 
  mood/relationship adaptation, and the `contemplation` field so Raec carries idle thoughts 
  between conversations.
- **JSON robustness**: Added fallback parsing that strips markdown fences if Gemini wraps 
  its JSON output.
- **Commands**: Added `!presence` (relationship status), `!forget_me` (memory wipe), 
  `!raec_status` (debug: show current mood/energy/contemplation).
- **on_message**: Now calls `bot.process_commands()` for proper command routing.
- **Entry point**: Added env var validation before `bot.run()`.

### update_ace_db.py — Expanded Schema
- `ace_semantic`: Added confidence, memory_type, access_count, last_accessed, created_at, active.
- `ace_episodic`: Added active flag for soft-delete compaction.
- New table `entity_state`: Singleton row for Raec's global mood/energy/contemplation.
- New table `user_relationship`: Per-user interaction tracking and depth scoring.
- New table `ambient_log`: Tracks unprompted messages to prevent repetition.
- All migrations use ALTER TABLE with try/except for safe upgrades of existing DBs.

### raec_launcher.py — Bug Fixes
- Fixed `BOT_FILENAME` from `"raec_v3_organic.py"` → `"Raec_v3_ACE.py"`.
- Fixed `run_process` method indentation (was outside class body).
- Removed unused imports (signal).
- Updated subtitle label to "v3 ACE // Alive".

### start_raec.bat — Fixed
- Changed `python Raec_v3.0.py` → `python Raec_v3_ACE.py`.

### check_models.py — Security Fix
- **REMOVED HARDCODED API KEY.** Now loads from .env like everything else.
- Updated to use `google.genai` client (matching the bot's import style).

## Files To Manually Delete
- `.txt` — Empty file in project root, serves no purpose.

## Security Note
Your Gemini API key was previously exposed in plaintext in `check_models.py`. 
**You should rotate that key** in Google AI Studio since it's been in a file 
on disk. Generate a new one and update your `.env`.

## First Run After Upgrade
1. Run `python update_ace_db.py` to migrate the database schema.
2. Then launch normally via `python Raec_v3_ACE.py` or the launcher/bat.
   (The bot will also auto-detect missing schema and attempt migration on startup.)
