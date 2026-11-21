import sys
from pathlib import Path

# The scripts in src/ingest run directly (python src/ingest/x.py), not as a package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "ingest"))
