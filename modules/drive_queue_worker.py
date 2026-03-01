"""
drive_queue_worker.py - Drive 업로드 백그라운드 워커

큐 테이블(drive_upload_queue)을 폴링하여 순차 업로드 처리.
Google Drive API 쓰기 제한(초당 3건)을 지키기 위해 업로드 간격 0.8초.
"""

import time
import logging
import threading

logger = logging.getLogger(__name__)

POLL_INTERVAL = 2       # 큐 폴링 간격 (초)
UPLOAD_DELAY = 0.8      # 업로드 사이 간격 (초) — 초당 ~1.2건


class DriveQueueWorker:
    """백그라운드 데몬 스레드로 Drive 업로드 큐 처리"""

    def __init__(self, db_manager, drive_uploader, ai_verify_fn=None, kakao_notifier=None):
        self.db = db_manager
        self.uploader = drive_uploader
        self.ai_verify_fn = ai_verify_fn
        self.kakao_notifier = kakao_notifier
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        # 서버 재시작 시 processing → pending 복구
        try:
            self.db.reset_stale_processing()
            logger.info("Drive 큐: stale processing 항목 복구 완료")
        except Exception as e:
            logger.warning("Drive 큐: stale 복구 실패 (무시): %s", e)

        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="drive-queue-worker")
        self._thread.start()
        logger.info("Drive 업로드 워커 시작")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                job = self.db.claim_next_upload()
                if not job:
                    time.sleep(POLL_INTERVAL)
                    continue

                self._process(job)
                time.sleep(UPLOAD_DELAY)
            except Exception as e:
                logger.error("Drive 큐 루프 에러: %s", e, exc_info=True)
                time.sleep(POLL_INTERVAL)

    def _process(self, job: dict):
        queue_id = job["id"]
        progress_id = job["progress_id"]
        capture_type = job["capture_type"]
        filename = job["filename"]
        content_type = job["content_type"] or "image/jpeg"
        file_data = job["file_data"]

        if not file_data:
            logger.warning("Drive 큐 #%s: file_data 비어있음, 스킵", queue_id)
            self.db.complete_upload(queue_id)
            return

        try:
            desc = f"{capture_type}_progress{progress_id}"
            drive_link = self.uploader.upload(
                bytes(file_data), filename, content_type, capture_type, desc
            )
            logger.info("Drive 큐 #%s: 업로드 성공 → %s", queue_id, drive_link)

            # DB 업데이트: capture_url + 상태 변경
            self.db.update_after_upload(capture_type, progress_id, drive_link)

            # upload_pending 해제
            self.db.clear_upload_pending(progress_id)

            # 큐 완료 처리 (file_data 삭제)
            self.db.complete_upload(queue_id)

            # AI 검수 트리거
            if self.ai_verify_fn:
                try:
                    self.ai_verify_fn(capture_type, progress_id, drive_link)
                except Exception as e:
                    logger.warning("Drive 큐 #%s: AI 검수 트리거 실패 (무시): %s", queue_id, e)

        except Exception as e:
            attempt = job.get("attempt_count", 0)
            logger.error("Drive 큐 #%s: 업로드 실패 (attempt %s): %s",
                         queue_id, attempt, e)
            try:
                self.db.fail_upload(queue_id, str(e)[:500])
                # 5회 초과 최종 실패 → 카카오톡 알림
                if attempt >= 5 and self.kakao_notifier:
                    try:
                        self.kakao_notifier.notify_admin_upload_failure(
                            progress_id, capture_type, filename, str(e)[:100]
                        )
                    except Exception as ne:
                        logger.warning("Drive 큐 #%s: 카톡 알림 실패: %s", queue_id, ne)
            except Exception as fe:
                logger.error("Drive 큐 #%s: fail_upload 에러: %s", queue_id, fe)
