"""Capture the native LMCache STORE stream callback timestamps for B1."""
import json
from pathlib import Path
import runpy
import time


def store_record(event):
    return {
        "event": event.event_type.value,
        "request_id": event.session_id,
        "event_wall_ns": round(event.timestamp * 1e9),
        "drain_wall_ns": time.time_ns(),
        "drain_monotonic_ns": time.monotonic_ns(),
        **{key: event.metadata.get(key) for key in
           ("transfer_key", "engine_id", "device", "model_name", "stored_count",
            "total_bytes", "num_tokens")},
    }


def main():
    from lmcache.v1.mp_observability.event import EventType
    from lmcache.v1.mp_observability.event_bus import EventSubscriber
    from lmcache.v1.multiprocess import server

    class StoreTrace(EventSubscriber):
        def get_subscriptions(self):
            return {EventType.MP_STORE_START: self.record,
                    EventType.MP_STORE_END: self.record}

        def record(self, event):
            with Path("/output/store-events.jsonl").open("a") as stream:
                stream.write(json.dumps(store_record(event)) + "\n")

    original = server.init_observability

    def traced_init(*args, **kwargs):
        bus = original(*args, **kwargs)
        bus.register_subscriber(StoreTrace())
        with Path("/output/store-trace-clock.json").open("x") as stream:
            json.dump({
                "clock": "CLOCK_REALTIME",
                "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "time_namespace": str(Path("/proc/self/ns/time").readlink()),
            }, stream)
        return bus

    server.init_observability = traced_init
    runpy.run_module("lmcache.v1.multiprocess.http_server", run_name="__main__")


if __name__ == "__main__":
    main()
