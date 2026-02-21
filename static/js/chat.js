/**
 * chat.js - 카비서 WebSocket 채팅 클라이언트
 * 버튼/카드 UI 지원
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
        disableAllButtons();

        if (data.cards) {
            appendMessage('bot', data.message || '');
            renderCampaignCards(data.cards);
        } else {
            appendMessage('bot', data.message || '', null, data.buttons);
        }
        scrollToBottom();

        // 메뉴 키워드가 포함되면 퀵버튼 표시 (buttons가 없을 때만)
        var msg = data.message || '';
        if (!data.buttons && !data.cards) {
            if (msg.indexOf('도와드릴까요') !== -1 || msg.indexOf('선택해주세요') !== -1) {
                setTimeout(showQuickButtons, 200);
            }
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

    // ──────── 퀵 버튼 (메뉴) ────────

    var quickMenuItems = [
        { label: '체험단 신청', value: '1' },
        { label: '진행 상황', value: '2' },
        { label: '사진 제출', value: '3' },
        { label: '입금 현황', value: '4' },
        { label: '기타 문의', value: '5' }
    ];

    function sendQuickMessage(text) {
        disableAllButtons();
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

    // ──────── 인라인 버튼 (서버에서 전송) ────────

    function disableAllButtons() {
        // 이전 버튼 그룹 모두 비활성화
        hideQuickButtons();
        var groups = chatMessages.querySelectorAll('.inline-buttons:not(.disabled)');
        groups.forEach(function(g) {
            g.classList.add('disabled');
            g.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
        });
        // 카드 버튼도 비활성화
        var cards = chatMessages.querySelectorAll('.campaign-cards:not(.disabled)');
        cards.forEach(function(g) {
            g.classList.add('disabled');
            g.querySelectorAll('.campaign-card-btn').forEach(function(b) {
                b.style.pointerEvents = 'none';
                b.style.opacity = '0.5';
            });
        });
    }

    function renderInlineButtons(buttons, parentEl) {
        if (!buttons || !buttons.length) return;

        var wrap = document.createElement('div');
        wrap.className = 'inline-buttons';

        buttons.forEach(function(item) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'inline-btn';
            if (item.style === 'danger') btn.classList.add('inline-btn-danger');
            if (item.style === 'secondary') btn.classList.add('inline-btn-secondary');
            btn.textContent = item.label;
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                sendQuickMessage(item.value);
            });
            wrap.appendChild(btn);
        });

        parentEl.appendChild(wrap);
    }

    // ──────── 캠페인 카드 ────────

    function renderCampaignCards(campaigns) {
        if (!campaigns || !campaigns.length) return;

        var wrap = document.createElement('div');
        wrap.className = 'campaign-cards';

        campaigns.forEach(function(c) {
            var card = document.createElement('div');
            card.className = 'campaign-card';

            var urgentHtml = '';
            if (c.urgent) {
                urgentHtml = '<span class="campaign-urgent">마감 임박!</span>';
            }

            card.innerHTML =
                '<div class="campaign-card-header">' + escapeText(c.name) + urgentHtml + '</div>' +
                '<div class="campaign-card-body">' +
                '<div class="campaign-card-row"><span class="campaign-card-icon">🏪</span> ' + escapeText(c.store) + '</div>' +
                '<div class="campaign-card-row"><span class="campaign-card-icon">' +
                    (c.method === '링크유입' ? '🔗' : '🔍') +
                '</span> ' + escapeText(c.method) + '</div>' +
                '<div class="campaign-card-row remaining' + (c.urgent ? ' urgent' : '') + '">' +
                '<span class="campaign-card-icon">👥</span> 남은 ' + c.remaining + '자리</div>' +
                '</div>';

            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'campaign-card-btn';
            btn.textContent = '신청하기';
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                sendQuickMessage(c.value);
            });
            card.appendChild(btn);

            wrap.appendChild(card);
        });

        chatMessages.appendChild(wrap);
    }

    // ──────── 메시지 전송 ────────

    function sendMessage() {
        var message = chatInput.value.trim();
        if (!message) return;

        disableAllButtons();
        appendMessage('user', message);
        chatInput.value = '';
        chatInput.style.height = 'auto';
        chatInput.classList.remove('scrollable');
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

    // textarea 높이 자동 조절 (최대 3줄, 그 이상은 스크롤)
    var maxTextareaHeight = 84;
    chatInput.addEventListener('input', function() {
        this.style.height = 'auto';
        var newHeight = Math.min(this.scrollHeight, maxTextareaHeight);
        this.style.height = newHeight + 'px';

        if (this.scrollHeight > maxTextareaHeight) {
            this.classList.add('scrollable');
        } else {
            this.classList.remove('scrollable');
        }

        scrollToBottom();
    });

    // ──────── UI 헬퍼 ────────

    function appendMessage(sender, message, timestamp, buttons) {
        if (!message && !buttons) return;

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

            // 양식 템플릿 복사 버튼
            if (message && message.indexOf('수취인명:') !== -1 && message.indexOf('계좌:') !== -1) {
                var formLines = message.split('\n').filter(function(l) {
                    return /^(아이디|수취인명|연락처|결제금액|은행|계좌|예금주|주소|닉네임)\s*[:：]/.test(l.trim());
                });
                if (formLines.length >= 3) {
                    var formText = formLines.join('\n');
                    var copyWrap = document.createElement('div');
                    copyWrap.style.cssText = 'margin-top:8px;';
                    var copyBtn = document.createElement('button');
                    copyBtn.type = 'button';
                    copyBtn.className = 'chat-action-btn';
                    copyBtn.textContent = '📋 양식 복사';
                    copyBtn.addEventListener('click', function() {
                        navigator.clipboard.writeText(formText).then(function() {
                            copyBtn.textContent = '✅ 복사됨!';
                            setTimeout(function() { copyBtn.textContent = '📋 양식 복사'; }, 2000);
                        }).catch(function() {
                            var ta = document.createElement('textarea');
                            ta.value = formText;
                            ta.style.cssText = 'position:fixed;left:-9999px;';
                            document.body.appendChild(ta);
                            ta.select();
                            document.execCommand('copy');
                            document.body.removeChild(ta);
                            copyBtn.textContent = '✅ 복사됨!';
                            setTimeout(function() { copyBtn.textContent = '📋 양식 복사'; }, 2000);
                        });
                    });
                    copyWrap.appendChild(copyBtn);
                    bubble.querySelector('.bubble-content').appendChild(copyWrap);
                }
            }

            // 사진 제출 안내 메시지에 액션 버튼 추가
            if (message && message.indexOf('사진') !== -1 && message.indexOf('제출') !== -1) {
                var btnWrap = document.createElement('div');
                btnWrap.style.cssText = 'margin-top:8px;';
                var btn = document.createElement('a');
                btn.href = '/upload';
                btn.className = 'chat-action-btn';
                btn.textContent = '📸 사진 제출하기';
                btnWrap.appendChild(btn);
                bubble.querySelector('.bubble-content').appendChild(btnWrap);
            }

            // 서버 인라인 버튼
            if (buttons && buttons.length) {
                renderInlineButtons(buttons, bubble.querySelector('.bubble-content'));
            }
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

    function escapeText(text) {
        var div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    }

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text || '';
        var escaped = div.innerHTML.replace(/\n/g, '<br>');
        escaped = escaped.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener" style="color:#4a90d9;text-decoration:underline;">$1</a>');
        return escaped;
    }

    // ──────── 사이드바 ────────
    var menuToggle = document.getElementById('menuToggle');
    var sidebar = document.getElementById('sidebar');
    var sidebarOverlay = document.getElementById('sidebarOverlay');
    var sidebarClose = document.getElementById('sidebarClose');
    var logoutBtn = document.getElementById('logoutBtn');

    function openSidebar() {
        sidebar.classList.add('open');
        sidebarOverlay.classList.add('open');
    }
    function closeSidebar() {
        sidebar.classList.remove('open');
        sidebarOverlay.classList.remove('open');
    }

    if (menuToggle) menuToggle.addEventListener('click', openSidebar);
    if (sidebarClose) sidebarClose.addEventListener('click', closeSidebar);
    if (sidebarOverlay) sidebarOverlay.addEventListener('click', closeSidebar);

    if (logoutBtn) {
        logoutBtn.addEventListener('click', function() {
            if (confirm('로그아웃하시겠습니까?')) {
                localStorage.removeItem('kabiseo_user');
                window.location.href = '/';
            }
        });
    }

})();
