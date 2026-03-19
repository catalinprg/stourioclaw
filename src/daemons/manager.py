"""Daemon manager — spawns, monitors, and controls daemon agent tasks."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from src.agents.registry import AgentRegistry
from src.daemons.loop import run_daemon_loop
from src.persistence import audit
from src.persistence.database import async_session
from src.persistence.redis_store import get_pubsub_connection, DAEMON_EVENTS_CHANNEL

logger = logging.getLogger("stourio.daemons.manager")


class DaemonManager:
    """Manages lifecycle of all daemon agents."""

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop_events: dict[str, asyncio.Event] = {}
        self._last_heartbeat: dict[str, datetime] = {}
        self._control_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Load and start all active daemon agents."""
        async with async_session() as session:
            registry = AgentRegistry(session)
            daemons = await registry.list_daemons()

        for agent in daemons:
            await self._start_daemon(agent.name, agent.daemon_config or {})

        self._control_task = asyncio.create_task(self._listen_control_events())
        logger.info("Daemon manager started: %d daemon(s)", len(self._tasks))

    async def stop(self) -> None:
        """Gracefully stop all daemons."""
        if self._control_task:
            self._control_task.cancel()
            try:
                await self._control_task
            except asyncio.CancelledError:
                pass

        for name in list(self._tasks.keys()):
            await self._stop_daemon(name)

        logger.info("Daemon manager stopped")

    def is_running(self, name: str) -> bool:
        """Check if a daemon is currently running."""
        return name in self._tasks and not self._tasks[name].done()

    async def _start_daemon(self, name: str, config: dict) -> None:
        if name in self._tasks:
            logger.warning("Daemon '%s' already running", name)
            return

        stop_event = asyncio.Event()
        self._stop_events[name] = stop_event

        task = asyncio.create_task(self._run_with_health_check(name, config, stop_event))
        self._tasks[name] = task

        await audit.log("DAEMON_STARTED", f"Daemon '{name}' started")
        logger.info("Started daemon: %s", name)

    async def _stop_daemon(self, name: str) -> None:
        if name not in self._tasks:
            return

        self._stop_events[name].set()

        try:
            await asyncio.wait_for(self._tasks[name], timeout=60)
        except asyncio.TimeoutError:
            self._tasks[name].cancel()
            try:
                await self._tasks[name]
            except asyncio.CancelledError:
                pass

        del self._tasks[name]
        del self._stop_events[name]
        self._last_heartbeat.pop(name, None)

        await audit.log("DAEMON_STOPPED", f"Daemon '{name}' stopped")
        logger.info("Stopped daemon: %s", name)

    def _on_cycle_complete(self, agent_name: str) -> None:
        """Called after each daemon cycle for health monitoring."""
        self._last_heartbeat[agent_name] = datetime.now(timezone.utc)

    async def _run_with_health_check(self, name: str, config: dict, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                self._last_heartbeat[name] = datetime.now(timezone.utc)
                await run_daemon_loop(name, config, stop_event, on_cycle_complete=self._on_cycle_complete)
            except Exception as e:
                logger.error("Daemon '%s' crashed: %s. Restarting in 10s...", name, e)
                await audit.log("DAEMON_CRASHED", f"Daemon '{name}' crashed: {e}. Auto-restarting.")
                await asyncio.sleep(10)

    async def _listen_control_events(self) -> None:
        pubsub = await get_pubsub_connection()
        await pubsub.subscribe(DAEMON_EVENTS_CHANNEL)

        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    try:
                        data = json.loads(msg["data"])
                        event = data.get("event")
                        agent = data.get("agent")

                        if event == "start":
                            async with async_session() as session:
                                reg = AgentRegistry(session)
                                agent_model = await reg.get_by_name(agent)
                            if agent_model:
                                await self._start_daemon(agent, agent_model.daemon_config or {})
                        elif event == "stop":
                            await self._stop_daemon(agent)
                        elif event == "restart":
                            await self._stop_daemon(agent)
                            async with async_session() as session:
                                reg = AgentRegistry(session)
                                agent_model = await reg.get_by_name(agent)
                            if agent_model:
                                await self._start_daemon(agent, agent_model.daemon_config or {})
                    except Exception as e:
                        logger.error("Error handling daemon event: %s", e)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(DAEMON_EVENTS_CHANNEL)
            await pubsub.close()

    def status(self) -> dict:
        return {
            name: {
                "running": not task.done(),
                "last_heartbeat": self._last_heartbeat.get(name).isoformat() if self._last_heartbeat.get(name) else None,
            }
            for name, task in self._tasks.items()
        }
