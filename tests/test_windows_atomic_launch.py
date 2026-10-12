"""Check job-at-creation transactions with explicit fake native outputs."""
import io
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from native_standin import Standin
from dayz_serverman.adapters.windows.process_tree import OwnedProcessTree
from dayz_serverman.adapters.windows.process_custody import CustodyError
from dayz_serverman.adapters.windows.process_child import NativeChild, OwnedStream
from dayz_serverman.adapters.windows.process_creation import environment_block, CREATION_FLAGS


def attach(child, pipe):
    """Transfer the fake pipe to one exact fake stream without acquiring a real fd."""
    handle = pipe.value()
    ref = child.custody.reserve('stdout-stream', 'fake-stream', lambda stream: child.kernel.close(handle))
    ref.acquire(io.BytesIO)
    pipe.transfer()
    child.stdout = OwnedStream(ref)


class LaunchTests(unittest.TestCase):
    """Cover each atomic creation seam and every native out-parameter owner."""
    def setUp(self):
        """Replace descriptor boundaries only inside this deterministic fixture."""
        self.patches = [patch('os.open', return_value=900), patch('os.close'),
                        patch('msvcrt.get_osfhandle', return_value=901),
                        patch.object(NativeChild, 'attach_stdout', attach)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def launch(self, kernel):
        """Exercise the reviewed full transaction with fixed argument/environment data."""
        tree = OwnedProcessTree.prepare(kernel=kernel)
        child = tree.launch(['D:/exact/python.exe', '', 'with space', 'quote"tail\\'],
                            Path('D:/exact'), {'A169_VALUE': 'value with space'}, Mock(fileno=lambda: 902))
        return tree, child

    def release(self, tree):
        """Release only the exact fake references after fake process absence."""
        tree.job.kernel.fail, tree.job.kernel.after = None, None
        child = tree.custody.anchors.get('native-child')
        if child is not None and child.process_reference.state == 'owned':
            child.wait(0.01)
            child.close_handle()
        tree.job.kernel.pids = []
        tree.close_job()
        for ref in tree.custody.references:
            if ref.state == 'owned':
                ref.close()

    def test_atomic_attributes_limits_order_and_natural_interface(self):
        """All limits/attributes precede creation; stdout EOF and natural exit are retained."""
        kernel = Standin()
        tree, child = self.launch(kernel)
        calls = kernel.calls
        self.assertLess(calls.index('SetInformationJobObject'), calls.index('CreateProcessW'))
        self.assertEqual(calls.count('UpdateProcThreadAttribute'), 2)
        self.assertNotIn('AssignProcessToJobObject', calls)
        self.assertEqual(child.stdout.read(512), b'')
        self.assertEqual(child.wait(0.01), 0)
        self.assertGreater(child.launch_monotonic, 0)
        self.assertEqual(CREATION_FLAGS & 0x1000000, 0)
        self.release(tree)
        self.assertFalse(kernel.live)

    def test_every_creation_boundary_keeps_original_and_partial_custody(self):
        """Native failures never lose a returned pipe, duplicate, process or thread."""
        operations = ('CreatePipe', 'SetHandleInformation', 'GetCurrentProcess', 'DuplicateHandle',
                      'InitializeProcThreadAttributeList', 'UpdateProcThreadAttribute', 'CreateProcessW',
                      'IsProcessInJob', 'GetProcessTimes')
        for operation in operations:
            with self.subTest(operation=operation):
                kernel = Standin(fail=operation)
                tree = OwnedProcessTree.prepare(kernel=kernel)
                with self.assertRaises(CustodyError) as caught:
                    tree.launch(['D:/exact/python.exe'], Path('D:/exact'), {}, Mock(fileno=lambda: 902))
                self.assertIs(caught.exception.original, kernel.error)
                self.assertIs(caught.exception.custody, tree.custody)
                if operation != 'GetProcessTimes':
                    self.assertEqual(tree.custody.primary, kernel.error)
                self.release(tree)
                self.assertFalse(kernel.live)

    def test_native_side_effect_then_error_retains_out_parameters(self):
        """Out-parameter custody survives errors after native acquisition."""
        for operation in ('CreatePipe', 'DuplicateHandle', 'CreateProcessW'):
            with self.subTest(operation=operation):
                kernel = Standin(after=operation)
                tree = OwnedProcessTree.prepare(kernel=kernel)
                with self.assertRaises(CustodyError) as caught:
                    tree.launch(['D:/exact/python.exe'], Path('D:/exact'), {}, Mock(fileno=lambda: 902))
                self.assertIs(caught.exception.original, kernel.error)
                self.assertTrue(any(ref.state == 'owned' for ref in tree.custody.references))
                if operation == 'CreateProcessW':
                    child = tree.custody.anchors['native-child']
                    self.assertEqual(child.process_reference.state, 'owned')
                    self.assertEqual(child.thread_reference.state, 'released')
                self.release(tree)
                self.assertFalse(kernel.live)

    def test_descriptor_and_stream_bookkeeping_faults_keep_native_process(self):
        """Failures after process creation retain exact child/job for containment."""
        for operation in ('open_osfhandle', 'fdopen'):
            with self.subTest(operation=operation):
                kernel = Standin()
                tree = OwnedProcessTree.prepare(kernel=kernel)
                original = MemoryError('descriptor bookkeeping fault')
                with patch.object(NativeChild, 'attach_stdout', NativeChild.attach_stdout.__wrapped__ if hasattr(NativeChild.attach_stdout, '__wrapped__') else ORIGINAL_ATTACH), \
                     patch('msvcrt.open_osfhandle', side_effect=original if operation == 'open_osfhandle' else None, return_value=903), \
                     patch('os.fdopen', side_effect=original if operation == 'fdopen' else None):
                    with self.assertRaises(CustodyError) as caught:
                        tree.launch(['D:/exact/python.exe'], Path('D:/exact'), {}, Mock(fileno=lambda: 902))
                self.assertIs(caught.exception.original, original)
                self.assertEqual(tree.process.process_reference.state, 'owned')
                if operation == 'fdopen':
                    # The native read end transferred to the retained descriptor, not a second owner.
                    read_ref = next(ref for ref in tree.custody.references if ref.kind == 'stdout-pipe')
                    self.assertEqual(read_ref.state, 'transferred')
                    kernel.live.remove(read_ref.handle)
                self.release(tree)
                self.assertFalse(kernel.live)

    def test_environment_and_quoting_preserve_empty_unicode_and_spaces(self):
        """The launch transaction preserves Windows string/environment boundaries."""
        self.assertEqual(environment_block({'Z': 'á space', 'A': ''}), 'A=\0Z=á space\0\0')
        self.assertIn('""', subprocess.list2cmdline(['D:/x.exe', '', 'a b']))
        for values in ({'BAD=KEY': 'x'}, {'A': 'x\0y'}):
            with self.assertRaises(ValueError):
                environment_block(values)

    def test_creation_does_not_release_unrelated_caller_reference(self):
        """Launch cleanup owns its temporary references rather than every owner's slot."""
        kernel = Standin()
        tree = OwnedProcessTree.prepare(kernel=kernel)
        caller_close = Mock()
        reference = tree.custody.reserve('caller-resource', 'external', caller_close)
        reference.acquire(lambda: 42)
        tree.launch(['D:/exact/python.exe'], Path('D:/exact'), {}, Mock(fileno=lambda: 902))
        self.assertEqual(reference.state, 'owned')
        caller_close.assert_not_called()
        self.release(tree)
        caller_close.assert_called_once_with(42)


# Preserve the actual implementation before fixture substitution.
ORIGINAL_ATTACH = NativeChild.attach_stdout
