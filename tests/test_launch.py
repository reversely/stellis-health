"""Explicit launches isolate profiles, credentials and persisted records."""

import argparse
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from stellis import launch, runtime_profile


def values(profile, tmp_path):
    path = tmp_path / f'{profile}.env'
    path.write_text(launch.EXAMPLES[profile])
    result = launch.read_env(path)
    result['STELLIS_DATA_DIR'] = str(tmp_path / profile)
    if profile == 'cloud':
        result.update(NEBIUS_API_KEY='inference-secret', STELLIS_AGENT_GATEWAY_TOKEN='agent-secret',
                      STELLIS_AGENT_GATEWAY_URL='https://agents.example.test')
    return result


def test_local_discards_inherited_cloud_settings_and_credentials(tmp_path):
    inherited = {'PATH': '/usr/bin', 'STELLIS_AGENT_GATEWAY_URL':'https://wrong.example',
                 'STELLIS_AGENT_GATEWAY_TOKEN':'wrong-secret', 'NEBIUS_API_KEY':'wrong-key',
                 'STELLIS_DATA_DIR':'/existing-account', 'STELLIS_RUNTIME_PROFILE':'direct'}
    with patch('stellis.launch.shutil.which', return_value='/usr/bin/nemoclaw'):
        result = launch.environment('local', values('local', tmp_path), inherited)
    assert result['STELLIS_RUNTIME_PROFILE'] == 'local'
    assert result['STELLIS_DATA_DIR'] == str(tmp_path / 'local')
    assert result['NEMOCLAW_GATEWAY_PORT'] == '8990'
    assert not any(key in result for key in ('NEBIUS_API_KEY','STELLIS_AGENT_GATEWAY_TOKEN','STELLIS_AGENT_GATEWAY_URL'))


def test_cloud_uses_gateway_and_injected_secrets_without_local_fallback(tmp_path):
    configured = values('cloud', tmp_path)
    configured['NEBIUS_API_KEY'] = ''
    configured['STELLIS_AGENT_GATEWAY_TOKEN'] = ''
    inherited = {'PATH':'/usr/bin', 'NEBIUS_API_KEY':'injected-inference',
                 'STELLIS_AGENT_GATEWAY_TOKEN':'injected-gateway', 'STELLIS_NEMOCLAW_SANDBOX':'wrong-local',
                 'NEMOCLAW_GATEWAY_PORT':'8990', 'STELLIS_LOCAL_INFERENCE_URL':'http://127.0.0.1:11434/v1'}
    result = launch.environment('cloud', configured, inherited)
    assert result['NEBIUS_API_KEY'] == 'injected-inference'
    assert result['STELLIS_AGENT_GATEWAY_TOKEN'] == 'injected-gateway'
    assert not any(key.startswith(('STELLIS_LOCAL_', 'STELLIS_NEMOCLAW_', 'NEMOCLAW_')) for key in result)
    with patch.dict(os.environ, result, clear=True):
        assert runtime_profile.agent_execution() == 'Managed HTTP agent gateway'
        assert runtime_profile.remote_agents()
        assert runtime_profile.inference_url() == 'https://api.tokenfactory.nebius.com/v1'


@pytest.mark.parametrize('change', [
    {'STELLIS_RUNTIME_PROFILE':'direct'}, {'STELLIS_AGENT_GATEWAY_URL':'http://agents.example'},
    {'STELLIS_AGENT_GATEWAY_URL':'https://user:secret@agents.example'},
    {'STELLIS_AGENT_GATEWAY_URL':'https://localhost'}, {'STELLIS_AGENT_GATEWAY_ACCOUNT_ID':'2'},
    {'STELLIS_AGENT_GATEWAY_TOKEN':''}, {'STELLIS_LOCAL_INFERENCE_MODEL':'local-model'},
])
def test_invalid_cloud_configuration_fails_without_fallback_or_secret_output(tmp_path, change):
    configured = {**values('cloud', tmp_path), **change}
    with pytest.raises(ValueError) as error:
        launch.environment('cloud', configured, {})
    assert 'inference-secret' not in str(error.value)
    assert 'agent-secret' not in str(error.value)
    assert 'user:secret' not in str(error.value)


def test_environment_file_never_executes_shell_and_rejects_host_overrides(tmp_path):
    config = tmp_path / 'profile.env'
    marker = tmp_path / 'executed'
    config.write_text(f"STELLIS_DATA_DIR='$(touch {marker})'\n")
    assert launch.read_env(config)['STELLIS_DATA_DIR'] == f'$(touch {marker})'
    assert not marker.exists()
    config.write_text('PATH=/untrusted/bin\n')
    with pytest.raises(ValueError, match='Unsupported assignment'):
        launch.read_env(config)


def test_init_is_private_and_never_overwrites_existing_config(tmp_path):
    config = tmp_path / 'cloud.env'
    args = argparse.Namespace(version='cloud', env_file=str(config), init=True, check=False, port=None)
    launch.run(args)
    assert config.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        launch.run(args)


def test_launch_executes_serve_with_explicit_profile_and_port(tmp_path):
    config = tmp_path / 'cloud.env'
    config.write_text('\n'.join(f'{key}={value}' for key,value in values('cloud', tmp_path).items()))
    args = argparse.Namespace(version='cloud', env_file=str(config), init=False, check=False, port=None)
    with patch('stellis.launch.os.execvpe') as execute:
        launch.run(args)
    _, command, env = execute.call_args.args
    assert command[-3:] == ['serve','--port','8822']
    assert env['STELLIS_RUNTIME_PROFILE'] == 'cloud'
    assert env['STELLIS_DATA_DIR'] == str(tmp_path / 'cloud')


def test_check_refuses_shared_data_root_and_never_starts_service(tmp_path, capsys):
    configured = values('cloud', tmp_path)
    config = tmp_path / 'cloud.env'
    config.write_text('\n'.join(f'{key}={value}' for key,value in configured.items()))
    args = argparse.Namespace(version='cloud', env_file=str(config), init=False, check=True, port=None)
    with patch('stellis.launch.os.execvpe') as execute:
        launch.run(args)
        execute.assert_not_called()
    assert 'have not been checked' in capsys.readouterr().out
    (tmp_path / 'local.env').write_text('STELLIS_DATA_DIR='+configured['STELLIS_DATA_DIR'])
    with pytest.raises(ValueError, match='separate data'):
        launch.run(args)


@pytest.mark.parametrize(('profile','label'), [('local','NemoClaw not configured'),('cloud','Managed HTTP agent gateway'),
                                             ('direct','Application inference with Token Factory')])
def test_labels_distinguish_agent_execution_from_model_provider(profile, label):
    with patch.dict(os.environ, {'STELLIS_RUNTIME_PROFILE':profile}, clear=True):
        assert runtime_profile.agent_execution() == label


@pytest.mark.parametrize(('profile','label'), [('local','Direct Ollama (demo)'),('cloud','Direct Token Factory (demo)')])
def test_demo_labels_do_not_claim_managed_agent_execution(tmp_path, profile, label):
    from stellis import storage
    with patch.dict(os.environ, {'STELLIS_RUNTIME_PROFILE':profile}, clear=True), storage.demo_workspace(tmp_path):
        assert runtime_profile.agent_execution() == label
        assert runtime_profile.label() == label


def test_direct_profile_label_does_not_claim_nemoclaw():
    with patch.dict(os.environ, {'STELLIS_RUNTIME_PROFILE':'direct'}, clear=True):
        assert runtime_profile.label() == 'Cloud inference, Application inference with Token Factory'
        assert 'NemoClaw' not in runtime_profile.label()
