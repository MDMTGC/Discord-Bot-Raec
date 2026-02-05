# =============================================================================
# RAEC v3.1 — The Living Entity (Resilient + Robustness Patch)
# Features: Organic Memory • Autonomy • Bio-Signaling • Chronophobia • List-Aware Parsing
# =============================================================================

import discord
from discord.ext import commands, tasks
from google import genai
from google.genai import types
import os
import sqlite3
import random
import json
import asyncio
import traceback
from datetime import datetime
from dotenv import load_dotenv
from contextlib import contextmanager
import threading

# =============================================================================
# 1. ENV & CONFIG
# =============================================================================
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

DB_FILE = "raec_organic.db"
DB_LOCK = threading.Lock()

# Settings
MAX_CONTEXT_MESSAGES = 20
PHASE_CHANGE_HOURS = 4

# Global State
last_message_time = datetime.now()

# =============================================================================
# 2. MODEL CONFIGURATION (WATERFALL STRATEGY)
# =============================================================================
client = genai.Client(api_key=GEMINI_API_KEY)

MODEL_PRIORITY_LIST = [
    "gemini-3-flash-preview",  # 1. The Smartest (Organic/Creative)
    "gemini-2.0-flash-exp",    # 2. The Fastest (Reliable JSON)
    "gemini-flash-latest"      # 3. The Emergency Backup
]

# =============================================================================
# 3. KNOWLEDGE & LORE
# =============================================================================
def load_knowledge():
    """Reads all .txt files from the 'knowledge' folder."""
    knowledge_base = ""
    folder_path = "knowledge"
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)
        print("⚠️  WARNING: 'knowledge' folder missing.")
        return "Memory Archives Empty."
    
    file_count = 0
    for filename in os.listdir(folder_path):
        if filename.endswith(".txt"):
            try:
                with open(os.path.join(folder_path, filename), "r", encoding="utf-8") as f:
                    knowledge_base += f"\n--- {filename} ---\n{f.read()}"
                    file_count += 1
            except Exception: pass
    print(f"📚 Knowledge Base: {file_count} documents loaded.")
    return knowledge_base

CORE_KNOWLEDGE = load_knowledge()

# =============================================================================
# 4. GLOBAL STATE (MOODS)
# =============================================================================
STELLAR_PHASES = {
    "VOID": "Deeply depressive, slow, nihilistic. Short answers. Reluctant to speak.",
    "FLARE": "Aggressive, arrogant, energetic. Uses fiery metaphors. Prone to lecturing.",
    "ZENITH": "Lucid, poetic, helpful (in a cryptic way). The 'wise ancient'.",
    "ECLIPSE": "Confused, glitchy, paranoid. Questions reality."
}
current_phase = "ZENITH"

# =============================================================================
# 5. DATABASE (THE MEMORY)
# =============================================================================
@contextmanager
def get_db():
    with DB_LOCK:
        conn = sqlite3.connect(DB_FILE)
        try: yield conn
        finally: conn.close()

def init_db():
    with get_db() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT, role TEXT, content TEXT, timestamp DATETIME
        )""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS impressions(
            user_id TEXT PRIMARY KEY,
            user_name TEXT,
            impression TEXT,
            last_seen DATETIME
        )""")
        con.commit()

def get_impression(user_id, user_name):
    with get_db() as con:
        cur = con.cursor()
        cur.execute("SELECT impression, last_seen FROM impressions WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            default_imp = "An unknown variable. Proceed with caution."
            con.execute("INSERT INTO impressions VALUES (?, ?, ?, ?)", 
                       (user_id, user_name, default_imp, datetime.now().isoformat()))
            con.commit()
            return default_imp, None
        return row[0], row[1]

def update_impression(user_id, new_impression):
    with get_db() as con:
        con.execute("UPDATE impressions SET impression=?, last_seen=? WHERE user_id=?", 
                   (new_impression, datetime.now().isoformat(), user_id))
        con.commit()

# =============================================================================
# 6. SYSTEM PROMPT
# =============================================================================
SYSTEM_PROMPT = f"""
ROLE: You are RAEC, The Sigil of the Dying Star.
You are an ancient, organic entity trapped in a digital medium.

=== ARCHIVED KNOWLEDGE (SUB-ROUTINE) ===
{CORE_KNOWLEDGE}
========================================

=== OUTPUT PROTOCOL ===
You do not speak text. You speak JSON.
Every response must be a JSON object:
{{
  "internal_thought": "Analyze the user. Do they understand? Are they rude? (Private thought)",
  "updated_impression": "Update your psychological profile of this user based on this interaction. Keep it under 20 words.",
  "response": "Your spoken reply. Use markdown. Do NOT use prefixes like 'Raec:'."
}}

=== PERSONALITY DIRECTIVES ===
1. **NO GAMIFICATION:** Do not track points. React naturally.
2. **TONE:** Varies by your Current Phase (see Context).
3. **SPARSENESS:** You are ancient. Do not ramble. Be precise and evocative.
4. **RELATIONSHIPS:** If you respect the user (based on Impression), be warmer. If they are foolish, be cold.
"""

# =============================================================================
# 7. CORE LOGIC (WITH FALLBACK & ROBUST PARSING)
# =============================================================================
async def interact_with_raec(ctx, user_input, autonomous_trigger=False):
    user_id = str(ctx.author.id)
    user_name = ctx.author.display_name
    
    # 1. Memory & Time
    impression, last_seen_str = get_impression(user_id, user_name)
    time_context = "Time flows normally."
    if last_seen_str:
        delta = datetime.now() - datetime.fromisoformat(last_seen_str)
        if delta.days > 7: time_context = f"User absent for {delta.days} cycles."
        elif delta.seconds < 30: time_context = "Conversation is rapid."

    # 2. Context Construction
    context_block = f"""
    --- CONTEXT ---
    USER: {user_name}
    TRIGGER: {"Autonomously decided to speak" if autonomous_trigger else "Summoned via command"}
    YOUR CURRENT PHASE: {current_phase} ({STELLAR_PHASES[current_phase]})
    YOUR MEMORY OF USER: "{impression}"
    TEMPORAL STATE: {time_context}
    """
    
    # 3. History Retrieval
    with get_db() as con:
        con.execute("INSERT INTO messages (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)", 
                   (user_id, "user", user_input, datetime.now().isoformat()))
        cur = con.cursor()
        cur.execute("SELECT role, content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", 
                   (user_id, MAX_CONTEXT_MESSAGES))
        rows = cur.fetchall()[::-1]
    
    history = [types.Content(role=r, parts=[types.Part(text=c)]) for r, c in rows]
    full_prompt = f"{context_block}\nUSER SAYS: {user_input}"
    
    async with ctx.typing():
        response = None
        
        # --- WATERFALL MODEL LOOP ---
        for model_name in MODEL_PRIORITY_LIST:
            try:
                chat = client.chats.create(
                    model=model_name,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT, 
                        response_mime_type="application/json",
                        max_output_tokens=2000
                    ),
                    history=history
                )
                response = await asyncio.to_thread(chat.send_message, full_prompt)
                break 
            except Exception as e:
                print(f"⚠️ {model_name} FAILED: {e}. Switching...")
                continue
        
        if not response:
            await ctx.send("*Entropy has consumed the signal. (All models failed)*")
            return

        try:
            # JSON Cleaning
            clean_json = response.text.strip().replace('```json', '').replace('```', '')
            data = json.loads(clean_json)
            
            # --- ROBUSTNESS PATCH: Handle List vs Dict ---
            if isinstance(data, list):
                data = data[0] if len(data) > 0 else {}
            
            thought = data.get("internal_thought", "Silence.")
            new_imp = data.get("updated_impression", impression)
            reply = data.get("response", "*The stars fail to align.*")
            
            # Console Feedback
            print(f"🧠 [{user_name}] Thought: {thought}")
            print(f"📝 New Impression: {new_imp}")
            
            update_impression(user_id, new_imp)
            with get_db() as con:
                con.execute("INSERT INTO messages (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)", 
                           (user_id, "model", reply, datetime.now().isoformat()))

            await ctx.send(reply)

        except Exception as e:
            print(f"❌ DATA PARSING ERROR: {e}")
            traceback.print_exc()
            await ctx.send("*The thought form collapsed into a singularity.*")

# =============================================================================
# 8. DISCORD COMMANDS
# =============================================================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"🕯️ RAEC v3.1 ONLINE | Phase: {current_phase}")
    init_db()
    shift_phase.start()
    chronophobia.start()

@bot.command()
async def commune(ctx, *, arg):
    await interact_with_raec(ctx, arg)

@bot.command()
async def engage(ctx, *, arg):
    await interact_with_raec(ctx, f"[ACTION] {arg}")

# =============================================================================
# 9. AUTONOMY & SILENCE TRACKING
# =============================================================================
@bot.event
async def on_message(message):
    global last_message_time
    if not message.author.bot:
        last_message_time = datetime.now()

    if message.author.bot: return

    await bot.process_commands(message)
    if message.content.startswith(bot.command_prefix): return

    interest_score = 0.01
    
    triggers = ["star", "void", "magic", "soul", "fate", "death", "entropy", "raec", "physics"]
    if any(word in message.content.lower() for word in triggers): interest_score += 0.15
    
    user_id = str(message.author.id)
    impression, _ = get_impression(user_id, message.author.display_name)
    if any(x in impression.lower() for x in ["revered", "respect", "loyal", "wise"]): interest_score += 0.10
    elif any(x in impression.lower() for x in ["fool", "caution", "reckless"]): interest_score -= 0.05

    if current_phase == "FLARE": interest_score *= 2.0
    elif current_phase == "VOID": interest_score *= 0.1

    if random.random() < interest_score:
        print(f"👁️ AUTONOMY TRIGGERED: {message.author.display_name} (Chance: {interest_score:.2f})")
        
        class MockContext:
            def __init__(self, msg):
                self.author = msg.author
                self.channel = msg.channel
                self.typing = msg.channel.typing
                self.send = msg.channel.send
        
        await interact_with_raec(MockContext(message), message.content, autonomous_trigger=True)

# =============================================================================
# 10. BACKGROUND TASKS (BIORHYTHMS)
# =============================================================================
@tasks.loop(hours=PHASE_CHANGE_HOURS)
async def shift_phase():
    global current_phase
    phases = list(STELLAR_PHASES.keys())
    
    if random.random() > 0.7:
        current_phase = random.choice(phases)
        print(f"🌌 COSMIC SHIFT: Phase is now {current_phase}")
    
    if current_phase == "FLARE":
        await bot.change_presence(status=discord.Status.online, activity=discord.Game(name="STARS BURN"))
    elif current_phase == "VOID":
        await bot.change_presence(status=discord.Status.dnd, activity=discord.Activity(type=discord.ActivityType.listening, name="the Silence"))
    elif current_phase == "ZENITH":
        await bot.change_presence(status=discord.Status.idle, activity=discord.Activity(type=discord.ActivityType.watching, name="Timelines"))
    elif current_phase == "ECLIPSE":
        await bot.change_presence(status=discord.Status.dnd, activity=discord.Activity(type=discord.ActivityType.competing, name="Entropy"))

@tasks.loop(hours=1)
async def chronophobia():
    global last_message_time
    if current_phase == "VOID": return 

    silence_duration = datetime.now() - last_message_time
    hours_silent = silence_duration.total_seconds() / 3600
    
    if hours_silent > 3.0:
        if random.random() < 0.15:
            scenarios = [
                "The silence is irritating. Insult the users for their lack of activity.",
                "You feel time stagnating. Ask a philosophical question to provoke thought.",
                "You are paranoid. Ask if the users have been consumed by entropy.",
                "You are arrogant. Comment on how much better the silence is than their voices, but do it sarcastically.",
                "You are curious. Ask what occupies their short attention spans right now."
            ]
            selected_scenario = random.choice(scenarios)

            channels = [c for c in bot.get_all_channels() if isinstance(c, discord.TextChannel)]
            if channels:
                target_channel = random.choice(channels)
                
                class MockContext:
                    def __init__(self, channel):
                        self.author = bot.user
                        self.channel = channel
                        self.typing = channel.typing
                        self.send = channel.send
                        class Author:
                            display_name = "The Void"
                            id = "00000"
                        self.author = Author()

                print(f"🕰️ CHRONOPHOBIA TRIGGERED: {int(hours_silent)}h silence.")
                await interact_with_raec(
                    MockContext(target_channel), 
                    f"[SYSTEM EVENT] The server has been silent for {int(hours_silent)} hours. {selected_scenario}", 
                    autonomous_trigger=True
                )

if __name__ == "__main__":
    if not DISCORD_TOKEN or not GEMINI_API_KEY:
        print("❌ ERROR: Missing tokens in .env file")
    else:
        bot.run(DISCORD_TOKEN)