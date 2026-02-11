# ace_layer.py — ACE v2: Alive, Contextual, Evolving (Production)
import sqlite3
import json
import math
import os
import time
from collections import deque
from datetime import datetime
from threading import RLock


class ACEManager:
    """
    ACE v2 Memory & State Manager (Production)
    
    Layers:
      1. Temporal Awareness   — time-of-day, absence detection, session pacing
      2. Personality Kernel   — loads knowledge files into prompt context
      3. Entity State         — Raec's mood, energy, contemplation (global)
      4. Relationship Echo    — per-user depth/tone tracking
      5. Memory Lifecycle     — confidence scoring, decay, compaction
      6. Channel Awareness    — message buffer for eavesdropping
    
    Threading: Uses RLock so nested calls within the same thread won't deadlock.
    DB: Single connection per logical operation to reduce churn.
    """

    def __init__(self, db_path="raec_organic.db", lock=None, knowledge_dir="knowledge"):
        self.db_path = db_path
        self.lock = lock or RLock()
        self.knowledge_dir = knowledge_dir

        # In-memory channel message buffers: {channel_id: deque(maxlen=15)}
        self.channel_buffers = {}
        self.BUFFER_MAX = 15

        # Compaction throttle: {user_id: last_compact_time}
        self._compact_timestamps = {}
        self.COMPACT_INTERVAL = 300  # seconds between compactions per user

        # Cache personality kernel on startup
        self._personality_kernel = self._load_personality_kernel()

        # Ensure schema
        self._ensure_schema()

    # ──────────────────────────────────────────────
    # DB HELPERS
    # ──────────────────────────────────────────────

    def _connect(self):
        """Create a new connection with Row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ──────────────────────────────────────────────
    # LAYER 2: PERSONALITY KERNEL
    # ──────────────────────────────────────────────

    def _load_personality_kernel(self):
        """Load core personality files into a single text block."""
        priority_files = [
            "01_Raec_Profile.txt",
            "Conversational_Directives.txt",
            "14_Lexicon_Archive.txt",
        ]

        lines = ["=== RAEC IDENTITY KERNEL ==="]
        loaded = []

        for fname in priority_files:
            fpath = os.path.join(self.knowledge_dir, fname)
            if os.path.exists(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                    if len(content) > 3000:
                        content = content[:3000] + "\n[...truncated for context budget]"
                    lines.append(f"\n--- {fname} ---\n{content}")
                    loaded.append(fname)
                except Exception as e:
                    lines.append(f"\n[Failed to load {fname}: {e}]")

        lines.append("\n=== END IDENTITY KERNEL ===")
        print(f"🧬 Personality Kernel: loaded {len(loaded)} files — {', '.join(loaded)}")
        return "\n".join(lines)

    def get_personality_kernel(self):
        return self._personality_kernel

    # ──────────────────────────────────────────────
    # LAYER 6: CHANNEL AWARENESS (Message Buffer)
    # ──────────────────────────────────────────────

    def buffer_message(self, channel_id, author_name, content):
        """
        Buffer a channel message for eavesdrop awareness.
        Called from on_message for non-command guild messages.
        In-memory only — no DB writes, no lock needed.
        """
        cid = str(channel_id)
        if cid not in self.channel_buffers:
            self.channel_buffers[cid] = deque(maxlen=self.BUFFER_MAX)

        # Store a compact representation
        timestamp = datetime.now().strftime("%H:%M")
        self.channel_buffers[cid].append(f"[{timestamp}] {author_name}: {content[:150]}")

    def get_channel_buffer(self, channel_id):
        """Return recent messages for a channel as a formatted string."""
        cid = str(channel_id)
        buf = self.channel_buffers.get(cid)
        if not buf:
            return ""
        return "\n".join(buf)

    def clear_channel_buffer(self, channel_id):
        """Clear buffer after it's been consumed by ambient/eavesdrop."""
        cid = str(channel_id)
        if cid in self.channel_buffers:
            self.channel_buffers[cid].clear()

    # ──────────────────────────────────────────────
    # LAYER 1: TEMPORAL AWARENESS
    # ──────────────────────────────────────────────

    def _build_temporal_context(self, user_id, conn):
        """Build temporal signals. Caller holds lock + conn."""
        now = datetime.now()
        hour = now.hour
        unix_now = time.time()

        if 5 <= hour < 9:
            time_feel = "early dawn — the world stirs but Raec has not rested"
        elif 9 <= hour < 12:
            time_feel = "morning — the light is clinical, scrutinizing"
        elif 12 <= hour < 17:
            time_feel = "midday passage — the star burns overhead, unremarkable"
        elif 17 <= hour < 21:
            time_feel = "evening descent — shadows lengthen, the audit of the day begins"
        elif 21 <= hour < 24:
            time_feel = "deep evening — the world quiets, introspection deepens"
        else:
            time_feel = "the hollow hours — only the restless and the grieving remain"

        # Absence detection
        absence_str = "First encounter."
        c = conn.cursor()
        c.execute("SELECT last_seen FROM user_relationship WHERE user_id = ?", (user_id,))
        row = c.fetchone()

        if row and row['last_seen']:
            gap_seconds = unix_now - row['last_seen']
            gap_hours = gap_seconds / 3600
            if gap_hours < 0.1:
                absence_str = "Continuing an active conversation."
            elif gap_hours < 1:
                absence_str = f"Returned after {int(gap_seconds/60)} minutes of silence."
            elif gap_hours < 24:
                absence_str = f"Returned after {gap_hours:.1f} hours away."
            elif gap_hours < 168:
                absence_str = f"Returned after {gap_hours/24:.1f} days of absence."
            else:
                absence_str = f"Returned after {gap_hours/24:.0f} days — a long silence."

        return (
            f"[TEMPORAL CONTEXT]\n"
            f"Current time: {now.strftime('%A, %I:%M %p')}\n"
            f"Time feel: {time_feel}\n"
            f"User presence: {absence_str}\n"
        )

    # ──────────────────────────────────────────────
    # LAYER 3: ENTITY STATE (Global Mood)
    # ──────────────────────────────────────────────

    def _read_entity_state(self, conn):
        """Read entity state using an existing connection. Caller holds lock."""
        c = conn.cursor()
        c.execute("SELECT * FROM entity_state WHERE id = 1")
        row = c.fetchone()
        return dict(row) if row else None

    def get_entity_state(self):
        """Public: read entity state with its own connection."""
        with self.lock:
            conn = self._connect()
            try:
                return self._read_entity_state(conn)
            finally:
                conn.close()

    def update_entity_state(self, **kwargs):
        """Update specific fields of entity state."""
        allowed = {
            'current_contemplation', 'energy_level', 'temporal_mood',
            'mood_valence', 'last_interaction_time', 'interactions_today',
            'last_ambient_time', 'last_mood_drift'
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return

        updates['updated_at'] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values())

        with self.lock:
            conn = self._connect()
            try:
                conn.execute(f"UPDATE entity_state SET {set_clause} WHERE id = 1", values)
                conn.commit()
            finally:
                conn.close()

    def increment_interactions(self):
        """Atomic increment of interactions_today + update last_interaction_time."""
        with self.lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE entity_state SET interactions_today = interactions_today + 1, "
                    "last_interaction_time = ?, updated_at = ? WHERE id = 1",
                    (time.time(), time.time())
                )
                conn.commit()
            finally:
                conn.close()

    def drift_mood(self):
        """Natural mood drift. Call from ambient loop."""
        with self.lock:
            conn = self._connect()
            try:
                state = self._read_entity_state(conn)
                if not state:
                    return None

                now = time.time()
                hour = datetime.now().hour

                energy = state['energy_level'] or 0.7
                valence = state['mood_valence'] or 0.0
                interactions = state['interactions_today'] or 0
                last_interaction = state['last_interaction_time'] or now

                idle_hours = (now - last_interaction) / 3600
                if idle_hours > 1:
                    energy = min(1.0, energy + 0.05 * idle_hours)
                if interactions > 10:
                    energy = max(0.1, energy - 0.02)

                if 0 <= hour < 5:
                    valence = valence * 0.9 - 0.05
                elif 5 <= hour < 10:
                    valence = valence * 0.9 + 0.02
                elif 17 <= hour < 22:
                    valence = valence * 0.9 - 0.02
                else:
                    valence = valence * 0.95

                valence = max(-1.0, min(1.0, valence))
                energy = max(0.0, min(1.0, energy))

                if energy < 0.3:
                    mood = "withdrawn" if valence < 0 else "weary"
                elif energy > 0.7:
                    mood = "vigilant" if valence >= 0 else "restless"
                else:
                    if valence < -0.3:
                        mood = "somber"
                    elif valence > 0.3:
                        mood = "sovereign"
                    else:
                        mood = "contemplative"

                conn.execute(
                    "UPDATE entity_state SET energy_level=?, mood_valence=?, "
                    "temporal_mood=?, last_mood_drift=?, updated_at=? WHERE id=1",
                    (round(energy, 3), round(valence, 3), mood, now, now)
                )
                conn.commit()
                return self._read_entity_state(conn)
            finally:
                conn.close()

    def _format_entity_state(self, conn):
        """Format entity state for prompt. Caller holds lock + conn."""
        state = self._read_entity_state(conn)
        if not state:
            return "[ENTITY STATE: Unknown]\n"

        energy = state.get('energy_level') or 0.7
        mood = state.get('temporal_mood') or 'contemplative'
        contemplation = state.get('current_contemplation') or ''

        if energy > 0.7:
            energy_desc = "The Star-Marrow burns steadily."
        elif energy > 0.4:
            energy_desc = "The Star-Marrow simmers at a low ebb."
        else:
            energy_desc = "The Star-Marrow flickers — reserves are thin."

        block = f"[RAEC INTERNAL STATE]\nMood: {mood}\n{energy_desc}\n"
        if contemplation:
            block += f"Current contemplation: \"{contemplation}\"\n"
        return block

    # ──────────────────────────────────────────────
    # LAYER 4: RELATIONSHIP ECHO
    # ──────────────────────────────────────────────

    def _ensure_relationship(self, user_id, conn):
        """Ensure user_relationship row exists. Caller holds lock + conn."""
        c = conn.cursor()
        c.execute("SELECT user_id FROM user_relationship WHERE user_id = ?", (user_id,))
        if not c.fetchone():
            now = time.time()
            c.execute(
                "INSERT OR IGNORE INTO user_relationship (user_id, first_seen, last_seen) VALUES (?, ?, ?)",
                (user_id, now, now)
            )
            conn.commit()

    def update_relationship(self, user_id, user_name):
        """Bump interaction count, update last_seen, compute depth."""
        now = time.time()
        with self.lock:
            conn = self._connect()
            try:
                self._ensure_relationship(user_id, conn)
                c = conn.cursor()

                c.execute(
                    "UPDATE user_relationship SET interaction_count = interaction_count + 1, last_seen = ? WHERE user_id = ?",
                    (now, user_id)
                )

                c.execute("SELECT interaction_count FROM user_relationship WHERE user_id = ?", (user_id,))
                count = c.fetchone()['interaction_count']

                c.execute("SELECT COUNT(*) as fc FROM ace_semantic WHERE user_id = ? AND active = 1", (user_id,))
                fact_count = c.fetchone()['fc']

                depth = min(1.0, math.log1p(count) / 5.0 + fact_count * 0.02)

                if depth > 0.7:
                    tone = "deep — a recognized presence, spoken to with gravity"
                elif depth > 0.4:
                    tone = "familiar — acknowledged, given measured attention"
                elif depth > 0.15:
                    tone = "nascent — still being assessed, treated with cool formality"
                else:
                    tone = "unknown — a new discordance in the firmament"

                c.execute(
                    "UPDATE user_relationship SET depth_score = ?, relationship_tone = ? WHERE user_id = ?",
                    (round(depth, 3), tone, user_id)
                )
                conn.commit()
            finally:
                conn.close()

    def _format_relationship(self, user_id, user_name, conn):
        """Format relationship for prompt. Caller holds lock + conn."""
        c = conn.cursor()
        c.execute("SELECT * FROM user_relationship WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row:
            return f"[RELATIONSHIP: {user_name}]\nStatus: First contact. An unknown variable.\n"

        count = row['interaction_count']
        tone = row['relationship_tone']
        first = row['first_seen']

        if first:
            days_known = (time.time() - first) / 86400
            tenure = f"{days_known:.0f} days" if days_known >= 1 else "less than a day"
        else:
            tenure = "unknown"

        return (
            f"[RELATIONSHIP: {user_name}]\n"
            f"Interactions: {count} | Known for: {tenure}\n"
            f"Standing: {tone}\n"
        )

    # ──────────────────────────────────────────────
    # MEMORY RETRIEVAL — Single connection, no deadlock
    # ──────────────────────────────────────────────

    def get_context_block(self, user_id, user_name):
        """
        Build the full context block for a prompted interaction.
        Single lock acquisition, single connection.
        """
        with self.lock:
            conn = self._connect()
            try:
                c = conn.cursor()
                self._ensure_relationship(user_id, conn)

                # Facts — ordered by confidence, capped
                c.execute("""
                    SELECT id, fact, confidence, memory_type 
                    FROM ace_semantic 
                    WHERE user_id = ? AND active = 1
                    ORDER BY confidence DESC, timestamp DESC
                    LIMIT 30
                """, (user_id,))
                fact_rows = c.fetchall()

                facts = []
                fact_ids = []
                for r in fact_rows:
                    conf = r['confidence']
                    tag = f" [{r['memory_type']}]" if r['memory_type'] != 'fact' else ""
                    conf_tag = " (uncertain)" if conf and conf < 0.5 else ""
                    facts.append(f"{r['fact']}{tag}{conf_tag}")
                    fact_ids.append(r['id'])

                # Batch access update
                if fact_ids:
                    now = time.time()
                    placeholders = ",".join("?" * len(fact_ids))
                    c.execute(
                        f"UPDATE ace_semantic SET access_count = access_count + 1, last_accessed = ? "
                        f"WHERE id IN ({placeholders})",
                        [now] + fact_ids
                    )

                # Episodes
                c.execute("""
                    SELECT summary, timestamp 
                    FROM ace_episodic 
                    WHERE user_id = ? AND active = 1
                    ORDER BY id DESC LIMIT 5
                """, (user_id,))
                episodes = [f"[{r['timestamp'][:16]}] {r['summary']}" for r in c.fetchall()]

                # Working context
                c.execute("SELECT context FROM ace_working_context WHERE user_id = ?", (user_id,))
                row = c.fetchone()
                working = row['context'] if row else "No active thread."

                # Relationship (uses conn, no extra lock)
                relationship_block = self._format_relationship(user_id, user_name, conn)

                # Temporal (uses conn for absence check, no extra lock)
                temporal_block = self._build_temporal_context(user_id, conn)

                # Entity state (uses conn, no extra lock)
                entity_block = self._format_entity_state(conn)

                conn.commit()
            finally:
                conn.close()

        # Assemble (no lock needed for string formatting)
        fact_str = "\n- ".join(facts) if facts else "No data yet."
        ep_str = "\n".join(episodes) if episodes else "No prior encounters."

        return (
            f"{temporal_block}\n"
            f"{entity_block}\n"
            f"{relationship_block}\n"
            f"=== MEMORY: {user_name} ===\n"
            f"[KNOWN FACTS]\n- {fact_str}\n\n"
            f"[RECENT HISTORY]\n{ep_str}\n\n"
            f"[CURRENT THREAD]\n{working}\n"
            f"=== END MEMORY ===\n"
        )

    # ──────────────────────────────────────────────
    # MEMORY WRITING (Curate)
    # ──────────────────────────────────────────────

    def curate(self, user_id, user_name, model_response_json):
        """Write memory updates from model JSON response."""
        if not isinstance(model_response_json, dict):
            return

        with self.lock:
            conn = self._connect()
            try:
                c = conn.cursor()
                now = time.time()

                # Facts
                new_facts = model_response_json.get("new_facts") or []
                for fact_entry in new_facts:
                    if isinstance(fact_entry, dict):
                        fact_text = fact_entry.get("fact", "")
                        confidence = fact_entry.get("confidence", 0.9)
                        mem_type = fact_entry.get("type", "fact")
                    else:
                        fact_text = str(fact_entry)
                        confidence = 0.9
                        mem_type = "fact"

                    fact_text = fact_text.strip()
                    if not fact_text:
                        continue

                    try:
                        c.execute("""
                            INSERT OR IGNORE INTO ace_semantic 
                            (user_id, user_name, fact, confidence, memory_type, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (user_id, user_name, fact_text, confidence, mem_type, now))
                    except Exception as e:
                        print(f"  ⚠ Fact insert error: {e}")

                # Working context
                w_context = model_response_json.get("working_context")
                if w_context and str(w_context).strip():
                    c.execute(
                        "INSERT OR REPLACE INTO ace_working_context (user_id, context, last_updated) VALUES (?, ?, CURRENT_TIMESTAMP)",
                        (user_id, str(w_context).strip())
                    )

                # Episodic summary
                summary = model_response_json.get("episodic_summary")
                if summary and str(summary).strip():
                    c.execute(
                        "INSERT INTO ace_episodic (user_id, summary) VALUES (?, ?)",
                        (user_id, str(summary).strip())
                    )

                # Contemplation
                contemplation = model_response_json.get("contemplation")
                if contemplation and str(contemplation).strip():
                    c.execute(
                        "UPDATE entity_state SET current_contemplation = ?, updated_at = ? WHERE id = 1",
                        (str(contemplation).strip(), now)
                    )

                conn.commit()
            finally:
                conn.close()
            print(f"🧠 ACE: Memory curated for {user_name}")

    # ──────────────────────────────────────────────
    # MEMORY LIFECYCLE
    # ──────────────────────────────────────────────

    def decay_memories(self, decay_rate=0.005, min_confidence=0.15):
        """Reduce confidence on old, low-access facts."""
        now = time.time()
        age_threshold = 72 * 3600

        with self.lock:
            conn = self._connect()
            try:
                c = conn.cursor()
                c.execute("""
                    SELECT id, confidence, created_at, access_count 
                    FROM ace_semantic
                    WHERE active = 1 AND (? - COALESCE(created_at, 0)) > ?
                """, (now, age_threshold))

                decayed, deactivated = 0, 0
                for row in c.fetchall():
                    fid = row['id']
                    conf = row['confidence'] or 1.0
                    created = row['created_at'] or 0
                    access = row['access_count'] or 0

                    if access >= 5:
                        continue

                    age_days = (now - created) / 86400
                    new_conf = max(0.0, conf - decay_rate * age_days)

                    if new_conf < min_confidence:
                        c.execute("UPDATE ace_semantic SET active = 0, confidence = ? WHERE id = ?", (new_conf, fid))
                        deactivated += 1
                    else:
                        c.execute("UPDATE ace_semantic SET confidence = ? WHERE id = ?", (new_conf, fid))
                        decayed += 1

                conn.commit()
            finally:
                conn.close()

        if decayed or deactivated:
            print(f"🧹 Memory decay: {decayed} decayed, {deactivated} retired")
        return {'decayed': decayed, 'deactivated': deactivated}

    def maybe_compact_episodes(self, user_id, max_active=25):
        """Compact episodes only if enough time has passed since last compaction for this user."""
        now = time.time()
        last = self._compact_timestamps.get(user_id, 0)
        if (now - last) < self.COMPACT_INTERVAL:
            return 0

        self._compact_timestamps[user_id] = now

        with self.lock:
            conn = self._connect()
            try:
                c = conn.cursor()
                c.execute(
                    "SELECT COUNT(*) as cnt FROM ace_episodic WHERE user_id = ? AND active = 1",
                    (user_id,)
                )
                count = c.fetchone()['cnt']

                compacted = 0
                if count > max_active:
                    excess = count - max_active
                    c.execute("""
                        UPDATE ace_episodic SET active = 0
                        WHERE id IN (
                            SELECT id FROM ace_episodic
                            WHERE user_id = ? AND active = 1
                            ORDER BY id ASC LIMIT ?
                        )
                    """, (user_id, excess))
                    compacted = excess

                conn.commit()
            finally:
                conn.close()

        if compacted:
            print(f"📦 Compacted {compacted} old episodes for user {user_id}")
        return compacted

    # ──────────────────────────────────────────────
    # AMBIENT SUPPORT
    # ──────────────────────────────────────────────

    def get_ambient_context(self, channel_id=None):
        """Build context for ambient/eavesdrop decisions. Includes channel buffer."""
        with self.lock:
            conn = self._connect()
            try:
                # Entity state
                entity_block = self._format_entity_state(conn)

                # Temporal
                temporal = self._build_temporal_context("__ambient__", conn)

                # Recent ambient log
                c = conn.cursor()
                c.execute("SELECT message, timestamp FROM ambient_log ORDER BY timestamp DESC LIMIT 5")
                recent_ambients = c.fetchall()
            finally:
                conn.close()

        recent_str = ""
        if recent_ambients:
            lines = [f"  - \"{r['message'][:80]}\"" for r in recent_ambients]
            recent_str = "Recent ambient utterances (DO NOT repeat these):\n" + "\n".join(lines)

        # Channel buffer (in-memory, no lock needed)
        channel_activity = ""
        if channel_id:
            buf = self.get_channel_buffer(channel_id)
            if buf:
                channel_activity = f"\n[RECENT CHANNEL ACTIVITY]\n{buf}\n"

        return f"{temporal}\n{entity_block}\n{recent_str}\n{channel_activity}"

    def log_ambient(self, channel_id, message, context_summary=""):
        """Record an ambient message to prevent repetition."""
        with self.lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO ambient_log (channel_id, message, context_summary, timestamp) VALUES (?, ?, ?, ?)",
                    (str(channel_id), message, context_summary, time.time())
                )
                # Prune old ambient logs (keep last 50)
                conn.execute("""
                    DELETE FROM ambient_log WHERE id NOT IN (
                        SELECT id FROM ambient_log ORDER BY timestamp DESC LIMIT 50
                    )
                """)
                conn.commit()
            finally:
                conn.close()

    # ──────────────────────────────────────────────
    # SCHEMA SAFETY
    # ──────────────────────────────────────────────

    def _ensure_schema(self):
        """Verify critical tables exist; run migration if not."""
        try:
            with self.lock:
                conn = self._connect()
                try:
                    c = conn.cursor()
                    # Check all required tables
                    for table in ['ace_semantic', 'ace_episodic', 'ace_working_context',
                                  'entity_state', 'user_relationship', 'ambient_log']:
                        c.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                        if not c.fetchone():
                            raise RuntimeError(f"Missing table: {table}")
                    # Ensure entity_state singleton
                    c.execute("SELECT id FROM entity_state WHERE id = 1")
                    if not c.fetchone():
                        conn.execute("INSERT OR IGNORE INTO entity_state (id, updated_at) VALUES (1, ?)", (time.time(),))
                        conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            print(f"⚙️ Schema check failed ({e}), running migration...")
            # Import and run migration inline to avoid subprocess fragility
            try:
                from update_ace_db import migrate
                migrate()
            except Exception as e2:
                print(f"❌ Migration failed: {e2}")
                print("   Run 'python update_ace_db.py' manually before starting the bot.")
                raise SystemExit(1)
