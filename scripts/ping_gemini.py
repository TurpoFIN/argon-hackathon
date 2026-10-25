import sys
from google import genai
client = genai.Client()
for m in sys.argv[1:]:
    try:
        it = client.interactions.create(model=m, input="Reply with just: ok", generation_config={"thinking_level": "low"} if "argon" in m else None)
        print(m, "->", repr(it.output_text)[:80])
    except Exception as e:
        print(m, "ERR", type(e).__name__, str(e)[:200])
