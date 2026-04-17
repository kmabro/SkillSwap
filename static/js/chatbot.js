(function () {
    "use strict";

    function ready(fn) {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", fn);
            return;
        }
        fn();
    }

    ready(function () {
        var root = document.querySelector(".skillswap-chat");
        if (!root) {
            return;
        }

        var STORAGE_KEYS = {
            open: "skillswap_chat_open",
            messages: "skillswap_chat_messages"
        };
        var MAX_STORED_MESSAGES = 80;

        var launcher = document.getElementById("chat-launcher");
        var chatWindow = document.getElementById("chat-window");
        var closeBtn = document.getElementById("close-btn");
        var refreshBtn = document.getElementById("refresh-btn");
        var minimizeBtn = document.getElementById("minimize-btn");
        var chatBody = document.getElementById("chat-body");
        var msgInput = document.getElementById("msg-input");
        var sendBtn = document.getElementById("send-btn");
        var emptyState = document.getElementById("empty-state");
        var typingEl = document.getElementById("typing-indicator");

        if (!launcher || !chatWindow || !closeBtn || !refreshBtn || !minimizeBtn || !chatBody || !msgInput || !sendBtn || !typingEl) {
            return;
        }

        var isOpen = false;
        var hasMessages = false;
        var defaultWindowStyle = {
            left: "",
            top: "",
            right: "1rem",
            bottom: "1rem"
        };
        var dragState = {
            active: false,
            pointerX: 0,
            pointerY: 0,
            rectLeft: 0,
            rectTop: 0
        };

        function timeNow() {
            return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }

        function scrollToBottom() {
            chatBody.scrollTop = chatBody.scrollHeight;
        }

        function openChat() {
            isOpen = true;
            chatWindow.classList.add("visible");
            launcher.classList.add("open");
            launcher.setAttribute("aria-expanded", "true");
            resetWindowPosition();
            persistOpenState();
            msgInput.focus();
        }

        function minimizeChat() {
            isOpen = false;
            chatWindow.classList.remove("visible");
            launcher.classList.remove("open");
            launcher.setAttribute("aria-expanded", "false");
            resetWindowPosition();
            persistOpenState();
        }

        async function resetAssistantSession() {
            try {
                await fetch("/api/chat/reset", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-Requested-With": "XMLHttpRequest"
                    },
                    credentials: "same-origin",
                    cache: "no-store"
                });
            } catch (_err) {
            }
        }

        async function refreshChat() {
            clearConversation();
            await resetAssistantSession();
            if (isOpen) {
                msgInput.focus();
            }
        }

        async function closeChat() {
            isOpen = false;
            chatWindow.classList.remove("visible");
            launcher.classList.remove("open");
            launcher.setAttribute("aria-expanded", "false");
            resetWindowPosition();
            clearConversation();
            persistOpenState();
            if (!defaultAllowed) {
                root.classList.remove("chat-enabled");
            }
            await resetAssistantSession();
        }

        function toggleChat() {
            if (!isOpen) {
                openChat();
            }
        }

        function clamp(num, min, max) {
            return Math.min(Math.max(num, min), max);
        }

        function resetWindowPosition() {
            chatWindow.style.left = defaultWindowStyle.left;
            chatWindow.style.top = defaultWindowStyle.top;
            chatWindow.style.right = defaultWindowStyle.right;
            chatWindow.style.bottom = defaultWindowStyle.bottom;
        }

        function onDragMove(event) {
            if (!dragState.active) {
                return;
            }
            var dx = event.clientX - dragState.pointerX;
            var dy = event.clientY - dragState.pointerY;
            var nextX = dragState.rectLeft + dx;
            var nextY = dragState.rectTop + dy;
            var maxX = window.innerWidth - chatWindow.offsetWidth;
            var maxY = window.innerHeight - chatWindow.offsetHeight;
            var clampedX = clamp(nextX, 0, Math.max(0, maxX));
            var clampedY = clamp(nextY, 0, Math.max(0, maxY));

            chatWindow.style.left = clampedX + "px";
            chatWindow.style.top = clampedY + "px";
            chatWindow.style.right = "auto";
            chatWindow.style.bottom = "auto";
        }

        function onDragEnd() {
            if (!dragState.active) {
                return;
            }
            dragState.active = false;
            document.removeEventListener("mousemove", onDragMove);
            document.removeEventListener("mouseup", onDragEnd);
        }

        function onDragStart(event) {
            if (event.target && event.target.closest && event.target.closest(".chat-tool")) {
                return;
            }
            dragState.active = true;
            dragState.pointerX = event.clientX;
            dragState.pointerY = event.clientY;

            var rect = chatWindow.getBoundingClientRect();
            dragState.rectLeft = rect.left;
            dragState.rectTop = rect.top;

            chatWindow.style.left = rect.left + "px";
            chatWindow.style.top = rect.top + "px";
            chatWindow.style.right = "auto";
            chatWindow.style.bottom = "auto";

            document.addEventListener("mousemove", onDragMove);
            document.addEventListener("mouseup", onDragEnd);
            event.preventDefault();
        }

        function appendMessage(text, role, providedTime, skipStore) {
            if (!hasMessages && emptyState) {
                emptyState.style.display = "none";
                hasMessages = true;
            }

            var group = document.createElement("div");
            group.className = "msg-group " + role;

            var bubble = document.createElement("div");
            bubble.className = "chat-bubble";
            bubble.textContent = text;

            var meta = document.createElement("div");
            meta.className = "chat-meta";
            var renderedTime = providedTime || timeNow();
            meta.textContent = renderedTime;

            group.appendChild(bubble);
            group.appendChild(meta);

            chatBody.insertBefore(group, typingEl);
            scrollToBottom();

            if (!skipStore) {
                persistConversationMessage(text, role, renderedTime);
            }
        }

        function clearConversation() {
            var groups = chatBody.querySelectorAll(".msg-group");
            for (var i = 0; i < groups.length; i += 1) {
                groups[i].remove();
            }
            hasMessages = false;
            if (emptyState) {
                emptyState.style.display = "";
            }
            hideTyping();
            msgInput.value = "";
            sendBtn.disabled = true;
            clearPersistedConversation();
        }

        function safeStorageSet(key, value) {
            try {
                window.sessionStorage.setItem(key, value);
            } catch (_err) {
            }
        }

        function safeStorageGet(key) {
            try {
                return window.sessionStorage.getItem(key);
            } catch (_err) {
                return null;
            }
        }

        function safeStorageRemove(key) {
            try {
                window.sessionStorage.removeItem(key);
            } catch (_err) {
            }
        }

        var isAuthenticated = root.getAttribute("data-authenticated") === "1";
        var role = (root.getAttribute("data-role") || "guest").toLowerCase();
        var endpoint = (root.getAttribute("data-endpoint") || "").toLowerCase();
        var defaultAllowed = (role === "admin" || role === "super_admin")
            ? endpoint === "admin_dashboard"
            : endpoint === "index";
        var persistedOpen = safeStorageGet(STORAGE_KEYS.open) === "1";

        if (!isAuthenticated) {
            safeStorageRemove(STORAGE_KEYS.open);
            safeStorageRemove(STORAGE_KEYS.messages);
            return;
        }

        if (!(defaultAllowed || persistedOpen)) {
            return;
        }

        root.classList.add("chat-enabled");

        function persistOpenState() {
            safeStorageSet(STORAGE_KEYS.open, isOpen ? "1" : "0");
        }

        function loadPersistedMessages() {
            var raw = safeStorageGet(STORAGE_KEYS.messages);
            if (!raw) {
                return [];
            }
            try {
                var parsed = JSON.parse(raw);
                if (!Array.isArray(parsed)) {
                    return [];
                }
                return parsed.filter(function (item) {
                    return item && typeof item.text === "string" && (item.role === "user" || item.role === "bot");
                });
            } catch (_err) {
                return [];
            }
        }

        function persistConversationMessage(text, role, renderedTime) {
            var existing = loadPersistedMessages();
            existing.push({
                text: text,
                role: role,
                time: renderedTime || timeNow()
            });

            if (existing.length > MAX_STORED_MESSAGES) {
                existing = existing.slice(existing.length - MAX_STORED_MESSAGES);
            }

            safeStorageSet(STORAGE_KEYS.messages, JSON.stringify(existing));
        }

        function clearPersistedConversation() {
            safeStorageRemove(STORAGE_KEYS.messages);
        }

        function restorePersistedConversation() {
            var restored = loadPersistedMessages();
            if (!restored.length) {
                return;
            }

            for (var i = 0; i < restored.length; i += 1) {
                appendMessage(restored[i].text, restored[i].role, restored[i].time, true);
            }
        }

        function restoreOpenState() {
            var raw = safeStorageGet(STORAGE_KEYS.open);
            if (raw === "1") {
                openChat();
            }
        }

        function showTyping() {
            typingEl.classList.add("visible");
            scrollToBottom();
        }

        function hideTyping() {
            typingEl.classList.remove("visible");
        }

        function typingDelay() {
            return new Promise(function (resolve) {
                var delayMs = 300 + Math.floor(Math.random() * 501);
                window.setTimeout(resolve, delayMs);
            });
        }

        async function requestAssistantReply(message) {
            var controller = new AbortController();
            var timeoutId = window.setTimeout(function () {
                controller.abort();
            }, 20000);

            console.log("[SkillSwapChat] Sending message:", message);

            var response;
            try {
                response = await fetch("/api/chat", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-Requested-With": "XMLHttpRequest"
                    },
                    credentials: "same-origin",
                    cache: "no-store",
                    signal: controller.signal,
                    body: JSON.stringify({ message: message })
                });
            } catch (err) {
                if (err && err.name === "AbortError") {
                    throw new Error("Response is taking longer than expected. Please try again.");
                }
                throw err;
            } finally {
                window.clearTimeout(timeoutId);
            }

            var data = {};
            try {
                data = await response.json();
            } catch (_err) {
                data = {};
            }

            if (!response.ok) {
                if (response.status === 429) {
                    throw new Error("Service is temporarily busy. Please try again shortly.");
                }
                if (data && data.response) {
                    throw new Error(data.response);
                }
                throw new Error("Sorry, something went wrong. Please try again.");
            }

            var reply = (data.response || "").trim();
            if (!reply) {
                throw new Error("Sorry, something went wrong. Please try again.");
            }

            console.log("[SkillSwapChat] Received response:", reply);
            return {
                response: reply,
                action: data.action || null
            };
        }

        function handleAssistantAction(action) {
            if (!action || typeof action !== "object") {
                return;
            }

            if (action.type === "navigate" && action.url) {
                window.location.href = action.url;
                return;
            }

            if (action.type === "download" && action.url) {
                var target = action.url;
                if (target.indexOf("http") !== 0) {
                    target = window.location.origin + target;
                }
                window.open(target, "_blank", "noopener");
            }
        }

        async function handleSend(textOverride) {
            var text = (typeof textOverride === "string" ? textOverride : msgInput.value).trim();
            if (!text) {
                return;
            }
            if (text.length > 500) {
                appendMessage("Please keep your message under 500 characters.", "bot");
                return;
            }

            appendMessage(text, "user");
            msgInput.value = "";
            sendBtn.disabled = true;
            showTyping();

            try {
                var result = await requestAssistantReply(text);
                await typingDelay();
                hideTyping();
                appendMessage(result.response, "bot");
                handleAssistantAction(result.action);
            } catch (err) {
                await typingDelay();
                hideTyping();
                appendMessage(err.message || "Something went wrong. Please try again.", "bot");
            } finally {
                sendBtn.disabled = msgInput.value.trim() === "";
                msgInput.focus();
            }
        }

        launcher.addEventListener("click", toggleChat);
        closeBtn.addEventListener("click", function () {
            closeChat();
        });
        refreshBtn.addEventListener("click", function () {
            refreshChat();
        });
        minimizeBtn.addEventListener("click", function () {
            minimizeChat();
        });
        var headerEl = chatWindow.querySelector(".chat-header");
        if (headerEl) {
            headerEl.addEventListener("mousedown", onDragStart);
        }
        sendBtn.addEventListener("click", function () {
            handleSend();
        });

        msgInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                handleSend();
            }
        });

        msgInput.addEventListener("input", function () {
            sendBtn.disabled = msgInput.value.trim() === "";
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && isOpen) {
                closeChat();
            }
        });

        sendBtn.disabled = true;
        resetWindowPosition();
        restorePersistedConversation();
        restoreOpenState();

        window.addEventListener("resize", function () {
            if (!isOpen) {
                resetWindowPosition();
                return;
            }
            var rect = chatWindow.getBoundingClientRect();
            var maxX = window.innerWidth - chatWindow.offsetWidth;
            var maxY = window.innerHeight - chatWindow.offsetHeight;
            var clampedX = clamp(rect.left, 0, Math.max(0, maxX));
            var clampedY = clamp(rect.top, 0, Math.max(0, maxY));
            chatWindow.style.left = clampedX + "px";
            chatWindow.style.top = clampedY + "px";
            chatWindow.style.right = "auto";
            chatWindow.style.bottom = "auto";
        });

        window.SkillSwapChat = {
            open: openChat,
            close: closeChat,
            minimize: minimizeChat,
            refresh: refreshChat,
            toggle: toggleChat,
            ask: handleSend
        };
    });
})();
