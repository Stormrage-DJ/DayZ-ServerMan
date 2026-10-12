"""Check early parent and borrowed-query failure seams with retained evidence."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, Mock
from native_standin import Standin
from check_release_fixtures import fixtures
from parent_teardown import finalize
from startup_custody import Startup
from startup_logs import OwnedLog
from control_audit import Audit
from release_steps import Releases
import run_probe
import watchdog
import pipe_cancel
import process_memory
import progress
from dayz_serverman.adapters.windows.process_tree import OwnedProcessTree
from dayz_serverman.adapters.windows.process_custody import CustodyError


class EdgeTests(unittest.TestCase):
    """Preserve every independent release after early setup and evidence faults."""
    def test_parent_service_folder_runtime_and_first_bookend_faults(self):
        """Original early faults retain startup custody and attempt every later write."""
        for seam in ('services', 'folder', 'runtime', 'bookend'):
            with self.subTest(seam=seam):
                original = MemoryError('early ' + seam)
                operations, services, before, *_ = fixtures(None)
                folder = Mock()
                class Root:
                    """Resolve only the two exact dummy paths used by parent setup."""
                    def __truediv__(self, name):
                        """Expose no filesystem operations outside the fixture."""
                        return folder if name == 'pipe1' else self
                if seam == 'folder':
                    folder.mkdir.side_effect = original
                if seam == 'runtime':
                    folder.mkdir.side_effect = [None, original]
                call_services = Mock(side_effect=original if seam == 'services' else None, return_value=services)
                with patch.object(run_probe, 'ROOT', Root()), patch.object(run_probe, 'DEFAULT_SERVICES', services), \
                     patch.object(run_probe, 'finalization_services', call_services), \
                     patch.object(run_probe, 'snapshot', side_effect=original), patch.object(run_probe, 'write') as root_write, \
                     patch('authorization.authorize'), patch('sys.argv', ['run_probe.py', 'pipe1']):
                    with self.assertRaises(MemoryError) as caught:
                        run_probe.main()
                self.assertIs(caught.exception, original)
                self.assertIsNotNone(original.startup_custody)
                self.assertFalse(original.release_report['finalization_succeeded'])
                self.assertIn('source_capture', operations.seen)
                self.assertIn('import_capture', operations.seen)
                root_write.assert_called_once()

    def test_held_child_before_tree_is_contained_and_handle_close_requires_absence(self):
        """Pre-admission cleanup uses only the held child and records wait failure."""
        for absent in (True, False):
            operations, services, before, child, *_ = fixtures(None)
            child.terminate = Mock()
            if not absent:
                child.wait = Mock(side_effect=TimeoutError('held wait'))
            result = {}
            releases = finalize(services, Path('.'), Path('.'), before, result, child=child,
                                primary=RuntimeError('admission fault'))
            child.terminate.assert_called_once()
            self.assertEqual(result['owned_tree_absent'], absent)
            self.assertEqual('child_handle' in operations.seen, absent)
            self.assertIn('final_write', operations.seen)
            if not absent:
                self.assertEqual(releases.steps['owned_asset_cleanup']['status'], 'blocked')

    def test_memory_audit_partial_start_closes_consumer_before_retained_log(self):
        """A failed guard audit constructor keeps its acquired file and started thread."""
        owner, original, handle = Startup(), MemoryError('memory audit poststart'), io.StringIO()
        class Partial(Audit):
            """Represent a started finite audit with an exact retained close operation."""
            def __init__(self, log, fault, custody, label):
                """Register partial custody before simulating the constructor failure."""
                super().__init__(log, fault, custody, label)
                raise original
        class Folder:
            """Supply only a fake exact retained memory file."""
            def __truediv__(self, name):
                """Expose the fixture sink without creating a real file."""
                return NS(open=lambda *args, **kw: handle)
        with patch.object(watchdog, 'Audit', Partial):
            with self.assertRaises(MemoryError) as caught:
                watchdog.watch(None, None, Folder(), None, 0, Mock(), owner)
        self.assertIs(caught.exception, original)
        self.assertFalse(owner.find('memory-audit').thread.is_alive())
        self.assertTrue(handle.closed)
        self.assertEqual(original.release_report['release_steps']['memory_log_close']['status'], 'passed')

    def test_cancel_thread_query_primary_and_close_faults_remain_ordered(self):
        """Cancellation borrows one exact thread reference and retains both refusals."""
        kernel = Standin(fail='CancelSynchronousIo', close_fail=True)
        with patch('dayz_serverman.adapters.windows.process_bindings.Kernel', return_value=kernel):
            with self.assertRaises(CustodyError) as caught:
                pipe_cancel.cancel_reader(NS(native_id=77, is_alive=lambda: True))
        self.assertIs(caught.exception.original, kernel.error)
        self.assertEqual(caught.exception.custody.references[0].state, 'release-unknown')
        self.assertEqual(caught.exception.secondary[0][1].error_code, 6)
        self.assertEqual(len(kernel.closed), 1)

    def test_memory_query_membership_and_close_fault_keep_exact_custody(self):
        """A memory sample cannot hide failed query release behind counter evidence."""
        kernel = Standin()
        tree = OwnedProcessTree.prepare(kernel=kernel)
        kernel.pids, kernel.member, kernel.close_fail = [777], False, True
        with patch.object(tree, '_job_pids', return_value=(777,)), patch('process_memory.ctypes.WinDLL', return_value=Mock()):
            with self.assertRaises(CustodyError) as caught:
                process_memory.sample(tree)
        self.assertEqual(tree.custody.references[-1].state, 'release-unknown')
        self.assertTrue(caught.exception.secondary)
        kernel.close_fail, kernel.pids = False, []
        tree.close_job()

    def test_writer_redirect_fault_preserves_original_and_release_fault(self):
        """A failed redirect retains duplicated-descriptor custody without a retry."""
        original = TypeError('inherit setup')
        with patch('progress.os.dup', return_value=91), patch('progress.os.set_inheritable', side_effect=original), \
             patch('progress.os.close', side_effect=OSError('descriptor close')) as close:
            with self.assertRaises(TypeError) as caught:
                progress.capture_writer()
        self.assertIs(caught.exception, original)
        self.assertEqual(original.writer_custody.state, 'release-unknown')
        self.assertEqual(len(original.writer_custody.secondary), 1)
        close.assert_called_once_with(91)
        self.assertIsNone(progress._writer)

    def test_all_evidence_failures_still_attempt_later_independent_obligations(self):
        """Primary and later writes do not skip source/import/log release obligations."""
        operations, services, before, child, tree, control, guard, logs = fixtures('source_capture')
        original, result = TypeError('primary'), {}
        services.write = Mock(side_effect=OSError('all writes refused'))
        releases = finalize(services, Path('.'), Path('.'), before, result, child, tree, control, guard, logs, original)
        self.assertIs(releases.primary, original)
        self.assertIn('import_capture', operations.seen)
        self.assertIn('log0', operations.seen)
        self.assertIn('log1', operations.seen)
        self.assertGreaterEqual(services.write.call_count, 3)
        self.assertFalse(result['finalization_succeeded'])
