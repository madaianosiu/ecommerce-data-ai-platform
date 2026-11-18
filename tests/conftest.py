import sys
from pathlib import Path

# Scripturile din src/ingest se rulează direct (python src/ingest/x.py), nu ca pachet.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "ingest"))
