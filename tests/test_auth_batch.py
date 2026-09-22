import tempfile
import base64
import json
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from token_manager.auth_batch import (
    authorization_result_view,
    plan_authorization,
    remove_account_lines,
    run_checked_authorization,
)
from token_manager.auth_proxy import authorization_proxy, authorization_settings
from token_manager.config import default_config
from token_manager.credential_vault import CredentialVault
from token_manager.gui_auth import GUIAuthMixin, saved_credential_lines
from token_manager.store import TokenStore
from tools.auth_2fa_live import parse_account_lines


def account(email='a@example.test'):
    return parse_account_lines(f'{email}----private-password----JBSWY3DPEHPK3PXP')[0][0]


def remote(email='a@example.test', **kw):
    return dict(id=42, email=email, platform='openai', type='oauth', **kw)


def token_with_workspace(workspace):
    payload = base64.urlsafe_b64encode(json.dumps({
        'https://api.openai.com/auth': {'chatgpt_account_id': workspace}
    }).encode()).decode().rstrip('=')
    return f'header.{payload}.signature'


class AuthPreflightTest(unittest.TestCase):
    def test_result_view_exposes_reasons_without_tokens_or_proxy_credentials(self):
        result = authorization_result_view({
            'success_count': 1,
            'fail_count': 1,
            'skipped_count': 1,
            'input_error_count': 1,
            'summary_path': 'batch.json',
            'results': [
                {
                    'ok': True,
                    'email': 'ok@example.test',
                    'message': 'ok',
                    'token_data': {'access_token': 'secret-access-token'},
                },
                {
                    'ok': False,
                    'email': 'failed@example.test',
                    'message': (
                        'proxy socks5://proxy-user:proxy-pass@proxy.example:1080 '
                        'access_token=eyJsecret'
                    ),
                    'token_data': {'refresh_token': 'secret-refresh-token'},
                    'report_path': 'failed.json',
                },
            ],
            'skipped': [{
                'email': 'skipped@example.test',
                'reason': 'Sub2API 授权状态正常，无需重复登录',
            }],
            'input_errors': ['第 4 行 输入格式错误'],
        }, '测试结果')
        self.assertEqual(
            [row['status'] for row in result['rows']],
            ['成功', '失败', '跳过', '输入错误'],
        )
        self.assertIn('授权状态正常', result['rows'][2]['message'])
        self.assertIn('proxy', result['rows'][1]['message'])
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('token_data', serialized)
        self.assertNotIn('secret-access-token', serialized)
        self.assertNotIn('secret-refresh-token', serialized)
        self.assertNotIn('proxy-user', serialized)
        self.assertNotIn('proxy-pass', serialized)

    def test_authorization_proxy_is_independent_from_sub2api_proxy_pool(self):
        cfg = default_config()
        cfg['http_proxy'] = 'http://software-interface.test:8080'
        cfg['auth_proxy'] = 'socks5://authorization.test:1080'
        cfg['integrations']['sub2api']['proxy_id'] = '7,8,9'
        prepared = authorization_settings(cfg)
        self.assertEqual(authorization_proxy(cfg), 'socks5://authorization.test:1080')
        self.assertEqual(prepared['http_proxy'], 'socks5://authorization.test:1080')
        self.assertEqual(prepared['integrations']['sub2api']['proxy_id'], '7,8,9')

    def test_saved_credentials_can_rebuild_authorization_input_without_editor(self):
        raw = saved_credential_lines({'a@example.test': {
            'email': 'a@example.test', 'password': 'private-password', 'totp_secret': 'JBSWY3DPEHPK3PXP'
        }})
        self.assertEqual(raw, 'a@example.test----private-password----JBSWY3DPEHPK3PXP')

    def test_active_including_cooldown_and_disabled_scheduling_skips_login(self):
        for fields in ({'schedulable': True}, {'schedulable': False}, {'rate_limit_reset_at': '2099-01-01T00:00:00Z'}):
            eligible, skipped = plan_authorization([account()], [{'email': 'a@example.test'}], [remote(status='active', **fields)])
            self.assertFalse(eligible)
            self.assertIn('正常', skipped[0]['reason'])

    def test_authorization_failures_and_new_accounts_are_eligible(self):
        local = [{'email': 'a@example.test'}]
        for remotes in ([], [remote(status='error', error_message='Token revoked (401)')],
                        [remote(status='error', error_message='HTTP 401')]):
            eligible, skipped = plan_authorization([account()], local, remotes)
            self.assertEqual(len(eligible), 1)
            self.assertFalse(skipped)

    def test_deleted_local_account_is_never_authorized_from_saved_material(self):
        eligible, skipped = plan_authorization(
            [account()], [], [remote(status='error', error_message='Token revoked (401)')]
        )
        self.assertFalse(eligible)
        self.assertIn('废弃', skipped[0]['reason'])

    def test_other_failures_and_inactive_accounts_do_not_trigger_login(self):
        for row in (remote(status='error', error_message='429'), remote(status='inactive'), remote(status='unknown')):
            eligible, skipped = plan_authorization([account()], [], [row])
            self.assertFalse(eligible)
            self.assertEqual(len(skipped), 1)

    def test_ambiguous_identity_skips(self):
        eligible, skipped = plan_authorization([account()], [{'email': 'a@example.test'}], [remote(status='active'), remote(status='error')])
        self.assertFalse(eligible)
        self.assertIn('多个', skipped[0]['reason'])

    def test_saved_pending_token_is_not_reauthorized(self):
        local = {'email':'a@example.test', 'access_token':'new-at', 'refresh_token':'new-rt',
                 'expired':'2099-01-01T00:00:00Z', 'sub2api_recovery':{'pending_upload':True}}
        eligible, skipped = plan_authorization([account()], [local], [remote(status='error', error_message='401')])
        self.assertFalse(eligible)
        self.assertIn('已有新凭据', skipped[0]['reason'])

    def test_recreated_remote_same_identity_is_checked_against_new_id(self):
        local = {
            'email': 'a@example.test',
            'account_id': 'workspace',
            'sub2api_recovery': {'remote_id': 41},
        }
        row = remote(status='active', credentials={
            'email': 'a@example.test',
            'chatgpt_account_id': 'workspace',
        })
        eligible, skipped = plan_authorization([account()], [local], [row])
        self.assertFalse(eligible)
        self.assertIn('正常', skipped[0]['reason'])

    def test_transient_auth_session_id_does_not_override_jwt_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = default_config()
            cfg.update(
                tokens_dir=str(Path(folder) / 'tokens'),
                outputs_dir=str(Path(folder) / 'outputs'),
            )
            local = TokenStore(cfg).normalize({
                'email': 'a@example.test',
                'account_id': 'authsess_old_login_session',
                'access_token': token_with_workspace('workspace'),
            })
        row = remote(
            status='active',
            credentials={
                'email': 'a@example.test',
                'chatgpt_account_id': 'workspace',
            },
        )
        eligible, skipped = plan_authorization([account()], [local], [row])
        self.assertEqual(local['account_id'], 'workspace')
        self.assertFalse(eligible)
        self.assertIn('正常', skipped[0]['reason'])

    def test_remote_transient_auth_session_id_is_not_a_workspace_mismatch(self):
        local = {'email': 'a@example.test', 'account_id': 'workspace'}
        row = remote(
            status='active',
            credentials={
                'email': 'a@example.test',
                'chatgpt_account_id': 'authsess_remote_login_session',
            },
        )
        eligible, skipped = plan_authorization([account()], [local], [row])
        self.assertFalse(eligible)
        self.assertIn('正常', skipped[0]['reason'])

    def test_checked_batch_only_passes_unhealthy_accounts_to_runner(self):
        cfg=default_config();cfg['integrations']['sub2api']['api_url']='https://sub.test'
        raw='\n'.join(account(email).raw_line for email in ('a@example.test','b@example.test'))
        runner, log=Mock(return_value={'success_count':1}), Mock()
        with patch('token_manager.auth_batch.fetch_sub2api_accounts',return_value=[remote(status='active'),remote('b@example.test',status='error',error_message='401')]):
            result=run_checked_authorization(raw,cfg,[{'email':'a@example.test'},{'email':'b@example.test'}],runner,{},log_fn=log)
        self.assertEqual(result['skipped_count'],1)
        self.assertNotIn('a@example.test',runner.call_args.args[0])
        self.assertIn('b@example.test',runner.call_args.args[0])
        self.assertNotIn('private-password',str(log.call_args_list))
        runner.reset_mock()
        with patch('token_manager.auth_batch.fetch_sub2api_accounts',side_effect=RuntimeError('network down')):
            with self.assertRaises(RuntimeError):run_checked_authorization(raw,cfg,[],runner,{})
        runner.assert_not_called()

    def test_checked_batch_uses_dedicated_runner_settings(self):
        cfg=default_config();cfg['integrations']['sub2api']['api_url']='https://sub.test'
        cfg['auth_proxy']='socks5://authorization.test:1080'
        runner=Mock(return_value={'success_count':1})
        with patch('token_manager.auth_batch.fetch_sub2api_accounts',return_value=[
            remote(status='error',error_message='401')
        ]):
            run_checked_authorization(
                account().raw_line,
                cfg,
                [{'email':'a@example.test'}],
                runner,
                {},
                runner_settings=authorization_settings(cfg),
            )
        self.assertEqual(runner.call_args.args[1]['http_proxy'],'socks5://authorization.test:1080')

    def test_all_healthy_never_starts_runner(self):
        cfg=default_config();cfg['integrations']['sub2api']['api_url']='https://sub.test'
        runner=Mock()
        with patch('token_manager.auth_batch.fetch_sub2api_accounts',return_value=[remote(status='active')]):
            result=run_checked_authorization(account().raw_line,cfg,[],runner,{})
        runner.assert_not_called()
        self.assertEqual(result['skipped_count'],1)
        self.assertEqual(result['success_count'],0)


class VaultDeleteTest(unittest.TestCase):
    def test_2fa_placeholder_is_visible_and_is_cleared_by_oauth_token(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = default_config()
            cfg.update(
                tokens_dir=str(Path(folder) / 'tokens'),
                outputs_dir=str(Path(folder) / 'outputs'),
            )
            store = TokenStore(cfg)
            path = store.save_record({
                'email': 'pending@example.test',
                'metadata': {
                    'auth2fa_pending': True,
                    'auth2fa_added_at': '2026-09-22T00:00:00Z',
                },
            })
            loaded = store.load_all()
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0]['metadata']['auth2fa_pending'])
            store.save_token_response({
                'email': 'pending@example.test',
                'access_token': 'access',
                'refresh_token': 'refresh',
                'id_token': 'id',
            }, existing_filename=path)
            converted = store.load_all()[0]
            self.assertTrue(converted['access_token'])
            self.assertFalse((converted.get('metadata') or {}).get('auth2fa_pending'))

    def test_selected_deletion_keeps_other_credentials_encrypted(self):
        with tempfile.TemporaryDirectory() as folder:
            vault=CredentialVault(Path(folder)/'accounts.dpapi')
            vault.save_accounts([account(),account('b@example.test')])
            self.assertEqual(vault.delete_accounts(['A@example.test']),{'a@example.test'})
            self.assertEqual(set(vault.load()),{'b@example.test'})
            self.assertNotIn(b'private-password',vault.path.read_bytes())
            vault.delete_accounts(['b@example.test'])
            self.assertEqual(vault.load(),{})

    def test_delete_also_clears_editor_so_close_does_not_resave(self):
        with tempfile.TemporaryDirectory() as folder:
            vault=CredentialVault(Path(folder)/'accounts.dpapi')
            vault.save_accounts([account(),account('b@example.test')])
            root=tk.Tk();root.withdraw()
            try:
                app=GUIAuthMixin();app.root=root
                app.is_running=Mock(return_value=False);app.auto_refresh_running=False
                app.auth2fa_input=tk.Text(root)
                app.auth2fa_input.insert('1.0',account().raw_line+'\n'+account('b@example.test').raw_line)
                app.auth2fa_stats_var=tk.StringVar(master=root)
                app.auth2fa_vault_var=tk.StringVar(master=root)
                app.log=Mock()
                with patch('token_manager.gui_auth.CredentialVault',return_value=vault):
                    app.delete_saved_auth2fa_accounts(['a@example.test'])
                    self.assertTrue(app.save_auth2fa_credentials(silent=True))
                self.assertEqual(set(vault.load()),{'b@example.test'})
                self.assertNotIn('a@example.test',app.auth2fa_input.get('1.0','end'))
                app.auto_refresh_running=True
                with self.assertRaises(ValueError):app.delete_saved_auth2fa_accounts(['b@example.test'])
            finally:root.destroy()

    def test_remove_lines_preserves_unsaved_unrelated_input(self):
        text=account().raw_line+'\n# note\nb@example.test--unfinished\nnew@example.test----unsaved'
        result=remove_account_lines(text,['A@example.test'])
        self.assertNotIn('a@example.test',result)
        self.assertIn('new@example.test----unsaved',result)
