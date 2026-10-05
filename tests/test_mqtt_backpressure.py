"""Exercise a discovery burst larger than Paho's bounded outgoing queue."""
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'maria_network_monitor'))
from resilience import BoundedPublisher


class Message:
    def __init__(self, client, rc):
        self.client, self.rc = client, rc

    def wait_for_publish(self, timeout):
        self.client.waits.append(timeout)
        if self.client.drain:
            self.client.pending = 0


class Broker:
    def __init__(self, drain=True):
        self.pending = 0
        self.accepted = []
        self.waits = []
        self.drain = drain
        self.connected = True

    def max_queued_messages_set(self, limit):
        self.limit = limit

    def is_connected(self):
        return self.connected

    def publish(self, topic, payload, **kwargs):
        if self.pending >= self.limit:
            return Message(self, 15)
        self.pending += 1
        self.accepted.append((topic, payload))
        return Message(self, 0)


class BackpressureTests(unittest.TestCase):
    def test_large_discovery_burst_delivers_last_site_state_without_refresh_loop(self):
        broker, refresh = Broker(), threading.Event()
        publisher = BoundedPublisher(broker, refresh, 15)
        for number in range(750):
            publisher.publish(f'discovery/{number}/config', '{}', qos=1, retain=True)
        publisher.publish('dashboard/last-site/state', '{"total":5}', qos=1, retain=True)
        self.assertEqual(len(broker.accepted), 751)
        self.assertEqual(broker.accepted[-1][0], 'dashboard/last-site/state')
        self.assertFalse(refresh.is_set())
        self.assertLessEqual(broker.pending, 256)

    def test_stalled_broker_wait_is_bounded_for_whole_cycle_and_retries_later(self):
        broker, refresh = Broker(drain=False), threading.Event()
        publisher = BoundedPublisher(broker, refresh, 15, clock=lambda: 0)
        for number in range(800):
            publisher.publish(str(number), '{}', qos=1)
        self.assertEqual(sum(broker.waits), 2.0)
        self.assertTrue(refresh.is_set())
        broker.drain = True
        publisher.begin_cycle()
        refresh.clear()
        self.assertEqual(publisher.publish('retry', '{}', qos=1).rc, 0)
        self.assertFalse(refresh.is_set())

    def test_disconnected_broker_never_waits(self):
        broker, refresh = Broker(), threading.Event()
        publisher = BoundedPublisher(broker, refresh, 15)
        for number in range(256):
            publisher.publish(str(number), '{}', qos=1)
        broker.connected = False
        publisher.publish('overflow', '{}', qos=1)
        self.assertEqual(broker.waits, [])
        self.assertTrue(refresh.is_set())


if __name__ == '__main__':
    unittest.main()
