import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.server import app  # noqa: F401  – Vercel looks for `app`
