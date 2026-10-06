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
