"""
Entry point for running the FastAPI server on Windows.
Sets SelectorEventLoop policy required by psycopg3 async mode.
"""
import asyncio
import selectors
import sys

# psycopg3 async requires SelectorEventLoop (not Windows default ProactorEventLoop)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(
        asyncio.WindowsSelectorEventLoopPolicy()
    )

import uvicorn

if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, log_level="info")
