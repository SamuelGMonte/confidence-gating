"""Package init — auto-loads .env so no `source .env` / exports are needed."""
from __future__ import annotations

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dependency missing — .env autoload skipped
    pass
