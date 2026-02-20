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
        var messages = data.messages || [];
        chatMessages.innerHTML = '';
        messages.forEach(function(msg) {
            appendMessage(msg.sender, msg.message, msg.timestamp);
        });
        scrollToBottom();
    });

    socket.on('bot_message', function(data) {
        removeTyping();
        appendMessage('bot', data.message);
        scrollToBottom();

        // 메뉴 키워드가 포함되면 퀵버튼 표시
        var msg = data.message || '';
        if (msg.indexOf('체험단 신청') !== -1 || msg.indexOf('도와드릴까요') !== -1 || msg.indexOf('선택해주세요') !== -1 || msg.indexOf('기타 문의') !== -1) {
            setTimeout(showQuickButtons, 200);
        }
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

    // ──────── 퀵 버튼 ────────

    var quickMenuItems = [
        { label: '체험단 신청', value: '1' },
        { label: '진행 상황', value: '2' },
        { label: '사진 제출', value: '3' },
        { label: '입금 현황', value: '4' },
        { label: '기타 문의', value: '5' }
    ];

    function sendQuickMessage(text) {
        hideQuickButtons();
        appendMessage('user', text);
        scrollToBottom();
        socket.emit('user_message', {
            name: user.name,
            phone: user.phone,
            message: text
        });
    }

    function showQuickButtons() {
        hideQuickButtons();
        var wrap = document.createElement('div');
        wrap.id = 'quickButtons';
        wrap.className = 'quick-buttons';

        quickMenuItems.forEach(function(item) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'quick-btn';
            btn.textContent = item.label;
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                sendQuickMessage(item.value);
            });
            wrap.appendChild(btn);
        });

        chatMessages.appendChild(wrap);
        scrollToBottom();
    }

    function hideQuickButtons() {
        var el = document.getElementById('quickButtons');
        if (el) el.remove();
    }

    // ──────── 메시지 전송 ────────

    function sendMessage() {
        var message = chatInput.value.trim();
        if (!message) return;

        hideQuickButtons();
        appendMessage('user', message);
        chatInput.value = '';
        chatInput.style.height = 'auto';
        scrollToBottom();

        socket.emit('user_message', {
            name: user.name,
            phone: user.phone,
            message: message
        });
    }

    sendBtn.addEventListener('click', sendMessage);

    // Enter = 줄바꿈 (기본), Shift+Enter = 전송
    chatInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // textarea 높이 자동 조절
    chatInput.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });

    // ──────── UI 헬퍼 ────────

    function appendMessage(sender, message, timestamp) {
        var bubble = document.createElement('div');
        bubble.className = 'chat-bubble ' + sender;

        var now = timestamp ? new Date(timestamp * 1000) : new Date();
        var timeStr = now.getHours().toString().padStart(2, '0') + ':' +
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
        var typing = document.createElement('div');
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
        var el = document.getElementById('typingIndicator');
        if (el) el.remove();
    }

    function scrollToBottom() {
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML.replace(/\n/g, '<br>');
    }

    // ──────── 메뉴 토글 ────────
    var menuToggle = document.getElementById('menuToggle');
    if (menuToggle) {
        menuToggle.addEventListener('click', function() {
            if (confirm('로그아웃하시겠습니까?\n(다른 이름으로 접속하려면 로그아웃 해주세요)')) {
                localStorage.removeItem('kabiseo_user');
                window.location.href = '/';
            }
        });
    }

})();
