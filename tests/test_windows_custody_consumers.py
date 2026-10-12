"""Check fault custody through SteamCMD reporting and the runtime admission gate."""
import json
import tempfile
import threading
import inspect
from dataclasses import asdict
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from native_standin import Standin
from native_identity import admit_runtime
from dayz_serverman.adapters.windows.process_custody import Custody, CustodyError
from dayz_serverman.adapters.windows.process_tree import OwnedProcessTree
from dayz_serverman.adapters.windows import steamcmd
from dayz_serverman.adapters.windows.steamcmd_custody import retaining_call


class ConsumerTests(unittest.TestCase):
    """Preserve gates, public exceptions, legacy patches and retained ownership."""
    def test_result_constructor_and_serialized_fields_are_unchanged(self):
        """Custody attachments do not expand the existing dataclass contract."""
        fields = ('exit_code', 'lines', 'cancelled', 'termination_confirmed', 'process_id', 'child_evidence')
        result = steamcmd.SteamCmdRunResult(0, (), False, True, 1)
        self.assertEqual(tuple(inspect.signature(steamcmd.SteamCmdRunResult).parameters), fields)
        self.assertEqual(tuple(asdict(result)), fields)
        from dayz_serverman.adapters.windows.steamcmd_custody import with_custody
        with_custody(result, Custody())
        self.assertEqual(tuple(asdict(result)), fields)
        self.assertIsNotNone(result.failure_custody)
    def test_runtime_nonmember_identity_and_close_faults_never_open_gate(self):
        """Runtime query rights cannot become a late assignment or gate authority."""
        for partition in ('nonmember', 'identity', 'close', 'open'):
            with self.subTest(partition=partition), tempfile.TemporaryDirectory(prefix='a169-gate-') as directory:
                folder, kernel = Path(directory), Standin()
                tree = OwnedProcessTree.prepare(kernel=kernel)
                identity = {'pid': 777, 'creation_filetime': 12345 if partition != 'identity' else 54321}
                (folder / 'runtime-identity.json').write_text(json.dumps(identity), encoding='utf-8')
                kernel.member = partition != 'nonmember'
                kernel.close_fail = partition == 'close'
                kernel.fail = 'OpenProcess' if partition == 'open' else None
                with self.assertRaises(CustodyError):
                    admit_runtime(tree, folder)
                self.assertFalse((folder / 'webview-gate').exists())
                self.assertNotIn('AssignProcessToJobObject', kernel.calls)
                query = tree.custody.references[-1]
                if partition == 'close':
                    self.assertEqual(query.state, 'release-unknown')
                else:
                    self.assertIn(query.state, ('released', 'reserved'))
                kernel.fail, kernel.close_fail = None, False
                tree.close_job()

    def test_runtime_success_requires_identity_member_and_query_release(self):
        """An identity file admits the runtime only after independent handle proof."""
        with tempfile.TemporaryDirectory(prefix='a169-gate-') as directory:
            folder, kernel = Path(directory), Standin()
            tree = OwnedProcessTree.prepare(kernel=kernel)
            identity = {'pid': 777, 'creation_filetime': 12345}
            (folder / 'runtime-identity.json').write_text(json.dumps(identity), encoding='utf-8')
            self.assertEqual(admit_runtime(tree, folder), identity)
            self.assertTrue((folder / 'webview-gate').exists())
            self.assertEqual(tree.custody.references[-1].state, 'released')
            tree.close_job()
            self.assertFalse(kernel.live)

    def test_original_callback_fault_is_retained_through_adapter_boundary(self):
        """Existing callback exceptions remain the same object with surviving custody."""
        process = Mock(pid=777, stdout=None, returncode=0)
        process.poll.return_value = 0
        tree = Mock(custody=Custody())
        tree.wait_absent.return_value = True
        original = TypeError('public callback failure')
        adapter = NS(custody_holds=[])
        with patch.object(steamcmd, 'OwnedProcessTree', return_value=tree):
            with self.assertRaises(TypeError) as caught:
                retaining_call(adapter, steamcmd.supervise_owned, process, lambda: False,
                               Mock(side_effect=original), [])
        self.assertIs(caught.exception, original)
        self.assertIs(adapter.custody_holds[0], tree.custody)
        self.assertIs(tree.custody.anchors['legacy-process'], process)
        tree.request_close.assert_called_once()

    def test_reader_start_failure_after_side_effect_retains_finite_thread(self):
        """An output-reader start error cannot lose the started reader or process."""
        real_thread = threading.Thread
        class StartedThenFailed(real_thread):
            """Inject a constructor-boundary fault after actual finite reader start."""
            def start(self):
                """Start the real fixture then retain its original failure."""
                super().start()
                raise MemoryError('reader start after side effect')
        process = Mock(pid=777, stdout=iter(()), returncode=0)
        process.poll.return_value = 0
        tree = Mock(custody=Custody())
        tree.wait_absent.return_value = True
        with patch.object(steamcmd, 'OwnedProcessTree', return_value=tree), patch.object(steamcmd.threading, 'Thread', StartedThenFailed):
            with self.assertRaises(MemoryError) as caught:
                steamcmd.supervise_owned(process, lambda: False, lambda evidence: None, [])
        self.assertIs(caught.exception.custody, tree.custody)
        self.assertFalse(tree.custody.anchors['steamcmd-reader'].is_alive())

    def test_constructor_original_plus_termination_failure_is_unconfirmed(self):
        """Constructor containment failures survive the existing unconfirmed result."""
        custody = Custody()
        original = OSError('assignment failed')
        custody.fault(original)
        process = Mock(pid=777, stdout=None)
        process.terminate.side_effect = OSError('terminate refused')
        process.wait.return_value = 0
        with patch.object(steamcmd, 'OwnedProcessTree', side_effect=CustodyError(original, custody)):
            result = steamcmd.supervise_owned(process, lambda: False, lambda evidence: None, [])
        self.assertFalse(result.termination_confirmed)
        self.assertIs(result.failure_custody.primary, original)
        self.assertEqual(result.failure_custody.secondary[0][0], 'held-terminate')

    def test_reader_failure_is_finite_and_survives_result_without_unhandled_thread(self):
        """A failed output iterator latches the original fault and completes its supervisor."""
        original = MemoryError('reader allocation')
        class Broken:
            """Refuse a single reader operation without any actual pipe."""
            def __iter__(self):
                """Expose this exact fixture iterator."""
                return self
            def __next__(self):
                """Raise the selected original read failure."""
                raise original
        process = Mock(pid=777, stdout=Broken(), returncode=0)
        process.poll.return_value = 0
        tree = Mock(custody=Custody())
        tree.wait_absent.return_value = True
        with patch.object(steamcmd, 'OwnedProcessTree', return_value=tree):
            result = steamcmd.supervise_owned(process, lambda: False, lambda evidence: None, [])
        self.assertFalse(result.termination_confirmed)
        self.assertIs(result.failure_custody.primary, original)
        self.assertFalse(result.failure_custody.anchors['steamcmd-reader'].is_alive())
