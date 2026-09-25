"""Start the local API on Food AI's development port (separate from Atlas)."""

import os
from pathlib import Path

import uvicorn

if __name__ == "__main__":
    # Resolve .env, static assets and reload paths against this repository even
    # when the launcher is called from another directory.
    os.chdir(Path(__file__).resolve().parent)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8002, reload=True)
