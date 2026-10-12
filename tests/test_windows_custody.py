"""Check shared exact job/identity custody without actual native operations."""
import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from native_standin import Standin
from dayz_serverman.adapters.windows.process_custody import Custody, CustodyError
from dayz_serverman.adapters.windows.process_tree import OwnedProcessTree, WindowsChildProbe, ProcessIdentityUnavailable
from dayz_serverman.adapters.windows.process_jobs import PrivateJob, members, STRICT_COMMIT, STRICT_FLAGS
from dayz_serverman.adapters.windows.process_identity import for_pid
from dayz_serverman.adapters.windows.process_bindings import Kernel, SIGNATURES


class CustodyTests(unittest.TestCase):
    """Exercise acquisition, conservative query, containment and single release."""
    def test_original_secondary_unknown_close_and_no_retry(self):
        """Keep the original failure and refuse reuse after ambiguous native release."""
        kernel, custody = Standin(close_fail=True), Custody()
        ref = custody.reserve('query', 'exact', kernel.close)
        kernel.acquire(ref, 'OpenProcess')
        primary = MemoryError('allocation after acquisition')
        custody.fault(primary)
        custody.release(ref)
        with self.assertRaises(CustodyError) as caught:
            custody.raise_failure()
        self.assertIs(caught.exception.original, primary)
        self.assertIs(caught.exception.secondary[0][1].error_code, 6)
        self.assertEqual(ref.state, 'release-unknown')
        with self.assertRaises(OSError):
            ref.close()
        self.assertEqual(len(kernel.closed), 1)
        self.assertNotIn('handle', custody.report()['references'][0])

    def test_job_acquisition_and_preparation_faults_keep_partial_owner(self):
        """Every setup failure exposes exact acquired references before launch."""
        for operation in ('CreateJobObjectW', 'GetHandleInformation', 'SetInformationJobObject', 'QueryInformationJobObject'):
            with self.subTest(operation=operation):
                kernel, custody = Standin(fail=operation), Custody()
                with self.assertRaises(CustodyError) as caught:
                    OwnedProcessTree.prepare(custody=custody, kernel=kernel)
                self.assertIs(caught.exception.original, kernel.error)
                self.assertIs(custody.anchors['tree'].custody, custody)
                self.assertNotIn('CreateProcessW', kernel.calls)
                job = custody.anchors['tree'].job
                kernel.fail = None  # Restore the fake query boundary before fixture disposal.
                if job.reference.state == 'owned':
                    job.close()
                self.assertFalse(kernel.live)

    def test_new_identity_collision_grants_only_reference_release(self):
        """An existing named job never grants containment or launch authority."""
        kernel, custody = Standin(), Custody()
        kernel.collision = True
        with self.assertRaises(CustodyError):
            OwnedProcessTree.prepare(custody=custody, kernel=kernel)
        job = custody.anchors['tree'].job
        with self.assertRaises(OSError):
            job.terminate()
        self.assertNotIn('TerminateJobObject', kernel.calls)
        job.close()
        self.assertFalse(kernel.live)

    def test_strict_and_legacy_limits_remain_distinct(self):
        """Strict creation configures 4 GiB; legacy adoption retains kill-on-close."""
        kernel, custody = Standin(), Custody()
        job = PrivateJob(custody, kernel)
        job.create()
        job.configure_legacy()
        self.assertEqual((kernel.flags, kernel.memory), (0x2000, 0))
        job.configure()
        self.assertEqual((kernel.flags, kernel.memory), (STRICT_FLAGS, STRICT_COMMIT))
        self.assertTrue(job.prepared)
        job.close()

    def test_false_containment_is_not_absence(self):
        """A failed termination remains a fault even if a later query is empty."""
        kernel = Standin(fail='TerminateJobObject')
        tree = OwnedProcessTree.prepare(kernel=kernel)
        with self.assertRaises(CustodyError):
            tree.terminate_tree()
        self.assertIs(tree.custody.primary, kernel.error)
        self.assertTrue(tree.wait_absent(0))
        tree.close_job()

    def test_borrowed_identity_release_and_specific_error_compatibility(self):
        """Identity failures preserve their old catch category and checked close."""
        kernel, custody = Standin(fail='GetProcessTimes'), Custody()
        with self.assertRaises(CustodyError) as caught:
            for_pid(777, custody, kernel)
        self.assertIs(caught.exception.original, kernel.error)
        self.assertFalse(kernel.live)
        kernel = Standin(fail='OpenProcess')
        with self.assertRaises(ProcessIdentityUnavailable) as caught:
            for_pid(777, Custody(), kernel)
        self.assertEqual(caught.exception.error_code, 5)

    def test_bound_and_probe_release_failure_remain_conservative(self):
        """Oversized membership and failed borrowed close cannot prove absence."""
        kernel = Standin()
        kernel.pids = list(range(257))
        with self.assertRaises(OSError):
            members(99, kernel)
        kernel.pids, kernel.close_fail = [], True
        probe = WindowsChildProbe()
        with patch('dayz_serverman.adapters.windows.process_bindings.Kernel', return_value=kernel):
            self.assertFalse(probe._job_absent('fake-job'))
        self.assertEqual(probe.custodies[0].references[0].state, 'release-unknown')

    def test_non_windows_legacy_schema_and_strict_refusal(self):
        """The old sentinel behavior remains usable without a native library."""
        process = SimpleNamespace(pid=31, poll=lambda: 0)
        with patch('dayz_serverman.adapters.windows.process_tree.os.name', 'posix'):
            tree = OwnedProcessTree(process)
            self.assertEqual(tree.evidence.to_dict()['schema_version'], 1)
            self.assertEqual(tree.evidence.creation_identity, 'pid:31:owned')
            with self.assertRaises(CustodyError):
                OwnedProcessTree.prepare()

    def test_pointer_results_and_kernel_false_bool_capture(self):
        """All native handle results are pointer-sized and BOOL failures retain code."""
        for operation in ('CreateJobObjectW', 'OpenProcess', 'OpenThread', 'OpenJobObjectW'):
            self.assertEqual(ctypes.sizeof(SIGNATURES[operation][0]), ctypes.sizeof(ctypes.c_void_p))
        kernel = Kernel.__new__(Kernel)
        with patch.object(kernel, 'raw', return_value=(0, 5)):
            with self.assertRaises(OSError) as caught:
                kernel.checked('TerminateJobObject', 1, 1)
        self.assertEqual(caught.exception.error_code, 5)

    def test_legacy_assignment_failure_keeps_local_job_and_primary_plus_close_error(self):
        """Failed adoption retains the formerly local handle without changing catch category."""
        kernel = Standin(fail='AssignProcessToJobObject', close_fail=True)
        process = SimpleNamespace(pid=777, _handle=101)
        with patch('dayz_serverman.adapters.windows.process_jobs.Kernel', return_value=kernel), \
             patch.object(OwnedProcessTree, '_read_creation_identity', return_value='windows-filetime:12345'):
            with self.assertRaises(CustodyError) as caught:
                OwnedProcessTree(process)
        self.assertIs(caught.exception.original, kernel.error)
        tree = caught.exception.custody.anchors['tree']
        self.assertEqual(tree.job.reference.state, 'owned')
        kernel.fail = None
        with self.assertRaises(CustodyError):
            tree.close_job()
        self.assertIs(tree.custody.primary, kernel.error)
        self.assertEqual(tree.job.reference.state, 'release-unknown')
        self.assertEqual(kernel.flags, 0x2000)
        self.assertEqual(kernel.memory, 0)
        with self.assertRaises(CustodyError):
            tree.close_job()
        self.assertEqual(len(kernel.closed), 1)

    def test_probe_unexpected_error_preserves_original_and_borrowed_release_fault(self):
        """Compatibility probes retain unexpected faults without changing their exception identity."""
        kernel = Standin(fail='QueryInformationJobObject', close_fail=True)
        original = MemoryError('borrowed query allocation')
        kernel.error = original
        probe = WindowsChildProbe()
        with patch('dayz_serverman.adapters.windows.process_bindings.Kernel', return_value=kernel):
            with self.assertRaises(MemoryError) as caught:
                probe._job_absent('fake-job')
        self.assertIs(caught.exception, original)
        self.assertIs(original.custody.primary, original)
        self.assertEqual(original.custody.references[0].state, 'release-unknown')
