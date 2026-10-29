"""Explicit local and cloud launches with isolated configuration and data."""

import os
from pathlib import Path
import re
import shlex
import shutil
import sys
from urllib.parse import urlsplit

EXAMPLES = {
    'local': '''# Local NemoClaw and Ollama on GN100. Speech runs on the Stellis host.
STELLIS_RUNTIME_PROFILE=local
STELLIS_DATA_DIR=~/.local/share/stellis/local
STELLIS_ACCOUNT_ID=1
STELLIS_NEMOCLAW_COMMAND=nemoclaw
STELLIS_NEMOCLAW_SANDBOX=stellis-local
NEMOCLAW_GATEWAY_PORT=8990
STELLIS_LOCAL_INFERENCE_URL=http://127.0.0.1:11434/v1
STELLIS_LOCAL_INFERENCE_MODEL=nemotron-3-nano:latest
# Add speech model paths here when reusing an existing installation.
''',
    'cloud': '''# Cloud NemoClaw uses Token Factory for model inference.
# Speech still runs on the Stellis host until the serverless adapter is delivered.
STELLIS_RUNTIME_PROFILE=cloud
STELLIS_DATA_DIR=~/.local/share/stellis/cloud
STELLIS_ACCOUNT_ID=1
STELLIS_AGENT_GATEWAY_ACCOUNT_ID=1
STELLIS_AGENT_GATEWAY_URL=
STELLIS_AGENT_GATEWAY_TOKEN=
NEBIUS_API_KEY=
STELLIS_CLOUD_MODEL_PROVIDER=nebius
STELLIS_AGENT_GATEWAY_TAVILY_READY=0
''',
}
SECRET_KEYS = {'NEBIUS_API_KEY', 'STELLIS_AGENT_GATEWAY_TOKEN', 'TAVILY_API_KEY'}


def read_env(path):
    """Read literal environment assignments without executing shell code."""
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:]
        key, separator, value = line.partition('=')
        key = key.strip()
        if (not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key)
                or not (key.startswith(('STELLIS_', 'NEMOCLAW_')) or key in SECRET_KEYS)):
            raise ValueError(f'Unsupported assignment on line {number}')
        try:
            parts = shlex.split(value, comments=True)
        except ValueError as error:
            raise ValueError(f'Invalid quoting on line {number}') from error
        if len(parts) > 1:
            raise ValueError(f'Quote values containing spaces on line {number}')
        values[key] = parts[0] if parts else ''
    return values


def environment(profile, values, inherited):
    """Discard inherited runtime settings; accept explicitly injected profile secrets."""
    if values.get('STELLIS_RUNTIME_PROFILE') != profile:
        raise ValueError(f'The configuration must set STELLIS_RUNTIME_PROFILE={profile}')
    env = {key: value for key, value in inherited.items()
           if not key.startswith(('STELLIS_', 'NEMOCLAW_', 'NEBIUS_', 'TAVILY_'))}
    env.update(values)
    if profile == 'cloud':
        for key in SECRET_KEYS:
            if not env.get(key) and inherited.get(key):
                env[key] = inherited[key]
    else:
        for key in ('NEBIUS_API_KEY', 'STELLIS_AGENT_GATEWAY_TOKEN'):
            env.pop(key, None)
    root = env.get('STELLIS_DATA_DIR', '').strip()
    if not root:
        raise ValueError('Set a separate STELLIS_DATA_DIR for this version')
    env['STELLIS_DATA_DIR'] = str(Path(root).expanduser().resolve())
    if profile == 'local':
        forbidden = [key for key in values if key.startswith('STELLIS_AGENT_GATEWAY_')]
        if forbidden:
            raise ValueError('Local configuration must not include cloud agent gateway settings')
        endpoint = urlsplit(env.get('STELLIS_LOCAL_INFERENCE_URL', 'http://127.0.0.1:11434/v1'))
        if (endpoint.scheme not in {'http', 'https'} or endpoint.hostname not in {'localhost', '127.0.0.1', '::1'}
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
            raise ValueError('Local inference requires a loopback URL')
        if not env.get('STELLIS_NEMOCLAW_SANDBOX'):
            raise ValueError('Set STELLIS_NEMOCLAW_SANDBOX')
        command = env.get('STELLIS_NEMOCLAW_COMMAND', 'nemoclaw')
        if not shutil.which(command, path=env.get('PATH')):
            raise ValueError('Install NemoClaw or set STELLIS_NEMOCLAW_COMMAND to its executable')
    else:
        if any(key.startswith(('NEMOCLAW_', 'STELLIS_LOCAL_', 'STELLIS_NEMOCLAW_')) for key in values):
            raise ValueError('Cloud configuration must not include local inference or sandbox settings')
        required = ('NEBIUS_API_KEY', 'STELLIS_AGENT_GATEWAY_URL', 'STELLIS_AGENT_GATEWAY_TOKEN',
                    'STELLIS_ACCOUNT_ID', 'STELLIS_AGENT_GATEWAY_ACCOUNT_ID')
        missing = [key for key in required if not env.get(key, '').strip()]
        if missing:
            raise ValueError('Set ' + ', '.join(missing))
        endpoint = urlsplit(env['STELLIS_AGENT_GATEWAY_URL'])
        if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password
                or endpoint.query or endpoint.fragment or endpoint.hostname in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError('Cloud agents require an authenticated HTTPS gateway URL')
        if env['STELLIS_ACCOUNT_ID'] != env['STELLIS_AGENT_GATEWAY_ACCOUNT_ID']:
            raise ValueError('The cloud gateway must belong to STELLIS_ACCOUNT_ID')
    return env


def run(args):
    """Initialize, check or launch exactly one requested version."""
    profile = args.version
    path = Path(args.env_file or f'.private/{profile}.env')
    if args.init:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'x', opener=lambda name, flags: os.open(name, flags, 0o600)) as handle:
            handle.write(EXAMPLES[profile])
        command = f'stellis launch {profile}'
        if args.env_file:
            command += ' --env-file ' + shlex.quote(str(path))
        print(f'Created {path}. Edit it, then run {command} --check.')
        return
    if not path.is_file():
        raise ValueError(f'Create configuration first: stellis launch {profile} --init')
    env = environment(profile, read_env(path), os.environ)
    other = path.with_name(('cloud' if profile == 'local' else 'local') + '.env')
    if other.is_file():
        other_root = read_env(other).get('STELLIS_DATA_DIR')
        if other_root and Path(other_root).expanduser().resolve() == Path(env['STELLIS_DATA_DIR']):
            raise ValueError('Local and cloud versions must use separate data directories')
    port = args.port if args.port is not None else 8821 if profile == 'local' else 8822
    if not 1 <= port <= 65535:
        raise ValueError('--port must be between 1 and 65535')
    print(f'Version: {profile}')
    print('Agents: NemoClaw on ' + ('GN100' if profile == 'local' else 'Nebius Cloud'))
    print('Model inference: ' + ('GN100 Ollama' if profile == 'local' else 'Nebius Token Factory'))
    print('Speech: local worker on the Stellis host; Nebius serverless integration pending')
    print(f'Data: {env["STELLIS_DATA_DIR"]}')
    print(f'Port: {port}')
    if args.check:
        print('Configuration valid. Live connections and model responses have not been checked.')
        return
    sys.stdout.flush()
    os.execvpe(sys.executable, [sys.executable, '-c', 'from stellis.cli import main; main()',
                              'serve', '--port', str(port)], env)
