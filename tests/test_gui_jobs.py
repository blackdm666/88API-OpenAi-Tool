import threading
import unittest
from unittest.mock import Mock, patch

from token_manager.gui_common import GUICommonMixin


class BackgroundJobTest(unittest.TestCase):
    def make_app(self, running=False, maintenance=False):
        app = GUICommonMixin()
        app._running_job_lock = threading.Lock()
        app.running_job = running
        app.auto_refresh_running = maintenance
        app.status_var = Mock()
        app.root = Mock()
        app.log = Mock()
        return app

    def test_busy_clicks_do_not_open_modal_or_hold_lock_during_ui_calls(self):
        for running, maintenance in [(True, False), (False, True)]:
            with self.subTest(running=running, maintenance=maintenance):
                app = self.make_app(running, maintenance)
                def check_unlocked(*args):
                    acquired = app._running_job_lock.acquire(timeout=0.1)
                    self.assertTrue(acquired, 'UI callback called with job lock held')
                    if acquired:
                        app._running_job_lock.release()
                    self.assertEqual(app.is_running(), running)
                app.log.side_effect = check_unlocked
                worker, done = Mock(), Mock()
                with patch('tkinter.messagebox.showinfo') as modal, patch('token_manager.gui_common.threading.Thread') as thread:
                    for _ in range(3):
                        app.run_background('正在刷新账号', worker, done)
                    modal.assert_not_called()
                    thread.assert_not_called()
                worker.assert_not_called()
                done.assert_not_called()
                app.status_var.set.assert_not_called()
                self.assertEqual(app.log.call_count, 3)
                self.assertEqual(app.auto_refresh_running, maintenance)

    def test_job_completes_after_duplicate_click_is_rejected(self):
        app = self.make_app()
        result = {'success_count': 1}
        worker = Mock(return_value=result)
        def done(value):
            self.assertIs(value, result)
            app.set_running(False, '刷新完成')
        with patch('token_manager.gui_common.threading.Thread') as thread:
            app.run_background('正在刷新账号', worker, done)
            self.assertTrue(app.is_running())
            runner = thread.call_args.kwargs['target']
            app.run_background('正在刷新账号', Mock(), Mock())
            self.assertEqual(thread.call_count, 1)
            runner()
        callback = app.root.after.call_args.args[1]
        callback()
        worker.assert_called_once()
        self.assertFalse(app.is_running())
        app.status_var.set.assert_called_with('刷新完成')


if __name__ == '__main__':
    unittest.main()
