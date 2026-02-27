"""
drive_uploader.py - Google Drive 이미지 업로드
"""

import io
import time
import logging

from googleapiclient.http import MediaIoBaseUpload

logger = logging.getLogger(__name__)


class DriveUploader:
    """Google Drive 파일 업로드 매니저"""

    def __init__(self, drive_service, folder_order: str, folder_review: str):
        self.service = drive_service
        self.folder_order = folder_order
        self.folder_review = folder_review

    def upload(self, file_bytes: bytes, filename: str, content_type: str,
               capture_type: str = "purchase", description: str = "") -> str:
        """파일 업로드 → 공유링크 반환 (최대 2회 재시도)"""
        folder_id = self.folder_order if capture_type == "purchase" else self.folder_review

        metadata = {"name": filename, "description": description}
        if folder_id:
            metadata["parents"] = [folder_id]

        last_err = None
        for attempt in range(3):
            try:
                media = MediaIoBaseUpload(
                    io.BytesIO(file_bytes), mimetype=content_type, resumable=True
                )
                uploaded = self.service.files().create(
                    body=metadata, media_body=media, fields="id, webViewLink",
                    supportsAllDrives=True,
                ).execute()

                file_id = uploaded["id"]

                try:
                    self.service.permissions().create(
                        fileId=file_id,
                        body={"type": "anyone", "role": "reader"},
                        supportsAllDrives=True,
                    ).execute()
                except Exception as e:
                    logger.warning(f"공유 설정 실패 (공유 드라이브는 자동 공유): {e}")

                link = uploaded.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")
                logger.info(f"Drive 업로드 완료: {filename} → {link}")
                return link
            except Exception as e:
                last_err = e
                logger.warning(f"Drive 업로드 시도 {attempt+1}/3 실패: {e}")
                if attempt < 2:
                    time.sleep(1)

        raise last_err

    def upload_from_flask_file(self, file_storage, capture_type: str = "purchase",
                                description: str = "") -> str:
        """Flask FileStorage에서 직접 업로드"""
        filename = file_storage.filename or "upload.jpg"
        content_type = file_storage.content_type or "image/jpeg"
        file_bytes = file_storage.read()
        return self.upload(file_bytes, filename, content_type, capture_type, description)
