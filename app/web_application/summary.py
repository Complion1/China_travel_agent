"""One terminal finalizer per run; bounded small receipts and explicit release."""
from __future__ import annotations

import asyncio
import logging
from app.demo_workflow import DemoSummaryView
from app.workflow_contracts import TravelSessionState
from .errors import WebApplicationDependencyError, WebApplicationUsageError, WebApplicationConflictError
from .models import fresh
from .store import SummaryReceipt

_LOG = logging.getLogger(__name__)
MAX_PENDING = 8
MAX_ATTEMPTS = 3
ATTEMPT_SECONDS = 2.0
LIFETIME_SECONDS = 8.0
BACKOFF = (0.25, 1.0)


class SummaryFinalizer:
    def __init__(self, *, dependencies, on_release=None):
        self._deps = dependencies
        self._tasks = {}
        self._frozen = set()
        self._collecting = True
        self._loop = asyncio.get_running_loop()
        self._on_release = on_release

    def pending(self, *, run_id):
        return run_id in self._tasks

    async def begin(self, *, view, execution_stopped: bool):
        if asyncio.get_running_loop() is not self._loop or type(execution_stopped) is not bool or not execution_stopped:
            raise WebApplicationUsageError()
        if not self._collecting or view.lifecycle != "closed" or view.activity != "terminal" or view.terminal_code is None:
            return
        if view.run_id in self._tasks:
            return
        if len(self._tasks) >= MAX_PENDING:
            await self._release(view.run_id)
            _LOG.warning("summary_not_saved")
            return
        self._tasks[view.run_id] = self._loop.create_task(self._finalize(view))
        self._tasks[view.run_id].add_done_callback(lambda task, run=view.run_id: self._done(run, task))

    def _done(self, run_id, task):
        self._tasks.pop(run_id, None)
        self._frozen.discard(run_id)
        if not task.cancelled() and task.exception() is not None:
            _LOG.error("summary_finalization_failed")

    async def _release(self, run_id):
        # Both public owners have independent contexts. Never touch private histories.
        try:
            self._deps.diagnostics.release_run(run_id=run_id)
        finally:
            await self._deps.definition.release_run(run_id)
        if self._on_release is not None:
            self._on_release(run_id)

    async def _finalize(self, view):
        run_id = view.run_id
        released = False
        try:
            state = fresh(await self._deps.durable.get_state(run_id=run_id), TravelSessionState)
            budgets = await self._deps.durable.get_terminal_budget_snapshots(run_id=run_id)
            if (state.run_id, state.trace_id, state.terminal_code) != (view.run_id, view.trace_id, view.terminal_code):
                raise WebApplicationDependencyError()
            existing = await self._deps.store.get_summary(run_id=run_id, trace_id=view.trace_id, terminal_code=view.terminal_code)
            if existing is not None:
                summary = existing.summary()
                if summary is not None and summary.budget_snapshots != budgets:
                    raise WebApplicationConflictError()
                return
            observed = fresh(await self._deps.diagnostics.finish(state=state, budget_snapshots=budgets), DemoSummaryView)
            receipt = SummaryReceipt.freeze(run_id=run_id, trace_id=view.trace_id,
                terminal_code=view.terminal_code, summary=observed.summary)
            # Drop complete upstream objects before entering any retry lifetime.
            del state, budgets, observed
            self._frozen.add(run_id)
            deadline = self._loop.time() + LIFETIME_SECONDS
            for attempt in range(MAX_ATTEMPTS):
                remaining = deadline - self._loop.time()
                if remaining <= 0:
                    break
                try:
                    async with asyncio.timeout(min(ATTEMPT_SECONDS, remaining)):
                        try:
                            await self._deps.store.save_summary(receipt=receipt)
                        except WebApplicationDependencyError:
                            # An ambiguous commit can only be resolved by this exact receipt.
                            confirmed = await self._deps.store.get_summary(run_id=run_id,
                                trace_id=receipt.trace_id, terminal_code=receipt.terminal_code)
                            if confirmed != receipt:
                                raise WebApplicationDependencyError()
                    return
                except (WebApplicationDependencyError, TimeoutError):
                    if not released:
                        await self._release(run_id)
                        released = True
                    if attempt < MAX_ATTEMPTS - 1:
                        remaining = deadline - self._loop.time()
                        if remaining <= 0:
                            break
                        await asyncio.sleep(min(BACKOFF[attempt], remaining))
            _LOG.warning("summary_not_saved")
        finally:
            if not released:
                await self._release(run_id)

    async def before_durable_close(self, *, deadline=None):
        deadline = self._loop.time() + LIFETIME_SECONDS if deadline is None else deadline
        tasks = tuple(self._tasks.values())
        if tasks:
            await asyncio.wait(tasks, timeout=max(0.0, deadline - self._loop.time()))
        self._collecting = False
        collecting = tuple(task for run, task in self._tasks.items() if run not in self._frozen)
        for task in collecting:
            task.cancel()
        if collecting:
            _, pending = await asyncio.wait(collecting, timeout=2.0)
            if pending:
                # The owner must retain M11 and its suppliers until stop is proved.
                raise WebApplicationDependencyError()

    async def after_durable_close(self):
        # No new queries/finish here: only already frozen receipts may remain.
        tasks = tuple(self._tasks.values())
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=LIFETIME_SECONDS)
            if pending:
                for task in pending:
                    task.cancel()
                _, pending = await asyncio.wait(pending, timeout=2.0)
                if pending:
                    raise WebApplicationDependencyError()
