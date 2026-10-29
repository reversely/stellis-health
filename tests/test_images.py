"""Private image attachment API, finalization, and deletion checks."""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from stellis import images, storage, tools
from stellis.server import app
from test_import import wav_bytes, mp3_bytes


def png_bytes() -> bytes:
    output = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "private metadata")
    Image.new("RGB", (120, 80), "#3456a0").save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


class ImageWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"STELLIS_DATA_DIR": self.temp.name})
        self.env.start()
        self.client = TestClient(app)
        response = self.client.post("/api/v1/conversations/import",
                                    files={"file": ("meeting.mp3", mp3_bytes(), "audio/mpeg")})
        self.conversation_id = response.json()["conversation"]["id"]

    def tearDown(self) -> None:
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def _upload(self, owner: str, time_ms: int = 500):
        return self.client.post(f"/api/v1/{owner}/images",
                                files={"file": ("screenshot.png", png_bytes(), "image/png")},
                                data={"media_position_ms": str(time_ms), "caption": "Shared slide"})

    def test_upload_read_caption_and_trash_lifecycle(self) -> None:
        owner = f"conversations/{self.conversation_id}"
        uploaded = self._upload(owner)
        self.assertEqual(uploaded.status_code, 200)
        item = uploaded.json()
        self.assertEqual((item["width"], item["height"], item["media_position_ms"]), (120, 80, 500))
        self.assertEqual(len(self.client.get(f"/api/v1/{owner}/images").json()), 1)
        file_url = f"/api/v1/{owner}/images/{item['id']}/file"
        response = self.client.get(file_url)
        self.assertEqual(response.headers["content-type"], "image/webp")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        with Image.open(io.BytesIO(response.content)) as normalized:
            self.assertEqual(normalized.size, (120, 80))
            self.assertNotIn("private metadata", str(normalized.info))
        self.assertIn("attachment", self.client.get(file_url + "?download=true").headers["content-disposition"])
        changed = self.client.patch(f"/api/v1/{owner}/images/{item['id']}", json={"caption": "Orbit diagram"})
        self.assertEqual(changed.json()["caption"], "Orbit diagram")
        self.client.post(f"/api/v1/conversations/{self.conversation_id}/trash")
        self.assertEqual(self.client.get(file_url).status_code, 404)
        self.client.post(f"/api/v1/trash/{self.conversation_id}/restore")
        self.assertEqual(self.client.get(file_url).status_code, 200)
        self.client.post(f"/api/v1/conversations/{self.conversation_id}/trash")
        self.assertEqual(self.client.delete(f"/api/v1/trash/{self.conversation_id}").status_code, 200)
        self.assertEqual(list((Path(self.temp.name) / "images").glob("*.webp")), [])

    def test_rejects_bad_content_and_time_without_files(self) -> None:
        owner = f"conversations/{self.conversation_id}"
        wrong = self.client.post(f"/api/v1/{owner}/images",
                                 files={"file": ("fake.png", b"not an image", "image/png")},
                                 data={"media_position_ms": "500"})
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(self._upload(owner, 2000).status_code, 400)
        oversized = self.client.post(f"/api/v1/{owner}/images",
                                     files={"file": ("big.png", b"a" * 12_000_001, "image/png")},
                                     data={"media_position_ms": "500"})
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(list((Path(self.temp.name) / "images").glob("*.webp")), [])

    def test_jpeg_and_webp_uploads_are_decoded(self) -> None:
        owner = f"conversations/{self.conversation_id}"
        for fmt, mime in (("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            with self.subTest(fmt=fmt):
                output = io.BytesIO()
                Image.new("RGB", (16, 12), "#28647a").save(output, format=fmt)
                response = self.client.post(f"/api/v1/{owner}/images",
                                            files={"file": (f"sample.{fmt.lower()}", output.getvalue(), mime)},
                                            data={"media_position_ms": "0"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual((response.json()["width"], response.json()["height"]), (16, 12))

    def test_animated_gif_and_webp_keep_their_frames_within_limits(self) -> None:
        owner = f"conversations/{self.conversation_id}"
        frames = [Image.effect_noise((64, 48), 80).convert("RGB").point(lambda v, i=i: (v + i * 70) % 256) for i in range(3)]
        buffers = {}
        for fmt in ("GIF", "WEBP"):
            buffers[fmt] = io.BytesIO()
            frames[0].save(buffers[fmt], format=fmt, save_all=True, append_images=frames[1:], duration=80, loop=0)
        for fmt, mime in (("GIF", "image/gif"), ("WEBP", "image/webp")):
            with self.subTest(fmt=fmt):
                response = self.client.post(f"/api/v1/{owner}/images", data={"media_position_ms": "0"},
                                            files={"file": ("a." + fmt.lower(), buffers[fmt].getvalue(), mime)})
                self.assertEqual(response.status_code, 200, response.text)
                served = self.client.get(f"/api/v1/{owner}/images/{response.json()['id']}/file")
                with Image.open(io.BytesIO(served.content)) as stored:
                    self.assertEqual((stored.format, stored.n_frames, stored.size), (served.headers["content-type"][6:].upper(), 3, (64, 48)))
        with patch.object(images, "MAX_FRAMES", 2):
            too_many = self.client.post(f"/api/v1/{owner}/images", data={"media_position_ms": "0"},
                                        files={"file": ("a.gif", buffers["GIF"].getvalue(), "image/gif")})
        self.assertEqual(too_many.status_code, 400)
        self.assertIn("at most 2 frames", too_many.json()["detail"])
        self.assertEqual(len(list((Path(self.temp.name) / "images").iterdir())), 2)

    def test_animated_uploads_are_re_encoded_without_metadata(self) -> None:
        owner = f"conversations/{self.conversation_id}"
        frames = [Image.effect_noise((64, 48), 80).convert("RGB").point(lambda v, i=i: (v + i * 70) % 256) for i in range(3)]
        exif = Image.Exif()
        exif[0x010E] = "GPS-laden-description"
        webp, gif = io.BytesIO(), io.BytesIO()
        frames[0].save(webp, format="WEBP", save_all=True, append_images=frames[1:], duration=[50, 90, 120],
                       loop=3, exif=exif.tobytes(), xmp=b"<x:xmpmeta>secret-location</x:xmpmeta>")
        frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:], duration=80, loop=0,
                       comment=b"secret-comment")
        self.assertIn(b"GPS-laden-description", webp.getvalue())
        self.assertIn(b"secret-comment", gif.getvalue())
        for name, upload, mime in (("a.webp", webp, "image/webp"), ("a.gif", gif, "image/gif")):
            with self.subTest(name=name):
                response = self.client.post(f"/api/v1/{owner}/images", data={"media_position_ms": "0"},
                                            files={"file": (name, upload.getvalue(), mime)})
                self.assertEqual(response.status_code, 200, response.text)
                stored = self.client.get(f"/api/v1/{owner}/images/{response.json()['id']}/file").content
                for secret in (b"GPS-laden-description", b"secret-location", b"secret-comment", b"EXIF", b"XMP"):
                    self.assertNotIn(secret, stored)
                with Image.open(io.BytesIO(stored)) as image:
                    self.assertEqual(image.n_frames, 3)
                    if name.endswith("webp"):
                        self.assertEqual(image.info["loop"], 3)
                        durations = []
                        for index in range(3):
                            image.seek(index)
                            image.load()
                            durations.append(image.info["duration"])
                        self.assertEqual(durations, [50, 90, 120])

    def test_selected_passage_keeps_audio_time_after_new_generation(self) -> None:
        owner = f"conversations/{self.conversation_id}"
        stamp = datetime.now(timezone.utc).isoformat()
        def generation(version: str, segment_id: str) -> None:
            storage.save_transcript({"conversation_id": self.conversation_id, "pipeline_version": version,
                                     "revision": 0, "language": "en", "updated_at": stamp,
                                     "segments_json": json.dumps([{"id": segment_id, "start_ms": 350,
                                                                        "end_ms": 850, "text": "A visual moment."}]),
                                     "turns_json": "[]"})
        generation("image-fixture-1", "before")
        uploaded = self.client.post(f"/api/v1/{owner}/images",
                                    files={"file": ("slide.png", png_bytes(), "image/png")},
                                    data={"media_position_ms": "500", "segment_id": "before"})
        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(uploaded.json()["media_position_ms"], 350)
        self.assertEqual(uploaded.json()["segment_id"], "before")
        generation("image-fixture-2", "after")
        retained = self.client.get(f"/api/v1/{owner}/images").json()[0]
        self.assertEqual((retained["media_position_ms"], retained["segment_id"]), (350, "before"))
        stale = self.client.post(f"/api/v1/{owner}/images",
                                 files={"file": ("slide.png", png_bytes(), "image/png")},
                                 data={"media_position_ms": "500", "segment_id": "before"})
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(len(self.client.get(f"/api/v1/{owner}/images").json()), 1)

    def test_selected_excerpt_is_atomic_and_uses_displayed_words(self) -> None:
        owner = f"conversations/{self.conversation_id}"
        stamp = datetime.now(timezone.utc).isoformat()
        storage.save_transcript({"conversation_id": self.conversation_id, "pipeline_version": "image-range-1",
                                 "revision": 0, "language": "en", "updated_at": stamp,
                                 "segments_json": json.dumps([{"id": "turn-1", "start_ms": 350,
                                                                "end_ms": 850, "text": "An image moment."}]),
                                 "turns_json": "[]"})
        transcript = storage.get_transcript(self.conversation_id)
        revision = int(transcript["revision"])
        with storage.connect() as conn:
            conn.execute("INSERT INTO transcript_edits (conversation_id, segment_id, text, updated_at) VALUES (?, ?, ?, ?)",
                         (self.conversation_id, "turn-1", "A visual moment.", stamp))
        data = {"media_position_ms": "500", "segment_id": "turn-1", "start_offset": "2",
                "end_offset": "8", "expected_revision": str(revision)}
        endpoint = f"/api/v1/{owner}/images"
        def upload(fields):
            return self.client.post(endpoint, files={"file": ("slide.png", png_bytes(), "image/png")}, data=fields)
        stale = upload({**data, "expected_revision": str(revision + 1)})
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(self.client.get(endpoint).json(), [])
        self.assertEqual(list((Path(self.temp.name) / "images").glob("*.webp")), [])
        invalid = upload({**data, "end_offset": "999"})
        self.assertEqual(invalid.status_code, 400)
        created = upload(data)
        self.assertEqual(created.status_code, 200, created.text)
        image = created.json()
        self.assertEqual((image["quote"], image["start_offset"], image["end_offset"], image["media_position_ms"]),
                         ("visual", 2, 8, 350))
        self.assertEqual(self.client.get(endpoint).json()[0]["id"], image["id"])
        with storage.connect() as conn:
            row = conn.execute("SELECT quote, segment_id FROM conversation_images WHERE id = ?", (image["id"],)).fetchone()
        self.assertEqual((row["quote"], row["segment_id"]), ("visual", "turn-1"))

    def test_recording_image_survives_finalization_and_discard_removes_images(self) -> None:
        session = self.client.post("/api/v1/recordings", json={"title": "Visual meeting", "media_type": "audio/webm"}).json()
        owner = f"recordings/{session['id']}"
        image = self._upload(owner, 700).json()
        self.assertEqual(len(self.client.get(f"/api/v1/{owner}/images").json()), 1)
        path = Path(self.temp.name) / "sample.webm"
        subprocess.run([tools.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=2", "-c:a", "libopus", str(path)],
                       check=True, timeout=20)
        self.client.put(f"/api/v1/{owner}/chunks/0", content=path.read_bytes())
        finalized = self.client.post(f"/api/v1/{owner}/finalize")
        self.assertEqual(finalized.status_code, 200)
        conversation_id = finalized.json()["conversation"]["id"]
        transferred = self.client.get(f"/api/v1/conversations/{conversation_id}/images").json()
        self.assertEqual([item["id"] for item in transferred], [image["id"]])
        self.assertEqual(self.client.get(f"/api/v1/conversations/{conversation_id}/images/{image['id']}/file").status_code, 200)
        discarded = self.client.post("/api/v1/recordings", json={"title": "Discard", "media_type": "audio/webm"}).json()
        self._upload(f"recordings/{discarded['id']}")
        self.assertEqual(self.client.delete(f"/api/v1/recordings/{discarded['id']}").status_code, 200)
        with storage.connect() as conn:
            self.assertFalse(conn.execute("SELECT 1 FROM conversation_images WHERE recording_id = ?", (discarded["id"],)).fetchone())

    def test_page_images_survive_startup_copy_to_other_owners_and_delete_with_page(self) -> None:
        from stellis import images, library
        page = self.client.post("/api/v1/pages", json={"title": "Visual"}).json()
        owner = f"pages/{page['id']}"
        upload = self.client.post(f"/api/v1/{owner}/images", files={"file": ("a.png", png_bytes(), "image/png")},
                                  data={"caption": "Diagram"})
        self.assertEqual(upload.status_code, 200)
        image = upload.json()
        self.assertNotIn("media_position_ms", image)
        self.assertEqual(self.client.patch(f"/api/v1/{owner}/images/{image['id']}", json={"caption": "Chart"}).json()["caption"], "Chart")
        images.recover_orphans()
        self.assertEqual(self.client.get(f"/api/v1/{owner}/images/{image['id']}/file").status_code, 200)

        copied = self.client.post(f"/api/v1/images/{image['id']}/copy",
                                  json={"scope": "conversation", "owner_id": self.conversation_id})
        self.assertEqual(copied.status_code, 201)
        copy_id = copied.json()["id"]
        self.assertNotEqual(copy_id, image["id"])
        listed = self.client.get(f"/api/v1/conversations/{self.conversation_id}/images").json()
        self.assertEqual([(item["id"], item["caption"]) for item in listed], [(copy_id, "Chart")])
        other = self.client.post("/api/v1/pages", json={"title": "Other"}).json()
        self.assertEqual(self.client.post(f"/api/v1/images/{copy_id}/copy",
                                          json={"scope": "page", "owner_id": other["id"]}).status_code, 201)
        self.assertEqual(self.client.get(f"/api/v1/pages/{other['id']}/images/{image['id']}/file").status_code, 404)
        self.assertEqual(self.client.post(f"/api/v1/images/{copy_id}/copy",
                                          json={"scope": "page", "owner_id": "missing"}).status_code, 404)

        self.client.post(f"/api/v1/pages/{page['id']}/trash")
        self.assertEqual(self.client.delete(f"/api/v1/trash/{page['id']}").status_code, 200)
        self.assertFalse((Path(self.temp.name) / "images" / f"{image['id']}.webp").exists())
        library.erase_all()
        with storage.connect() as conn:
            self.assertFalse(conn.execute("SELECT 1 FROM page_images").fetchone())
        self.assertEqual(list((Path(self.temp.name) / "images").glob("*.webp")), [])


if __name__ == "__main__":
    unittest.main()
