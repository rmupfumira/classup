/**
 * ClassUp v2 - Messages Module
 *
 * Handles thread reply submission, auto-scroll, compose cascading selects,
 * and auto-resizing textarea.
 */

const Messages = {
    /**
     * Initialize the thread (chat) view.
     */
    initThread(studentId, otherUserId, currentUserId) {
        const chatContainer = document.getElementById('chat-messages');
        const replyForm = document.getElementById('reply-form');
        const replyBody = document.getElementById('reply-body');
        const sendBtn = document.getElementById('send-btn');

        // Auto-scroll to bottom
        if (chatContainer) {
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        // Auto-resize textarea
        if (replyBody) {
            replyBody.addEventListener('input', () => this.autoResize(replyBody));
        }

        // Handle reply submission
        if (replyForm) {
            replyForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                const body = replyBody.value.trim();
                if (!body) return;

                sendBtn.disabled = true;
                try {
                    const result = await ClassUp.fetch(
                        `/api/v1/messages/thread/${studentId}/${otherUserId}/reply`,
                        { method: 'POST', body: { body } }
                    );

                    if (result && result.data) {
                        this.appendMessage(result.data, currentUserId);
                        replyBody.value = '';
                        this.autoResize(replyBody);

                        // Mark as read
                        ClassUp.fetch(
                            `/api/v1/messages/thread/${studentId}/${otherUserId}/read`,
                            { method: 'PUT' }
                        ).catch(() => {});
                    }
                } catch (error) {
                    // ClassUp.fetch already shows toast on error
                } finally {
                    sendBtn.disabled = false;
                    replyBody.focus();
                }
            });

            // Submit on Enter (without Shift)
            replyBody.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    replyForm.dispatchEvent(new Event('submit'));
                }
            });
        }
    },

    /**
     * Append a message bubble to the chat area.
     */
    appendMessage(data, currentUserId) {
        const chatContainer = document.getElementById('chat-messages');
        if (!chatContainer) return;

        // Remove empty state if present
        const emptyState = chatContainer.querySelector('.text-center');
        if (emptyState) emptyState.remove();

        const isOwn = data.sender_id === currentUserId;
        const time = new Date(data.created_at).toLocaleDateString(undefined, {
            month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
        });

        const wrapper = document.createElement('div');
        wrapper.className = `flex ${isOwn ? 'justify-end' : 'justify-start'}`;
        wrapper.innerHTML = `
            <div class="max-w-[80%] sm:max-w-[70%]">
                ${!isOwn && data.sender_name ? `<p class="text-xs text-neutral-400 mb-1 ml-1">${ClassUp.escapeHtml(data.sender_name.split(' ')[0])}</p>` : ''}
                <div class="${isOwn ? 'bg-primary-500 text-white' : 'bg-white border border-neutral-200 text-neutral-800'} px-3.5 py-2.5 rounded-2xl shadow-sm">
                    <p class="text-sm whitespace-pre-wrap break-words">${ClassUp.escapeHtml(data.body)}</p>
                </div>
                <p class="text-[10px] mt-1 ${isOwn ? 'text-right mr-1' : 'ml-1'} text-neutral-400">${time}</p>
            </div>
        `;

        chatContainer.appendChild(wrapper);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    },

    /**
     * Initialize the compose form with cascading selects.
     */
    initCompose(recipientData) {
        const studentSelect = document.getElementById('student-select');
        const recipientSelect = document.getElementById('recipient-select');
        const composeForm = document.getElementById('compose-form');
        const sendBtn = document.getElementById('send-btn');

        if (!studentSelect || !recipientSelect) return;

        // Build a lookup: student_id -> recipients[]
        const recipientMap = {};
        for (const item of recipientData) {
            recipientMap[item.student_id] = item.recipients || [];
        }

        // Update recipients when student changes
        studentSelect.addEventListener('change', () => {
            const sid = studentSelect.value;
            recipientSelect.innerHTML = '';

            if (!sid) {
                recipientSelect.innerHTML = '<option value="">Select a student first...</option>';
                recipientSelect.disabled = true;
                return;
            }

            const recipients = recipientMap[sid] || [];
            if (recipients.length === 0) {
                recipientSelect.innerHTML = '<option value="">No recipients available</option>';
                recipientSelect.disabled = true;
                return;
            }

            recipientSelect.disabled = false;
            recipientSelect.innerHTML = '<option value="">Select recipient...</option>';
            for (const r of recipients) {
                const opt = document.createElement('option');
                opt.value = r.id;
                opt.textContent = `${r.name} (${r.role.toLowerCase()})`;
                recipientSelect.appendChild(opt);
            }

            // Auto-select if only one recipient
            if (recipients.length === 1) {
                recipientSelect.value = recipients[0].id;
            }
        });

        // Trigger change if pre-selected
        if (studentSelect.value) {
            studentSelect.dispatchEvent(new Event('change'));
        }

        // Handle form submission
        if (composeForm) {
            composeForm.addEventListener('submit', async (e) => {
                e.preventDefault();

                const studentId = studentSelect.value;
                const recipientId = recipientSelect.value;
                const body = document.getElementById('message-body').value.trim();

                if (!studentId || !recipientId || !body) {
                    ClassUp.toast('Please fill in all fields', 'warning');
                    return;
                }

                sendBtn.disabled = true;
                sendBtn.textContent = 'Sending...';

                try {
                    const result = await ClassUp.fetch('/api/v1/messages', {
                        method: 'POST',
                        body: {
                            student_id: studentId,
                            recipient_id: recipientId,
                            body: body,
                        }
                    });

                    if (result) {
                        ClassUp.toast('Message sent', 'success');
                        // Redirect to the thread
                        window.location.href = `/messages/thread/${studentId}/${recipientId}`;
                    }
                } catch (error) {
                    sendBtn.disabled = false;
                    sendBtn.textContent = 'Send Message';
                }
            });
        }
    },

    /**
     * Auto-resize a textarea up to max-height.
     */
    autoResize(textarea) {
        textarea.style.height = 'auto';
        const maxHeight = 120;
        textarea.style.height = Math.min(textarea.scrollHeight, maxHeight) + 'px';
    }
};
