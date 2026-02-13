/**
 * ClassUp v2 - Global JavaScript Utilities
 *
 * This file provides core functionality for the application:
 * - Authenticated fetch wrapper with error handling
 * - Toast notifications
 * - Confirm dialogs
 * - Debounce for search inputs
 * - Date/time formatting
 * - WebSocket connection (placeholder)
 */

const ClassUp = {
    /**
     * Authenticated fetch wrapper with CSRF and error handling
     * @param {string} url - The URL to fetch
     * @param {object} options - Fetch options
     * @returns {Promise<object>} - Parsed JSON response
     */
    async fetch(url, options = {}) {
        const defaults = {
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            credentials: 'same-origin'  // Sends cookies automatically
        };

        // Merge headers
        const mergedOptions = {
            ...defaults,
            ...options,
            headers: {
                ...defaults.headers,
                ...(options.headers || {})
            }
        };

        // If body is an object, stringify it
        if (mergedOptions.body && typeof mergedOptions.body === 'object') {
            mergedOptions.body = JSON.stringify(mergedOptions.body);
        }

        try {
            const response = await fetch(url, mergedOptions);

            // Handle 401 Unauthorized - redirect to login
            if (response.status === 401) {
                window.location.href = '/login?message=session_expired';
                return;
            }

            // Handle 403 Forbidden
            if (response.status === 403) {
                ClassUp.toast('You do not have permission to perform this action', 'error');
                throw new Error('Forbidden');
            }

            // Parse JSON response
            const data = await response.json();

            // Handle error responses
            if (!response.ok) {
                const errorMessage = data.message || 'Something went wrong';
                ClassUp.toast(errorMessage, 'error');
                throw new Error(errorMessage);
            }

            return data;
        } catch (error) {
            if (error.name === 'TypeError' && error.message.includes('fetch')) {
                ClassUp.toast('Network error. Please check your connection.', 'error');
            }
            throw error;
        }
    },

    /**
     * Display a toast notification
     * @param {string} message - The message to display
     * @param {string} type - Type: 'success', 'error', 'warning', 'info'
     * @param {number} duration - Duration in milliseconds
     */
    toast(message, type = 'success', duration = 4000) {
        const container = document.getElementById('toast-container');
        if (!container) {
            console.warn('Toast container not found');
            return;
        }

        const colors = {
            success: 'bg-green-50 border-green-500 text-green-800',
            error: 'bg-red-50 border-red-500 text-red-800',
            warning: 'bg-amber-50 border-amber-500 text-amber-800',
            info: 'bg-blue-50 border-blue-500 text-blue-800'
        };

        const icons = {
            success: `<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/>
            </svg>`,
            error: `<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/>
            </svg>`,
            warning: `<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/>
            </svg>`,
            info: `<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"/>
            </svg>`
        };

        const toast = document.createElement('div');
        toast.className = `flex items-center p-4 rounded-lg border-l-4 shadow-md ${colors[type]}
                          transform transition-all duration-300 translate-x-full toast-enter`;
        toast.innerHTML = `
            <span class="flex-shrink-0 mr-3">${icons[type]}</span>
            <span class="flex-1 text-sm font-medium">${this.escapeHtml(message)}</span>
            <button class="ml-4 flex-shrink-0 text-gray-400 hover:text-gray-600" onclick="this.parentElement.remove()">
                <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                    <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/>
                </svg>
            </button>
        `;

        container.appendChild(toast);

        // Animate in
        requestAnimationFrame(() => {
            toast.classList.remove('translate-x-full');
        });

        // Auto-remove after duration
        setTimeout(() => {
            toast.classList.add('translate-x-full');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    },

    /**
     * Display a confirm dialog
     * @param {string} message - The confirmation message
     * @param {object} options - Dialog options
     * @returns {Promise<boolean>} - True if confirmed, false otherwise
     */
    async confirm(message, options = {}) {
        const {
            title = 'Confirm',
            confirmText = 'Confirm',
            cancelText = 'Cancel',
            danger = false
        } = options;

        return new Promise((resolve) => {
            // Create modal backdrop
            const backdrop = document.createElement('div');
            backdrop.className = 'fixed inset-0 z-50 overflow-y-auto';
            backdrop.innerHTML = `
                <div class="flex min-h-screen items-center justify-center p-4">
                    <div class="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity" id="confirm-backdrop"></div>
                    <div class="relative transform overflow-hidden rounded-xl bg-white shadow-xl transition-all sm:w-full sm:max-w-lg">
                        <div class="bg-white px-6 py-5">
                            <div class="flex items-start">
                                <div class="flex-shrink-0 flex items-center justify-center h-12 w-12 rounded-full ${danger ? 'bg-red-100' : 'bg-primary-100'}">
                                    ${danger ? `
                                        <svg class="h-6 w-6 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
                                        </svg>
                                    ` : `
                                        <svg class="h-6 w-6 text-primary-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
                                        </svg>
                                    `}
                                </div>
                                <div class="ml-4 mt-0.5">
                                    <h3 class="text-lg font-semibold text-gray-900">${this.escapeHtml(title)}</h3>
                                    <p class="mt-2 text-sm text-gray-500">${this.escapeHtml(message)}</p>
                                </div>
                            </div>
                        </div>
                        <div class="bg-gray-50 px-6 py-4 flex justify-end space-x-3">
                            <button type="button" id="confirm-cancel" class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-primary-500">
                                ${this.escapeHtml(cancelText)}
                            </button>
                            <button type="button" id="confirm-ok" class="px-4 py-2 text-sm font-medium text-white rounded-lg focus:outline-none focus:ring-2 focus:ring-offset-2 ${danger ? 'bg-red-600 hover:bg-red-700 focus:ring-red-500' : 'bg-primary-600 hover:bg-primary-700 focus:ring-primary-500'}">
                                ${this.escapeHtml(confirmText)}
                            </button>
                        </div>
                    </div>
                </div>
            `;

            document.body.appendChild(backdrop);
            document.body.classList.add('overflow-hidden');

            const cleanup = () => {
                backdrop.remove();
                document.body.classList.remove('overflow-hidden');
            };

            // Handle button clicks
            backdrop.querySelector('#confirm-ok').addEventListener('click', () => {
                cleanup();
                resolve(true);
            });

            backdrop.querySelector('#confirm-cancel').addEventListener('click', () => {
                cleanup();
                resolve(false);
            });

            // Handle backdrop click
            backdrop.querySelector('#confirm-backdrop').addEventListener('click', () => {
                cleanup();
                resolve(false);
            });

            // Handle escape key
            const handleEscape = (e) => {
                if (e.key === 'Escape') {
                    document.removeEventListener('keydown', handleEscape);
                    cleanup();
                    resolve(false);
                }
            };
            document.addEventListener('keydown', handleEscape);

            // Focus the cancel button
            backdrop.querySelector('#confirm-cancel').focus();
        });
    },

    /**
     * Debounce a function call
     * @param {Function} fn - The function to debounce
     * @param {number} delay - Delay in milliseconds
     * @returns {Function} - Debounced function
     */
    debounce(fn, delay = 300) {
        let timer;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    },

    /**
     * Throttle a function call
     * @param {Function} fn - The function to throttle
     * @param {number} limit - Time limit in milliseconds
     * @returns {Function} - Throttled function
     */
    throttle(fn, limit = 100) {
        let lastCall = 0;
        return function (...args) {
            const now = Date.now();
            if (now - lastCall >= limit) {
                lastCall = now;
                return fn.apply(this, args);
            }
        };
    },

    /**
     * Format a date string
     * @param {string} dateString - ISO date string
     * @param {string} format - Format type: 'short', 'long', 'relative'
     * @returns {string} - Formatted date
     */
    formatDate(dateString, format = 'short') {
        if (!dateString) return '';

        const date = new Date(dateString);
        const now = new Date();

        if (format === 'relative') {
            return this.timeAgo(dateString);
        }

        const options = format === 'long'
            ? { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' }
            : { year: 'numeric', month: 'short', day: 'numeric' };

        return date.toLocaleDateString(undefined, options);
    },

    /**
     * Format a time string
     * @param {string} dateString - ISO date string
     * @returns {string} - Formatted time
     */
    formatTime(dateString) {
        if (!dateString) return '';
        const date = new Date(dateString);
        return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    },

    /**
     * Get relative time string (e.g., "2 hours ago")
     * @param {string} dateString - ISO date string
     * @returns {string} - Relative time
     */
    timeAgo(dateString) {
        if (!dateString) return '';

        const date = new Date(dateString);
        const now = new Date();
        const seconds = Math.floor((now - date) / 1000);

        const intervals = [
            { label: 'year', seconds: 31536000 },
            { label: 'month', seconds: 2592000 },
            { label: 'week', seconds: 604800 },
            { label: 'day', seconds: 86400 },
            { label: 'hour', seconds: 3600 },
            { label: 'minute', seconds: 60 }
        ];

        for (const interval of intervals) {
            const count = Math.floor(seconds / interval.seconds);
            if (count >= 1) {
                return `${count} ${interval.label}${count > 1 ? 's' : ''} ago`;
            }
        }

        return 'Just now';
    },

    /**
     * Escape HTML to prevent XSS
     * @param {string} text - Text to escape
     * @returns {string} - Escaped text
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    /**
     * Copy text to clipboard
     * @param {string} text - Text to copy
     * @returns {Promise<boolean>} - Success status
     */
    async copyToClipboard(text) {
        try {
            await navigator.clipboard.writeText(text);
            this.toast('Copied to clipboard', 'success', 2000);
            return true;
        } catch (err) {
            this.toast('Failed to copy to clipboard', 'error');
            return false;
        }
    },

    /**
     * Format a file size
     * @param {number} bytes - Size in bytes
     * @returns {string} - Formatted size
     */
    formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    },

    /**
     * Initialize global event handlers
     */
    init() {
        // Handle data-action buttons (for inline actions without page reload)
        document.addEventListener('click', async (e) => {
            const actionBtn = e.target.closest('[data-action]');
            if (!actionBtn) return;

            e.preventDefault();
            const action = actionBtn.dataset.action;
            const url = actionBtn.dataset.url;
            const confirmMsg = actionBtn.dataset.confirm;

            if (confirmMsg) {
                const confirmed = await this.confirm(confirmMsg, {
                    danger: action === 'delete',
                    confirmText: action === 'delete' ? 'Delete' : 'Confirm'
                });
                if (!confirmed) return;
            }

            try {
                actionBtn.disabled = true;
                const method = action === 'delete' ? 'DELETE' : 'POST';
                const result = await this.fetch(url, { method });

                if (result.message) {
                    this.toast(result.message, 'success');
                }

                // Refresh the page or remove element based on action
                if (action === 'delete') {
                    const row = actionBtn.closest('tr, .card, [data-item]');
                    if (row) {
                        row.remove();
                    } else {
                        window.location.reload();
                    }
                } else {
                    window.location.reload();
                }
            } catch (error) {
                console.error('Action failed:', error);
            } finally {
                actionBtn.disabled = false;
            }
        });

        // Handle modal triggers
        document.addEventListener('click', (e) => {
            const modalTrigger = e.target.closest('[data-modal]');
            if (!modalTrigger) return;

            e.preventDefault();
            const modalId = modalTrigger.dataset.modal;
            const modal = document.getElementById(modalId);
            if (modal) {
                modal.showModal();
            }
        });

        // Handle modal close buttons
        document.addEventListener('click', (e) => {
            if (e.target.closest('[data-close-modal]')) {
                const modal = e.target.closest('dialog');
                if (modal) {
                    modal.close();
                }
            }
        });

        // Handle form submissions with data-ajax attribute
        document.addEventListener('submit', async (e) => {
            const form = e.target;
            if (!form.dataset.ajax) return;

            e.preventDefault();
            const submitBtn = form.querySelector('[type="submit"]');
            const originalText = submitBtn?.textContent;

            try {
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.innerHTML = `
                        <svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-white inline" fill="none" viewBox="0 0 24 24">
                            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                        </svg>
                        Processing...
                    `;
                }

                const formData = new FormData(form);
                const data = Object.fromEntries(formData.entries());
                const method = form.method?.toUpperCase() || 'POST';
                const url = form.action || window.location.pathname;

                const result = await this.fetch(url, { method, body: data });

                if (result.message) {
                    this.toast(result.message, 'success');
                }

                // Close modal if form is in a dialog
                const modal = form.closest('dialog');
                if (modal) {
                    modal.close();
                }

                // Redirect if specified
                if (form.dataset.redirect) {
                    window.location.href = form.dataset.redirect;
                } else if (result.redirect) {
                    window.location.href = result.redirect;
                } else {
                    window.location.reload();
                }
            } catch (error) {
                console.error('Form submission failed:', error);
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = originalText;
                }
            }
        });

        // Show URL message as toast (e.g., after redirect)
        const urlParams = new URLSearchParams(window.location.search);
        const message = urlParams.get('message');
        if (message) {
            const messageMap = {
                'session_expired': { text: 'Your session has expired. Please log in again.', type: 'warning' },
                'logged_out': { text: 'You have been logged out.', type: 'info' },
                'password_reset': { text: 'Your password has been reset. Please log in.', type: 'success' }
            };
            const msg = messageMap[message] || { text: message, type: 'info' };
            setTimeout(() => this.toast(msg.text, msg.type), 100);

            // Clean URL
            const newUrl = window.location.pathname;
            window.history.replaceState({}, document.title, newUrl);
        }

        console.log('ClassUp initialized');
    }
};

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => ClassUp.init());
} else {
    ClassUp.init();
}

// Export for module systems (if needed)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ClassUp;
}
