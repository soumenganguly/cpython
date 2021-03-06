"""Tests for tasks.py."""

import gc
import unittest
import unittest.mock
from unittest.mock import Mock

from asyncio import events
from asyncio import futures
from asyncio import tasks
from asyncio import test_utils


class Dummy:

    def __repr__(self):
        return 'Dummy()'

    def __call__(self, *args):
        pass


class TaskTests(unittest.TestCase):

    def setUp(self):
        self.loop = test_utils.TestLoop()
        events.set_event_loop(None)

    def tearDown(self):
        self.loop.close()
        gc.collect()

    def test_task_class(self):
        @tasks.coroutine
        def notmuch():
            return 'ok'
        t = tasks.Task(notmuch(), loop=self.loop)
        self.loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ok')
        self.assertIs(t._loop, self.loop)

        loop = events.new_event_loop()
        t = tasks.Task(notmuch(), loop=loop)
        self.assertIs(t._loop, loop)
        loop.close()

    def test_async_coroutine(self):
        @tasks.coroutine
        def notmuch():
            return 'ok'
        t = tasks.async(notmuch(), loop=self.loop)
        self.loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ok')
        self.assertIs(t._loop, self.loop)

        loop = events.new_event_loop()
        t = tasks.async(notmuch(), loop=loop)
        self.assertIs(t._loop, loop)
        loop.close()

    def test_async_future(self):
        f_orig = futures.Future(loop=self.loop)
        f_orig.set_result('ko')

        f = tasks.async(f_orig)
        self.loop.run_until_complete(f)
        self.assertTrue(f.done())
        self.assertEqual(f.result(), 'ko')
        self.assertIs(f, f_orig)

        loop = events.new_event_loop()

        with self.assertRaises(ValueError):
            f = tasks.async(f_orig, loop=loop)

        loop.close()

        f = tasks.async(f_orig, loop=self.loop)
        self.assertIs(f, f_orig)

    def test_async_task(self):
        @tasks.coroutine
        def notmuch():
            return 'ok'
        t_orig = tasks.Task(notmuch(), loop=self.loop)
        t = tasks.async(t_orig)
        self.loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'ok')
        self.assertIs(t, t_orig)

        loop = events.new_event_loop()

        with self.assertRaises(ValueError):
            t = tasks.async(t_orig, loop=loop)

        loop.close()

        t = tasks.async(t_orig, loop=self.loop)
        self.assertIs(t, t_orig)

    def test_async_neither(self):
        with self.assertRaises(TypeError):
            tasks.async('ok')

    def test_task_repr(self):
        @tasks.coroutine
        def notmuch():
            yield from []
            return 'abc'

        t = tasks.Task(notmuch(), loop=self.loop)
        t.add_done_callback(Dummy())
        self.assertEqual(repr(t), 'Task(<notmuch>)<PENDING, [Dummy()]>')
        t.cancel()  # Does not take immediate effect!
        self.assertEqual(repr(t), 'Task(<notmuch>)<CANCELLING, [Dummy()]>')
        self.assertRaises(futures.CancelledError,
                          self.loop.run_until_complete, t)
        self.assertEqual(repr(t), 'Task(<notmuch>)<CANCELLED>')
        t = tasks.Task(notmuch(), loop=self.loop)
        self.loop.run_until_complete(t)
        self.assertEqual(repr(t), "Task(<notmuch>)<result='abc'>")

    def test_task_repr_custom(self):
        @tasks.coroutine
        def coro():
            pass

        class T(futures.Future):
            def __repr__(self):
                return 'T[]'

        class MyTask(tasks.Task, T):
            def __repr__(self):
                return super().__repr__()

        gen = coro()
        t = MyTask(gen, loop=self.loop)
        self.assertEqual(repr(t), 'T[](<coro>)')
        gen.close()

    def test_task_basics(self):
        @tasks.coroutine
        def outer():
            a = yield from inner1()
            b = yield from inner2()
            return a+b

        @tasks.coroutine
        def inner1():
            return 42

        @tasks.coroutine
        def inner2():
            return 1000

        t = outer()
        self.assertEqual(self.loop.run_until_complete(t), 1042)

    def test_cancel(self):

        def gen():
            when = yield
            self.assertAlmostEqual(10.0, when)
            yield 0

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        @tasks.coroutine
        def task():
            yield from tasks.sleep(10.0, loop=loop)
            return 12

        t = tasks.Task(task(), loop=loop)
        loop.call_soon(t.cancel)
        with self.assertRaises(futures.CancelledError):
            loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertTrue(t.cancelled())
        self.assertFalse(t.cancel())

    def test_cancel_yield(self):
        @tasks.coroutine
        def task():
            yield
            yield
            return 12

        t = tasks.Task(task(), loop=self.loop)
        test_utils.run_briefly(self.loop)  # start coro
        t.cancel()
        self.assertRaises(
            futures.CancelledError, self.loop.run_until_complete, t)
        self.assertTrue(t.done())
        self.assertTrue(t.cancelled())
        self.assertFalse(t.cancel())

    def test_cancel_inner_future(self):
        f = futures.Future(loop=self.loop)

        @tasks.coroutine
        def task():
            yield from f
            return 12

        t = tasks.Task(task(), loop=self.loop)
        test_utils.run_briefly(self.loop)  # start task
        f.cancel()
        with self.assertRaises(futures.CancelledError):
            self.loop.run_until_complete(t)
        self.assertTrue(f.cancelled())
        self.assertTrue(t.cancelled())

    def test_cancel_both_task_and_inner_future(self):
        f = futures.Future(loop=self.loop)

        @tasks.coroutine
        def task():
            yield from f
            return 12

        t = tasks.Task(task(), loop=self.loop)
        test_utils.run_briefly(self.loop)

        f.cancel()
        t.cancel()

        with self.assertRaises(futures.CancelledError):
            self.loop.run_until_complete(t)

        self.assertTrue(t.done())
        self.assertTrue(f.cancelled())
        self.assertTrue(t.cancelled())

    def test_cancel_task_catching(self):
        fut1 = futures.Future(loop=self.loop)
        fut2 = futures.Future(loop=self.loop)

        @tasks.coroutine
        def task():
            yield from fut1
            try:
                yield from fut2
            except futures.CancelledError:
                return 42

        t = tasks.Task(task(), loop=self.loop)
        test_utils.run_briefly(self.loop)
        self.assertIs(t._fut_waiter, fut1)  # White-box test.
        fut1.set_result(None)
        test_utils.run_briefly(self.loop)
        self.assertIs(t._fut_waiter, fut2)  # White-box test.
        t.cancel()
        self.assertTrue(fut2.cancelled())
        res = self.loop.run_until_complete(t)
        self.assertEqual(res, 42)
        self.assertFalse(t.cancelled())

    def test_cancel_task_ignoring(self):
        fut1 = futures.Future(loop=self.loop)
        fut2 = futures.Future(loop=self.loop)
        fut3 = futures.Future(loop=self.loop)

        @tasks.coroutine
        def task():
            yield from fut1
            try:
                yield from fut2
            except futures.CancelledError:
                pass
            res = yield from fut3
            return res

        t = tasks.Task(task(), loop=self.loop)
        test_utils.run_briefly(self.loop)
        self.assertIs(t._fut_waiter, fut1)  # White-box test.
        fut1.set_result(None)
        test_utils.run_briefly(self.loop)
        self.assertIs(t._fut_waiter, fut2)  # White-box test.
        t.cancel()
        self.assertTrue(fut2.cancelled())
        test_utils.run_briefly(self.loop)
        self.assertIs(t._fut_waiter, fut3)  # White-box test.
        fut3.set_result(42)
        res = self.loop.run_until_complete(t)
        self.assertEqual(res, 42)
        self.assertFalse(fut3.cancelled())
        self.assertFalse(t.cancelled())

    def test_cancel_current_task(self):
        loop = events.new_event_loop()
        self.addCleanup(loop.close)

        @tasks.coroutine
        def task():
            t.cancel()
            self.assertTrue(t._must_cancel)  # White-box test.
            # The sleep should be cancelled immediately.
            yield from tasks.sleep(100, loop=loop)
            return 12

        t = tasks.Task(task(), loop=loop)
        self.assertRaises(
            futures.CancelledError, loop.run_until_complete, t)
        self.assertTrue(t.done())
        self.assertFalse(t._must_cancel)  # White-box test.
        self.assertFalse(t.cancel())

    def test_stop_while_run_in_complete(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.1, when)
            when = yield 0.1
            self.assertAlmostEqual(0.2, when)
            when = yield 0.1
            self.assertAlmostEqual(0.3, when)
            yield 0.1

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        x = 0
        waiters = []

        @tasks.coroutine
        def task():
            nonlocal x
            while x < 10:
                waiters.append(tasks.sleep(0.1, loop=loop))
                yield from waiters[-1]
                x += 1
                if x == 2:
                    loop.stop()

        t = tasks.Task(task(), loop=loop)
        self.assertRaises(
            RuntimeError, loop.run_until_complete, t)
        self.assertFalse(t.done())
        self.assertEqual(x, 2)
        self.assertAlmostEqual(0.3, loop.time())

        # close generators
        for w in waiters:
            w.close()

    def test_wait_for(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.2, when)
            when = yield 0
            self.assertAlmostEqual(0.1, when)
            when = yield 0.1
            self.assertAlmostEqual(0.4, when)
            yield 0.1

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        @tasks.coroutine
        def foo():
            yield from tasks.sleep(0.2, loop=loop)
            return 'done'

        fut = tasks.Task(foo(), loop=loop)

        with self.assertRaises(futures.TimeoutError):
            loop.run_until_complete(tasks.wait_for(fut, 0.1, loop=loop))

        self.assertFalse(fut.done())
        self.assertAlmostEqual(0.1, loop.time())

        # wait for result
        res = loop.run_until_complete(
            tasks.wait_for(fut, 0.3, loop=loop))
        self.assertEqual(res, 'done')
        self.assertAlmostEqual(0.2, loop.time())

    def test_wait_for_with_global_loop(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.2, when)
            when = yield 0
            self.assertAlmostEqual(0.01, when)
            yield 0.01

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        @tasks.coroutine
        def foo():
            yield from tasks.sleep(0.2, loop=loop)
            return 'done'

        events.set_event_loop(loop)
        try:
            fut = tasks.Task(foo(), loop=loop)
            with self.assertRaises(futures.TimeoutError):
                loop.run_until_complete(tasks.wait_for(fut, 0.01))
        finally:
            events.set_event_loop(None)

        self.assertAlmostEqual(0.01, loop.time())
        self.assertFalse(fut.done())

        # move forward to close generator
        loop.advance_time(10)
        loop.run_until_complete(fut)

    def test_wait(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.1, when)
            when = yield 0
            self.assertAlmostEqual(0.15, when)
            yield 0.15

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        a = tasks.Task(tasks.sleep(0.1, loop=loop), loop=loop)
        b = tasks.Task(tasks.sleep(0.15, loop=loop), loop=loop)

        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a], loop=loop)
            self.assertEqual(done, set([a, b]))
            self.assertEqual(pending, set())
            return 42

        res = loop.run_until_complete(tasks.Task(foo(), loop=loop))
        self.assertEqual(res, 42)
        self.assertAlmostEqual(0.15, loop.time())

        # Doing it again should take no time and exercise a different path.
        res = loop.run_until_complete(tasks.Task(foo(), loop=loop))
        self.assertAlmostEqual(0.15, loop.time())
        self.assertEqual(res, 42)

    def test_wait_with_global_loop(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.01, when)
            when = yield 0
            self.assertAlmostEqual(0.015, when)
            yield 0.015

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        a = tasks.Task(tasks.sleep(0.01, loop=loop), loop=loop)
        b = tasks.Task(tasks.sleep(0.015, loop=loop), loop=loop)

        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a])
            self.assertEqual(done, set([a, b]))
            self.assertEqual(pending, set())
            return 42

        events.set_event_loop(loop)
        try:
            res = loop.run_until_complete(
                tasks.Task(foo(), loop=loop))
        finally:
            events.set_event_loop(None)

        self.assertEqual(res, 42)

    def test_wait_errors(self):
        self.assertRaises(
            ValueError, self.loop.run_until_complete,
            tasks.wait(set(), loop=self.loop))

        self.assertRaises(
            ValueError, self.loop.run_until_complete,
            tasks.wait([tasks.sleep(10.0, loop=self.loop)],
                       return_when=-1, loop=self.loop))

    def test_wait_first_completed(self):

        def gen():
            when = yield
            self.assertAlmostEqual(10.0, when)
            when = yield 0
            self.assertAlmostEqual(0.1, when)
            yield 0.1

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        a = tasks.Task(tasks.sleep(10.0, loop=loop), loop=loop)
        b = tasks.Task(tasks.sleep(0.1, loop=loop), loop=loop)
        task = tasks.Task(
            tasks.wait([b, a], return_when=tasks.FIRST_COMPLETED,
                       loop=loop),
            loop=loop)

        done, pending = loop.run_until_complete(task)
        self.assertEqual({b}, done)
        self.assertEqual({a}, pending)
        self.assertFalse(a.done())
        self.assertTrue(b.done())
        self.assertIsNone(b.result())
        self.assertAlmostEqual(0.1, loop.time())

        # move forward to close generator
        loop.advance_time(10)
        loop.run_until_complete(tasks.wait([a, b], loop=loop))

    def test_wait_really_done(self):
        # there is possibility that some tasks in the pending list
        # became done but their callbacks haven't all been called yet

        @tasks.coroutine
        def coro1():
            yield

        @tasks.coroutine
        def coro2():
            yield
            yield

        a = tasks.Task(coro1(), loop=self.loop)
        b = tasks.Task(coro2(), loop=self.loop)
        task = tasks.Task(
            tasks.wait([b, a], return_when=tasks.FIRST_COMPLETED,
                       loop=self.loop),
            loop=self.loop)

        done, pending = self.loop.run_until_complete(task)
        self.assertEqual({a, b}, done)
        self.assertTrue(a.done())
        self.assertIsNone(a.result())
        self.assertTrue(b.done())
        self.assertIsNone(b.result())

    def test_wait_first_exception(self):

        def gen():
            when = yield
            self.assertAlmostEqual(10.0, when)
            yield 0

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        # first_exception, task already has exception
        a = tasks.Task(tasks.sleep(10.0, loop=loop), loop=loop)

        @tasks.coroutine
        def exc():
            raise ZeroDivisionError('err')

        b = tasks.Task(exc(), loop=loop)
        task = tasks.Task(
            tasks.wait([b, a], return_when=tasks.FIRST_EXCEPTION,
                       loop=loop),
            loop=loop)

        done, pending = loop.run_until_complete(task)
        self.assertEqual({b}, done)
        self.assertEqual({a}, pending)
        self.assertAlmostEqual(0, loop.time())

        # move forward to close generator
        loop.advance_time(10)
        loop.run_until_complete(tasks.wait([a, b], loop=loop))

    def test_wait_first_exception_in_wait(self):

        def gen():
            when = yield
            self.assertAlmostEqual(10.0, when)
            when = yield 0
            self.assertAlmostEqual(0.01, when)
            yield 0.01

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        # first_exception, exception during waiting
        a = tasks.Task(tasks.sleep(10.0, loop=loop), loop=loop)

        @tasks.coroutine
        def exc():
            yield from tasks.sleep(0.01, loop=loop)
            raise ZeroDivisionError('err')

        b = tasks.Task(exc(), loop=loop)
        task = tasks.wait([b, a], return_when=tasks.FIRST_EXCEPTION,
                          loop=loop)

        done, pending = loop.run_until_complete(task)
        self.assertEqual({b}, done)
        self.assertEqual({a}, pending)
        self.assertAlmostEqual(0.01, loop.time())

        # move forward to close generator
        loop.advance_time(10)
        loop.run_until_complete(tasks.wait([a, b], loop=loop))

    def test_wait_with_exception(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.1, when)
            when = yield 0
            self.assertAlmostEqual(0.15, when)
            yield 0.15

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        a = tasks.Task(tasks.sleep(0.1, loop=loop), loop=loop)

        @tasks.coroutine
        def sleeper():
            yield from tasks.sleep(0.15, loop=loop)
            raise ZeroDivisionError('really')

        b = tasks.Task(sleeper(), loop=loop)

        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a], loop=loop)
            self.assertEqual(len(done), 2)
            self.assertEqual(pending, set())
            errors = set(f for f in done if f.exception() is not None)
            self.assertEqual(len(errors), 1)

        loop.run_until_complete(tasks.Task(foo(), loop=loop))
        self.assertAlmostEqual(0.15, loop.time())

        loop.run_until_complete(tasks.Task(foo(), loop=loop))
        self.assertAlmostEqual(0.15, loop.time())

    def test_wait_with_timeout(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.1, when)
            when = yield 0
            self.assertAlmostEqual(0.15, when)
            when = yield 0
            self.assertAlmostEqual(0.11, when)
            yield 0.11

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        a = tasks.Task(tasks.sleep(0.1, loop=loop), loop=loop)
        b = tasks.Task(tasks.sleep(0.15, loop=loop), loop=loop)

        @tasks.coroutine
        def foo():
            done, pending = yield from tasks.wait([b, a], timeout=0.11,
                                                  loop=loop)
            self.assertEqual(done, set([a]))
            self.assertEqual(pending, set([b]))

        loop.run_until_complete(tasks.Task(foo(), loop=loop))
        self.assertAlmostEqual(0.11, loop.time())

        # move forward to close generator
        loop.advance_time(10)
        loop.run_until_complete(tasks.wait([a, b], loop=loop))

    def test_wait_concurrent_complete(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.1, when)
            when = yield 0
            self.assertAlmostEqual(0.15, when)
            when = yield 0
            self.assertAlmostEqual(0.1, when)
            yield 0.1

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        a = tasks.Task(tasks.sleep(0.1, loop=loop), loop=loop)
        b = tasks.Task(tasks.sleep(0.15, loop=loop), loop=loop)

        done, pending = loop.run_until_complete(
            tasks.wait([b, a], timeout=0.1, loop=loop))

        self.assertEqual(done, set([a]))
        self.assertEqual(pending, set([b]))
        self.assertAlmostEqual(0.1, loop.time())

        # move forward to close generator
        loop.advance_time(10)
        loop.run_until_complete(tasks.wait([a, b], loop=loop))

    def test_as_completed(self):

        def gen():
            yield 0
            yield 0
            yield 0.01
            yield 0

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)
        completed = set()
        time_shifted = False

        @tasks.coroutine
        def sleeper(dt, x):
            nonlocal time_shifted
            yield from tasks.sleep(dt, loop=loop)
            completed.add(x)
            if not time_shifted and 'a' in completed and 'b' in completed:
                time_shifted = True
                loop.advance_time(0.14)
            return x

        a = sleeper(0.01, 'a')
        b = sleeper(0.01, 'b')
        c = sleeper(0.15, 'c')

        @tasks.coroutine
        def foo():
            values = []
            for f in tasks.as_completed([b, c, a], loop=loop):
                values.append((yield from f))
            return values

        res = loop.run_until_complete(tasks.Task(foo(), loop=loop))
        self.assertAlmostEqual(0.15, loop.time())
        self.assertTrue('a' in res[:2])
        self.assertTrue('b' in res[:2])
        self.assertEqual(res[2], 'c')

        # Doing it again should take no time and exercise a different path.
        res = loop.run_until_complete(tasks.Task(foo(), loop=loop))
        self.assertAlmostEqual(0.15, loop.time())

    def test_as_completed_with_timeout(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.12, when)
            when = yield 0
            self.assertAlmostEqual(0.1, when)
            when = yield 0
            self.assertAlmostEqual(0.15, when)
            when = yield 0.1
            self.assertAlmostEqual(0.12, when)
            yield 0.02

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        a = tasks.sleep(0.1, 'a', loop=loop)
        b = tasks.sleep(0.15, 'b', loop=loop)

        @tasks.coroutine
        def foo():
            values = []
            for f in tasks.as_completed([a, b], timeout=0.12, loop=loop):
                try:
                    v = yield from f
                    values.append((1, v))
                except futures.TimeoutError as exc:
                    values.append((2, exc))
            return values

        res = loop.run_until_complete(tasks.Task(foo(), loop=loop))
        self.assertEqual(len(res), 2, res)
        self.assertEqual(res[0], (1, 'a'))
        self.assertEqual(res[1][0], 2)
        self.assertIsInstance(res[1][1], futures.TimeoutError)
        self.assertAlmostEqual(0.12, loop.time())

        # move forward to close generator
        loop.advance_time(10)
        loop.run_until_complete(tasks.wait([a, b], loop=loop))

    def test_as_completed_reverse_wait(self):

        def gen():
            yield 0
            yield 0.05
            yield 0

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        a = tasks.sleep(0.05, 'a', loop=loop)
        b = tasks.sleep(0.10, 'b', loop=loop)
        fs = {a, b}
        futs = list(tasks.as_completed(fs, loop=loop))
        self.assertEqual(len(futs), 2)

        x = loop.run_until_complete(futs[1])
        self.assertEqual(x, 'a')
        self.assertAlmostEqual(0.05, loop.time())
        loop.advance_time(0.05)
        y = loop.run_until_complete(futs[0])
        self.assertEqual(y, 'b')
        self.assertAlmostEqual(0.10, loop.time())

    def test_as_completed_concurrent(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.05, when)
            when = yield 0
            self.assertAlmostEqual(0.05, when)
            yield 0.05

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        a = tasks.sleep(0.05, 'a', loop=loop)
        b = tasks.sleep(0.05, 'b', loop=loop)
        fs = {a, b}
        futs = list(tasks.as_completed(fs, loop=loop))
        self.assertEqual(len(futs), 2)
        waiter = tasks.wait(futs, loop=loop)
        done, pending = loop.run_until_complete(waiter)
        self.assertEqual(set(f.result() for f in done), {'a', 'b'})

    def test_sleep(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.05, when)
            when = yield 0.05
            self.assertAlmostEqual(0.1, when)
            yield 0.05

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        @tasks.coroutine
        def sleeper(dt, arg):
            yield from tasks.sleep(dt/2, loop=loop)
            res = yield from tasks.sleep(dt/2, arg, loop=loop)
            return res

        t = tasks.Task(sleeper(0.1, 'yeah'), loop=loop)
        loop.run_until_complete(t)
        self.assertTrue(t.done())
        self.assertEqual(t.result(), 'yeah')
        self.assertAlmostEqual(0.1, loop.time())

    def test_sleep_cancel(self):

        def gen():
            when = yield
            self.assertAlmostEqual(10.0, when)
            yield 0

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        t = tasks.Task(tasks.sleep(10.0, 'yeah', loop=loop),
                       loop=loop)

        handle = None
        orig_call_later = loop.call_later

        def call_later(self, delay, callback, *args):
            nonlocal handle
            handle = orig_call_later(self, delay, callback, *args)
            return handle

        loop.call_later = call_later
        test_utils.run_briefly(loop)

        self.assertFalse(handle._cancelled)

        t.cancel()
        test_utils.run_briefly(loop)
        self.assertTrue(handle._cancelled)

    def test_task_cancel_sleeping_task(self):

        def gen():
            when = yield
            self.assertAlmostEqual(0.1, when)
            when = yield 0
            self.assertAlmostEqual(5000, when)
            yield 0.1

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        sleepfut = None

        @tasks.coroutine
        def sleep(dt):
            nonlocal sleepfut
            sleepfut = tasks.sleep(dt, loop=loop)
            yield from sleepfut

        @tasks.coroutine
        def doit():
            sleeper = tasks.Task(sleep(5000), loop=loop)
            loop.call_later(0.1, sleeper.cancel)
            try:
                yield from sleeper
            except futures.CancelledError:
                return 'cancelled'
            else:
                return 'slept in'

        doer = doit()
        self.assertEqual(loop.run_until_complete(doer), 'cancelled')
        self.assertAlmostEqual(0.1, loop.time())

    def test_task_cancel_waiter_future(self):
        fut = futures.Future(loop=self.loop)

        @tasks.coroutine
        def coro():
            yield from fut

        task = tasks.Task(coro(), loop=self.loop)
        test_utils.run_briefly(self.loop)
        self.assertIs(task._fut_waiter, fut)

        task.cancel()
        test_utils.run_briefly(self.loop)
        self.assertRaises(
            futures.CancelledError, self.loop.run_until_complete, task)
        self.assertIsNone(task._fut_waiter)
        self.assertTrue(fut.cancelled())

    def test_step_in_completed_task(self):
        @tasks.coroutine
        def notmuch():
            return 'ko'

        gen = notmuch()
        task = tasks.Task(gen, loop=self.loop)
        task.set_result('ok')

        self.assertRaises(AssertionError, task._step)
        gen.close()

    def test_step_result(self):
        @tasks.coroutine
        def notmuch():
            yield None
            yield 1
            return 'ko'

        self.assertRaises(
            RuntimeError, self.loop.run_until_complete, notmuch())

    def test_step_result_future(self):
        # If coroutine returns future, task waits on this future.

        class Fut(futures.Future):
            def __init__(self, *args, **kwds):
                self.cb_added = False
                super().__init__(*args, **kwds)

            def add_done_callback(self, fn):
                self.cb_added = True
                super().add_done_callback(fn)

        fut = Fut(loop=self.loop)
        result = None

        @tasks.coroutine
        def wait_for_future():
            nonlocal result
            result = yield from fut

        t = tasks.Task(wait_for_future(), loop=self.loop)
        test_utils.run_briefly(self.loop)
        self.assertTrue(fut.cb_added)

        res = object()
        fut.set_result(res)
        test_utils.run_briefly(self.loop)
        self.assertIs(res, result)
        self.assertTrue(t.done())
        self.assertIsNone(t.result())

    def test_step_with_baseexception(self):
        @tasks.coroutine
        def notmutch():
            raise BaseException()

        task = tasks.Task(notmutch(), loop=self.loop)
        self.assertRaises(BaseException, task._step)

        self.assertTrue(task.done())
        self.assertIsInstance(task.exception(), BaseException)

    def test_baseexception_during_cancel(self):

        def gen():
            when = yield
            self.assertAlmostEqual(10.0, when)
            yield 0

        loop = test_utils.TestLoop(gen)
        self.addCleanup(loop.close)

        @tasks.coroutine
        def sleeper():
            yield from tasks.sleep(10, loop=loop)

        base_exc = BaseException()

        @tasks.coroutine
        def notmutch():
            try:
                yield from sleeper()
            except futures.CancelledError:
                raise base_exc

        task = tasks.Task(notmutch(), loop=loop)
        test_utils.run_briefly(loop)

        task.cancel()
        self.assertFalse(task.done())

        self.assertRaises(BaseException, test_utils.run_briefly, loop)

        self.assertTrue(task.done())
        self.assertFalse(task.cancelled())
        self.assertIs(task.exception(), base_exc)

    def test_iscoroutinefunction(self):
        def fn():
            pass

        self.assertFalse(tasks.iscoroutinefunction(fn))

        def fn1():
            yield
        self.assertFalse(tasks.iscoroutinefunction(fn1))

        @tasks.coroutine
        def fn2():
            yield
        self.assertTrue(tasks.iscoroutinefunction(fn2))

    def test_yield_vs_yield_from(self):
        fut = futures.Future(loop=self.loop)

        @tasks.coroutine
        def wait_for_future():
            yield fut

        task = wait_for_future()
        with self.assertRaises(RuntimeError):
            self.loop.run_until_complete(task)

        self.assertFalse(fut.done())

    def test_yield_vs_yield_from_generator(self):
        @tasks.coroutine
        def coro():
            yield

        @tasks.coroutine
        def wait_for_future():
            gen = coro()
            try:
                yield gen
            finally:
                gen.close()

        task = wait_for_future()
        self.assertRaises(
            RuntimeError,
            self.loop.run_until_complete, task)

    def test_coroutine_non_gen_function(self):
        @tasks.coroutine
        def func():
            return 'test'

        self.assertTrue(tasks.iscoroutinefunction(func))

        coro = func()
        self.assertTrue(tasks.iscoroutine(coro))

        res = self.loop.run_until_complete(coro)
        self.assertEqual(res, 'test')

    def test_coroutine_non_gen_function_return_future(self):
        fut = futures.Future(loop=self.loop)

        @tasks.coroutine
        def func():
            return fut

        @tasks.coroutine
        def coro():
            fut.set_result('test')

        t1 = tasks.Task(func(), loop=self.loop)
        t2 = tasks.Task(coro(), loop=self.loop)
        res = self.loop.run_until_complete(t1)
        self.assertEqual(res, 'test')
        self.assertIsNone(t2.result())

    def test_current_task(self):
        self.assertIsNone(tasks.Task.current_task(loop=self.loop))
        @tasks.coroutine
        def coro(loop):
            self.assertTrue(tasks.Task.current_task(loop=loop) is task)

        task = tasks.Task(coro(self.loop), loop=self.loop)
        self.loop.run_until_complete(task)
        self.assertIsNone(tasks.Task.current_task(loop=self.loop))

    def test_current_task_with_interleaving_tasks(self):
        self.assertIsNone(tasks.Task.current_task(loop=self.loop))

        fut1 = futures.Future(loop=self.loop)
        fut2 = futures.Future(loop=self.loop)

        @tasks.coroutine
        def coro1(loop):
            self.assertTrue(tasks.Task.current_task(loop=loop) is task1)
            yield from fut1
            self.assertTrue(tasks.Task.current_task(loop=loop) is task1)
            fut2.set_result(True)

        @tasks.coroutine
        def coro2(loop):
            self.assertTrue(tasks.Task.current_task(loop=loop) is task2)
            fut1.set_result(True)
            yield from fut2
            self.assertTrue(tasks.Task.current_task(loop=loop) is task2)

        task1 = tasks.Task(coro1(self.loop), loop=self.loop)
        task2 = tasks.Task(coro2(self.loop), loop=self.loop)

        self.loop.run_until_complete(tasks.wait((task1, task2), loop=self.loop))
        self.assertIsNone(tasks.Task.current_task(loop=self.loop))

    # Some thorough tests for cancellation propagation through
    # coroutines, tasks and wait().

    def test_yield_future_passes_cancel(self):
        # Cancelling outer() cancels inner() cancels waiter.
        proof = 0
        waiter = futures.Future(loop=self.loop)

        @tasks.coroutine
        def inner():
            nonlocal proof
            try:
                yield from waiter
            except futures.CancelledError:
                proof += 1
                raise
            else:
                self.fail('got past sleep() in inner()')

        @tasks.coroutine
        def outer():
            nonlocal proof
            try:
                yield from inner()
            except futures.CancelledError:
                proof += 100  # Expect this path.
            else:
                proof += 10

        f = tasks.async(outer(), loop=self.loop)
        test_utils.run_briefly(self.loop)
        f.cancel()
        self.loop.run_until_complete(f)
        self.assertEqual(proof, 101)
        self.assertTrue(waiter.cancelled())

    def test_yield_wait_does_not_shield_cancel(self):
        # Cancelling outer() makes wait() return early, leaves inner()
        # running.
        proof = 0
        waiter = futures.Future(loop=self.loop)

        @tasks.coroutine
        def inner():
            nonlocal proof
            yield from waiter
            proof += 1

        @tasks.coroutine
        def outer():
            nonlocal proof
            d, p = yield from tasks.wait([inner()], loop=self.loop)
            proof += 100

        f = tasks.async(outer(), loop=self.loop)
        test_utils.run_briefly(self.loop)
        f.cancel()
        self.assertRaises(
            futures.CancelledError, self.loop.run_until_complete, f)
        waiter.set_result(None)
        test_utils.run_briefly(self.loop)
        self.assertEqual(proof, 1)

    def test_shield_result(self):
        inner = futures.Future(loop=self.loop)
        outer = tasks.shield(inner)
        inner.set_result(42)
        res = self.loop.run_until_complete(outer)
        self.assertEqual(res, 42)

    def test_shield_exception(self):
        inner = futures.Future(loop=self.loop)
        outer = tasks.shield(inner)
        test_utils.run_briefly(self.loop)
        exc = RuntimeError('expected')
        inner.set_exception(exc)
        test_utils.run_briefly(self.loop)
        self.assertIs(outer.exception(), exc)

    def test_shield_cancel(self):
        inner = futures.Future(loop=self.loop)
        outer = tasks.shield(inner)
        test_utils.run_briefly(self.loop)
        inner.cancel()
        test_utils.run_briefly(self.loop)
        self.assertTrue(outer.cancelled())

    def test_shield_shortcut(self):
        fut = futures.Future(loop=self.loop)
        fut.set_result(42)
        res = self.loop.run_until_complete(tasks.shield(fut))
        self.assertEqual(res, 42)

    def test_shield_effect(self):
        # Cancelling outer() does not affect inner().
        proof = 0
        waiter = futures.Future(loop=self.loop)

        @tasks.coroutine
        def inner():
            nonlocal proof
            yield from waiter
            proof += 1

        @tasks.coroutine
        def outer():
            nonlocal proof
            yield from tasks.shield(inner(), loop=self.loop)
            proof += 100

        f = tasks.async(outer(), loop=self.loop)
        test_utils.run_briefly(self.loop)
        f.cancel()
        with self.assertRaises(futures.CancelledError):
            self.loop.run_until_complete(f)
        waiter.set_result(None)
        test_utils.run_briefly(self.loop)
        self.assertEqual(proof, 1)

    def test_shield_gather(self):
        child1 = futures.Future(loop=self.loop)
        child2 = futures.Future(loop=self.loop)
        parent = tasks.gather(child1, child2, loop=self.loop)
        outer = tasks.shield(parent, loop=self.loop)
        test_utils.run_briefly(self.loop)
        outer.cancel()
        test_utils.run_briefly(self.loop)
        self.assertTrue(outer.cancelled())
        child1.set_result(1)
        child2.set_result(2)
        test_utils.run_briefly(self.loop)
        self.assertEqual(parent.result(), [1, 2])

    def test_gather_shield(self):
        child1 = futures.Future(loop=self.loop)
        child2 = futures.Future(loop=self.loop)
        inner1 = tasks.shield(child1, loop=self.loop)
        inner2 = tasks.shield(child2, loop=self.loop)
        parent = tasks.gather(inner1, inner2, loop=self.loop)
        test_utils.run_briefly(self.loop)
        parent.cancel()
        # This should cancel inner1 and inner2 but bot child1 and child2.
        test_utils.run_briefly(self.loop)
        self.assertIsInstance(parent.exception(), futures.CancelledError)
        self.assertTrue(inner1.cancelled())
        self.assertTrue(inner2.cancelled())
        child1.set_result(1)
        child2.set_result(2)
        test_utils.run_briefly(self.loop)


class GatherTestsBase:

    def setUp(self):
        self.one_loop = test_utils.TestLoop()
        self.other_loop = test_utils.TestLoop()

    def tearDown(self):
        self.one_loop.close()
        self.other_loop.close()

    def _run_loop(self, loop):
        while loop._ready:
            test_utils.run_briefly(loop)

    def _check_success(self, **kwargs):
        a, b, c = [futures.Future(loop=self.one_loop) for i in range(3)]
        fut = tasks.gather(*self.wrap_futures(a, b, c), **kwargs)
        cb = Mock()
        fut.add_done_callback(cb)
        b.set_result(1)
        a.set_result(2)
        self._run_loop(self.one_loop)
        self.assertEqual(cb.called, False)
        self.assertFalse(fut.done())
        c.set_result(3)
        self._run_loop(self.one_loop)
        cb.assert_called_once_with(fut)
        self.assertEqual(fut.result(), [2, 1, 3])

    def test_success(self):
        self._check_success()
        self._check_success(return_exceptions=False)

    def test_result_exception_success(self):
        self._check_success(return_exceptions=True)

    def test_one_exception(self):
        a, b, c, d, e = [futures.Future(loop=self.one_loop) for i in range(5)]
        fut = tasks.gather(*self.wrap_futures(a, b, c, d, e))
        cb = Mock()
        fut.add_done_callback(cb)
        exc = ZeroDivisionError()
        a.set_result(1)
        b.set_exception(exc)
        self._run_loop(self.one_loop)
        self.assertTrue(fut.done())
        cb.assert_called_once_with(fut)
        self.assertIs(fut.exception(), exc)
        # Does nothing
        c.set_result(3)
        d.cancel()
        e.set_exception(RuntimeError())

    def test_return_exceptions(self):
        a, b, c, d = [futures.Future(loop=self.one_loop) for i in range(4)]
        fut = tasks.gather(*self.wrap_futures(a, b, c, d),
                           return_exceptions=True)
        cb = Mock()
        fut.add_done_callback(cb)
        exc = ZeroDivisionError()
        exc2 = RuntimeError()
        b.set_result(1)
        c.set_exception(exc)
        a.set_result(3)
        self._run_loop(self.one_loop)
        self.assertFalse(fut.done())
        d.set_exception(exc2)
        self._run_loop(self.one_loop)
        self.assertTrue(fut.done())
        cb.assert_called_once_with(fut)
        self.assertEqual(fut.result(), [3, 1, exc, exc2])


class FutureGatherTests(GatherTestsBase, unittest.TestCase):

    def wrap_futures(self, *futures):
        return futures

    def _check_empty_sequence(self, seq_or_iter):
        events.set_event_loop(self.one_loop)
        self.addCleanup(events.set_event_loop, None)
        fut = tasks.gather(*seq_or_iter)
        self.assertIsInstance(fut, futures.Future)
        self.assertIs(fut._loop, self.one_loop)
        self._run_loop(self.one_loop)
        self.assertTrue(fut.done())
        self.assertEqual(fut.result(), [])
        fut = tasks.gather(*seq_or_iter, loop=self.other_loop)
        self.assertIs(fut._loop, self.other_loop)

    def test_constructor_empty_sequence(self):
        self._check_empty_sequence([])
        self._check_empty_sequence(())
        self._check_empty_sequence(set())
        self._check_empty_sequence(iter(""))

    def test_constructor_heterogenous_futures(self):
        fut1 = futures.Future(loop=self.one_loop)
        fut2 = futures.Future(loop=self.other_loop)
        with self.assertRaises(ValueError):
            tasks.gather(fut1, fut2)
        with self.assertRaises(ValueError):
            tasks.gather(fut1, loop=self.other_loop)

    def test_constructor_homogenous_futures(self):
        children = [futures.Future(loop=self.other_loop) for i in range(3)]
        fut = tasks.gather(*children)
        self.assertIs(fut._loop, self.other_loop)
        self._run_loop(self.other_loop)
        self.assertFalse(fut.done())
        fut = tasks.gather(*children, loop=self.other_loop)
        self.assertIs(fut._loop, self.other_loop)
        self._run_loop(self.other_loop)
        self.assertFalse(fut.done())

    def test_one_cancellation(self):
        a, b, c, d, e = [futures.Future(loop=self.one_loop) for i in range(5)]
        fut = tasks.gather(a, b, c, d, e)
        cb = Mock()
        fut.add_done_callback(cb)
        a.set_result(1)
        b.cancel()
        self._run_loop(self.one_loop)
        self.assertTrue(fut.done())
        cb.assert_called_once_with(fut)
        self.assertFalse(fut.cancelled())
        self.assertIsInstance(fut.exception(), futures.CancelledError)
        # Does nothing
        c.set_result(3)
        d.cancel()
        e.set_exception(RuntimeError())

    def test_result_exception_one_cancellation(self):
        a, b, c, d, e, f = [futures.Future(loop=self.one_loop)
                            for i in range(6)]
        fut = tasks.gather(a, b, c, d, e, f, return_exceptions=True)
        cb = Mock()
        fut.add_done_callback(cb)
        a.set_result(1)
        zde = ZeroDivisionError()
        b.set_exception(zde)
        c.cancel()
        self._run_loop(self.one_loop)
        self.assertFalse(fut.done())
        d.set_result(3)
        e.cancel()
        rte = RuntimeError()
        f.set_exception(rte)
        res = self.one_loop.run_until_complete(fut)
        self.assertIsInstance(res[2], futures.CancelledError)
        self.assertIsInstance(res[4], futures.CancelledError)
        res[2] = res[4] = None
        self.assertEqual(res, [1, zde, None, 3, None, rte])
        cb.assert_called_once_with(fut)


class CoroutineGatherTests(GatherTestsBase, unittest.TestCase):

    def setUp(self):
        super().setUp()
        events.set_event_loop(self.one_loop)

    def tearDown(self):
        events.set_event_loop(None)
        super().tearDown()

    def wrap_futures(self, *futures):
        coros = []
        for fut in futures:
            @tasks.coroutine
            def coro(fut=fut):
                return (yield from fut)
            coros.append(coro())
        return coros

    def test_constructor_loop_selection(self):
        @tasks.coroutine
        def coro():
            return 'abc'
        gen1 = coro()
        gen2 = coro()
        fut = tasks.gather(gen1, gen2)
        self.assertIs(fut._loop, self.one_loop)
        gen1.close()
        gen2.close()
        gen3 = coro()
        gen4 = coro()
        fut = tasks.gather(gen3, gen4, loop=self.other_loop)
        self.assertIs(fut._loop, self.other_loop)
        gen3.close()
        gen4.close()

    def test_cancellation_broadcast(self):
        # Cancelling outer() cancels all children.
        proof = 0
        waiter = futures.Future(loop=self.one_loop)

        @tasks.coroutine
        def inner():
            nonlocal proof
            yield from waiter
            proof += 1

        child1 = tasks.async(inner(), loop=self.one_loop)
        child2 = tasks.async(inner(), loop=self.one_loop)
        gatherer = None

        @tasks.coroutine
        def outer():
            nonlocal proof, gatherer
            gatherer = tasks.gather(child1, child2, loop=self.one_loop)
            yield from gatherer
            proof += 100

        f = tasks.async(outer(), loop=self.one_loop)
        test_utils.run_briefly(self.one_loop)
        self.assertTrue(f.cancel())
        with self.assertRaises(futures.CancelledError):
            self.one_loop.run_until_complete(f)
        self.assertFalse(gatherer.cancel())
        self.assertTrue(waiter.cancelled())
        self.assertTrue(child1.cancelled())
        self.assertTrue(child2.cancelled())
        test_utils.run_briefly(self.one_loop)
        self.assertEqual(proof, 0)

    def test_exception_marking(self):
        # Test for the first line marked "Mark exception retrieved."

        @tasks.coroutine
        def inner(f):
            yield from f
            raise RuntimeError('should not be ignored')

        a = futures.Future(loop=self.one_loop)
        b = futures.Future(loop=self.one_loop)

        @tasks.coroutine
        def outer():
            yield from tasks.gather(inner(a), inner(b), loop=self.one_loop)

        f = tasks.async(outer(), loop=self.one_loop)
        test_utils.run_briefly(self.one_loop)
        a.set_result(None)
        test_utils.run_briefly(self.one_loop)
        b.set_result(None)
        test_utils.run_briefly(self.one_loop)
        self.assertIsInstance(f.exception(), RuntimeError)


if __name__ == '__main__':
    unittest.main()
