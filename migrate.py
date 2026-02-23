"""
migrate.py - 구글시트 → PostgreSQL 일회성 마이그레이션 스크립트

사용법:
    DATABASE_URL=postgresql://... python migrate.py

환경변수:
    DATABASE_URL: PostgreSQL 연결 URL (필수)
    GOOGLE_CREDENTIALS: 구글 서비스계정 JSON (base64) — 기존 환경변수 재사용
    SPREADSHEET_ID: 구글 스프레드시트 ID — 기존 환경변수 재사용
"""

import os
import sys
import json
import re
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _format_phone(raw: str) -> str:
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return raw


def _safe_int(v) -> int:
    try:
        return int(str(v).replace(",", "").strip() or "0")
    except (ValueError, TypeError):
        return 0


def get_sheets_client():
    """기존 google_client.py 로직 재사용"""
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    import base64

    creds_b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "") or os.environ.get("GOOGLE_CREDENTIALS", "")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID", "")

    if not creds_b64 or not spreadsheet_id:
        logger.error("GOOGLE_CREDENTIALS 또는 SPREADSHEET_ID 환경변수 누락")
        sys.exit(1)

    creds_json = json.loads(base64.b64decode(creds_b64))
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
    client = gspread.authorize(creds)
    return client, spreadsheet_id


def migrate():
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        logger.error("DATABASE_URL 환경변수 필수")
        sys.exit(1)

    from modules.db_manager import DBManager
    db = DBManager(database_url)
    logger.info("DB 연결 완료")

    client, spreadsheet_id = get_sheets_client()
    spreadsheet = client.open_by_key(spreadsheet_id)
    logger.info("구글시트 연결 완료")

    # ─── 1. 캠페인관리 시트 → campaigns 테이블 ───
    logger.info("=== 캠페인 마이그레이션 시작 ===")
    try:
        ws_campaigns = spreadsheet.worksheet("캠페인관리")
        headers = ws_campaigns.row_values(1)
        all_rows = ws_campaigns.get_all_values()

        for i, row in enumerate(all_rows[1:], start=2):
            row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
            campaign_id = row_dict.get("캠페인ID", "").strip()
            if not campaign_id:
                continue

            try:
                db.create_campaign(row_dict)
                logger.info(f"  캠페인 등록: {campaign_id} - {row_dict.get('상품명', '')}")
            except Exception as e:
                if "duplicate key" in str(e).lower():
                    logger.warning(f"  캠페인 중복 스킵: {campaign_id}")
                else:
                    logger.error(f"  캠페인 등록 에러: {e}")

        logger.info(f"캠페인 마이그레이션 완료: {len(all_rows) - 1}건 처리")
    except Exception as e:
        logger.error(f"캠페인관리 시트 읽기 에러: {e}")

    # ─── 2. 리뷰어DB 시트 → reviewers 테이블 ───
    logger.info("=== 리뷰어 마이그레이션 시작 ===")
    try:
        ws_reviewers = spreadsheet.worksheet("리뷰어DB")
        headers = ws_reviewers.row_values(1)
        all_rows = ws_reviewers.get_all_values()

        for i, row in enumerate(all_rows[1:], start=2):
            row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
            name = row_dict.get("이름", "").strip()
            phone = _format_phone(row_dict.get("연락처", "").strip())
            if not name or not phone:
                continue

            try:
                reviewer_id = db.upsert_reviewer(name, phone)
                # 추가 필드 업데이트
                store_ids = row_dict.get("아이디목록", "").strip()
                participation = _safe_int(row_dict.get("참여횟수", 0))
                kakao = row_dict.get("카톡친구", "N").strip().upper() == "Y"
                memo = row_dict.get("메모", "")

                db._execute(
                    """UPDATE reviewers SET store_ids = %s, participation = %s,
                       kakao_friend = %s, memo = %s WHERE id = %s""",
                    (store_ids, participation, kakao, memo, reviewer_id)
                )
                logger.info(f"  리뷰어 등록: {name} ({phone})")
            except Exception as e:
                logger.error(f"  리뷰어 등록 에러: {e}")

        logger.info(f"리뷰어 마이그레이션 완료: {len(all_rows) - 1}건 처리")
    except Exception as e:
        logger.error(f"리뷰어DB 시트 읽기 에러: {e}")

    # ─── 3. 카비서_정리 시트 → progress 테이블 ───
    logger.info("=== 진행건 마이그레이션 시작 ===")
    try:
        ws_main = spreadsheet.worksheet("카비서_정리")
        headers = ws_main.row_values(1)
        all_rows = ws_main.get_all_values()

        success = 0
        skip = 0
        error_count = 0

        for i, row in enumerate(all_rows[1:], start=2):
            row_dict = {headers[j]: row[j] for j in range(len(headers)) if j < len(row)}
            campaign_id = row_dict.get("캠페인ID", "").strip()
            if not campaign_id:
                skip += 1
                continue

            # 캠페인 존재 확인
            campaign = db.get_campaign_by_id(campaign_id)
            if not campaign:
                logger.warning(f"  캠페인 없음 스킵 (row {i}): {campaign_id}")
                skip += 1
                continue

            # 진행자 정보
            name = row_dict.get("진행자이름", "").strip() or row_dict.get("수취인명", "").strip()
            phone = _format_phone(
                row_dict.get("진행자연락처", "").strip() or row_dict.get("연락처", "").strip()
            )
            if not name or not phone:
                skip += 1
                continue

            # reviewer 확보
            reviewer_id = db.upsert_reviewer(name, phone)

            try:
                # progress 행 직접 INSERT
                purchase_date = row_dict.get("구매일", "").strip() or None
                review_deadline = row_dict.get("리뷰기한", "").strip() or None
                review_submit_date = row_dict.get("리뷰제출일", "").strip() or None
                settlement_date = row_dict.get("입금정리", "").strip() or None
                settled_date = row_dict.get("입금완료", "").strip() or None

                db._execute(
                    """INSERT INTO progress (
                        campaign_id, reviewer_id, store_id, status, created_at,
                        recipient_name, phone, bank, account, depositor,
                        address, nickname, payment_amount, order_number,
                        purchase_date, purchase_capture_url, review_deadline,
                        review_submit_date, review_capture_url,
                        review_fee, payment_total, settlement_date, settled_date,
                        is_collected, remark
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s, %s
                    )""",
                    (
                        campaign_id,
                        reviewer_id,
                        row_dict.get("아이디", ""),
                        row_dict.get("상태", "신청"),
                        row_dict.get("날짜", None) or None,
                        row_dict.get("수취인명", ""),
                        _format_phone(row_dict.get("연락처", "")),
                        row_dict.get("은행", ""),
                        row_dict.get("계좌", ""),
                        row_dict.get("예금주", ""),
                        row_dict.get("주소", ""),
                        row_dict.get("닉네임", ""),
                        _safe_int(row_dict.get("결제금액", 0)),
                        row_dict.get("주문번호", ""),
                        purchase_date,
                        row_dict.get("구매캡쳐링크", ""),
                        review_deadline,
                        review_submit_date,
                        row_dict.get("리뷰캡쳐링크", ""),
                        _safe_int(row_dict.get("리뷰비", 0)),
                        _safe_int(row_dict.get("입금금액", 0)),
                        settlement_date,
                        settled_date,
                        row_dict.get("회수여부", "").strip().upper() == "Y",
                        row_dict.get("비고", ""),
                    )
                )
                success += 1
            except Exception as e:
                logger.error(f"  진행건 등록 에러 (row {i}): {e}")
                error_count += 1

        logger.info(f"진행건 마이그레이션 완료: 성공 {success}, 스킵 {skip}, 에러 {error_count}")
    except Exception as e:
        logger.error(f"카비서_정리 시트 읽기 에러: {e}")

    logger.info("=== 마이그레이션 완료 ===")


if __name__ == "__main__":
    migrate()
