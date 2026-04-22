"""Dedicated worker process for durable graph builds.

See ``src.worker.build_worker`` for the entry point. Launched by
``src.entrypoint`` when ``APP_MODE=worker``; never runs inside the API
process, so it has its own asyncio loop and cannot rely on the API's
captured ``_main_loop`` used by ``fire_and_forget``.
"""
