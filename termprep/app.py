"""Hugging Face Space entry point for TermPrep.

HF Space will look for app.py and use the exported `app` object
if available, or run the script directly.
"""

import sys
import os

# Ensure termprep is importable (project root is one level up from this file)
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from termprep.web.server import create_app

# Export the FastAPI app for HF Space
app = create_app()

if __name__ == "__main__":
    import uvicorn
    # HF Space uses port 7860 by default
    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port)
