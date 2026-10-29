"""The self-hosted voice listing never grants arbitrary filesystem access."""
from pathlib import Path
from scripts import chatterbox_server as server

def test_voice_directory_rejects_outside_symlinks(tmp_path,monkeypatch):
 voices=tmp_path/'voices';voices.mkdir();(voices/'Elena.wav').write_bytes(b'fixture')
 outside=tmp_path/'private.wav';outside.write_bytes(b'private');(voices/'escape.wav').symlink_to(outside)
 monkeypatch.setenv('CHATTERBOX_VOICES_DIR',str(voices));monkeypatch.delenv('CHATTERBOX_TOKEN',raising=False)
 assert list(server.voice_files())==['Elena.wav']
 assert server.voices(None)=={'voices':['default','Elena.wav']}
 assert 'private.wav' not in server.voice_files()
