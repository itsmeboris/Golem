"""Tests for golem.priority_gate — priority-aware concurrency gate."""

import asyncio

import pytest

from golem.priority_gate import PriorityGate


class TestPriorityGateBasic:
    def test_allows_up_to_max_concurrent(self):
        gate = PriorityGate(max_concurrent=2)
        assert gate.max_concurrent == 2
        assert gate.running_count == 0
        assert gate.waiting_count == 0

    def test_single_slot(self):
        gate = PriorityGate(max_concurrent=1)

        async def run():
            async with gate.slot():
                assert gate.running_count == 1
            assert gate.running_count == 0

        asyncio.run(run())

    def test_concurrent_within_limit(self):
        gate = PriorityGate(max_concurrent=3)
        entered = []

        async def run():
            async def task(name):
                async with gate.slot():
                    entered.append(name)

            await asyncio.gather(task("a"), task("b"), task("c"))
            assert len(entered) == 3

        asyncio.run(run())


class TestPriorityOrdering:
    def test_higher_priority_served_first(self):
        gate = PriorityGate(max_concurrent=1)
        order = []

        async def run():
            async with gate.slot(priority=0):
                waiters = []
                waiters.append(asyncio.create_task(_waiter(gate, 10, "low", order)))
                waiters.append(asyncio.create_task(_waiter(gate, 1, "high", order)))
                waiters.append(asyncio.create_task(_waiter(gate, 5, "mid", order)))
                await asyncio.sleep(0.01)

            await asyncio.gather(*waiters)

        asyncio.run(run())
        assert order == ["high", "mid", "low"]

    def test_same_priority_fifo(self):
        gate = PriorityGate(max_concurrent=1)
        order = []

        async def run():
            async with gate.slot(priority=0):
                waiters = []
                for name in ["first", "second", "third"]:
                    waiters.append(asyncio.create_task(_waiter(gate, 5, name, order)))
                    await asyncio.sleep(0.001)

            await asyncio.gather(*waiters)

        asyncio.run(run())
        assert order == ["first", "second", "third"]


class TestPriorityGateCancellation:
    def test_cancelled_waiter_removed(self):
        gate = PriorityGate(max_concurrent=1)

        async def run():
            async with gate.slot():
                waiter = asyncio.create_task(_waiter(gate, 5, "x", []))
                await asyncio.sleep(0.01)
                assert gate.waiting_count == 1

                waiter.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiter

                assert gate.waiting_count == 0

        asyncio.run(run())


class TestPriorityGateDefaultPriority:
    def test_default_is_five(self):
        assert PriorityGate.DEFAULT_PRIORITY == 5


async def _waiter(gate, priority, name, order):
    async with gate.slot(priority=priority):
        order.append(name)
