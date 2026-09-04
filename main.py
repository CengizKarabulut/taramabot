"""Compatibility entry point for the risk-aware taramabot orchestration."""

from main_enhanced import *  # noqa: F401,F403


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_bot())
