import json
import sys
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'maria_network_monitor'))
from reports import update_reports, site_report
from dashboard import site_key, update_inventory
from resilience import confirm_absences, RouterPoller, BoundedPublisher


def stamp(value):
    return datetime.fromisoformat(value + '+00:00').timestamp()


def printer(count, at, source='snmp'):
    return {'id': 'demo', 'site': 'Demo', 'name': 'Demo printer', 'type': 'printer', 'online': True,
            'printer': {'page_count': count, 'page_count_observed_at': stamp(at), 'page_count_source': source}}


class ReportsTests(unittest.TestCase):
    def update(self, state, count, at, source='snmp', zone='UTC'):
        return update_reports(state, [printer(count, at, source)], zone, stamp(at))

    def report(self, state, at):
        return site_report(state, {'site_id': site_key('Demo')}, stamp(at))

    def test_counts_deltas_not_lifetime_or_cached_samples_and_survives_restart(self):
        state = self.update({}, 8000, '2026-10-01T08:00:00')
        state = json.loads(json.dumps(state))
        self.update(state, 8000, '2026-10-01T08:00:00')
        self.update(state, 8042, '2026-10-01T09:00:00')
        day = self.report(state, '2026-10-01T09:00:00')['days'][0]
        self.assertEqual(day['pages'], 42)
        self.assertIn('partial_start', day['rows'][0]['flags'])
        self.assertEqual(next(iter(state['printers'].values()))['days']['2026-10-01']['samples'], 2)

    def test_cross_midnight_delta_is_unallocated_not_double_counted(self):
        state = self.update({}, 100, '2026-10-01T23:59:00')
        self.update(state, 130, '2026-10-02T00:01:00')
        self.update(state, 135, '2026-10-02T01:00:00')
        days = self.report(state, '2026-10-02T01:00:00')['days']
        self.assertEqual(days[0]['pages'], 5)
        self.assertEqual(days[0]['rows'][0]['unallocated'], 30)
        self.assertIn('boundary_gap', days[1]['rows'][0]['flags'])

    def test_reset_and_source_change_do_not_inflate_pages(self):
        state = self.update({}, 9000, '2026-10-01T08:00:00')
        self.update(state, 10, '2026-10-01T09:00:00')
        self.update(state, 9500, '2026-10-01T10:00:00', 'ipp:pages_completed')
        self.update(state, 9503, '2026-10-01T11:00:00', 'ipp:pages_completed')
        day = self.report(state, '2026-10-01T11:00:00')['days'][0]
        self.assertEqual(day['pages'], 3)
        self.assertTrue({'counter_reset', 'source_changed'} <= set(day['rows'][0]['flags']))

    def test_missing_days_are_unknown_not_zero(self):
        state = self.update({}, 100, '2026-10-01T08:00:00')
        days = self.report(state, '2026-10-03T10:00:00')['days']
        self.assertIsNone(days[0]['rows'][0]['pages'])
        self.assertFalse(days[0]['has_samples'])
        self.assertIn('partial_end', days[2]['rows'][0]['flags'])

    def test_timezone_changes_boundary_and_cannot_relabel_existing_ledger(self):
        state = self.update({}, 100, '2026-10-01T20:59:00', zone='Europe/Moscow')
        self.update(state, 120, '2026-10-01T21:01:00', zone='Europe/Moscow')
        day = self.report(state, '2026-10-01T21:01:00')['days'][0]
        self.assertEqual(day['date'], '2026-10-02')
        self.assertEqual(day['rows'][0]['unallocated'], 20)
        with self.assertRaises(ValueError):
            self.update(state, 123, '2026-10-01T21:02:00', zone='UTC')

    def test_invalid_and_out_of_order_samples_are_ignored(self):
        state = self.update({}, 100, '2026-10-01T10:00:00')
        for count, at in [(999, '2026-10-01T09:00:00'), (-1, '2026-10-01T11:00:00'), (float('nan'), '2026-10-01T11:00:00')]:
            self.update(state, count, at)
        self.assertEqual(next(iter(state['printers'].values()))['last']['count'], 100)

    def test_retention_is_bounded(self):
        state = self.update({}, 100, '2026-01-01T10:00:00')
        update_reports(state, [], 'UTC', stamp('2026-10-01T10:00:00'))
        self.assertEqual(state['printers'], {})


class ResilienceTests(unittest.TestCase):
    def cycle(self, inventory, ok=True, devices=None):
        results = [{'site': {'name': 'Demo'}, 'ok': ok, 'devices': devices or []}]
        confirm_absences(inventory, results)
        return update_inventory(inventory, results)

    def test_three_successful_absences_required_and_failure_resets_streak(self):
        item = printer(100, '2026-10-01T10:00:00')
        inventory = self.cycle({}, devices=[item])
        inventory = self.cycle(inventory)
        self.assertTrue(next(iter(inventory['sites'].values()))['devices'][0]['pending_removal'])
        inventory = self.cycle(inventory, ok=False)
        for _ in range(2):
            inventory = self.cycle(json.loads(json.dumps(inventory)))
            self.assertEqual(next(iter(inventory['sites'].values()))['total'], 1)
        inventory = self.cycle(inventory)
        self.assertEqual(next(iter(inventory['sites'].values()))['total'], 0)

    def test_reappearance_clears_pending_removal(self):
        item = printer(100, '2026-10-01T10:00:00')
        state = self.cycle(self.cycle({}, devices=[item]))
        state = self.cycle(state, devices=[item])
        site = next(iter(state['sites'].values()))
        self.assertFalse(site['stale'])
        self.assertEqual(site['missing_polls'], {})

    def test_slow_site_does_not_accumulate_jobs_or_hide_healthy_results(self):
        release = threading.Event()
        def collect(site, *_):
            if site['name'] == 'Slow':
                release.wait(2)
            return {'ok': True, 'site': site, 'devices': []}
        # This test checks concurrent job reuse, not wall-clock expiry. Avoid
        # marking the healthy result stale under a busy Windows scheduler.
        poller = RouterPoller(budget=.2, clock=lambda: 0)
        sites = [{'name': 'Slow'}, {'name': 'Fast'}]
        try:
            results = poller.collect(sites, collect, '', '')
            pending = poller.pending['Slow'][0]
            self.assertFalse(results[0]['ok'])
            self.assertTrue(results[1]['ok'])
            poller.collect(sites, collect, '', '')
            self.assertIs(poller.pending['Slow'][0], pending)
        finally:
            release.set()
            poller.close()

    def test_queue_rejection_requests_discovery_retry(self):
        client, event = Mock(), threading.Event()
        client.publish.return_value.rc = 15
        publisher = BoundedPublisher(client, event, 15)
        publisher.publish('demo/config', '{}')
        client.max_queued_messages_set.assert_called_once_with(256)
        self.assertTrue(event.is_set())


if __name__ == '__main__':
    unittest.main()
