from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from token_manager.config import default_config
from token_manager.credential_vault import CredentialVault
from token_manager.maintenance import recovery_cycle
from token_manager.recovery_support import (authorize_saved_account, NeedsUser,
    remote_health, test_sub2api_account as run_probe)
from token_manager.store import TokenStore
from token_manager.integrations import get_sub2api_account_credentials
from tools.auth_2fa_live import AuthAccount


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = default_config()
        self.settings.update(tokens_dir=self.temp.name+'/tokens', outputs_dir=self.temp.name+'/outputs')
        self.settings['integrations']['sub2api']['api_url'] = 'https://sub.test'
        self.store = TokenStore(self.settings)
        self.local = dict(email='a@example.test', account_id='workspace', access_token='old',
                          refresh_token='old-rt', expired='2099-01-01T00:00:00Z', sub2api_recovery={'enabled':True})
        self.store.save_record(self.local)
        self.remote = dict(id=42, email='a@example.test', platform='openai', type='oauth', status='error',
                           schedulable=True, error_message='Token revoked (401)', group_ids=[24], proxy_id=8,
                           credentials={'email':'a@example.test', 'chatgpt_account_id':'workspace',
                                        'access_token':'old', 'refresh_token':'old-rt'})
        self.saved = dict(email='a@example.test', password='secret-pass', totp_secret='JBSWY3DPEHPK3PXP')
        self.vault = self.enterContext(patch('token_manager.credential_vault.CredentialVault'))
        self.vault.return_value.load.return_value = {'a@example.test': self.saved}
        self.fetch = self.enterContext(patch('token_manager.maintenance.fetch_sub2api_accounts', side_effect=lambda *a,**kw:[deepcopy(self.remote)]))
        self.new = {**self.local, 'access_token':'new', 'refresh_token':'new-rt'}
        self.auth = self.enterContext(patch('token_manager.recovery_support.authorize_saved_account', return_value=self.new))
        self.probe = self.enterContext(patch('token_manager.recovery_support.test_sub2api_account', return_value={'ok':True,'model':'gpt-5.5','checked_at':1234}))
        self.detail = self.enterContext(patch('token_manager.integrations.get_sub2api_account', side_effect=lambda *a,**kw:deepcopy(self.remote)))
        self.schedule = self.enterContext(patch('token_manager.integrations.set_sub2api_schedulable', side_effect=lambda *a,**kw:{**self.remote, 'status':'active', 'schedulable':True, 'credentials':{'access_token':'new','refresh_token':'new-rt'}}))
        def apply(local, remote, *a, **kw):
            self.remote.update(status='active', error_message='')
            self.remote['credentials'].update(access_token=local['access_token'],refresh_token=local['refresh_token'])
            return deepcopy(self.remote)
        self.apply = self.enterContext(patch('token_manager.maintenance.apply_sub2api_credentials', side_effect=apply))

    def test_full_workflow_saves_new_token_checks_remote_and_tests_once(self):
        result = recovery_cycle(self.store, self.settings)
        self.assertEqual(result['recovered'],1)
        record = self.store.load_all()[0]
        self.assertEqual(record['access_token'],'new')
        self.assertTrue(record['uploads']['sub2api']['ok'])
        self.assertTrue(record['sub2api_recovery']['probe_ok'])
        self.assertEqual(result['records'][0]['status'],'active')
        recovery_cycle(self.store, self.settings)
        self.auth.assert_called_once()
        self.apply.assert_called_once()
        self.probe.assert_called_once()

    def test_reauthorization_uses_dedicated_auth_proxy_not_remote_proxy_pool(self):
        self.settings['http_proxy'] = 'http://software-interface.test:8080'
        self.settings['auth_proxy'] = 'socks5://authorization.test:1080'
        self.settings['integrations']['sub2api']['proxy_id'] = '7,8,9'
        recovery_cycle(self.store, self.settings)
        auth_settings = self.auth.call_args.args[3]
        self.assertEqual(auth_settings['http_proxy'], 'socks5://authorization.test:1080')
        self.assertEqual(auth_settings['integrations']['sub2api']['proxy_id'], '7,8,9')

    def test_uploaded_account_is_auto_enrolled_without_monitor_toggle(self):
        local = self.store.load_all()[0]
        local['sub2api_recovery'] = {}
        local['uploads'] = {}
        self.store.save_record(local, filename=local.get('_filename'))
        result = recovery_cycle(self.store, self.settings)
        self.assertEqual(result['recovered'], 1)
        saved = self.store.load_all()[0]
        self.assertTrue(saved['sub2api_recovery']['enabled'])
        self.assertTrue(saved['sub2api_recovery']['auto_enrolled'])
        self.auth.assert_called_once()

    def test_recreated_remote_row_is_rebound_before_recovery(self):
        local = self.store.load_all()[0]
        local['sub2api_recovery']['remote_id'] = 41
        self.store.save_record(local, filename=local.get('_filename'))
        result = recovery_cycle(self.store, self.settings)
        self.assertEqual(result['recovered'], 1)
        saved = self.store.load_all()[0]
        self.assertEqual(saved['sub2api_recovery']['remote_id'], 42)
        self.auth.assert_called_once()

    def test_redacted_list_fetches_only_target_credentials_before_recovery(self):
        full = deepcopy(self.remote)
        redacted = deepcopy(self.remote)
        redacted['credentials'].pop('access_token')
        redacted['credentials'].pop('refresh_token')
        self.fetch.side_effect = lambda *a,**kw:[redacted]
        with patch('token_manager.integrations.get_sub2api_account_credentials',return_value=full) as read:
            result = recovery_cycle(self.store,self.settings)
        self.assertEqual(read.call_args.args[1],42)
        self.assertEqual(result['recovered'],1)

    def test_network_failure_retries_upload_without_reauthorizing(self):
        original = self.apply.side_effect
        self.apply.side_effect = RuntimeError('network down')
        recovery_cycle(self.store, self.settings)
        record = self.store.load_all()[0]
        self.assertEqual(record['access_token'],'new')
        self.assertTrue(record['sub2api_recovery']['pending_upload'])
        record['sub2api_recovery']['next_attempt_at']=0
        self.store.save_record(record)
        self.apply.side_effect=original
        recovery_cycle(self.store,self.settings)
        self.auth.assert_called_once()
        self.probe.assert_called_once()

    def test_failed_probe_is_not_repeated_by_polling(self):
        self.probe.side_effect=RuntimeError('429 rate limited')
        recovery_cycle(self.store,self.settings)
        self.assertEqual(self.store.load_all()[0]['sub2api_recovery']['status'],'测试未通过')
        recovery_cycle(self.store,self.settings)
        self.probe.assert_called_once()
        self.auth.assert_called_once()

    def test_disabled_account_and_429_never_start_login(self):
        for status,error in [('inactive','Token revoked (401)'),('error','429')]:
            self.remote.update(status=status,error_message=error)
            recovery_cycle(self.store,self.settings)
        self.auth.assert_not_called()
        self.apply.assert_not_called()

    def test_cooldown_waits_before_probe(self):
        self.remote['schedulable']=False
        recovery_cycle(self.store,self.settings)
        self.probe.assert_called_once()
        self.schedule.assert_called_once()

    def test_missing_material_resumes_after_vault_save(self):
        self.vault.return_value.load.return_value={}
        recovery_cycle(self.store,self.settings)
        self.auth.assert_not_called()
        self.vault.return_value.load.return_value={'a@example.test':self.saved}
        recovery_cycle(self.store,self.settings)
        self.auth.assert_called_once()

    def test_cancellation_after_login_keeps_new_credential_for_upload(self):
        stopped=False
        def auth(*args,**kwargs):
            nonlocal stopped
            stopped=True
            return self.new
        self.auth.side_effect=auth
        recovery_cycle(self.store,self.settings,cancelled=lambda:stopped)
        self.apply.assert_not_called()
        self.assertTrue(self.store.load_all()[0]['sub2api_recovery']['pending_upload'])
        recovery_cycle(self.store,self.settings)
        self.auth.assert_called_once()
        self.apply.assert_called_once()

    def test_real_authorization_adapter_does_not_save_wrong_workspace(self):
        token={**self.new,'account_id':'different'}
        with patch('tools.auth_2fa_live.authorize_account',return_value={'ok':True,'token_data':token}) as runner:
            with self.assertRaises(NeedsUser):
                authorize_saved_account(self.local,self.remote,self.saved,self.settings)
            self.assertFalse(runner.call_args.kwargs['write_report'])
            self.assertFalse(runner.call_args.kwargs['save_token'])
        self.assertEqual(self.store.load_all()[0]['access_token'],'old')

    def test_challenge_stops_and_does_not_leak_password(self):
        with patch('tools.auth_2fa_live.authorize_account',return_value={'ok':False,'message':'captcha secret-pass'}):
            with self.assertRaises(NeedsUser) as caught:
                authorize_saved_account(self.local,self.remote,self.saved,self.settings)
            self.assertNotIn('secret-pass',str(caught.exception))


class VaultAndProbeTest(unittest.TestCase):
    def test_official_export_is_id_scoped_and_checks_owner(self):
        detail={'id':42,'platform':'openai','type':'oauth','status':'active',
                'credentials':{'email':'a@example.test','chatgpt_account_id':'workspace'}}
        exported={'platform':'openai','type':'oauth','credentials':{
            'email':'a@example.test','chatgpt_account_id':'workspace','access_token':'secret'}}
        response=Mock(status_code=200)
        with patch('token_manager.integrations.get_sub2api_account',return_value=detail), \
             patch('token_manager.integrations._sub2api_request',return_value=response) as request, \
             patch('token_manager.integrations._sub2api_response_data',return_value={'accounts':[exported]}):
            result=get_sub2api_account_credentials({},42)
            self.assertEqual(result['credentials']['access_token'],'secret')
            self.assertEqual(request.call_args.kwargs['params'],{'ids':'42','include_proxies':'false'})
            exported['credentials']['email']='other@example.test'
            with self.assertRaises(ValueError):get_sub2api_account_credentials({},42)

    def test_export_permission_failure_never_falls_back_to_unknown_token(self):
        with patch('token_manager.integrations.get_sub2api_account',return_value={'id':42}), \
             patch('token_manager.integrations._sub2api_request',return_value=Mock(status_code=403)):
            with self.assertRaisesRegex(RuntimeError,'403'):
                get_sub2api_account_credentials({},42)

    def test_encrypted_vault_roundtrip_and_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            vault=CredentialVault(Path(folder)/'accounts.dpapi')
            vault.save_accounts([AuthAccount('a@example.test','secret-pass','JBSWY3DPEHPK3PXP','')])
            ciphertext=vault.path.read_bytes()
            self.assertNotIn(b'secret-pass',ciphertext)
            self.assertNotIn(b'JBSWY3DPEHPK3PXP',ciphertext)
            self.assertEqual(vault.lookup('A@example.test')['password'],'secret-pass')
            vault.save_accounts([AuthAccount('b@example.test','second','JBSWY3DPEHPK3PXP','')])
            self.assertEqual(len(vault.load()),2)
            vault.path.write_bytes(b'broken')
            with self.assertRaises(RuntimeError):vault.load()

    def test_probe_requires_successful_terminal_event(self):
        settings=default_config()
        for lines,success in [([b'data: {"type":"test_complete","success":true}'],True),
                              ([b'data: {"type":"content","text":"hi"}'],False),
                              ([b'data: {"type":"error","error":"401"}'],False)]:
            with self.subTest(lines=lines):
                response=Mock(status_code=200)
                response.iter_lines.return_value=iter(lines)
                with patch('token_manager.recovery_support._sub2api_request',return_value=response) as request:
                    if success:self.assertTrue(run_probe(settings,42)['ok'])
                    else:
                        with self.assertRaises(RuntimeError):run_probe(settings,42)
                    self.assertEqual(request.call_args.kwargs['json']['model_id'],'gpt-5.5')
                response.close.assert_called_once()

    def test_health_distinguishes_active_from_schedulable(self):
        self.assertEqual(remote_health({'status':'active','schedulable':False}),'已启用·停调度')
        self.assertEqual(remote_health({'status':'active','temp_unschedulable_until':'2099-01-01T00:00:00Z'}),'已启用·冷却中')
