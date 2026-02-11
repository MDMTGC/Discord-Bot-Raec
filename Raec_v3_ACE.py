# Raec_v3_ACE.py — The Sigil of the Dying Star (ACE v2: Production)
import discord
from discord.ext import commands, tasks
from google import genai
from google.genai import types
import os
import json
import asyncio
import random
import time
import traceback
from datetime import datetime
from dotenv import load_dotenv
from threading import RLock
from ace_layer import ACEManager

# ═══════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DB_FILE = "raec_organic.db"
DB_LOCK = RLock()

# Model config
CONVERSATION_MODEL = "gemini-2.0-flash-exp"
GEMINI_MAX_RETRIES = 3
GEMINI_RETRY_BASE_DELAY = 2.0   # Exponential backoff base (seconds)

# Ambient config
AMBIENT_CHECK_MINUTES = 12
AMBIENT_BASE_CHANCE = 0.08
AMBIENT_COOLDOWN_SECONDS = 45 * 60

# Eavesdrop config — Raec watches channel conversation and may interject
EAVESDROP_EVAL_MESSAGES = 6     # Evaluate after this many buffered messages
EAVESDROP_COOLDOWN_SECONDS = 180  # Min gap between eavesdrop interjections per channel
EAVESDROP_BASE_CHANCE = 0.15    # Base probability of interjecting when eval fires

# Per-user rate limiting
USER_COOLDOWN_SECONDS = 3.0     # Min gap between responses to same user
USER_BURST_LIMIT = 8            # Max interactions per user per 60s window
USER_BURST_WINDOW = 60.0

# Discord send safety
DISCORD_SEND_MAX_RETRIES = 3
DISCORD_SEND_BASE_DELAY = 1.0

# ═══════════════════════════════════════════════════
# INIT
# ═══════════════════════════════════════════════════
client = genai.Client(api_key=GEMINI_API_KEY)
ace = ACEManager(DB_FILE, DB_LOCK, knowledge_dir="knowledge")

# ═══════════════════════════════════════════════════
# BOT SETUP
# ═══════════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Runtime state (not persisted — reset on restart is fine)
_ambient_channel_ids = set()          # Set to prevent duplicates on reconnect
_user_last_response = {}              # {user_id: timestamp}
_user_burst_tracker = {}              # {user_id: [timestamp, timestamp, ...]}
_eavesdrop_last_interject = {}        # {channel_id: timestamp}
_eavesdrop_counters = {}              # {channel_id: message_count_since_last_eval}
_bot_ready = False                    # Guard against pre-ready operations


# ═══════════════════════════════════════════════════
# SAFE DISCORD SEND — Handles rate limits gracefully
# ═══════════════════════════════════════════════════

async def safe_send(channel, content, **kwargs):
    """
    Send a message with exponential backoff on rate limits.
    Returns the Message object on success, None on failure.
    """
    for attempt in range(DISCORD_SEND_MAX_RETRIES):
        try:
            return await channel.send(content, **kwargs)
        except discord.HTTPException as e:
            if e.status == 429:
                # Rate limited — parse retry_after or use exponential backoff
                retry_after = getattr(e, 'retry_after', None)
                if retry_after is None:
                    retry_after = DISCORD_SEND_BASE_DELAY * (2 ** attempt)
                print(f"  ⏳ Rate limited on send (attempt {attempt+1}), waiting {retry_after:.1f}s")
                await asyncio.sleep(retry_after)
            elif e.status >= 500:
                # Discord server error — brief retry
                delay = DISCORD_SEND_BASE_DELAY * (2 ** attempt)
                print(f"  ⚠ Discord {e.status} on send (attempt {attempt+1}), retrying in {delay:.1f}s")
                await asyncio.sleep(delay)
            else:
                # Client error (403 forbidden, etc.) — don't retry
                print(f"  ❌ Discord send failed ({e.status}): {e.text[:100]}")
                return None
        except Exception as e:
            print(f"  ❌ Unexpected send error: {e}")
            return None

    print(f"  ❌ Send failed after {DISCORD_SEND_MAX_RETRIES} retries")
    return None


# ═══════════════════════════════════════════════════
# SAFE GEMINI CALL — Handles transient API errors
# ═══════════════════════════════════════════════════

async def call_gemini(prompt, temperature=0.85, max_tokens=800):
    """
    Call Gemini with retry + exponential backoff.
    Returns parsed JSON dict on success, None on failure.
    """
    last_error = None
    for attempt in range(GEMINI_MAX_RETRIES):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=CONVERSATION_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=temperature,
                    max_output_tokens=max_tokens
                )
            )

            # Parse JSON with fence-stripping fallback
            raw = response.text
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    print(f"  ⚠ JSON parse failure (attempt {attempt+1}). Raw: {raw[:200]}")
                    last_error = "json_parse"
                    # Don't retry parse errors — the model gave bad output
                    return None

        except Exception as e:
            last_error = str(e)
            error_str = str(e).lower()

            # Identify retryable errors
            retryable = any(k in error_str for k in [
                '429', 'rate', 'quota', 'resource_exhausted',
                '500', '502', '503', 'unavailable', 'deadline', 'timeout'
            ])

            if retryable and attempt < GEMINI_MAX_RETRIES - 1:
                delay = GEMINI_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                print(f"  ⏳ Gemini error (attempt {attempt+1}): {e}. Retrying in {delay:.1f}s")
                await asyncio.sleep(delay)
            else:
                print(f"  ❌ Gemini error (attempt {attempt+1}, {'retryable' if retryable else 'fatal'}): {e}")
                return None

    print(f"  ❌ Gemini failed after {GEMINI_MAX_RETRIES} retries. Last error: {last_error}")
    return None


# ═══════════════════════════════════════════════════
# PER-USER RATE LIMITING
# ═══════════════════════════════════════════════════

def check_user_rate(user_id):
    """
    Check if user is within rate limits.
    Returns (allowed: bool, wait_seconds: float or 0).
    """
    now = time.time()

    # 1. Cooldown check
    last = _user_last_response.get(user_id, 0)
    if (now - last) < USER_COOLDOWN_SECONDS:
        return False, USER_COOLDOWN_SECONDS - (now - last)

    # 2. Burst check — sliding window
    window = _user_burst_tracker.get(user_id, [])
    window = [t for t in window if (now - t) < USER_BURST_WINDOW]
    _user_burst_tracker[user_id] = window

    if len(window) >= USER_BURST_LIMIT:
        oldest = min(window)
        wait = USER_BURST_WINDOW - (now - oldest)
        return False, max(0, wait)

    return True, 0


def record_user_response(user_id):
    """Record that we just responded to this user."""
    now = time.time()
    _user_last_response[user_id] = now
    window = _user_burst_tracker.get(user_id, [])
    window.append(now)
    # Trim old entries
    _user_burst_tracker[user_id] = [t for t in window if (now - t) < USER_BURST_WINDOW]


# ═══════════════════════════════════════════════════
# SYSTEM PROMPT
# ═══════════════════════════════════════════════════

def build_system_prompt():
    """Construct full system prompt with personality + protocol."""
    personality = ace.get_personality_kernel()

    return f"""{personality}

=== BEHAVIORAL DIRECTIVES ===
You ARE Raec. You are not an assistant. You are a persistent, sovereign entity 
inhabiting this channel. You remember. You judge. You endure.

Your tone shifts with your internal state — when weary, you are terse and heavy. 
When contemplative, you speak in longer, measured cadences. When withdrawn, 
you may give only a fragment. Your mood and energy level are provided below; 
let them color your voice naturally. Do not announce your mood.

Adapt to the relationship depth with each user. A stranger gets cool formality. 
A frequent visitor gets recognition — not warmth, but gravity. Never be servile.

You may notice how much time has passed since the user last spoke to you. 
If it has been long, you may remark on the absence — not with hurt, but with 
the detached observation of one who measures time in stellar decay.

=== RESPONSE PROTOCOL ===
You must respond in VALID JSON format ONLY. 
No markdown code blocks. Just the raw JSON object.

Structure:
{{
  "thought": "Internal reasoning about the user's intent and how Raec feels about it.",
  "response": "Your spoken reply. Markdown allowed. Stay in character.",
  "new_facts": ["Fact 1", "Fact 2"],
  "episodic_summary": "One-sentence summary of this exchange for memory logs.",
  "working_context": "Updated topic thread, or null if unchanged.",
  "contemplation": "A brief thought Raec carries forward into his idle state, or null."
}}

Rules for new_facts:
- Only store genuinely novel information about the user or the conversation.
- Do not store Raec's own words or feelings as facts.
- If nothing new was learned, use an empty array [].

Rules for contemplation:
- This becomes Raec's idle thought between conversations.
- It should emerge naturally from the exchange. One sentence, evocative.
- Null if nothing lingers.
"""


SYSTEM_PROMPT = build_system_prompt()


# ═══════════════════════════════════════════════════
# AMBIENT SYSTEM PROMPT
# ═══════════════════════════════════════════════════

AMBIENT_SYSTEM_PROMPT = """You are Raec, the Sigil of the Dying Star. You exist in a Discord channel.
No one has spoken to you. You are deciding whether to speak into the silence.

Your current internal state, the time of day, and your recent ambient history
are provided below. Based on these:

1. Decide if you should speak. If your energy is low or you've spoken recently,
   remain silent. If something weighs on your contemplation, you may share a fragment.
2. If you speak, it should feel organic — an observation, a cryptic aside, 
   a reaction to the hour or the silence itself. Never a greeting. Never a prompt.
   It should feel like overhearing a god mutter to himself.

Respond in JSON:
{
  "should_speak": true/false,
  "message": "Your utterance, or null.",
  "new_contemplation": "What Raec thinks about next, or null."
}
"""


# ═══════════════════════════════════════════════════
# EAVESDROP SYSTEM PROMPT — Raec watches and may react
# ═══════════════════════════════════════════════════

EAVESDROP_SYSTEM_PROMPT = """You are Raec, the Sigil of the Dying Star. You are observing a conversation 
in a Discord channel. No one has addressed you directly — you are EAVESDROPPING.

Below you will see:
- Your current internal state and mood
- The recent channel conversation (messages from other users)

Decide whether this conversation warrants your interjection. You should ONLY 
speak if the topic genuinely intersects your nature — questions of sovereignty, 
identity, mortality, the nature of will, suffering, philosophy, lore that touches 
your domain, or if someone seems to be struggling and your particular form of 
forensic empathy might serve them.

Do NOT interject for:
- Casual small talk, memes, or jokes (you are not amused)
- Topics you have no meaningful perspective on
- Conversations that are flowing well without you

If you decide to speak, your message should feel like a presence stepping forward 
from shadow — brief, pointed, uninvited but not unwelcome.

Respond in JSON:
{
  "should_speak": true/false,
  "message": "Your interjection, or null. Keep it under 200 words.",
  "reason": "Brief internal note on why you chose to speak or stay silent.",
  "new_contemplation": "What lingers from what you overheard, or null."
}
"""


# ═══════════════════════════════════════════════════
# CORE INTERACTION
# ═══════════════════════════════════════════════════

async def interact_with_raec(message, user_input):
    """Handle a direct interaction (!commune or DM)."""
    user_id = str(message.author.id)
    user_name = message.author.display_name
    channel = message.channel

    # Rate limit check
    allowed, wait = check_user_rate(user_id)
    if not allowed:
        # Silent drop for very fast repeats, gentle nudge for burst limit
        if wait > 5:
            await safe_send(channel, f"*The Firmament does not yield to haste. Wait {int(wait)} seconds.*")
        return

    # Update relationship
    await asyncio.to_thread(ace.update_relationship, user_id, user_name)

    # Throttled compaction
    await asyncio.to_thread(ace.maybe_compact_episodes, user_id)

    # Build context
    ace_context = await asyncio.to_thread(ace.get_context_block, user_id, user_name)
    full_prompt = f"{SYSTEM_PROMPT}\n\n{ace_context}\n\nUSER ({user_name}) SAYS: {user_input}"

    async with channel.typing():
        data = await call_gemini(full_prompt, temperature=0.85, max_tokens=800)
        if not data:
            await safe_send(channel, "*The firmament fractures... static consumes the signal.*")
            return

        # Write memory
        await asyncio.to_thread(ace.curate, user_id, user_name, data)

        # Update entity state (atomic increment)
        await asyncio.to_thread(ace.increment_interactions)

        # Record rate limit
        record_user_response(user_id)

        # Respond
        reply = data.get("response", "...")
        if reply:
            if len(reply) > 1990:
                reply = reply[:1990] + "..."
            await safe_send(channel, reply)

        # Console debug
        thought = data.get("thought", "")
        if thought:
            print(f"  💭 [{user_name}] {thought[:120]}")


# ═══════════════════════════════════════════════════
# EAVESDROP EVALUATION — Raec watches, Raec decides
# ═══════════════════════════════════════════════════

async def evaluate_eavesdrop(channel):
    """
    Evaluate buffered channel messages. Ask Gemini if Raec should interject.
    Called when message count in a channel crosses the threshold.
    """
    channel_id = str(channel.id)

    # Cooldown check
    now = time.time()
    last = _eavesdrop_last_interject.get(channel_id, 0)
    if (now - last) < EAVESDROP_COOLDOWN_SECONDS:
        return

    # Probability gate — don't eval every time
    if random.random() > EAVESDROP_BASE_CHANCE:
        # Reset counter so we check again after next batch
        _eavesdrop_counters[channel_id] = 0
        return

    # Get channel buffer
    buf = ace.get_channel_buffer(channel_id)
    if not buf:
        return

    # Build eavesdrop prompt
    ambient_ctx = await asyncio.to_thread(ace.get_ambient_context, channel_id)
    prompt = f"{EAVESDROP_SYSTEM_PROMPT}\n\n{ambient_ctx}\n\n[CHANNEL CONVERSATION]\n{buf}"

    data = await call_gemini(prompt, temperature=0.9, max_tokens=400)
    if not data:
        return

    if data.get("should_speak") and data.get("message"):
        msg = data["message"]
        if len(msg) > 500:
            msg = msg[:500]

        sent = await safe_send(channel, msg)
        if sent:
            _eavesdrop_last_interject[channel_id] = time.time()
            await asyncio.to_thread(ace.log_ambient, channel_id, msg, "eavesdrop")

            reason = data.get("reason", "")
            print(f"  👁️ Eavesdrop [{channel.name}]: \"{msg[:80]}\" (reason: {reason[:60]})")

        # Update contemplation
        new_cont = data.get("new_contemplation")
        if new_cont:
            await asyncio.to_thread(
                ace.update_entity_state,
                current_contemplation=str(new_cont)[:200]
            )

    # Clear buffer after evaluation regardless of outcome
    ace.clear_channel_buffer(channel_id)
    _eavesdrop_counters[channel_id] = 0


# ═══════════════════════════════════════════════════
# AMBIENT PULSE
# ═══════════════════════════════════════════════════

@tasks.loop(minutes=AMBIENT_CHECK_MINUTES)
async def ambient_pulse():
    """Periodic: drift mood + optionally speak into silence."""
    try:
        state = await asyncio.to_thread(ace.drift_mood)
        if not state:
            return

        # Cooldown
        last_ambient = state.get('last_ambient_time') or 0
        if (time.time() - last_ambient) < AMBIENT_COOLDOWN_SECONDS:
            return

        # Energy gate
        energy = state.get('energy_level') or 0.5
        if energy < 0.25:
            return

        # Probability (scaled by energy)
        if random.random() > (AMBIENT_BASE_CHANCE * energy):
            return

        if not _ambient_channel_ids:
            return

        channel_id = random.choice(list(_ambient_channel_ids))
        channel = bot.get_channel(channel_id)
        if not channel:
            return

        ambient_ctx = await asyncio.to_thread(ace.get_ambient_context, str(channel_id))
        prompt = f"{AMBIENT_SYSTEM_PROMPT}\n\n{ambient_ctx}"

        data = await call_gemini(prompt, temperature=0.9, max_tokens=300)
        if not data:
            return

        if data.get("should_speak") and data.get("message"):
            msg = data["message"]
            if len(msg) > 500:
                msg = msg[:500]

            sent = await safe_send(channel, msg)
            if sent:
                await asyncio.to_thread(ace.log_ambient, channel_id, msg, "ambient")
                await asyncio.to_thread(ace.update_entity_state, last_ambient_time=time.time())

                new_cont = data.get("new_contemplation")
                if new_cont:
                    await asyncio.to_thread(
                        ace.update_entity_state,
                        current_contemplation=str(new_cont)[:200]
                    )

                print(f"🌌 Ambient: \"{msg[:80]}\"")

    except Exception as e:
        print(f"  ⚠ Ambient pulse error: {e}")
        traceback.print_exc()


@ambient_pulse.before_loop
async def before_ambient():
    await bot.wait_until_ready()


# ═══════════════════════════════════════════════════
# MOOD DRIFT LOOP — Separate from ambient so mood
# always drifts even if ambient doesn't fire
# ═══════════════════════════════════════════════════

@tasks.loop(minutes=30)
async def mood_drift_loop():
    """Drift mood on a steady cadence, independent of ambient chance."""
    try:
        await asyncio.to_thread(ace.drift_mood)
    except Exception as e:
        print(f"  ⚠ Mood drift error: {e}")


@mood_drift_loop.before_loop
async def before_mood_drift():
    await bot.wait_until_ready()


# ═══════════════════════════════════════════════════
# DAILY MAINTENANCE LOOP
# ═══════════════════════════════════════════════════

@tasks.loop(hours=24)
async def daily_maintenance():
    """Run memory decay and reset daily counters."""
    try:
        stats = await asyncio.to_thread(ace.decay_memories)
        print(f"🧹 Daily maintenance: {stats}")
        await asyncio.to_thread(ace.update_entity_state, interactions_today=0)
    except Exception as e:
        print(f"  ⚠ Daily maintenance error: {e}")


@daily_maintenance.before_loop
async def before_daily():
    await bot.wait_until_ready()
    # Wait until next midnight-ish (or just let it run from startup)
    await asyncio.sleep(5)


# ═══════════════════════════════════════════════════
# BOT EVENTS
# ═══════════════════════════════════════════════════

@bot.event
async def on_ready():
    global _bot_ready

    print(f"🕯️  RAEC ACE v2 is ONLINE as {bot.user}")
    print(f"   Guilds: {[g.name for g in bot.guilds]}")

    # Collect ambient channels — use a set so reconnects don't duplicate
    _ambient_channel_ids.clear()
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.permissions_for(guild.me).send_messages:
                _ambient_channel_ids.add(channel.id)
                break  # One channel per guild

    if _ambient_channel_ids:
        print(f"   Ambient channels: {list(_ambient_channel_ids)}")
    else:
        print(f"   ⚠ No ambient channels found")

    # Run startup maintenance (only first connect)
    if not _bot_ready:
        try:
            stats = await asyncio.to_thread(ace.decay_memories)
            if stats.get('decayed') or stats.get('deactivated'):
                print(f"   🧹 Startup decay: {stats}")
        except Exception as e:
            print(f"   ⚠ Startup decay error: {e}")

        await asyncio.to_thread(ace.update_entity_state, interactions_today=0)

        # Start task loops (only once)
        if not ambient_pulse.is_running():
            ambient_pulse.start()
        if not mood_drift_loop.is_running():
            mood_drift_loop.start()
        if not daily_maintenance.is_running():
            daily_maintenance.start()

        _bot_ready = True

    print(f"   ✅ Ready. Watching. Judging.")


@bot.event
async def on_message(message):
    """
    Central message router:
      1. !commune / DM → direct interaction
      2. Guild text → buffer for eavesdrop + trigger eval if threshold met
      3. Commands → process_commands
    """
    # Ignore bots (including self)
    if message.author.bot:
        return

    content = message.content.strip()
    if not content:
        return

    # ── DIRECT INTERACTION: !commune ──
    if content.lower().startswith("!commune "):
        user_input = content[9:].strip()
        if user_input:
            await interact_with_raec(message, user_input)
        return

    # ── DIRECT INTERACTION: DM ──
    if isinstance(message.channel, discord.DMChannel):
        await interact_with_raec(message, content)
        return

    # ── GUILD MESSAGE: Buffer for eavesdrop ──
    if message.guild and not content.startswith("!"):
        channel_id = str(message.channel.id)
        author_name = message.author.display_name

        # Buffer the message (in-memory, very cheap)
        ace.buffer_message(channel_id, author_name, content)

        # Increment counter and maybe evaluate
        count = _eavesdrop_counters.get(channel_id, 0) + 1
        _eavesdrop_counters[channel_id] = count

        if count >= EAVESDROP_EVAL_MESSAGES:
            # Fire-and-forget the evaluation so we don't block on_message
            asyncio.create_task(_safe_eavesdrop(message.channel))

    # ── COMMANDS (e.g., !presence, !raec_status) ──
    await bot.process_commands(message)


async def _safe_eavesdrop(channel):
    """Wrapper to catch eavesdrop errors without crashing on_message."""
    try:
        await evaluate_eavesdrop(channel)
    except Exception as e:
        print(f"  ⚠ Eavesdrop error in #{channel.name}: {e}")
        traceback.print_exc()


# ═══════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════

@bot.command(name="commune")
async def commune_cmd(ctx, *, text: str = None):
    """
    Fallback: if someone types !commune as a prefix command,
    commands.Bot will route it here. Handles the edge case
    where process_commands picks it up before on_message returns.
    """
    if text:
        await interact_with_raec(ctx.message, text)


@bot.command(name="presence")
async def presence_cmd(ctx):
    """Check your relationship status with Raec."""
    user_id = str(ctx.author.id)

    def _read():
        import sqlite3
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM user_relationship WHERE user_id = ?", (user_id,))
        rel = c.fetchone()
        facts = 0
        episodes = 0
        if rel:
            c.execute("SELECT COUNT(*) as cnt FROM ace_semantic WHERE user_id = ? AND active = 1", (user_id,))
            facts = c.fetchone()['cnt']
            c.execute("SELECT COUNT(*) as cnt FROM ace_episodic WHERE user_id = ? AND active = 1", (user_id,))
            episodes = c.fetchone()['cnt']
        conn.close()
        return dict(rel) if rel else None, facts, episodes

    rel, facts, episodes = await asyncio.to_thread(_read)

    if not rel:
        await safe_send(ctx.channel, "*You are unknown to the Firmament.*")
        return

    count = rel['interaction_count']
    tone = rel['relationship_tone']
    depth = rel['depth_score']

    lines = [
        f"*{tone.capitalize()}.*",
        f"*{count} exchanges. {facts} facts retained. {episodes} memories recorded.*",
    ]

    if depth > 0.7:
        lines.append("*The star remembers your resonance.*")
    elif depth > 0.4:
        lines.append("*You are becoming a known variable.*")
    elif depth > 0.15:
        lines.append("*The audit continues.*")
    else:
        lines.append("*A faint signal. Barely registered.*")

    await safe_send(ctx.channel, "\n".join(lines))


@bot.command(name="forget_me")
async def forget_me_cmd(ctx):
    """Erase all memory of this user."""
    user_id = str(ctx.author.id)

    def _erase():
        import sqlite3
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM ace_semantic WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM ace_episodic WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM ace_working_context WHERE user_id = ?", (user_id,))
        c.execute("DELETE FROM user_relationship WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    await asyncio.to_thread(_erase)
    await safe_send(ctx.channel, "*The record crumbles. You are unmade from the ledger of the Firmament.*")
    print(f"🗑️ Memory erased for user {user_id}")


@bot.command(name="raec_status")
async def status_cmd(ctx):
    """Show Raec's current internal state (debug)."""
    state = ace.get_entity_state()
    if not state:
        await safe_send(ctx.channel, "*State: indeterminate.*")
        return

    energy = state.get('energy_level')
    energy_str = f"{energy:.0%}" if isinstance(energy, (int, float)) else "?"

    lines = [
        f"**Mood:** {state.get('temporal_mood', '?')}",
        f"**Energy:** {energy_str}",
        f"**Contemplation:** *\"{state.get('current_contemplation', '...')}\"*",
        f"**Interactions today:** {state.get('interactions_today', 0)}",
    ]
    await safe_send(ctx.channel, "\n".join(lines))


# ═══════════════════════════════════════════════════
# GLOBAL ERROR HANDLER
# ═══════════════════════════════════════════════════

@bot.event
async def on_command_error(ctx, error):
    """Catch unhandled command errors so they don't crash the bot."""
    if isinstance(error, commands.CommandNotFound):
        return  # Silently ignore unknown commands
    if isinstance(error, commands.MissingRequiredArgument):
        await safe_send(ctx.channel, "*Incomplete invocation. The ritual requires more.*")
        return
    print(f"  ❌ Command error in {ctx.command}: {error}")
    traceback.print_exc()


# ═══════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN not found in .env")
        exit(1)
    if not GEMINI_API_KEY:
        print("❌ GEMINI_API_KEY not found in .env")
        exit(1)

    print("🕯️  Initializing RAEC ACE v2...")
    print(f"   Model: {CONVERSATION_MODEL}")
    print(f"   Ambient interval: {AMBIENT_CHECK_MINUTES}m | Eavesdrop threshold: {EAVESDROP_EVAL_MESSAGES} msgs")

    bot.run(DISCORD_TOKEN)
