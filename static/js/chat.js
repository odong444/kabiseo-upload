/**
 * chat.js - 카비서 WebSocket 채팅 클라이언트
 * 버튼/카드 UI 지원
 */

(function() {
    'use strict';

    // ──────── 사용자 인증 확인 ────────
    var isEmbed = new URLSearchParams(window.location.search).get('embed') === '1';
    const saved = localStorage.getItem('kabiseo_user');
    if (!saved) {
        if (!isEmbed) window.location.href = '/';
        return;
    }

    let user;
    try {
        user = JSON.parse(saved);
        if (!user.name || !user.phone) throw new Error();
    } catch(e) {
        localStorage.removeItem('kabiseo_user');
        if (!isEmbed) window.location.href = '/';
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

        // 부모 창에 새 메시지 알림 (모달 embed 모드)
        try {
            if (window.parent !== window) {
                window.parent.postMessage({type: 'kabiseo_chat_msg', message: data.message || ''}, '*');
            }
        } catch(e) {}

        if (data.cards) {
            appendMessage('bot', data.message || '');
            renderCampaignCards(data.cards);
        } else if (data.multi_select) {
            appendMessage('bot', data.message || '');
            renderMultiSelect(data.multi_select, data.buttons);
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
        { label: '배송사고 문의', value: '__q_shipping__' },
        { label: '입금 문의', value: '__q_payment__' },
        { label: '진행 문의', value: '__q_progress__' },
        { label: '정보수정 문의', value: '__q_edit__' },
        { label: '기타 문의', value: '__q_etc__' }
    ];

    function sendQuickMessage(text, displayText) {
        disableAllButtons();
        appendMessage('user', displayText || text);
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
                sendQuickMessage(item.value, item.label);
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
            g.querySelectorAll('.campaign-card-toggle').forEach(function(h) {
                h.style.pointerEvents = 'none';
            });
        });
        // 다중선택 비활성화
        var msList = chatMessages.querySelectorAll('.ms-wrap:not(.disabled)');
        msList.forEach(function(g) {
            g.classList.add('disabled');
            g.querySelectorAll('button').forEach(function(b) { b.disabled = true; });
            g.querySelectorAll('input').forEach(function(inp) { inp.disabled = true; });
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
                sendQuickMessage(item.value, item.label);
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
            card.className = 'campaign-card campaign-card-collapsed';

            // 뱃지 (마감 / 마감임박 / 구매시간외)
            var badgeHtml = '';
            if (c.closed) {
                badgeHtml = '<span class="campaign-closed">' + (c.closed_reason || '마감') + '</span>';
            } else if (c.buy_time_closed) {
                badgeHtml = '<span class="campaign-closed">구매시간 외</span>';
            } else if (c.urgent) {
                badgeHtml = '<span class="campaign-urgent">마감 임박!</span>';
            }

            // 남은자리 요약 (헤더에 간략 표시)
            var remainBadge = '';
            if (!c.closed && !c.buy_time_closed) {
                remainBadge = '<span class="campaign-remain-badge">' + c.remaining + '자리</span>';
            }

            // ── 헤더 (항상 표시) ──
            var header = document.createElement('div');
            header.className = 'campaign-card-header campaign-card-toggle';
            header.innerHTML =
                '<div class="campaign-card-title-row">' +
                    '<span class="campaign-card-name">' + escapeText(c.name) + '</span>' +
                    badgeHtml + remainBadge +
                '</div>' +
                '<span class="campaign-card-arrow">&#9660;</span>';

            card.appendChild(header);

            // ── 상세 영역 (접혀있음) ──
            var detail = document.createElement('div');
            detail.className = 'campaign-card-detail';

            // 상세 정보 행들
            var detailRows = '';
            detailRows += '<div class="campaign-card-row"><span class="campaign-card-icon">🏪</span> ' + escapeText(c.store) + '</div>';

            if (c.product_price) {
                detailRows += '<div class="campaign-card-row"><span class="campaign-card-icon">💰</span> 상품금액: ' + escapeText(c.product_price) + '</div>';
            }
            if (c.review_fee) {
                detailRows += '<div class="campaign-card-row"><span class="campaign-card-icon">💵</span> 리뷰비: ' + escapeText(c.review_fee) + '</div>';
            }
            if (c.platform) {
                detailRows += '<div class="campaign-card-row"><span class="campaign-card-icon">📦</span> ' + escapeText(c.platform) + '</div>';
            }

            detailRows += '<div class="campaign-card-row"><span class="campaign-card-icon">👥</span> 남은자리: ' + c.remaining + ' / ' + (c.total || c.remaining) + '</div>';

            if (c.daily_target && c.daily_target > 0) {
                var todayDone = c.today_done || 0;
                detailRows += '<div class="campaign-card-row"><span class="campaign-card-icon">📊</span> 금일 모집: ' + todayDone + ' / ' + c.daily_target + '</div>';
            }

            if (c.buy_time) {
                detailRows += '<div class="campaign-card-row"><span class="campaign-card-icon">⏰</span> ' + escapeText(c.buy_time) + '</div>';
            }

            // 내 진행 이력
            if (c.my_history && c.my_history.length) {
                var statusEmojis = {'입금완료':'✅','리뷰제출':'🟢','입금대기':'💰','구매내역제출':'🔵','가이드전달':'🟡','신청':'⚪','타임아웃취소':'⏰','취소':'⛔'};
                detailRows += '<div class="campaign-card-history">' +
                    '<div class="campaign-card-history-title">📌 내 진행 이력:</div>';
                c.my_history.forEach(function(h) {
                    var emoji = statusEmojis[h.status] || '';
                    detailRows += '<div class="campaign-card-history-item">' + escapeText(h.id) + ' - ' + escapeText(h.status) + ' ' + emoji + '</div>';
                });
                detailRows += '</div>';
            }

            detail.innerHTML = '<div class="campaign-card-body">' + detailRows + '</div>';

            // 신청 버튼 (상세 안에 포함)
            var btn = document.createElement('button');
            btn.type = 'button';
            if (c.closed) {
                btn.className = 'campaign-card-btn campaign-card-btn-disabled';
                btn.textContent = c.closed_reason || '마감';
                btn.disabled = true;
                card.style.opacity = '0.55';
            } else if (c.buy_time_closed) {
                btn.className = 'campaign-card-btn campaign-card-btn-disabled';
                btn.textContent = '구매시간 외';
                btn.disabled = true;
                card.style.opacity = '0.6';
            } else {
                btn.className = 'campaign-card-btn';
                btn.textContent = '신청하기';
                btn.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    sendQuickMessage(c.value, c.name);
                });
            }
            detail.appendChild(btn);

            card.appendChild(detail);

            // ── 토글 이벤트 ──
            header.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                var isOpen = card.classList.contains('campaign-card-expanded');
                // 다른 카드 모두 닫기
                wrap.querySelectorAll('.campaign-card-expanded').forEach(function(other) {
                    if (other !== card) {
                        other.classList.remove('campaign-card-expanded');
                        other.classList.add('campaign-card-collapsed');
                    }
                });
                if (isOpen) {
                    card.classList.remove('campaign-card-expanded');
                    card.classList.add('campaign-card-collapsed');
                } else {
                    card.classList.remove('campaign-card-collapsed');
                    card.classList.add('campaign-card-expanded');
                    setTimeout(scrollToBottom, 200);
                }
            });

            wrap.appendChild(card);
        });

        chatMessages.appendChild(wrap);
    }

    // ──────── 다중 선택 UI ────────

    function renderMultiSelect(msData, extraButtons) {
        var maxSelect = msData.max_select;
        var items = msData.items || [];

        var wrap = document.createElement('div');
        wrap.className = 'ms-wrap';
        wrap.style.cssText = 'background:#fff;border-radius:12px;padding:16px;margin:8px 0;box-shadow:0 1px 4px rgba(0,0,0,0.08);';

        var selected = {};
        var newIds = [];

        // 토글 버튼들
        items.forEach(function(item) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.style.cssText = 'display:block;width:100%;padding:10px 14px;margin-bottom:6px;border:1px solid #ddd;border-radius:8px;background:#fff;font-size:14px;text-align:left;cursor:pointer;';

            if (item.disabled) {
                btn.textContent = item.id + ' - ' + (item.reason || '진행중') + ' 🔒';
                btn.disabled = true;
                btn.style.background = '#f3f4f6';
                btn.style.color = '#9ca3af';
                btn.style.cursor = 'not-allowed';
            } else {
                btn.textContent = item.id + ' 선택';
                btn.addEventListener('click', function() {
                    if (selected[item.id]) {
                        delete selected[item.id];
                        btn.style.background = '#fff';
                        btn.style.borderColor = '#ddd';
                        btn.textContent = item.id + ' 선택';
                    } else {
                        var total = Object.keys(selected).length + newIds.length;
                        if (total >= maxSelect) return;
                        selected[item.id] = true;
                        btn.style.background = '#eef2ff';
                        btn.style.borderColor = '#6366f1';
                        btn.textContent = '✅ ' + item.id;
                    }
                    updateCounter();
                });
            }
            wrap.appendChild(btn);
        });

        // 신규 아이디 입력 영역
        var newIdSection = document.createElement('div');
        newIdSection.style.cssText = 'display:none;margin-top:8px;padding:10px;border:1px dashed #d1d5db;border-radius:8px;';

        var newIdTags = document.createElement('div');
        newIdTags.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;';
        newIdSection.appendChild(newIdTags);

        var newIdRow = document.createElement('div');
        newIdRow.style.cssText = 'display:flex;gap:6px;';

        var newIdInput = document.createElement('input');
        newIdInput.type = 'text';
        newIdInput.placeholder = '신규 아이디 입력';
        newIdInput.style.cssText = 'flex:1;padding:8px 12px;border:1px solid #ddd;border-radius:8px;font-size:14px;';

        var newIdAddBtn = document.createElement('button');
        newIdAddBtn.type = 'button';
        newIdAddBtn.textContent = '추가';
        newIdAddBtn.style.cssText = 'padding:8px 16px;border:none;border-radius:8px;background:#6366f1;color:#fff;font-size:14px;cursor:pointer;';
        newIdAddBtn.addEventListener('click', function() {
            var val = newIdInput.value.trim();
            if (!val) return;
            if (newIds.indexOf(val) !== -1 || selected[val]) return;
            var total = Object.keys(selected).length + newIds.length;
            if (total >= maxSelect) return;
            newIds.push(val);
            var tag = document.createElement('span');
            tag.style.cssText = 'display:inline-flex;align-items:center;gap:4px;padding:4px 10px;background:#e0e7ff;border-radius:16px;font-size:13px;cursor:pointer;';
            tag.textContent = val + ' ✕';
            tag.addEventListener('click', function() {
                var idx = newIds.indexOf(val);
                if (idx > -1) newIds.splice(idx, 1);
                tag.remove();
                updateCounter();
            });
            newIdTags.appendChild(tag);
            newIdInput.value = '';
            updateCounter();
        });

        newIdInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') { e.preventDefault(); newIdAddBtn.click(); }
        });

        newIdRow.appendChild(newIdInput);
        newIdRow.appendChild(newIdAddBtn);
        newIdSection.appendChild(newIdRow);

        // + 신규 아이디 입력 버튼
        var newIdToggle = document.createElement('button');
        newIdToggle.type = 'button';
        newIdToggle.textContent = '+ 신규 아이디 입력';
        newIdToggle.style.cssText = 'display:block;width:100%;padding:10px 14px;margin-bottom:6px;border:1px dashed #9ca3af;border-radius:8px;background:#fff;font-size:14px;text-align:center;cursor:pointer;color:#6366f1;';
        newIdToggle.addEventListener('click', function() {
            newIdSection.style.display = newIdSection.style.display === 'none' ? 'block' : 'none';
        });
        wrap.appendChild(newIdToggle);
        wrap.appendChild(newIdSection);

        // 카운터
        var counter = document.createElement('div');
        counter.style.cssText = 'text-align:center;font-size:14px;color:#6b7280;margin:10px 0 6px;font-weight:600;';
        counter.textContent = '선택: 0/' + maxSelect + '개';
        wrap.appendChild(counter);

        // 다음으로 버튼
        var submitBtn = document.createElement('button');
        submitBtn.type = 'button';
        submitBtn.textContent = '다음으로';
        submitBtn.disabled = true;
        submitBtn.style.cssText = 'display:block;width:100%;padding:12px;border:none;border-radius:8px;background:#d1d5db;color:#fff;font-size:15px;font-weight:600;cursor:not-allowed;';
        submitBtn.addEventListener('click', function() {
            var allIds = Object.keys(selected).concat(newIds);
            sendQuickMessage('__ms__' + allIds.join(','), allIds.join(', '));
        });
        wrap.appendChild(submitBtn);

        // 추가 버튼 (뒤로가기 등)
        if (extraButtons && extraButtons.length) {
            var btnWrap = document.createElement('div');
            btnWrap.className = 'inline-buttons';
            btnWrap.style.cssText = 'margin-top:8px;';
            extraButtons.forEach(function(item) {
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'inline-btn';
                if (item.style === 'secondary') btn.classList.add('inline-btn-secondary');
                if (item.style === 'danger') btn.classList.add('inline-btn-danger');
                btn.textContent = item.label;
                btn.addEventListener('click', function(e) {
                    e.preventDefault();
                    sendQuickMessage(item.value, item.label);
                });
                btnWrap.appendChild(btn);
            });
            wrap.appendChild(btnWrap);
        }

        function updateCounter() {
            var total = Object.keys(selected).length + newIds.length;
            counter.textContent = '선택: ' + total + '/' + maxSelect + '개';
            if (total === maxSelect) {
                submitBtn.disabled = false;
                submitBtn.style.background = '#6366f1';
                submitBtn.style.cursor = 'pointer';
            } else {
                submitBtn.disabled = true;
                submitBtn.style.background = '#d1d5db';
                submitBtn.style.cursor = 'not-allowed';
            }
        }

        chatMessages.appendChild(wrap);
        scrollToBottom();
    }

    // ──────── 이미지 첨부 ────────

    var chatFileInput = document.getElementById('chatFileInput');
    var attachBtn = document.getElementById('attachBtn');
    var imagePreview = document.getElementById('imagePreview');
    var imagePreviewImg = document.getElementById('imagePreviewImg');
    var imagePreviewRemove = document.getElementById('imagePreviewRemove');
    var imageUploadStatus = document.getElementById('imageUploadStatus');
    var pendingImageUrl = '';  // 업로드 완료된 Drive URL
    var isUploading = false;

    if (attachBtn) {
        attachBtn.addEventListener('click', function() {
            if (isUploading) return;
            chatFileInput.click();
        });
    }

    if (chatFileInput) {
        chatFileInput.addEventListener('change', function() {
            var file = this.files[0];
            if (!file) return;

            if (!file.type.startsWith('image/')) {
                alert('이미지 파일만 첨부 가능합니다.');
                this.value = '';
                return;
            }
            if (file.size > 5 * 1024 * 1024) {
                alert('5MB 이하 이미지만 첨부 가능합니다.');
                this.value = '';
                return;
            }

            // 프리뷰 표시
            var reader = new FileReader();
            reader.onload = function(e) {
                imagePreviewImg.src = e.target.result;
                imagePreview.style.display = 'block';
                scrollToBottom();
            };
            reader.readAsDataURL(file);

            // Drive 업로드 시작
            isUploading = true;
            pendingImageUrl = '';
            imageUploadStatus.textContent = '업로드 중...';
            attachBtn.style.opacity = '0.5';

            var formData = new FormData();
            formData.append('image', file);

            fetch('/api/chat/upload', { method: 'POST', body: formData })
                .then(function(r) { return r.json(); })
                .then(function(d) {
                    if (d.ok && d.url) {
                        pendingImageUrl = d.url;
                        imageUploadStatus.textContent = '첨부 완료';
                    } else {
                        imageUploadStatus.textContent = d.message || '업로드 실패';
                        setTimeout(clearImagePreview, 2000);
                    }
                })
                .catch(function() {
                    imageUploadStatus.textContent = '업로드 실패';
                    setTimeout(clearImagePreview, 2000);
                })
                .finally(function() {
                    isUploading = false;
                    attachBtn.style.opacity = '1';
                    chatFileInput.value = '';
                });
        });
    }

    if (imagePreviewRemove) {
        imagePreviewRemove.addEventListener('click', clearImagePreview);
    }

    function clearImagePreview() {
        pendingImageUrl = '';
        imagePreview.style.display = 'none';
        imagePreviewImg.src = '';
        imageUploadStatus.textContent = '';
        chatFileInput.value = '';
    }

    // ──────── 메시지 전송 ────────

    function sendMessage() {
        var message = chatInput.value.trim();
        var imageUrl = pendingImageUrl;

        // 텍스트도 이미지도 없으면 무시
        if (!message && !imageUrl) return;
        // 아직 업로드 중이면 대기
        if (isUploading) return;

        // 이미지가 있으면 [IMG:url] 태그 추가
        var fullMessage = message;
        if (imageUrl) {
            fullMessage = (message ? message + '\n' : '') + '[IMG:' + imageUrl + ']';
        }

        disableAllButtons();
        appendMessage('user', fullMessage);
        chatInput.value = '';
        chatInput.style.height = 'auto';
        chatInput.classList.remove('scrollable');
        clearImagePreview();
        scrollToBottom();

        socket.emit('user_message', {
            name: user.name,
            phone: user.phone,
            message: fullMessage
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
                if (isEmbed) btn.target = '_top';
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

    function driveToEmbed(url) {
        var match = url.match(/\/file\/d\/([^\/]+)/);
        if (match) {
            return 'https://drive.google.com/thumbnail?id=' + match[1] + '&sz=w400';
        }
        return url;
    }

    function escapeHtml(text) {
        // [IMG:url] 태그 추출
        var images = [];
        var cleanText = (text || '').replace(/\[IMG:(.*?)\]/g, function(m, url) {
            images.push(url);
            return '';
        });

        var div = document.createElement('div');
        div.textContent = cleanText;
        var escaped = div.innerHTML.replace(/\n/g, '<br>');
        var linkTarget = isEmbed ? '_top' : '_blank';
        escaped = escaped.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="' + linkTarget + '" rel="noopener" style="color:#4a90d9;text-decoration:underline;">$1</a>');

        // 이미지 렌더링
        var imageHtml = '';
        images.forEach(function(url) {
            var imgUrl = driveToEmbed(url);
            imageHtml += '<div style="margin:8px 0;"><img src="' + imgUrl + '" style="max-width:100%;border-radius:8px;" onerror="this.parentNode.style.display=\'none\'" loading="lazy" /></div>';
        });

        return imageHtml + escaped;
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
