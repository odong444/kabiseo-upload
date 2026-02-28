"""
timeout_manager.py - 30분 타임아웃 관리

신청 후 양식+구매캡쳐 미제출 시 타임아웃 취소.
25분 경고, 30분 취소.

이중 타임아웃:
  1) DB progress.created_at 기준 (서버 재시작 무관)
  2) 인메모리 last_activity 기준 (재접속 시 리셋)
  → 둘 중 더 나중 시간 기준으로 타임아웃 체크
"""

import time
import logging
import threading
from datetime import timezone

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 1800  # 30분
WARNING_SECONDS = 1500  # 25분 (경고)


class TimeoutManager:
    """세션 타임아웃 매니저 (WebSocket 알림 + DB 취소)"""

    def __init__(self, state_store, timeout_seconds: int = TIMEOUT_SECONDS,
                 warning_seconds: int = WARNING_SECONDS):
        self.state_store = state_store
        self.timeout = timeout_seconds
        self.warning = warning_seconds
        self._running = False
        self._thread = None
        self._warned = set()  # 이미 경고 보낸 reviewer_id
        self._socketio = None
        self._db_manager = None
        self._chat_logger = None
        self._kakao_notifier = None

    def set_socketio(self, socketio):
        """SocketIO 인스턴스 설정 (앱 시작 후)"""
        self._socketio = socketio

    def set_sheets_manager(self, db_manager):
        """하위 호환: sheets_manager → db_manager"""
        self._db_manager = db_manager

    def set_chat_logger(self, chat_logger):
        self._chat_logger = chat_logger

    def set_kakao_notifier(self, kakao_notifier):
        self._kakao_notifier = kakao_notifier

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._check_loop, daemon=True)
        self._thread.start()
        logger.info(f"타임아웃 매니저 시작 (경고: {self.warning}초, 취소: {self.timeout}초)")

    def stop(self):
        self._running = False

    def clear_warning(self, reviewer_id: str):
        """메시지 수신 시 경고 상태 클리어"""
        self._warned.discard(reviewer_id)

    def _check_loop(self):
        db_check_counter = 0
        deadline_check_counter = 0
        while self._running:
            try:
                self._check_all()
            except Exception as e:
                logger.error(f"타임아웃 체크 에러: {e}")

            # DB 기반 타임아웃: 30초마다 (15초 * 2)
            db_check_counter += 1
            if db_check_counter >= 2:
                db_check_counter = 0
                try:
                    self._check_db_stale()
                except Exception as e:
                    logger.error(f"DB 타임아웃 체크 에러: {e}")

            # 리뷰 기한 리마인더: 1시간마다 (15초 * 240 = 3600초)
            deadline_check_counter += 1
            if deadline_check_counter >= 240:
                deadline_check_counter = 0
                try:
                    self._check_review_deadlines()
                except Exception as e:
                    logger.error(f"리뷰 기한 체크 에러: {e}")

            time.sleep(15)  # 15초마다 체크

    def _get_db_created_epoch(self, state) -> float:
        """DB에서 progress.created_at을 가져와 epoch 반환 (캠페인 신청 시점)"""
        if not self._db_manager:
            return 0.0
        try:
            campaign_id = state.temp_data.get("campaign", {}).get("캠페인ID", "")
            store_ids = state.temp_data.get("store_ids", [])
            if not campaign_id or not store_ids:
                return 0.0
            rows = self._db_manager._fetchall(
                """SELECT MIN(created_at) as min_created FROM progress
                   WHERE campaign_id = %s AND store_id = ANY(%s)
                   AND status IN ('가이드전달', '구매캡쳐대기', '리뷰대기')""",
                (campaign_id, store_ids)
            )
            if rows and rows[0].get("min_created"):
                dt = rows[0]["min_created"]
                return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
        except Exception as e:
            logger.debug(f"DB created_at 조회 실패: {e}")
        return 0.0

    def _check_all(self):
        for state in self.state_store.all_states():
            if state.step < 4:
                continue

            # 이중 타임아웃: DB created_at vs 인메모리 last_activity 중 더 나중 기준
            db_created = self._get_db_created_epoch(state)
            baseline = max(state.last_activity, db_created) if db_created else state.last_activity
            elapsed = time.time() - baseline
            rid = state.reviewer_id

            # 30분 초과 → 취소
            if elapsed >= self.timeout:
                self._do_timeout_cancel(state)
                continue

            # 25분 초과 → 경고 (1회만)
            if elapsed >= self.warning and rid not in self._warned:
                self._send_warning(state)
                self._warned.add(rid)

    def _send_warning(self, state):
        """15분 경고 메시지 전송"""
        msg = "⏰ 5분 후 미제출 시 신청이 자동 취소됩니다. 양식과 구매캡쳐를 제출해주세요!"
        rid = state.reviewer_id
        logger.info(f"타임아웃 경고: {rid}")

        if self._chat_logger:
            self._chat_logger.log(rid, "bot", msg)

        if self._socketio:
            self._socketio.emit("bot_message", {"message": msg}, room=rid)

        # 카카오톡 경고
        if self._kakao_notifier:
            try:
                campaign = state.temp_data.get("campaign", {})
                product = campaign.get("캠페인명", "") or campaign.get("상품명", "")
                recipient = state.temp_data.get("recipient_name", "")
                sids = ", ".join(state.temp_data.get("store_ids", []))
                self._kakao_notifier.notify_timeout_warning(
                    state.name, state.phone, product, recipient, sids)
            except Exception as e:
                logger.warning(f"카톡 타임아웃 경고 실패: {e}")

    def _do_timeout_cancel(self, state):
        """30분 타임아웃 취소 처리"""
        rid = state.reviewer_id
        campaign = state.temp_data.get("campaign", {})
        campaign_id = campaign.get("캠페인ID", "")
        store_ids = state.temp_data.get("store_ids", [])

        logger.info(f"타임아웃 취소: {rid} (캠페인: {campaign_id}, 아이디: {store_ids})")

        # DB에서 해당 행 타임아웃취소 처리
        if self._db_manager and campaign_id and store_ids:
            try:
                cancelled = self._db_manager.cancel_by_timeout(
                    state.name, state.phone, campaign_id, store_ids
                )
                logger.info(f"DB 취소 {cancelled}건: {rid}")
            except Exception as e:
                logger.error(f"DB 취소 에러: {e}")

        # 알림 메시지
        msg = "⏰ 30분이 경과하여 신청이 자동 취소되었습니다.\n자리가 있으면 다시 신청 가능합니다.\n\n'메뉴'를 입력해주세요."

        if self._chat_logger:
            self._chat_logger.log(rid, "bot", msg)

        if self._socketio:
            self._socketio.emit("bot_message", {"message": msg}, room=rid)

        # 카카오톡 취소 알림
        if self._kakao_notifier:
            try:
                product = campaign.get("캠페인명", "") or campaign.get("상품명", "")
                recipient = state.temp_data.get("recipient_name", "")
                sids = ", ".join(store_ids) if isinstance(store_ids, list) else str(store_ids)
                self._kakao_notifier.notify_timeout_cancelled(
                    state.name, state.phone, product, recipient, sids)
            except Exception as e:
                logger.warning(f"카톡 타임아웃 취소 알림 실패: {e}")

        # 캠페인 모집마감 → 모집중 복원 (타임아웃으로 자리 생긴 경우)
        self._try_reopen_campaign(campaign_id)

        # 상태 리셋
        state.reset()
        self._warned.discard(rid)

    def _check_review_deadlines(self):
        """리뷰 기한 D-3, D-1 리마인더 발송"""
        if not self._kakao_notifier:
            return
        sent = self._kakao_notifier.send_review_deadline_reminders()
        if sent:
            logger.info(f"리뷰 기한 리마인더 발송: {sent}건")

    def _try_reopen_campaign(self, campaign_id: str):
        """타임아웃 취소 후 자리가 생겼으면 모집마감 → 모집중 복원"""
        if not self._db_manager or not campaign_id:
            return
        try:
            campaign = self._db_manager.get_campaign_by_id(campaign_id)
            if not campaign or campaign.get("상태") != "모집마감":
                return
            import models as _models
            if _models.campaign_manager and _models.campaign_manager.check_capacity(campaign_id) > 0:
                self._db_manager.update_campaign_status(campaign_id, "모집중")
                logger.info("캠페인 [%s] 타임아웃 취소로 자리 생김 → 모집중 복원", campaign_id)
        except Exception as e:
            logger.warning("모집중 복원 체크 실패: %s", e)

    def _check_db_stale(self):
        """DB 기반 타임아웃: 경고(25분) + 취소(30분) + 카톡 알림

        웹 플로우에서는 인메모리 세션이 없으므로
        DB created_at 기준으로 직접 경고/취소 처리.
        """
        if not self._db_manager:
            return
        from datetime import timedelta
        from modules.utils import now_kst

        now = now_kst()
        warning_cutoff = now - timedelta(seconds=self.warning)
        timeout_cutoff = now - timedelta(seconds=self.timeout)

        # 인메모리 세션이 있는 건은 _check_all()에서 처리하므로 제외
        active_keys = set()
        for state in self.state_store.all_states():
            if state.step >= 4:
                campaign_id = state.temp_data.get("campaign", {}).get("캠페인ID", "")
                for sid in state.temp_data.get("store_ids", []):
                    active_keys.add((campaign_id, sid))

        # 신청/가이드전달 상태이고 25분 경과한 건 모두 조회
        rows = self._db_manager._fetchall(
            """SELECT p.id, p.campaign_id, p.store_id, p.created_at,
                      r.name, r.phone,
                      COALESCE(NULLIF(c.campaign_name, ''), c.product_name, '') AS product_name
               FROM progress p
               JOIN reviewers r ON p.reviewer_id = r.id
               LEFT JOIN campaigns c ON p.campaign_id = c.id
               WHERE p.status IN ('신청', '가이드전달')
               AND p.created_at < %s""",
            (warning_cutoff,)
        )

        # 리뷰어별 그룹화 (같은 사람에게 중복 알림 방지)
        to_warn = {}   # {(name, phone): [rows]}
        to_cancel = {}  # {(name, phone): [rows]}

        for r in rows:
            if (r["campaign_id"], r["store_id"]) in active_keys:
                continue
            key = (r["name"], r["phone"])
            if r["created_at"].astimezone().timestamp() < timeout_cutoff.timestamp():
                to_cancel.setdefault(key, []).append(r)
            else:
                to_warn.setdefault(key, []).append(r)

        # ── 25분 경고 (카톡) ──
        for (name, phone), group in to_warn.items():
            pid = group[0]["id"]
            if pid in self._warned:
                continue
            self._warned.add(pid)
            if self._kakao_notifier:
                try:
                    product = group[0].get("product_name", "")
                    sids = ", ".join(r["store_id"] for r in group)
                    self._kakao_notifier.notify_timeout_warning(
                        name, phone, product, "", sids)
                    logger.info("DB 경고 발송: %s %s (%s)", name, phone, sids)
                except Exception as e:
                    logger.warning("DB 경고 카톡 실패: %s", e)

        # ── 30분 취소 (DB + 카톡) ──
        all_cancel_ids = []
        affected_campaigns = set()
        for (name, phone), group in to_cancel.items():
            ids = [r["id"] for r in group]
            all_cancel_ids.extend(ids)
            affected_campaigns.update(r["campaign_id"] for r in group if r["campaign_id"])
            # 카톡 취소 알림
            if self._kakao_notifier:
                try:
                    product = group[0].get("product_name", "")
                    sids = ", ".join(r["store_id"] for r in group)
                    self._kakao_notifier.notify_timeout_cancelled(
                        name, phone, product, "", sids)
                    logger.info("DB 취소 알림: %s %s (%s)", name, phone, sids)
                except Exception as e:
                    logger.warning("DB 취소 카톡 실패: %s", e)

        if all_cancel_ids:
            self._db_manager._execute(
                """UPDATE progress SET status = '타임아웃취소', updated_at = NOW()
                   WHERE id = ANY(%s)""",
                (all_cancel_ids,)
            )
            logger.info("DB 기반 자동 취소: %d건", len(all_cancel_ids))
            # warned 클리어
            for pid in all_cancel_ids:
                self._warned.discard(pid)
            # 모집중 복원 체크
            for cid in affected_campaigns:
                self._try_reopen_campaign(cid)

        deleted = self._db_manager.delete_old_cancelled_rows()
        if deleted:
            logger.info("취소 행 자동 삭제: %d건", deleted)
