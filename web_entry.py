"""Render entry point — exposes the FastAPI app for production."""

import os
import sys

# Ensure termprep module is importable
_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from termprep.web.server import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8672))
    uvicorn.run(app, host="0.0.0.0", port=port)
