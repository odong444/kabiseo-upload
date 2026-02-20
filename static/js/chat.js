/**
 * chat.js - 카비서 WebSocket 채팅 클라이언트
 */

(function() {
    'use strict';

    // ──────── 사용자 인증 확인 ────────
    const saved = localStorage.getItem('kabiseo_user');
    if (!saved) {
        window.location.href = '/';
        return;
    }

    let user;
    try {
        user = JSON.parse(saved);
        if (!user.name || !user.phone) throw new Error();
    } catch(e) {
        localStorage.removeItem('kabiseo_user');
        window.location.href = '/';
        return;
    }

    // ──────── DOM 요소 ────────
    const chatMessages = document.getElementById('chatMessages');
    const chatInput = document.getElementById('chatInput');
    const sendBtn = document.getElementById('sendBtn');
    const chatContainer = document.getElementById('chatContainer');
    const botStatus = document.getElementById('botStatus');

    let hasHistory = false;

    // ──────── SocketIO 연결 ────────
    const socket = io({
        transports: ['websocket', 'polling']
    });

    socket.on('connect', function() {
        console.log('WebSocket 연결됨');
        botStatus.textContent = '온라인';
        socket.emit('join', { name: user.name, phone: user.phone });
    });

    socket.on('disconnect', function() {
        botStatus.textContent = '연결 끊김';
    });

    socket.on('reconnect', function() {
        botStatus.textContent = '온라인';
        socket.emit('join', { name: user.name, phone: user.phone });
    });

    // ──────── 메시지 수신 ────────

    socket.on('chat_history', function(data) {
        const messages = data.messages || [];
        chatMessages.innerHTML = '';
        if (messages.length > 0) {
            hasHistory = true;
            messages.forEach(function(msg) {
                appendMessage(msg.sender, msg.message, msg.timestamp);
            });
        }
        scrollToBottom();
    });

    socket.on('bot_message', function(data) {
        removeTyping();
        hideQuickButtons();
        appendMessage('bot', data.message);
        scrollToBottom();
    });

    socket.on('bot_typing', function(data) {
        if (data.typing) {
            showTyping();
        } else {
            removeTyping();
        }
    });

    socket.on('error', function(data) {
        appendMessage('bot', data.message || '오류가 발생했습니다.');
        scrollToBottom();
    });

    // ──────── 퀵 버튼으로 메시지 전송 ────────

    function sendQuickMessage(text) {
        appendMessage('user', text);
        hideQuickButtons();
        scrollToBottom();
        socket.emit('user_message', {
            name: user.name,
            phone: user.phone,
            message: text
        });
    }

    // 전역으로 노출 (onclick에서 호출)
    window.sendQuickMessage = sendQuickMessage;

    // ──────── 시작 퀵버튼 표시 ────────

    function showQuickButtons() {
        if (document.getElementById('quickButtons')) return;
        const wrap = document.createElement('div');
        wrap.id = 'quickButtons';
        wrap.className = 'quick-buttons';
        wrap.innerHTML =
            '<button class="quick-btn" onclick="sendQuickMessage(\'1\')">&#10024; 체험단 신청</button>' +
            '<button class="quick-btn" onclick="sendQuickMessage(\'2\')">&#128203; 진행 상황</button>' +
            '<button class="quick-btn" onclick="sendQuickMessage(\'3\')">&#128247; 사진 제출</button>' +
            '<button class="quick-btn" onclick="sendQuickMessage(\'4\')">&#128176; 입금 현황</button>' +
            '<button class="quick-btn" onclick="sendQuickMessage(\'5\')">&#128172; 기타 문의</button>';
        chatMessages.appendChild(wrap);
        scrollToBottom();
    }

    function hideQuickButtons() {
        const el = document.getElementById('quickButtons');
        if (el) el.remove();
    }

    // 환영 메시지 후 퀵버튼 표시 (약간 딜레이)
    socket.on('bot_message', function() {
        setTimeout(function() {
            if (!document.getElementById('quickButtons')) {
                // 마지막 봇 메시지가 메뉴 관련이면 퀵버튼 표시
                const lastBot = chatMessages.querySelectorAll('.chat-bubble.bot .bubble-content');
                if (lastBot.length > 0) {
                    const lastText = lastBot[lastBot.length - 1].textContent;
                    if (lastText.includes('체험단 신청') || lastText.includes('도와드릴까요') || lastText.includes('선택해주세요')) {
                        showQuickButtons();
                    }
                }
            }
        }, 300);
    });

    // ──────── 메시지 전송 ────────

    function sendMessage() {
        const message = chatInput.value.trim();
        if (!message) return;

        appendMessage('user', message);
        chatInput.value = '';
        hideQuickButtons();
        scrollToBottom();

        socket.emit('user_message', {
            name: user.name,
            phone: user.phone,
            message: message
        });
    }

    sendBtn.addEventListener('click', sendMessage);
    chatInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            sendMessage();
        }
    });

    // ──────── UI 헬퍼 ────────

    function appendMessage(sender, message, timestamp) {
        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble ' + sender;

        const now = timestamp ? new Date(timestamp * 1000) : new Date();
        const timeStr = now.getHours().toString().padStart(2, '0') + ':' +
                        now.getMinutes().toString().padStart(2, '0');

        if (sender === 'bot') {
            bubble.innerHTML =
                '<div class="bubble-avatar">K</div>' +
                '<div class="bubble-content">' + escapeHtml(message) + '</div>' +
                '<span class="bubble-time">' + timeStr + '</span>';
        } else {
            bubble.innerHTML =
                '<div class="bubble-content">' + escapeHtml(message) + '</div>' +
                '<span class="bubble-time">' + timeStr + '</span>';
        }

        chatMessages.appendChild(bubble);
    }

    function showTyping() {
        removeTyping();
        const typing = document.createElement('div');
        typing.className = 'chat-bubble bot';
        typing.id = 'typingIndicator';
        typing.innerHTML =
            '<div class="bubble-avatar">K</div>' +
            '<div class="typing-indicator show">' +
            '<div class="typing-dots"><span></span><span></span><span></span></div>' +
            '</div>';
        chatMessages.appendChild(typing);
        scrollToBottom();
    }

    function removeTyping() {
        const el = document.getElementById('typingIndicator');
        if (el) el.remove();
    }

    function scrollToBottom() {
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML.replace(/\n/g, '<br>');
    }

    // ──────── 메뉴 토글 ────────
    const menuToggle = document.getElementById('menuToggle');
    if (menuToggle) {
        menuToggle.addEventListener('click', function() {
            if (confirm('로그아웃하시겠습니까?\n(다른 이름으로 접속하려면 로그아웃 해주세요)')) {
                localStorage.removeItem('kabiseo_user');
                window.location.href = '/';
            }
        });
    }

})();
