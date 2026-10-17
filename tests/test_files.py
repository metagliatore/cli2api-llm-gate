import os
import io
import json
import base64
import unittest
import tempfile
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

import files_store
from tasks import (
    format_messages_for_antigravity,
    prepare_messages_for_openai,
    convert_responses_input_to_messages,
)
from api import app


class TestFilesStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_files_dir = os.environ.get("FILES_DIR")
        os.environ["FILES_DIR"] = self.temp_dir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if self.orig_files_dir is not None:
            os.environ["FILES_DIR"] = self.orig_files_dir
        else:
            os.environ.pop("FILES_DIR", None)

    def test_save_get_list_delete_file(self):
        content = b"Sample document text content"
        f = files_store.save_file(content, "document.txt", purpose="assistants")
        file_id = f["id"]

        self.assertTrue(file_id.startswith("file-"))
        self.assertEqual(f["filename"], "document.txt")
        self.assertEqual(f["bytes"], len(content))
        self.assertEqual(f["purpose"], "assistants")
        self.assertEqual(f["status"], "processed")

        # Get file metadata
        retrieved = files_store.get_file(file_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["id"], file_id)
        self.assertEqual(retrieved["bytes"], len(content))

        # Get file content
        data, fname, mime = files_store.get_file_content(file_id)
        self.assertEqual(data, content)
        self.assertEqual(fname, "document.txt")
        self.assertEqual(mime, "text/plain")

        # List files
        all_files = files_store.list_files()
        self.assertTrue(any(item["id"] == file_id for item in all_files))

        # Delete file
        deleted = files_store.delete_file(file_id)
        self.assertTrue(deleted)
        self.assertIsNone(files_store.get_file(file_id))

    def test_extract_image_file_for_ai(self):
        # Raw PNG magic header bytes
        raw_img = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
        f = files_store.save_file(raw_img, "scan.png", purpose="vision")
        file_id = f["id"]

        ai_info = files_store.extract_file_for_ai(file_id)
        self.assertTrue(ai_info.get("is_image"))
        self.assertEqual(ai_info.get("mime_type"), "image/png")
        self.assertTrue(ai_info.get("data_url", "").startswith("data:image/png;base64,"))
        self.assertTrue(os.path.exists(ai_info.get("path", "")))

    def test_extract_pdf_file_for_ai(self):
        raw_pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        f = files_store.save_file(raw_pdf, "scanned_doc.pdf", purpose="assistants")
        ai_info = files_store.extract_file_for_ai(f["id"])

        self.assertTrue(ai_info.get("is_pdf"))
        self.assertEqual(ai_info.get("filename"), "scanned_doc.pdf")
        self.assertEqual(ai_info.get("mime_type"), "application/pdf")
        self.assertTrue(os.path.exists(ai_info.get("path", "")))

    def test_save_data_url_to_temp_file(self):
        b64_dummy = base64.b64encode(b"fake image bytes").decode()
        data_url = f"data:image/jpeg;base64,{b64_dummy}"

        res = files_store.save_data_url_to_temp_file(data_url, "test_scan")
        self.assertIsNotNone(res)
        file_path, mime = res
        self.assertTrue(os.path.exists(file_path))
        self.assertEqual(mime, "image/jpeg")
        with open(file_path, "rb") as f:
            self.assertEqual(f.read(), b"fake image bytes")


class TestMultimodalFormatting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_files_dir = os.environ.get("FILES_DIR")
        os.environ["FILES_DIR"] = self.temp_dir

        raw_img = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        f = files_store.save_file(raw_img, "receipt.jpg", purpose="vision")
        self.image_file_id = f["id"]

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if self.orig_files_dir is not None:
            os.environ["FILES_DIR"] = self.orig_files_dir
        else:
            os.environ.pop("FILES_DIR", None)

    def test_antigravity_resolves_attachments_and_data_urls(self):
        b64_dummy = base64.b64encode(b"inline image").decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analizza questa fattura e la scansione allegata"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_dummy}"}},
                ],
                "attachments": [{"file_id": self.image_file_id}],
            }
        ]

        prompt, dirs = format_messages_for_antigravity(messages, return_attached_dirs=True)

        self.assertIn("receipt.jpg", prompt)
        self.assertIn("[Attached Image:", prompt)
        self.assertIn("[Attached Files & Scans to Analyze]", prompt)
        self.assertGreaterEqual(len(dirs), 1)

    def test_copilot_resolves_attachments_to_image_url(self):
        messages = [
            {
                "role": "user",
                "content": "Analizza questo scontrino",
                "attachments": [{"file_id": self.image_file_id}],
            }
        ]

        prepared = prepare_messages_for_openai(messages)
        self.assertEqual(len(prepared), 1)
        self.assertNotIn("attachments", prepared[0])
        content = prepared[0]["content"]
        self.assertIsInstance(content, list)

        # Has text part
        text_parts = [p for p in content if p.get("type") == "text"]
        self.assertTrue(len(text_parts) >= 1)
        self.assertEqual(text_parts[0]["text"], "Analizza questo scontrino")

        # Has vision image_url part
        image_parts = [p for p in content if p.get("type") == "image_url"]
        self.assertEqual(len(image_parts), 1)
        self.assertTrue(image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_responses_api_converts_input_file(self):
        ha_input = [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Cosa vedi in questo file?"},
                    {"type": "input_file", "file_id": self.image_file_id},
                ],
            }
        ]

        msgs = convert_responses_input_to_messages(ha_input)
        self.assertEqual(len(msgs), 1)
        self.assertIsInstance(msgs[0]["content"], list)
        file_parts = [p for p in msgs[0]["content"] if p.get("type") == "file"]
        self.assertEqual(len(file_parts), 1)
        self.assertEqual(file_parts[0]["file_id"], self.image_file_id)


class TestOpenAIFilesAPIEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.orig_files_dir = os.environ.get("FILES_DIR")
        os.environ["FILES_DIR"] = self.temp_dir
        self.client = TestClient(app)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if self.orig_files_dir is not None:
            os.environ["FILES_DIR"] = self.orig_files_dir
        else:
            os.environ.pop("FILES_DIR", None)

    @patch("api.API_KEY", "test_key_123")
    def test_files_crud_api(self):
        headers = {"Authorization": "Bearer test_key_123"}

        # 1. Upload File
        file_content = b"%PDF-1.4 sample PDF bytes"
        files = {"file": ("scanned_invoice.pdf", io.BytesIO(file_content), "application/pdf")}
        data = {"purpose": "assistants"}

        resp = self.client.post("/v1/files", headers=headers, files=files, data=data)
        self.assertEqual(resp.status_code, 200, resp.text)
        created = resp.json()
        self.assertEqual(created["object"], "file")
        self.assertEqual(created["filename"], "scanned_invoice.pdf")
        self.assertEqual(created["bytes"], len(file_content))
        file_id = created["id"]

        # 2. List Files
        resp = self.client.get("/v1/files", headers=headers)
        self.assertEqual(resp.status_code, 200)
        list_data = resp.json()
        self.assertEqual(list_data["object"], "list")
        self.assertTrue(any(f["id"] == file_id for f in list_data["data"]))

        # 3. Retrieve File Metadata
        resp = self.client.get(f"/v1/files/{file_id}", headers=headers)
        self.assertEqual(resp.status_code, 200)
        meta = resp.json()
        self.assertEqual(meta["id"], file_id)
        self.assertEqual(meta["filename"], "scanned_invoice.pdf")

        # 4. Retrieve File Content
        resp = self.client.get(f"/v1/files/{file_id}/content", headers=headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, file_content)

        # 5. Delete File
        resp = self.client.delete(f"/v1/files/{file_id}", headers=headers)
        self.assertEqual(resp.status_code, 200)
        del_res = resp.json()
        self.assertEqual(del_res["id"], file_id)
        self.assertTrue(del_res["deleted"])

        # 6. Verify 404 after deletion
        resp = self.client.get(f"/v1/files/{file_id}", headers=headers)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
