#!/usr/bin/env python3
"""Record local_ai/metrics as JSONL; Ctrl-C flushes and closes the recording."""
import argparse
from pathlib import Path
import rclpy
from std_msgs.msg import String


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    rclpy.init()
    node = rclpy.create_node('local_voice_metrics_recorder')
    try:
        with args.output.open('a', buffering=1) as stream:
            node.create_subscription(String, 'local_ai/metrics', lambda msg: stream.write(msg.data + '\n'), 100)
            rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()
