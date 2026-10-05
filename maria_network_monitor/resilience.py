"""Inventory deletion confirmation and bounded router work across polling cycles."""
import time
from concurrent.futures import ThreadPoolExecutor, wait

from dashboard import site_key


class BoundedPublisher:
    """Bound memory and apply bounded backpressure instead of dropping each burst.

    Paho's network thread drains the queue while the main thread waits. The
    total wait budget is shared by the entire polling cycle, including outages.
    """
    def __init__(self, client, refresh, queue_full_code, clock=time.monotonic):
        self.client = client
        self.refresh = refresh
        self.queue_full_code = queue_full_code
        self.clock = clock
        self.last_accepted = None
        self.begin_cycle()
        client.max_queued_messages_set(256)

    def begin_cycle(self):
        self.wait_budget = 2.0

    def __getattr__(self, name):
        return getattr(self.client, name)

    def publish(self, *args, **kwargs):
        result = self.client.publish(*args, **kwargs)
        if (result.rc == self.queue_full_code and self.last_accepted is not None
                and self.wait_budget > 0 and self.client.is_connected()):
            started = self.clock()
            timeout = min(0.5, self.wait_budget)
            try:
                self.last_accepted.wait_for_publish(timeout=timeout)
            except (RuntimeError, ValueError):
                pass
            finally:
                # Charge at least the requested timeout: bound retries even
                # if wait_for_publish returns immediately on a broken message.
                self.wait_budget -= max(timeout, self.clock() - started)
            result = self.client.publish(*args, **kwargs)
        if result.rc == self.queue_full_code:
            self.refresh.set()
        elif result.rc == 0:
            self.last_accepted = result
        return result


def confirm_absences(previous, results, confirmations=3):
    for result in results:
        old = previous.get("sites", {}).get(site_key(result["site"]["name"]), {})
        missing = {}
        if result["ok"]:
            current = {d["id"] for d in result.get("devices", [])}
            for device in old.get("devices", []):
                if device["id"] not in current:
                    count = old.get("missing_polls", {}).get(device["id"], 0) + 1
                    if count < confirmations:
                        missing[device["id"]] = count
        # A failed poll cannot count as successful absence or preserve a streak.
        result["missing_polls"] = missing
        result["pending_ids"] = set(missing)


class RouterPoller:
    def __init__(self, workers=8, budget=20, clock=time.monotonic):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="router")
        self.pending = {}
        self.failures = {}
        self.retry_at = {}
        self.budget = budget
        self.clock = clock

    def collect(self, sites, collector, username, password):
        now = self.clock()
        for site in sites:
            key = site["name"]
            if key not in self.pending and now >= self.retry_at.get(key, 0):
                self.pending[key] = (self.executor.submit(collector, site, username, password), now)
        active = [self.pending[s["name"]][0] for s in sites if s["name"] in self.pending]
        if active:
            wait(active, timeout=self.budget)
        results = []
        for site in sites:
            key = site["name"]
            job = self.pending.get(key)
            result = {"ok": False, "site": site, "devices": [], "error": "retry_backoff"}
            if job:
                future, started = job
                if future.done():
                    del self.pending[key]
                    try:
                        result = future.result()
                    except Exception:
                        result["error"] = "router_api_error"
                    # Do not advertise a result delayed over two polling budgets as current.
                    if self.clock() - started > self.budget * 2:
                        result = {"ok": False, "site": site, "devices": [], "error": "poll_deadline"}
                    if result["ok"]:
                        self.failures.pop(key, None)
                        self.retry_at.pop(key, None)
                    else:
                        failures = min(self.failures.get(key, 0) + 1, 4)
                        self.failures[key] = failures
                        self.retry_at[key] = self.clock() + min(300, 30 * 2 ** (failures - 1))
                else:
                    result["error"] = "poll_deadline"
            results.append(result)
        return results

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
