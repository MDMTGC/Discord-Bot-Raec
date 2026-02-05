import google.generativeai as genai

# PASTE YOUR KEY HERE
GEMINI_API_KEY = 'AIzaSyBrdBMmoW-RSjdgxVfrwUA3yUuudXK1ZyE'

genai.configure(api_key=GEMINI_API_KEY)

print("--- CHECKING AVAILABLE MODELS ---")
try:
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"✅ AVAILABLE: {m.name}")
except Exception as e:
    print(f"❌ ERROR: {e}")
input("Press Enter to close...")