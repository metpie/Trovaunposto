import os
import sys

# Env minime PRIMA dell'import del modulo (che le legge a livello di modulo).
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1000")
os.environ.setdefault("DATA_DIR", os.path.join(os.path.dirname(__file__), "_data_tmp"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
