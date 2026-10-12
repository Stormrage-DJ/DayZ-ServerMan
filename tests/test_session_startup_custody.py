"""Check partial thread/log/descriptor startup custody without a native child."""
import io
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch, Mock
from control_audit import Audit
from pipe_control import Control
from watchdog_thread import Watchdog
from startup_custody import Startup, thread_absent
from startup_logs import OwnedLog
from writer_custody import Writer
from release_steps import Releases

REAL_THREAD = threading.Thread


class StartedThenFailed(REAL_THREAD):
    """Start a real finite thread before returning an exact injected start fault."""
    def start(self):
        """Preserve the actual started thread in its pre-registered owner."""
        super().start()
        raise MemoryError('after actual thread start')


class StartupTests(unittest.TestCase):
    """Exercise constructor seams with finite real threads and exact custody."""
    def setUp(self):
        """Collect any unexpected thread escape without suppressing its evidence."""
        self.unhandled, self.prior = [], threading.excepthook
        threading.excepthook = lambda args: self.unhandled.append(args.exc_value)
        self.addCleanup(setattr, threading, 'excepthook', self.prior)

    def tearDown(self):
        """Require no unhandled thread errors and no remaining owned consumers."""
        self.assertEqual(self.unhandled, [])

    def close(self, owner):
        """Attempt every exact resource release and require finite known-thread exit."""
        releases = Releases(owner.primary)
        owner.close_all(releases)
        for entry in owner.entries:
            if hasattr(entry.resource, 'thread'):
                self.assertTrue(thread_absent(entry.resource))
        return releases

    def test_audit_allocation_constructor_and_started_then_failed(self):
        """Queue/event/thread setup failures and actual side effects remain visible."""
        cases = [('control_audit.queue.Queue', MemoryError('queue')),
                 ('control_audit.threading.Event', OverflowError('event')),
                 ('control_audit.threading.Thread', TypeError('thread constructor')),
                 ('control_audit.threading.Thread', StartedThenFailed)]
        for target, fault in cases:
            with self.subTest(target=target, fault=str(fault)):
                owner = Startup()
                options = {'side_effect': fault} if isinstance(fault, BaseException) else {'new': fault}
                with patch(target, **options):
                    with self.assertRaises(BaseException) as caught:
                        Audit(io.StringIO(), lambda message: None, owner)
                self.assertIs(caught.exception.startup_custody, owner)
                self.assertIsNotNone(owner.find('audit'))
                self.close(owner)

    def test_control_reader_constructor_after_audit_started_and_start_fault(self):
        """A reader failure cannot orphan the audit which already started."""
        for start_fault in (False, True):
            owner, pipe = Startup(), Mock()
            def factory(*args, **kwargs):
                """Fail only the reader; keep the real audit constructor unchanged."""
                if kwargs.get('name') == 'control-reader':
                    if start_fault:
                        return StartedThenFailed(*args, **kwargs)
                    raise MemoryError('reader constructor after audit start')
                return REAL_THREAD(*args, **kwargs)
            with patch('pipe_control.threading.Thread', side_effect=factory), patch('pipe_control.os.read', return_value=b''):
                with self.assertRaises(MemoryError) as caught:
                    Control(pipe, io.StringIO(), 0, owner)
            self.assertIs(caught.exception.startup_custody, owner)
            self.assertIsNotNone(owner.find('control-audit').thread.ident)
            self.close(owner)
            pipe.close.assert_called_once()

    def test_control_lock_failure_and_guard_setup_failures(self):
        """Resources which never started release without asserting a missing join."""
        owner = Startup()
        with patch('pipe_control.threading.Lock', side_effect=MemoryError('lock')):
            with self.assertRaises(MemoryError):
                Control(Mock(), io.StringIO(), 0, owner)
        self.close(owner)
        for target in ('watchdog_thread.threading.Event', 'watchdog_thread.threading.Thread'):
            owner = Startup()
            with patch(target, side_effect=MemoryError('guard construction')):
                with self.assertRaises(MemoryError):
                    Watchdog(None, None, None, None, 0, None, owner)
            self.close(owner)

    def test_guard_started_then_throw_keeps_thread_and_original_fault(self):
        """A constructor which fails after the guard started retains its exact thread."""
        owner = Startup()
        with patch('watchdog_thread.threading.Thread', StartedThenFailed), \
             patch('watchdog_thread.watch', return_value={'fixture': True}):
            with self.assertRaises(MemoryError) as caught:
                Watchdog(None, Mock(), None, None, 0, Mock(), owner)
        self.assertIs(owner.primary, caught.exception)
        self.close(owner)

    def test_failed_start_without_identity_blocks_log_close_and_is_not_retried(self):
        """An ambiguous failed start never becomes evidence of consumer absence."""
        owner = Startup()
        fake = NS(ident=None, is_alive=lambda: False, start=Mock(side_effect=MemoryError('start unknown')))
        handle = Mock()
        log = OwnedLog(owner, NS(open=lambda *args, **kw: handle), 'log')
        with patch('control_audit.threading.Thread', return_value=fake):
            with self.assertRaises(MemoryError):
                Audit(log, lambda message: None, owner)
        releases = Releases(owner.primary)
        owner.close_all(releases)
        self.assertFalse(thread_absent(owner.find('audit')))
        handle.close.assert_not_called()
        before = len(releases.errors)
        owner.close_all(releases)
        self.assertEqual(len(releases.errors), before)
        self.assertTrue(all(entry.state == 'release-unknown' for entry in owner.entries))

    def test_log_before_bookkeeping_and_original_plus_close_failure(self):
        """A later bookkeeping fault cannot discard the acquired evidence file."""
        owner, handle = Startup(), Mock()
        log = OwnedLog(owner, NS(open=lambda *args, **kw: handle), 'retained')
        original = MemoryError('list bookkeeping')
        owner.fail(original)
        secondary = OSError('exact log close failed')
        handle.close.side_effect = secondary
        releases = self.close(owner)
        self.assertIs(releases.primary, original)
        self.assertIs(releases.errors[0][1], secondary)
        with self.assertRaises(OSError):
            owner.close_one(log)
        handle.close.assert_called_once()

    def test_descriptor_acquisition_then_redirect_and_close_fault(self):
        """Descriptor state survives later setup and refuses a recycled-fd retry."""
        writer, original = Writer(), MemoryError('redirect setup')
        with patch('writer_custody.os.dup', return_value=71), patch('writer_custody.os.close', side_effect=OSError('close')) as close:
            self.assertEqual(writer.acquire(), 71)
            writer.fail(original)
            with self.assertRaises(OSError):
                writer.close()
            with self.assertRaises(RuntimeError):
                writer.close()
        self.assertIs(writer.primary, original)
        self.assertEqual(writer.state, 'release-unknown')
        self.assertEqual(len(writer.secondary), 1)
        close.assert_called_once_with(71)

    def test_reader_buffer_allocation_failure_is_retained_without_thread_escape(self):
        """The first worker allocation must share the reader's retained fault boundary."""
        owner, original = Startup(), MemoryError('first worker frame allocation')
        with patch('pipe_control.bytearray', side_effect=original, create=True):
            control = Control(Mock(), io.StringIO(), 0, owner)
            control.thread.join(1)
        self.assertIs(control.reader_failure, original)
        self.assertTrue(control.done.is_set())
        with self.assertRaises(RuntimeError) as caught:
            control.check(0)
        self.assertIs(caught.exception.__cause__, original)
        self.close(owner)
