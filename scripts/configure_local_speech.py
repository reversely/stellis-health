"""Wire already installed speech assets into a service EnvironmentFile; no downloads."""
from __future__ import annotations
import argparse
import os
import shlex
import tempfile
from pathlib import Path


def configure(env_file: Path, worker: Path, online: Path, parakeet: Path, models: Path):
    """Validate existing assets and atomically preserve unrelated service settings."""
    worker, online, parakeet, models = (p.expanduser().resolve() for p in (worker, online, parakeet, models))
    if not worker.is_file() or not os.access(worker, os.X_OK):
        raise ValueError("The speech worker must be an installed executable")
    for directory in (online, parakeet):
        for choices in [('tokens.txt',), ('encoder.int8.onnx','encoder.onnx'), ('decoder.int8.onnx','decoder.onnx'), ('joiner.int8.onnx','joiner.onnx')]:
            if not any((directory/name).is_file() and (directory/name).stat().st_size for name in choices):
                raise ValueError(f"Incomplete speech model directory: {directory.name}")
    files={'STELLIS_WHISPER_MODEL':'ggml-small.en.bin','STELLIS_SEGMENTATION_MODEL':'pyannote-segmentation-3.0-int8.onnx','STELLIS_EMBEDDING_MODEL':'nemo-en-titanet-small.onnx'}
    if any(not (models/name).is_file() or not (models/name).stat().st_size for name in files.values()):
        raise ValueError("The finalized-transcript model directory is incomplete")
    settings={'STELLIS_AUDIO_WORKER':worker,'STELLIS_ONLINE_ASR_MODEL_DIR':online,'STELLIS_PARAKEET_MODEL_DIR':parakeet,**{key:models/name for key,name in files.items()}}
    if any('\n' in str(path) or '\r' in str(path) for path in settings.values()):
        raise ValueError("Model paths must not contain line breaks")
    env_file=env_file.expanduser();env_file.parent.mkdir(parents=True,exist_ok=True)
    existing=env_file.read_text().splitlines() if env_file.exists() else []
    kept=[line for line in existing if line.split('=',1)[0].strip() not in settings]
    text='\n'.join(kept+[key+'='+shlex.quote(str(value)) for key,value in settings.items()])+'\n'
    descriptor,name=tempfile.mkstemp(prefix='.speech-',dir=env_file.parent)
    try:
        with os.fdopen(descriptor,'w') as target:
            target.write(text);target.flush();os.fsync(target.fileno())
        os.chmod(name,0o600);os.replace(name,env_file)
    finally:
        Path(name).unlink(missing_ok=True)
    return settings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['env-file','worker','online','parakeet','models']:
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    try:configure(args.env_file,args.worker,args.online,args.parakeet,args.models)
    except (OSError,ValueError) as exc:parser.exit(1,str(exc)+'\n')
    print('Speech paths saved. Restart the selected service to apply them.')


if __name__=='__main__':main()
