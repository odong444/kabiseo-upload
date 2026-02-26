/**
 * reviewer.js - 리뷰어 웹 UI 공통 유틸리티
 */

const Reviewer = {
    /** localStorage에서 유저 정보 가져오기 */
    getUser() {
        try {
            const saved = localStorage.getItem('kabiseo_user');
            if (saved) {
                const user = JSON.parse(saved);
                if (user.name && user.phone) return user;
            }
        } catch (e) {}
        return null;
    },

    /** 로그인 필수 - 미로그인 시 로그인 페이지로 */
    requireLogin() {
        const user = this.getUser();
        if (!user) {
            window.location.href = '/';
            return null;
        }
        return user;
    },

    /** API 호출 (JSON) */
    async apiCall(url, options = {}) {
        try {
            const resp = await fetch(url, options);
            const data = await resp.json();
            return data;
        } catch (e) {
            console.error('API 에러:', e);
            return { ok: false, error: e.message };
        }
    },

    /** API GET with user params */
    async apiGet(url) {
        const user = this.getUser();
        const sep = url.includes('?') ? '&' : '?';
        const fullUrl = user
            ? `${url}${sep}name=${encodeURIComponent(user.name)}&phone=${encodeURIComponent(user.phone)}`
            : url;
        return this.apiCall(fullUrl);
    },

    /** API POST JSON */
    async apiPost(url, body) {
        return this.apiCall(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
    },

    /** API POST FormData */
    async apiPostForm(url, formData) {
        try {
            const resp = await fetch(url, { method: 'POST', body: formData });
            return await resp.json();
        } catch (e) {
            console.error('API 에러:', e);
            return { ok: false, error: e.message };
        }
    },

    /** 숫자 포맷 (1000 → 1,000) */
    formatNumber(n) {
        if (!n) return '0';
        return Number(String(n).replace(/[^0-9]/g, '') || 0).toLocaleString();
    },

    /** 상태 → 뱃지 HTML */
    statusBadge(status) {
        const map = {
            '신청': { color: 'yellow', label: '신청' },
            '가이드전달': { color: 'blue', label: '가이드전달' },
            '구매캡쳐대기': { color: 'orange', label: '구매대기' },
            '리뷰대기': { color: 'blue', label: '리뷰대기' },
            '리뷰제출': { color: 'green', label: '리뷰제출' },
            '입금대기': { color: 'green', label: '입금대기' },
            '입금완료': { color: 'done', label: '입금완료' },
        };
        const info = map[status] || { color: 'yellow', label: status };
        return `<span class="status-badge"><span class="status-dot ${info.color}"></span>${info.label}</span>`;
    },

    /** 토스트 메시지 표시 */
    toast(msg, type = 'info') {
        const el = document.createElement('div');
        el.className = `reviewer-toast reviewer-toast-${type}`;
        el.textContent = msg;
        document.body.appendChild(el);
        requestAnimationFrame(() => el.classList.add('show'));
        setTimeout(() => {
            el.classList.remove('show');
            setTimeout(() => el.remove(), 300);
        }, 2500);
    },

    /** 로딩 오버레이 */
    showLoading(msg = '처리중...') {
        let overlay = document.getElementById('reviewer-loading');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = 'reviewer-loading';
            overlay.className = 'reviewer-loading-overlay';
            overlay.innerHTML = `
                <div class="reviewer-loading-content">
                    <div class="reviewer-spinner"></div>
                    <p class="reviewer-loading-text">${msg}</p>
                </div>
            `;
            document.body.appendChild(overlay);
        } else {
            overlay.querySelector('.reviewer-loading-text').textContent = msg;
            overlay.style.display = 'flex';
        }
    },

    hideLoading() {
        const overlay = document.getElementById('reviewer-loading');
        if (overlay) overlay.style.display = 'none';
    },
};
