import asyncio

from control_center.core.api import topic_matches
from control_center.core.events import EventBus


def test_slow_subscriber_drops_oldest():
    async def run():
        bus = EventBus(queue_size=2)
        with bus.subscription() as q:
            for i in range(5):
                bus.publish("t", i)
            return [q.get_nowait().data, q.get_nowait().data]
    assert asyncio.run(run()) == [3, 4]


def test_subscription_cleanup():
    bus = EventBus()
    with bus.subscription():
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0


def test_topic_filter():
    assert topic_matches("dashboard.sample", ["dashboard"])
    assert not topic_matches("dashboards.x", ["dashboard"])
    assert topic_matches("anything", [])


def test_sse_wire_format_and_filter():
    import json

    from control_center.core.api import sse_events

    async def run():
        bus = EventBus()
        gen = sse_events(bus, ["logs"], heartbeat=0.05)
        first = await gen.__anext__()                       # subscription is active after the first chunk
        bus.publish("dashboard.snapshot", {"x": 1})         # filtered out
        bus.publish("logs.ollama", {"msg": "hi"})
        second = await gen.__anext__()
        third = await gen.__anext__()                       # nothing left -> heartbeat comment
        await gen.aclose()
        return first, second, third, bus.subscriber_count

    first, second, third, subs = asyncio.run(run())
    assert first.startswith("retry: ")
    assert second.startswith("data: ") and second.endswith("\n\n")
    assert json.loads(second[6:]) == {"topic": "logs.ollama", "data": {"msg": "hi"}}
    assert third == ": heartbeat\n\n"
    assert subs == 0                                        # closing the generator unsubscribes


def test_close_ends_open_streams():
    from control_center.core.api import sse_events

    async def run():
        bus = EventBus()
        gen = sse_events(bus, [], heartbeat=10)
        await gen.__anext__()                                # connected
        nxt = asyncio.ensure_future(gen.__anext__())          # waiting for the next event
        await asyncio.sleep(0)
        bus.close()
        try:
            await asyncio.wait_for(nxt, 1)
        except StopAsyncIteration:
            return "ended", bus.subscriber_count
        return "still open", bus.subscriber_count

    assert asyncio.run(run()) == ("ended", 0)


def test_subscribe_after_close_ends_immediately():
    from control_center.core.events import CLOSED

    async def run():
        bus = EventBus()
        bus.close()
        with bus.subscription() as q:
            return q.get_nowait()

    assert asyncio.run(run()) is CLOSED
