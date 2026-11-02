"""Combine renewable camera pauses from voice and remote performances."""

import time


PAUSE_TOPICS = ('voice_session/camera_paused', 'remote/camera_paused')


class CameraPauseLease:
    """Keep capture paused until every live source has returned to idle."""

    def __init__(self, timeout=2.0, clock=time.monotonic):
        self.timeout = timeout
        self.clock = clock
        self.deadlines = {}

    def update(self, source, paused):
        if paused:
            self.deadlines[source] = self.clock() + self.timeout
        else:
            self.deadlines.pop(source, None)

    @property
    def paused(self):
        now = self.clock()
        return any(now < deadline for deadline in self.deadlines.values())


def attach_camera_pause(node):
    """Subscribe to both heartbeats; expired publishers cannot latch a pause."""
    from std_msgs.msg import Bool

    lease = CameraPauseLease()
    for topic in PAUSE_TOPICS:
        node.create_subscription(
            Bool, topic,
            lambda msg, source=topic: lease.update(source, msg.data), 1)
    return lease
