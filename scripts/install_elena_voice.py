"""Install the upstream curated Elena reference with a pinned content digest."""
import argparse,hashlib,urllib.request
from pathlib import Path
URL='https://raw.githubusercontent.com/devnen/Chatterbox-TTS-Server/main/voices/Elena.wav'
SHA256='baa3c3894ccdcc3b258a35f35daf885f618e54d46f90f164b10a95dd0c858400'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);a=p.parse_args()
with urllib.request.urlopen(URL,timeout=45) as r:data=r.read(8*1024*1024)
if hashlib.sha256(data).hexdigest()!=SHA256:raise SystemExit('Voice content differs from the reviewed upstream asset; installation stopped.')
a.directory.mkdir(parents=True,exist_ok=True);(a.directory/'Elena.wav').write_bytes(data)
print('Installed verified Elena.wav reference. Source and licence: https://github.com/devnen/Chatterbox-TTS-Server')
