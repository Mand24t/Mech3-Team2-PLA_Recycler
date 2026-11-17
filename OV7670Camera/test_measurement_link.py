"""Exercise real localhost delivery, freshness and reconnection without hardware."""
import socket
import time
import unittest

from measurement_link import MeasurementPublisher, MeasurementReceiver, live_diameter


def sample(diameter=1.7523, sequence=5):
    now = time.monotonic()
    return dict(protocol=1, valid=True, diameter_mm=diameter,
                frame_received_at=now, published_at=now, frame_sequence=sequence)


class MeasurementLinkTests(unittest.TestCase):
    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for measurement link")

    def test_stale_invalid_and_nonfinite_measurements_are_unavailable(self):
        packet = sample()
        self.assertEqual(live_diameter(packet), 1.7523)
        for change in ({"valid": False}, {"diameter_mm": float("nan")},
                       {"diameter_mm": -1}, {"diameter_mm": True},
                       {"frame_received_at": time.monotonic() - 3},
                       {"published_at": time.monotonic() - 3},
                       {"frame_received_at": time.monotonic() + 10}):
            with self.subTest(change=change):
                self.assertIsNone(live_diameter(packet | change))
        self.assertIsNone(live_diameter(None))

    def test_exact_live_value_updates_and_disconnect(self):
        publisher = MeasurementPublisher(("127.0.0.1", 0))
        receiver = MeasurementReceiver(publisher.address)
        try:
            publisher.publish(sample())
            self.wait_for(lambda: live_diameter(receiver.snapshot()[0]) == 1.7523)
            publisher.publish(sample(2.3456, 6))
            self.wait_for(lambda: live_diameter(receiver.snapshot()[0]) == 2.3456)
            self.assertEqual(receiver.snapshot()[0]["frame_sequence"], 6)
            publisher.publish(dict(protocol=1, valid=False, diameter_mm=None, reason="Invalid edges"))
            self.wait_for(lambda: (receiver.snapshot()[0] or {}).get("reason") == "Invalid edges")
            self.assertIsNone(live_diameter(receiver.snapshot()[0]))
        finally:
            publisher.close()
        try:
            self.wait_for(lambda: receiver.snapshot()[0] is None)
        finally:
            receiver.close()

    def test_receiver_can_start_before_viewer(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            address = reservation.getsockname()
        receiver = MeasurementReceiver(address)
        publisher = MeasurementPublisher(address)
        try:
            publisher.publish(sample(1.9999))
            self.wait_for(lambda: live_diameter(receiver.snapshot()[0]) == 1.9999)
        finally:
            receiver.close()
            publisher.close()


if __name__ == "__main__":
    unittest.main()
