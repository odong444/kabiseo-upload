"""
app.py - Flask 웹 서버 (구매캡쳐 / 리뷰캡쳐 업로드)

Railway 배포용. 리뷰어가 모바일에서 접속하여
예금주명 검색 → 해당 건에 이미지 업로드 → Google Drive 저장 → 시트 업데이트.
"""

import os
from flask import Flask, render_template, request, redirect, url_for, flash

from google_client import (
    search_by_depositor,
    upload_to_drive,
    update_sheet_after_upload,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "kabiseo-upload-secret-key")


# ────────────────────── 라우트 ──────────────────────

@app.route("/")
def index():
    """메인 페이지: 구매캡쳐 / 리뷰캡쳐 선택"""
    return render_template("index.html")


@app.route("/purchase")
def purchase():
    """구매캡쳐 검색 페이지"""
    return render_template("search.html", capture_type="purchase", title="구매 캡쳐 제출")


@app.route("/review")
def review():
    """리뷰캡쳐 검색 페이지"""
    return render_template("search.html", capture_type="review", title="리뷰 캡쳐 제출")


@app.route("/purchase/search")
def purchase_search():
    """예금주 검색 → 양식접수 상태 목록"""
    q = request.args.get("q", "").strip()
    if not q:
        flash("예금주명을 입력해주세요.")
        return redirect(url_for("purchase"))

    items = search_by_depositor("purchase", q)
    return render_template(
        "items.html",
        capture_type="purchase",
        title="구매 캡쳐 제출",
        depositor=q,
        items=items,
    )


@app.route("/review/search")
def review_search():
    """예금주 검색 → 리뷰대기 상태 목록"""
    q = request.args.get("q", "").strip()
    if not q:
        flash("예금주명을 입력해주세요.")
        return redirect(url_for("review"))

    items = search_by_depositor("review", q)
    return render_template(
        "items.html",
        capture_type="review",
        title="리뷰 캡쳐 제출",
        depositor=q,
        items=items,
    )


@app.route("/purchase/upload/<int:row>", methods=["POST"])
def purchase_upload(row):
    """구매캡쳐 업로드 → Drive 저장 → 시트 업데이트"""
    return _handle_upload("purchase", row)


@app.route("/review/upload/<int:row>", methods=["POST"])
def review_upload(row):
    """리뷰캡쳐 업로드 → Drive 저장 → 시트 업데이트"""
    return _handle_upload("review", row)


def _handle_upload(capture_type: str, row: int):
    """공통 업로드 처리"""
    file = request.files.get("capture")
    if not file or file.filename == "":
        flash("파일을 선택해주세요.")
        return redirect(request.referrer or url_for("index"))

    try:
        # Drive 업로드
        desc = f"{capture_type}_row{row}"
        drive_link = upload_to_drive(file, capture_type=capture_type, description=desc)

        # 시트 업데이트
        update_sheet_after_upload(capture_type, row, drive_link)

        label = "구매" if capture_type == "purchase" else "리뷰"
        return render_template(
            "success.html",
            title=f"{label} 캡쳐 제출 완료",
            message=f"{label} 캡쳐가 성공적으로 제출되었습니다!",
            capture_type=capture_type,
        )

    except Exception as e:
        flash(f"업로드 중 오류가 발생했습니다: {e}")
        return redirect(request.referrer or url_for("index"))


# ────────────────────── 실행 ──────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
