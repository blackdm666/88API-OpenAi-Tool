import threading
import unittest
from unittest.mock import Mock, patch

from token_manager.gui_common import GUICommonMixin
from token_manager.gui_records import GUIRecordsMixin


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

    def test_sub2api_upload_refreshes_remote_snapshot_after_success(self):
        app = GUIRecordsMixin()
        app.selected_records = Mock(return_value=[{"email": "a@example.test"}])
        app.save_settings = Mock()
        app.upload_target_var = Mock()
        app.upload_target_var.get.return_value = "Sub2API"
        app.config = {}
        app._is_pending_auth2fa = Mock(return_value=False)
        app.with_progress = Mock(return_value=None)
        app.set_running = Mock()
        app.reload_tokens = Mock()
        app.refresh_sub2api_accounts = Mock()
        app.log = Mock()

        def capture(_status, _worker, done):
            app.done = done

        app.run_background = capture
        with patch("token_manager.gui_records.messagebox.showinfo"):
            app.upload_selected()
            app.done({"success_count": 1, "fail_count": 0})

        app.reload_tokens.assert_called_once_with(save_first=False)
        app.refresh_sub2api_accounts.assert_called_once_with()

    def test_sub2api_upload_failure_keeps_local_refresh_without_remote_refresh(self):
        app = GUIRecordsMixin()
        app.selected_records = Mock(return_value=[{"email": "a@example.test"}])
        app.save_settings = Mock()
        app.upload_target_var = Mock()
        app.upload_target_var.get.return_value = "sub2api"
        app.config = {}
        app._is_pending_auth2fa = Mock(return_value=False)
        app.with_progress = Mock(return_value=None)
        app.set_running = Mock()
        app.reload_tokens = Mock()
        app.refresh_sub2api_accounts = Mock()
        app.log = Mock()

        def capture(_status, _worker, done):
            app.done = done

        app.run_background = capture
        with patch("token_manager.gui_records.messagebox.showerror"):
            app.upload_selected()
            app.done({"error": "upload failed", "success_count": 0, "fail_count": 1})

        app.reload_tokens.assert_called_once_with(save_first=False)
        app.refresh_sub2api_accounts.assert_not_called()

    def test_local_status_is_only_normal_or_invalid(self):
        app = GUIRecordsMixin()
        valid = {
            "access_token": "access",
            "refresh_token": "refresh",
            "_is_expired": False,
        }
        self.assertEqual(app.account_status(valid), "正常")
        self.assertEqual(
            app.account_status({**valid, "_is_expired": True}),
            "失效",
        )
        self.assertEqual(
            app.account_status(
                {
                    "metadata": {"auth2fa_pending": True},
                    "_is_expired": True,
                }
            ),
            "失效",
        )
        self.assertEqual(
            app.account_status(
                valid,
                {
                    "status": "active",
                    "schedulable": False,
                },
            ),
            "正常",
        )
        self.assertEqual(
            app.account_status(
                valid,
                {
                    "status": "error",
                    "error_message": "HTTP 401 unauthorized",
                },
            ),
            "失效",
        )
        self.assertEqual(
            app.account_status(
                valid,
                {
                    "status": "active",
                    "credentials_status": {"has_access_token": False},
                },
            ),
            "失效",
        )

    def test_delete_selected_cascades_local_2fa_and_unique_remote_account(self):
        app = GUIRecordsMixin()
        record = {
            "email": "a@example.test",
            "account_id": "workspace-a",
            "_filename": "a.json",
        }
        remote = {"id": 42, "email": "a@example.test"}
        app.auto_refresh_running = False
        app.is_running = Mock(return_value=False)
        app.selected_records = Mock(return_value=[record])
        app.current_settings = Mock(
            return_value={
                "http_proxy": "",
                "integrations": {
                    "sub2api": {
                        "api_url": "https://sub.example.test",
                        "api_key": "admin-test",
                    }
                },
            }
        )
        app.store = Mock()
        app.log = Mock()
        app.reload_tokens = Mock()
        app.refresh_sub2api_accounts = Mock()
        app.set_running = Mock()
        app.run_background = lambda _status, worker, done: setattr(app, "_delete_job", (worker, done))

        with (
            patch("token_manager.gui_records.messagebox.askyesno", return_value=True),
            patch("token_manager.gui_records.messagebox.showinfo"),
            patch(
                "token_manager.gui_records.fetch_sub2api_accounts",
                return_value=[remote],
            ),
            patch(
                "token_manager.gui_records.match_remote",
                return_value=remote,
            ),
            patch(
                "token_manager.services.delete_sub2api_remote_records",
                return_value={
                    "success_count": 1,
                    "fail_count": 0,
                    "results": [(remote, True, "删除成功")],
                },
            ) as delete_remote,
            patch("token_manager.gui_records.CredentialVault") as vault,
        ):
            vault.return_value.delete_accounts.return_value = {"a@example.test"}
            app.delete_selected()
            worker, done = app._delete_job
            result = worker()
            done(result)

        delete_remote.assert_called_once()
        vault.return_value.delete_accounts.assert_called_once_with(["a@example.test"])
        app.store.delete.assert_called_once_with("a.json")
        app.refresh_sub2api_accounts.assert_called_once_with()
        logged = "\n".join(str(call.args[0]) for call in app.log.call_args_list)
        self.assertIn("删除完成", logged)

    def test_remove_selected_keep_remote_only_clears_local_and_2fa(self):
        app = GUIRecordsMixin()
        record = {
            "email": "a@example.test",
            "_filename": "a.json",
        }
        app.auto_refresh_running = False
        app.is_running = Mock(return_value=False)
        app.selected_records = Mock(return_value=[record])
        app.store = Mock()
        app.log = Mock()
        app.reload_tokens = Mock()
        app.set_running = Mock()
        app.run_background = lambda _status, worker, done: setattr(
            app, "_remove_job", (worker, done)
        )

        with (
            patch("token_manager.gui_records.messagebox.askyesno", return_value=True),
            patch("token_manager.gui_records.messagebox.showinfo"),
            patch("token_manager.gui_records.CredentialVault") as vault,
        ):
            vault.return_value.delete_accounts.return_value = {"a@example.test"}
            app.remove_selected_keep_remote()
            worker, done = app._remove_job
            result = worker()
            done(result)

        vault.return_value.delete_accounts.assert_called_once_with(["a@example.test"])
        app.store.delete.assert_called_once_with("a.json")
        app.reload_tokens.assert_called_once_with(save_first=False)
        logged = "\n".join(str(call.args[0]) for call in app.log.call_args_list)
        self.assertIn("保留Sub2API远端", logged)

    def test_export_preview_file_removes_proxy_fields(self):
        app = GUIRecordsMixin()
        app.primary_record = Mock(
            return_value={
                "email": "a@example.test",
                "access_token": "access",
                "refresh_token": "refresh",
            }
        )
        app._is_pending_auth2fa = Mock(return_value=False)
        app.save_settings = Mock()
        app.current_settings = Mock(return_value={"integrations": {"sub2api": {}}})
        app.root = Mock()
        app.status_var = Mock()
        app.log = Mock()

        with (
            patch(
                "token_manager.gui_records.sub2api_upload_payload",
                return_value={
                    "name": "a@example.test",
                    "credentials": {"access_token": "access"},
                    "proxy_id": 8,
                    "proxy": {"url": "socks5://secret.example.test:1080"},
                },
            ),
            patch(
                "token_manager.gui_records.filedialog.asksaveasfilename",
                return_value="export.json",
            ),
            patch("token_manager.gui_records.atomic_write_json") as write_json,
        ):
            app.export_preview_file()

        self.assertEqual(
            write_json.call_args.args[1],
            {
                "name": "a@example.test",
                "credentials": {"access_token": "access"},
            },
        )
        app.status_var.set.assert_called_once_with("Sub2API 文件已导出（不含代理）")


if __name__ == '__main__':
    unittest.main()
