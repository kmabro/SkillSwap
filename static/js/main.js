document.addEventListener("DOMContentLoaded", function () {
    var isAuthenticated = document.body && document.body.getAttribute("data-authenticated") === "1";
    if (isAuthenticated) {
        function sendActivityHeartbeat() {
            if (document.hidden) {
                return;
            }
            fetch("/update-activity", {
                method: "POST",
                headers: {
                    "X-Requested-With": "XMLHttpRequest"
                },
                keepalive: true,
                credentials: "same-origin"
            }).catch(function () {});
        }

        sendActivityHeartbeat();
        setInterval(sendActivityHeartbeat, 10000);
        document.addEventListener("visibilitychange", function () {
            if (!document.hidden) {
                sendActivityHeartbeat();
            }
        });
    }

    var navToggle = document.getElementById("navToggle");
    var siteNav = document.getElementById("siteNav");

    if (navToggle && siteNav) {
        navToggle.addEventListener("click", function () {
            siteNav.classList.toggle("open");
        });

        siteNav.querySelectorAll("a").forEach(function (link) {
            link.addEventListener("click", function () {
                siteNav.classList.remove("open");
            });
        });
    }

    var notifBell = document.getElementById("notifBell");
    var notifDropdown = document.getElementById("notifDropdown");
    if (notifBell && notifDropdown) {
        notifBell.addEventListener("click", function (event) {
            event.stopPropagation();
            notifDropdown.classList.toggle("open");
        });
        document.addEventListener("click", function (event) {
            if (!notifDropdown.contains(event.target) && !notifBell.contains(event.target)) {
                notifDropdown.classList.remove("open");
            }
        });
    }

    var profileMenuToggle = document.getElementById("profileMenuToggle");
    var profileDropdown = document.getElementById("profileDropdown");
    if (profileMenuToggle && profileDropdown) {
        profileMenuToggle.addEventListener("click", function (event) {
            event.stopPropagation();
            var isOpen = profileDropdown.classList.toggle("open");
            profileMenuToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
        });

        profileDropdown.addEventListener("click", function (event) {
            event.stopPropagation();
        });

        document.addEventListener("click", function (event) {
            if (!profileDropdown.contains(event.target) && !profileMenuToggle.contains(event.target)) {
                profileDropdown.classList.remove("open");
                profileMenuToggle.setAttribute("aria-expanded", "false");
            }
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                profileDropdown.classList.remove("open");
                profileMenuToggle.setAttribute("aria-expanded", "false");
            }
        });
    }

    var navSearchForm = document.getElementById("navSearchForm");
    var navSearchToggle = document.getElementById("navSearchToggle");
    var navSearchInput = document.getElementById("navSearchInput");
    if (navSearchForm && navSearchToggle && navSearchInput) {
        navSearchToggle.addEventListener("click", function (event) {
            event.preventDefault();
            window.location.href = "/search";
        });
    }

    var topUsersList = document.getElementById("topUsersList");
    var topUsersViewMore = document.getElementById("topUsersViewMore");
    if (topUsersList && topUsersViewMore) {
        var isTopUsersLoading = false;

        function escapeHtml(value) {
            return String(value || "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/\"/g, "&quot;")
                .replace(/'/g, "&#39;");
        }

        function buildTopUserCard(user, isAuthenticated) {
            var article = document.createElement("article");
            article.className = "card top-user-card";

            var skillsHtml = "<span class=\"chip muted\">No skills listed</span>";
            if (Array.isArray(user.skills) && user.skills.length) {
                skillsHtml = user.skills
                    .map(function (skill) {
                        return "<span class=\"chip\">" + escapeHtml(skill) + "</span>";
                    })
                    .join(" ");
            }

            var profileButtonHtml = "";
            if (isAuthenticated && user.profile_url) {
                profileButtonHtml =
                    "<a class=\"btn tiny secondary\" href=\"" +
                    escapeHtml(user.profile_url) +
                    "\">View Profile</a>";
            }

            var locationLabel = String(user.location || "").trim() || "Location not set";
            var avatarUrl = String(user.profile_image_url || "").trim();
            var avatarHtml = "";
            if (avatarUrl) {
                avatarHtml =
                    "<img src=\"" +
                    escapeHtml(avatarUrl) +
                    "\" alt=\"" +
                    escapeHtml((user.name || "User") + " avatar") +
                    "\" loading=\"lazy\" onerror=\"this.onerror=null;this.src='/static/images/default-avatar.svg';\">";
            }

            article.innerHTML =
                "<div class=\"top-user-header\">" +
                "<div class=\"top-user-avatar\" aria-hidden=\"true\">" + avatarHtml + "</div>" +
                "<div class=\"top-user-meta\">" +
                "<h3>" + escapeHtml(user.name) + "</h3>" +
                "<div class=\"top-user-subline\">" +
                "<span class=\"top-user-handle muted\">@" + escapeHtml(user.username) + "</span>" +
                "<span class=\"top-user-status-pill\">" + escapeHtml(user.availability_label || "Available") + "</span>" +
                "<span class=\"top-user-rating-line\">★ " + Number(user.avg_rating || 0).toFixed(1) + "</span>" +
                "</div>" +
                "<p class=\"top-user-location muted\">" + escapeHtml(locationLabel) + "</p>" +
                "</div>" +
                "</div>" +
                "<div class=\"top-user-skills-wrap\"><p>Skills Offered:</p>" + skillsHtml + "</div>" +
                profileButtonHtml;

            return article;
        }

        topUsersViewMore.addEventListener("click", function () {
            if (isTopUsersLoading) {
                return;
            }
            isTopUsersLoading = true;
            topUsersViewMore.disabled = true;
            topUsersViewMore.textContent = "Loading...";

            var offset = parseInt(topUsersViewMore.getAttribute("data-offset"), 10) || 0;
            var limit = parseInt(topUsersViewMore.getAttribute("data-limit"), 10) || 6;
            var isAuthenticated = (topUsersViewMore.getAttribute("data-auth") || "0") === "1";

            fetch("/api/top-users?offset=" + encodeURIComponent(offset) + "&limit=" + encodeURIComponent(limit), {
                headers: {
                    "X-Requested-With": "XMLHttpRequest"
                }
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error("Could not load more users.");
                    }
                    return response.json();
                })
                .then(function (payload) {
                    var users = Array.isArray(payload.users) ? payload.users : [];
                    users.forEach(function (user) {
                        topUsersList.appendChild(buildTopUserCard(user, isAuthenticated));
                    });

                    var nextOffset = Number(payload.next_offset || (offset + users.length));
                    topUsersViewMore.setAttribute("data-offset", String(nextOffset));

                    if (!payload.has_more || users.length === 0) {
                        topUsersViewMore.remove();
                    } else {
                        topUsersViewMore.disabled = false;
                        topUsersViewMore.textContent = "View More";
                    }
                })
                .catch(function () {
                    topUsersViewMore.disabled = false;
                    topUsersViewMore.textContent = "Try Again";
                })
                .finally(function () {
                    isTopUsersLoading = false;
                });
        });
    }

    var feedbackViewMoreBtn = document.getElementById("feedbackViewMoreBtn");
    if (feedbackViewMoreBtn) {
        feedbackViewMoreBtn.addEventListener("click", function () {
            document.querySelectorAll(".dashboard-feedback-extra.review-hidden").forEach(function (item) {
                item.classList.remove("review-hidden");
            });
            feedbackViewMoreBtn.remove();
        });
    }

    document.querySelectorAll(".password-toggle[data-target]").forEach(function (toggleButton) {
        toggleButton.addEventListener("click", function () {
            var targetId = toggleButton.getAttribute("data-target");
            var passwordInput = targetId ? document.getElementById(targetId) : null;
            if (!passwordInput) {
                return;
            }

            var reveal = passwordInput.type === "password";
            passwordInput.type = reveal ? "text" : "password";
            toggleButton.classList.toggle("is-visible", reveal);
            toggleButton.setAttribute("aria-pressed", reveal ? "true" : "false");
            toggleButton.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
        });
    });

    var messagesBox = document.getElementById("messagesBox");
    if (messagesBox) {
        var chatUsername = messagesBox.getAttribute("data-chat-username");
        var currentUserId = parseInt(messagesBox.getAttribute("data-current-user-id"), 10);
        var chatPresenceLabel = document.getElementById("chatPresenceLabel");
        var chatForm = document.querySelector(".chat-form");
        var attachBtn = document.getElementById("attachBtn");
        var fileInput = document.getElementById("fileInput");
        var attachmentUrlInput = document.getElementById("attachmentUrlInput");
        var attachmentTypeInput = document.getElementById("attachmentTypeInput");
        var attachmentFileNameInput = document.getElementById("attachmentFileNameInput");
        var attachmentFileSizeInput = document.getElementById("attachmentFileSizeInput");
        var selectedFilesContainer = document.getElementById("selectedFilesContainer");
        var selectedFilesList = document.getElementById("selectedFilesList");
        var selectedFilesCount = document.getElementById("selectedFilesCount");
        var selectedFiles = [];
        var isSubmittingAttachment = false;
        var messageRows = messagesBox.querySelectorAll(".message-row[data-message-id]");
        var lastMessageEl = messageRows.length ? messageRows[messageRows.length - 1] : null;
        var lastMessageId = lastMessageEl ? parseInt(lastMessageEl.getAttribute("data-message-id"), 10) : 0;
        var lastSeenAckId = 0;
        var renderedMessageIds = new Set();

        messagesBox.querySelectorAll(".message-row[data-message-id]").forEach(function (row) {
            var id = parseInt(row.getAttribute("data-message-id"), 10) || 0;
            if (id > 0) {
                renderedMessageIds.add(id);
            }
        });

        messagesBox.querySelectorAll(".message-row.sent[data-message-id]").forEach(function (row) {
            var state = row.querySelector(".status");
            var id = parseInt(row.getAttribute("data-message-id"), 10) || 0;
            if (state && state.textContent.trim() === "Seen") {
                lastSeenAckId = Math.max(lastSeenAckId, id);
            }
        });

        function scrollToBottom() {
            window.requestAnimationFrame(function () {
                messagesBox.scrollTop = messagesBox.scrollHeight;
            });
        }

        function formatUnreadCount(count) {
            if (!count || count < 1) {
                return "";
            }
            return count > 9 ? "9+" : String(count);
        }

        function syncSidebarUnreadPills(unreadMap) {
            if (!unreadMap) {
                return;
            }
            document.querySelectorAll(".chat-user[data-chat-user-id]").forEach(function (chatUser) {
                var userId = chatUser.getAttribute("data-chat-user-id");
                var numeric = unreadMap[userId] || unreadMap[Number(userId)] || 0;
                var label = formatUnreadCount(numeric);
                var pill = chatUser.querySelector(".chat-unread-pill");

                if (label) {
                    if (!pill) {
                        pill = document.createElement("span");
                        pill.className = "notif-pill chat-unread-pill";
                        chatUser.appendChild(pill);
                    }
                    pill.textContent = label;
                } else if (pill) {
                    pill.remove();
                }
            });
        }

        function playIncomingSound() {
            try {
                var AudioCtx = window.AudioContext || window.webkitAudioContext;
                if (!AudioCtx) {
                    return;
                }
                var context = new AudioCtx();
                var oscillator = context.createOscillator();
                var gain = context.createGain();

                oscillator.type = "sine";
                oscillator.frequency.setValueAtTime(880, context.currentTime);
                gain.gain.setValueAtTime(0.0001, context.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.04, context.currentTime + 0.01);
                gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.16);

                oscillator.connect(gain);
                gain.connect(context.destination);
                oscillator.start(context.currentTime);
                oscillator.stop(context.currentTime + 0.17);
            } catch (_err) {
                // Ignore audio failures (browser policies, permissions, etc.).
            }
        }

        function markMessagesSeen(seenIds) {
            if (!Array.isArray(seenIds) || !seenIds.length) {
                return;
            }
            seenIds.forEach(function (id) {
                var row = messagesBox.querySelector('.message-row.sent[data-message-id="' + id + '"]');
                if (!row) {
                    return;
                }
                var meta = row.querySelector(".message-meta");
                var state = row.querySelector(".status");
                if (!state) {
                    state = document.createElement("span");
                    state.className = "status";
                    if (meta) {
                        meta.appendChild(state);
                    }
                }
                state.textContent = "Seen";
                lastSeenAckId = Math.max(lastSeenAckId, id);
            });
        }

        function removeEmptyPlaceholder() {
            var emptyEl = messagesBox.querySelector(".messages-empty");
            if (emptyEl) {
                emptyEl.remove();
            }
        }

        function createMessageRow(msg, isUnreadIncoming) {
            var row = document.createElement("div");
            var bubble = document.createElement("div");
            var content = document.createElement("div");
            var meta = document.createElement("div");
            var time = document.createElement("span");
            var isMine = msg.sender_id === currentUserId;
            var messageText = String(msg.text || msg.message || "");
            var isSystem = (msg.message_type || "") === "system" || messageText.indexOf("[SYSTEM]") === 0;

            row.className = "message-row " + (isSystem ? "system" : (isMine ? "sent" : "received"));
            if (!isMine && isUnreadIncoming) {
                row.className += " unread-highlight";
            }

            row.setAttribute("data-message-id", String(msg.message_id));
            row.setAttribute("data-message-type", msg.message_type || "user");

            bubble.className = "message-bubble";
            content.className = "message-content";
            meta.className = "message-meta";
            time.className = "time";
            time.textContent = msg.created_at;

            if (messageText) {
                content.innerHTML = linkifyMessageText(messageText);
            }

            if (msg.attachment_url) {
                var attachmentWrap = document.createElement("div");
                attachmentWrap.className = "message-attachment";
                attachmentWrap.setAttribute("data-attachment-url", msg.attachment_url);
                attachmentWrap.setAttribute("data-attachment-type", msg.attachment_type || "file");
                renderAttachmentCard(attachmentWrap, msg.attachment_url, msg.attachment_type || "file");
                content.appendChild(attachmentWrap);
            }

            meta.appendChild(time);

            if (isMine && !isSystem) {
                var status = document.createElement("span");
                status.className = "status";
                status.textContent = msg.read ? "Seen" : "Delivered";
                meta.appendChild(status);
            }

            bubble.appendChild(content);
            bubble.appendChild(meta);
            row.appendChild(bubble);
            return row;
        }

        function appendMessageAndScroll(msg, isUnreadIncoming) {
            var numericId = Number(msg.message_id) || 0;
            if (numericId > 0 && renderedMessageIds.has(numericId)) {
                return;
            }

            removeEmptyPlaceholder();
            var row = createMessageRow(msg, isUnreadIncoming);
            messagesBox.appendChild(row);

            if (numericId > 0) {
                renderedMessageIds.add(numericId);
            }
            lastMessageId = Math.max(lastMessageId, numericId);
            scrollToBottom();
        }

        function linkifyMessageText(rawText) {
            var escaped = String(rawText || "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/\"/g, "&quot;")
                .replace(/'/g, "&#39;");

            return escaped.replace(/(https?:\/\/[^\s<]+)/g, function (url) {
                return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + url + "</a>";
            });
        }

        function formatFileSize(bytes) {
            var numeric = Number(bytes) || 0;
            if (numeric <= 0) {
                return "0 KB";
            }
            if (numeric < 1024 * 1024) {
                return Math.max(1, Math.round(numeric / 1024)) + " KB";
            }
            return (numeric / (1024 * 1024)).toFixed(2) + " MB";
        }

        function truncateFileName(fileName, maxLength) {
            var value = String(fileName || "attachment");
            if (value.length <= maxLength) {
                return value;
            }

            var dotIndex = value.lastIndexOf(".");
            if (dotIndex > 0 && dotIndex < value.length - 1) {
                var ext = value.slice(dotIndex);
                var base = value.slice(0, dotIndex);
                var room = maxLength - ext.length - 3;
                if (room > 6) {
                    return base.slice(0, room) + "..." + ext;
                }
            }

            return value.slice(0, maxLength - 3) + "...";
        }

        function detectTypeFromName(fileName) {
            var ext = String(fileName || "").split(".").pop().toLowerCase();
            if (["jpg", "jpeg", "png", "gif", "webp"].indexOf(ext) >= 0) {
                return "image";
            }
            if (["mp4", "webm", "mov"].indexOf(ext) >= 0) {
                return "video";
            }
            if (["mp3", "wav", "ogg"].indexOf(ext) >= 0) {
                return "audio";
            }
            return "file";
        }

        function parseAttachmentMeta(attachmentUrl, fallbackType) {
            var urlValue = String(attachmentUrl || "");
            var result = {
                url: urlValue,
                type: fallbackType || "file",
                name: "attachment",
                sizeBytes: null
            };

            if (!urlValue) {
                return result;
            }

            try {
                var parsed = new URL(urlValue, window.location.origin);
                var pathName = parsed.pathname.split("/").pop() || "attachment";
                result.name = decodeURIComponent(parsed.searchParams.get("name") || pathName);
                var sizeRaw = parsed.searchParams.get("size");
                if (sizeRaw) {
                    result.sizeBytes = Number(sizeRaw) || null;
                }
                if (!fallbackType || fallbackType === "file") {
                    result.type = detectTypeFromName(result.name);
                }
                result.url = parsed.toString();
            } catch (_e) {
                var plainName = urlValue.split("?")[0].split("/").pop() || "attachment";
                result.name = decodeURIComponent(plainName);
                if (!fallbackType || fallbackType === "file") {
                    result.type = detectTypeFromName(result.name);
                }
            }

            return result;
        }

        function renderAttachmentCard(container, attachmentUrl, attachmentType) {
            var meta = parseAttachmentMeta(attachmentUrl, attachmentType);
            container.innerHTML = "";
            container.className = "message-attachment";

            if (meta.type === "image") {
                var imageLink = document.createElement("button");
                imageLink.type = "button";
                imageLink.className = "attachment-thumb-link";
                imageLink.addEventListener("click", function () {
                    window.open(meta.url, "_blank");
                });

                var image = document.createElement("img");
                image.className = "attachment-preview-image";
                image.src = meta.url;
                image.alt = meta.name;
                imageLink.appendChild(image);
                container.appendChild(imageLink);
            }

            if (meta.type === "video") {
                var video = document.createElement("video");
                video.className = "attachment-preview-video";
                video.controls = true;
                var videoSource = document.createElement("source");
                videoSource.src = meta.url;
                video.appendChild(videoSource);
                container.appendChild(video);
            }

            if (meta.type === "audio") {
                var audio = document.createElement("audio");
                audio.controls = true;
                var audioSource = document.createElement("source");
                audioSource.src = meta.url;
                audio.appendChild(audioSource);
                container.appendChild(audio);
            }

            var card = document.createElement("div");
            card.className = "attachment-card";

            var main = document.createElement("div");
            main.className = "attachment-card-main";

            var openName = document.createElement("a");
            openName.className = "attachment-card-name";
            openName.href = meta.url;
            openName.target = "_blank";
            openName.rel = "noopener noreferrer";
            openName.textContent = truncateFileName(meta.name, 22);
            openName.title = meta.name;

            var size = document.createElement("span");
            size.className = "attachment-card-size";
            size.textContent = meta.sizeBytes ? "(" + formatFileSize(meta.sizeBytes) + ")" : "(File)";

            var download = document.createElement("a");
            download.className = "attachment-card-download";
            download.href = meta.url;
            download.target = "_blank";
            download.rel = "noopener noreferrer";
            download.download = meta.name;
            download.setAttribute("aria-label", "Download attachment");
            download.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3v11m0 0l4-4m-4 4l-4-4M5 16v3h14v-3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path></svg>';

            main.appendChild(openName);
            main.appendChild(size);
            card.appendChild(main);
            card.appendChild(download);
            container.appendChild(card);
        }

        function renderExistingAttachmentCards() {
            messagesBox.querySelectorAll(".message-attachment[data-attachment-url]").forEach(function (node) {
                renderAttachmentCard(
                    node,
                    node.getAttribute("data-attachment-url"),
                    node.getAttribute("data-attachment-type") || "file"
                );
            });
        }

        function clearDraftAttachment() {
            if (attachmentUrlInput) {
                attachmentUrlInput.value = "";
            }
            if (attachmentTypeInput) {
                attachmentTypeInput.value = "";
            }
            if (attachmentFileNameInput) {
                attachmentFileNameInput.value = "";
            }
            if (attachmentFileSizeInput) {
                attachmentFileSizeInput.value = "";
            }
            if (fileInput) {
                fileInput.value = "";
            }
            selectedFiles = [];
            renderSelectedFiles();
            if (attachBtn) {
                attachBtn.classList.remove("has-file");
            }
        }

        function isImageFile(file) {
            if (!file) {
                return false;
            }
            if (String(file.type || "").indexOf("image/") === 0) {
                return true;
            }
            return detectTypeFromName(file.name) === "image";
        }

        function renderSelectedFiles() {
            if (!selectedFilesContainer || !selectedFilesList || !selectedFilesCount) {
                return;
            }

            selectedFilesList.innerHTML = "";
            if (selectedFiles.length > 0) {
                selectedFilesCount.textContent = selectedFiles.length + (selectedFiles.length === 1 ? " File" : " Files");
                selectedFilesContainer.hidden = false;
                selectedFilesContainer.style.display = "grid";
            } else {
                selectedFilesCount.textContent = "";
                selectedFilesContainer.hidden = true;
                selectedFilesContainer.style.display = "none";
            }

            if (attachBtn) {
                if (selectedFiles.length) {
                    attachBtn.classList.add("has-file");
                } else {
                    attachBtn.classList.remove("has-file");
                }
            }

            selectedFiles.forEach(function (file, index) {
                var chip = document.createElement("div");
                chip.className = "selected-file-chip";

                var thumb = document.createElement("span");
                thumb.className = "selected-file-thumb";
                if (isImageFile(file)) {
                    var img = document.createElement("img");
                    img.alt = file.name;
                    img.src = URL.createObjectURL(file);
                    img.addEventListener("load", function () {
                        URL.revokeObjectURL(img.src);
                    });
                    thumb.appendChild(img);
                } else {
                    thumb.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M7 3h7l5 5v13H7zM14 3v6h5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"></path></svg>';
                }

                var name = document.createElement("span");
                name.className = "selected-file-name";
                name.textContent = truncateFileName(file.name, 22);
                name.title = file.name;

                var removeBtn = document.createElement("button");
                removeBtn.type = "button";
                removeBtn.className = "selected-file-remove";
                removeBtn.setAttribute("aria-label", "Remove file");
                removeBtn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path></svg>';
                removeBtn.addEventListener("click", function () {
                    selectedFiles.splice(index, 1);
                    console.log("[attachments] removed file:", file.name);
                    renderSelectedFiles();
                });

                chip.appendChild(thumb);
                chip.appendChild(name);
                chip.appendChild(removeBtn);
                selectedFilesList.appendChild(chip);
            });
        }

        function uploadAttachment(file) {
            var data = new FormData();
            data.append("file", file);

            return fetch("/upload_attachment", {
                method: "POST",
                body: data,
                headers: { "X-Requested-With": "XMLHttpRequest" }
            })
                .then(function (response) {
                    var contentType = response.headers.get("content-type") || "";
                    if (contentType.indexOf("application/json") >= 0) {
                        return response.json().then(function (payload) {
                            return { ok: response.ok, payload: payload };
                        });
                    }
                    return response.text().then(function (rawText) {
                        return { ok: response.ok, payload: { error: rawText || "Upload failed." } };
                    });
                })
                .then(function (result) {
                    if (!result.ok) {
                        throw new Error((result.payload && result.payload.error) || "Upload failed.");
                    }

                    var payload = result.payload || {};
                    console.log("[attachments] upload response:", payload);
                    var fileUrl = payload.file_url || "";
                    var fileName = payload.file_name || file.name || "attachment";
                    var fileSize = Number(payload.file_size || file.size || 0);
                    var joiner = fileUrl.indexOf("?") >= 0 ? "&" : "?";

                    return {
                        url: fileUrl + joiner + "name=" + encodeURIComponent(fileName) + "&size=" + encodeURIComponent(String(fileSize)),
                        type: payload.file_type || detectTypeFromName(fileName),
                        fileName: fileName,
                        fileSize: fileSize
                    };
                });
        }

        function sendMessageRequest(payload) {
            var path = chatForm.getAttribute("action") || window.location.pathname;
            return fetch(path, {
                method: "POST",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    "X-Requested-With": "XMLHttpRequest"
                },
                body: new URLSearchParams(payload).toString()
            })
                .then(function (response) {
                    var contentType = response.headers.get("content-type") || "";
                    if (contentType.indexOf("application/json") === -1) {
                        return response.text().then(function () {
                            throw new Error("Unexpected response from server.");
                        });
                    }
                    return response.json().then(function (payloadJson) {
                        if (!response.ok || !payloadJson || payloadJson.ok === false) {
                            throw new Error((payloadJson && payloadJson.error) || "Message send failed.");
                        }
                        return payloadJson;
                    });
                });
        }

        renderExistingAttachmentCards();
        renderSelectedFiles();
        scrollToBottom();

        if (chatUsername && window.location.pathname.indexOf("/messages/") === 0) {
            setInterval(function () {
                fetch(
                    "/messages/"
                        + encodeURIComponent(chatUsername)
                        + "/poll?after_id="
                        + lastMessageId
                        + "&after_seen_id="
                        + lastSeenAckId,
                    {
                    headers: { "X-Requested-With": "XMLHttpRequest" }
                    }
                )
                    .then(function (response) {
                        if (!response.ok) {
                            return null;
                        }
                        return response.json();
                    })
                    .then(function (payload) {
                        if (!payload) {
                            return;
                        }

                        if (chatPresenceLabel && payload.presence_label) {
                            chatPresenceLabel.textContent = payload.presence_label;
                        }

                        syncSidebarUnreadPills(payload.unread_map);

                        markMessagesSeen(payload.seen_ids || []);

                        if (!payload.messages || !payload.messages.length) {
                            return;
                        }

                        var hasIncoming = false;

                        payload.messages.forEach(function (msg) {
                            var isMine = msg.sender_id === currentUserId;
                            if (!isMine) {
                                hasIncoming = true;
                            }
                            appendMessageAndScroll(msg, !isMine);
                        });

                        if (hasIncoming) {
                            playIncomingSound();
                        }
                    })
                    .catch(function () {
                        // Ignore transient polling errors to avoid disrupting chat flow.
                    });
            }, 2500);
        }

        if (attachBtn && fileInput && attachmentUrlInput && attachmentTypeInput) {
            attachBtn.addEventListener("click", function () {
                fileInput.click();
            });

            fileInput.addEventListener("change", function () {
                var pickedFiles = fileInput.files ? Array.prototype.slice.call(fileInput.files) : [];
                if (!pickedFiles.length) {
                    console.log("[attachments] no file selected");
                    return;
                }

                pickedFiles.forEach(function (file) {
                    var exists = selectedFiles.some(function (entry) {
                        return entry.name === file.name && entry.size === file.size && entry.lastModified === file.lastModified;
                    });
                    if (!exists) {
                        selectedFiles.push(file);
                    }
                });

                console.log("[attachments] selected files:", selectedFiles.map(function (file) {
                    return { name: file.name, size: file.size, type: file.type };
                }));

                attachmentUrlInput.value = "";
                attachmentTypeInput.value = "";
                if (attachmentFileNameInput) {
                    attachmentFileNameInput.value = "";
                }
                if (attachmentFileSizeInput) {
                    attachmentFileSizeInput.value = "";
                }
                fileInput.value = "";
                renderSelectedFiles();
            });
        }

        if (chatForm && attachmentUrlInput) {
            chatForm.addEventListener("submit", function (event) {
                event.preventDefault();
                if (isSubmittingAttachment) {
                    return;
                }

                var messageInput = chatForm.querySelector('input[name="message"]');
                var textValue = messageInput ? (messageInput.value || "").trim() : "";
                var hasAttachment = selectedFiles.length > 0 || Boolean(attachmentUrlInput.value);
                if (!textValue && !hasAttachment) {
                    return;
                }

                var sendBtn = chatForm.querySelector('button[type="submit"]');
                isSubmittingAttachment = true;
                attachBtn.disabled = true;
                if (sendBtn) {
                    sendBtn.disabled = true;
                }

                if (!selectedFiles.length) {
                    sendMessageRequest({
                        message: textValue,
                        attachment_url: attachmentUrlInput.value || "",
                        attachment_type: attachmentTypeInput.value || "",
                        file_name: attachmentFileNameInput ? (attachmentFileNameInput.value || "") : "",
                        file_size: attachmentFileSizeInput ? (attachmentFileSizeInput.value || "") : ""
                    })
                        .then(function (payload) {
                            if (payload.unread_map) {
                                syncSidebarUnreadPills(payload.unread_map);
                            }
                            if (payload.message) {
                                appendMessageAndScroll(payload.message, false);
                            }
                            if (messageInput) {
                                messageInput.value = "";
                            }
                            clearDraftAttachment();
                        })
                        .catch(function (error) {
                            console.error(error.message || "Message send failed.");
                        })
                        .finally(function () {
                            isSubmittingAttachment = false;
                            attachBtn.disabled = false;
                            if (sendBtn) {
                                sendBtn.disabled = false;
                            }
                        });
                    return;
                }

                Promise.all(selectedFiles.map(uploadAttachment))
                    .then(function (uploadedFiles) {
                        var attachmentsJson = JSON.stringify(uploadedFiles.map(function (uploaded) {
                            return {
                                attachment_url: uploaded.url,
                                attachment_type: uploaded.type,
                                file_name: uploaded.fileName || "",
                                file_size: uploaded.fileSize ? String(uploaded.fileSize) : ""
                            };
                        }));

                        var chain = Promise.resolve();
                        uploadedFiles.forEach(function (uploaded, index) {
                            chain = chain.then(function () {
                                return sendMessageRequest({
                                    message: index === 0 ? textValue : "",
                                    attachment_url: uploaded.url,
                                    attachment_type: uploaded.type,
                                    file_name: uploaded.fileName || "",
                                    file_size: uploaded.fileSize ? String(uploaded.fileSize) : "",
                                    attachments: index === 0 ? attachmentsJson : ""
                                }).then(function (payload) {
                                    if (payload.unread_map) {
                                        syncSidebarUnreadPills(payload.unread_map);
                                    }
                                    if (payload.message) {
                                        appendMessageAndScroll(payload.message, false);
                                    }
                                });
                            });
                        });

                        return chain;
                    })
                    .then(function () {
                        if (messageInput) {
                            messageInput.value = "";
                        }
                        clearDraftAttachment();
                    })
                    .catch(function (error) {
                        console.error(error.message || "Attachment upload failed.");
                    })
                    .finally(function () {
                        isSubmittingAttachment = false;
                        attachBtn.disabled = false;
                        if (sendBtn) {
                            sendBtn.disabled = false;
                        }
                    });
            });
        }
    }

    var profileImageInput = document.getElementById("profileImageInput");
    var profilePreview = document.getElementById("profilePreview");
    if (profileImageInput && profilePreview) {
        profileImageInput.addEventListener("change", function (event) {
            var file = event.target.files && event.target.files[0];
            if (!file) {
                return;
            }
            var isImage = /image\/(png|jpe?g)/.test(file.type);
            if (!isImage) {
                profileImageInput.value = "";
                return;
            }
            var previewUrl = URL.createObjectURL(file);
            profilePreview.src = previewUrl;
        });
    }

    var reportModal = document.getElementById("reportModal");
    var reportUserIdInput = document.getElementById("reportUserIdInput");
    var reportForm = document.getElementById("reportForm");
    var reportAttachment = document.getElementById("reportAttachment");
    var reportErrorBox = document.getElementById("report-error");
    var reportMessageTimer = null;

    function showReportError(message) {
        var box = document.getElementById("report-error");
        if (!box) {
            return;
        }

        box.innerText = message || "";
        box.classList.remove("hidden");

        if (reportMessageTimer) {
            clearTimeout(reportMessageTimer);
        }

        reportMessageTimer = setTimeout(function () {
            box.classList.add("hidden");
            box.innerText = "";
            reportMessageTimer = null;
        }, 5000);
    }

    function resetReportError() {
        if (!reportErrorBox) {
            return;
        }
        if (reportMessageTimer) {
            clearTimeout(reportMessageTimer);
            reportMessageTimer = null;
        }
        reportErrorBox.classList.add("hidden");
        reportErrorBox.innerText = "";
    }

    if (reportModal && reportUserIdInput) {
        document.querySelectorAll(".open-report-modal").forEach(function (button) {
            button.addEventListener("click", function () {
                var userId = button.getAttribute("data-user-id");
                if (!userId) {
                    return;
                }
                reportUserIdInput.value = userId;
                resetReportError();
                reportModal.showModal();
            });
        });

        reportModal.querySelectorAll(".close-report-modal").forEach(function (button) {
            button.addEventListener("click", function () {
                if (reportForm) {
                    reportForm.reset();
                }
                resetReportError();
                reportModal.close();
            });
        });

        if (reportForm) {
            reportForm.addEventListener("submit", function (event) {
                event.preventDefault();
                var submitButton = reportForm.querySelector('button[type="submit"]');
                if (submitButton) {
                    submitButton.disabled = true;
                }
                resetReportError();

                var formData = new FormData(reportForm);
                formData.delete("attachments");
                if (reportAttachment && reportAttachment.files && reportAttachment.files.length) {
                    Array.prototype.slice.call(reportAttachment.files).forEach(function (file) {
                        formData.append("attachments", file);
                    });
                }

                fetch(reportForm.action, {
                    method: "POST",
                    body: formData,
                    headers: { "X-Requested-With": "XMLHttpRequest" }
                })
                    .then(function (response) {
                        return response.json().then(function (payload) {
                            return { ok: response.ok, payload: payload || {} };
                        });
                    })
                    .then(function (result) {
                        if (!result.ok || result.payload.error || result.payload.ok !== true) {
                            var duplicateMessage = "This user is already reported. Please wait for admin to review it.";
                            var message = (result.payload && result.payload.error) || "Failed to submit report.";
                            if (/already reported|wait for admin/i.test(message)) {
                                message = duplicateMessage;
                            }
                            showReportError(message);
                            return;
                        }
                        reportForm.reset();
                        showReportError("Report submitted successfully");
                    })
                    .catch(function (error) {
                        var duplicateMessage = "This user is already reported. Please wait for admin to review it.";
                        var message = error.message || "Failed to submit report.";
                        if (/already reported|wait for admin/i.test(message)) {
                            message = duplicateMessage;
                        }
                        showReportError(message);
                    })
                    .finally(function () {
                        if (submitButton) {
                            submitButton.disabled = false;
                        }
                    });
            });
        }
    }

    var liveSearchRoot = document.getElementById("liveSearchRoot");
    if (liveSearchRoot) {
        var liveSearchInput = document.getElementById("liveSearchInput");
        var liveSearchClear = document.getElementById("liveSearchClear");
        var chipAvailableOnly = document.getElementById("chipAvailableOnly");
        var searchResults = document.getElementById("liveSearchResults");
        var searchLoading = document.getElementById("searchLoading");
        var searchEmptyState = document.getElementById("searchEmptyState");
        var searchResultCopy = document.getElementById("searchResultCopy");
        var activeFilterChips = document.getElementById("activeFilterChips");
        var levelChips = Array.prototype.slice.call(document.querySelectorAll(".level-chip[data-level]"));
        var categoryChips = Array.prototype.slice.call(document.querySelectorAll(".category-chip[data-category]"));
        var queryValue = String(liveSearchRoot.getAttribute("data-initial-query") || "");
        var selectedLevel = String(liveSearchRoot.getAttribute("data-initial-level") || "");
        var selectedCategory = String(liveSearchRoot.getAttribute("data-initial-category") || "");
        var availableOnly = String(liveSearchRoot.getAttribute("data-initial-available") || "0") === "1";
        var searchDebounceTimer = null;
        var searchAbortController = null;

        function escapeHtml(value) {
            return String(value || "")
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/\"/g, "&quot;")
                .replace(/'/g, "&#39;");
        }

        function sanitizeAvailabilityLabel(label) {
            return String(label || "Unavailable").toLowerCase().replace(/\s+/g, "-");
        }

        function renderTrustLine(item) {
            var completion = item.completion_rate;
            var completionText = completion === null || typeof completion === "undefined" ? "-" : String(completion) + "%";
            return "Response " + String(item.response_rate || 0) + "% | Completion " + completionText;
        }

        function renderSkillChips(skills) {
            if (!Array.isArray(skills) || !skills.length) {
                return '<span class="chip muted">No skill highlights yet</span>';
            }

            return skills.map(function (skill) {
                var name = escapeHtml(skill.name || "Skill");
                var category = escapeHtml(skill.category || "General");
                var level = escapeHtml(skill.level || "Any");
                return '<span class="chip">' + name + '</span><span class="category-tag">' + category + '</span><span class="level-badge">' + level + '</span>';
            }).join(" ");
        }

        function renderResultCards(results) {
            if (!searchResults) {
                return;
            }

            searchResults.innerHTML = "";
            results.forEach(function (item) {
                var card = document.createElement("article");
                card.className = "card match-card search-user-card";

                var avatarInner = item.avatar_url
                    ? '<img src="' + escapeHtml(item.avatar_url) + '" alt="@' + escapeHtml(item.username) + ' avatar" loading="lazy">'
                    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" focusable="false"><path d="M20 21a8 8 0 0 0-16 0"></path><circle cx="12" cy="8" r="4"></circle></svg>';

                var ratingValue = item.average_rating === null || typeof item.average_rating === "undefined"
                    ? "Not rated yet"
                    : String(item.average_rating);

                var badgesHtml = "";
                if (Array.isArray(item.badges) && item.badges.length) {
                    badgesHtml = '<div class="search-badge-row">' + item.badges.map(function (badge) {
                        return '<span class="trust-badge">' + escapeHtml(badge) + '</span>';
                    }).join(" ") + "</div>";
                }

                var locationHtml = item.location
                    ? '<p class="search-user-location">' + escapeHtml(item.location) + '</p>'
                    : "";

                card.innerHTML =
                    '<div class="match-card-top">'
                    + '<div class="match-user-block">'
                    + '<div class="match-avatar" aria-hidden="true">' + avatarInner + '</div>'
                    + '<div class="match-user-meta">'
                    + '<p class="match-user-name">' + escapeHtml(item.name || item.username) + '</p>'
                    + '<p class="match-user-sub">'
                    + '<a class="user-handle-link" href="' + escapeHtml(item.profile_url) + '">@' + escapeHtml(item.username) + '</a>'
                    + '<span class="availability-badge availability-' + sanitizeAvailabilityLabel(item.availability_label) + '">' + escapeHtml(item.availability_label || "Unavailable") + '</span>'
                    + '<span class="match-user-rating-inline"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false"><path d="M12 2.5l2.9 5.88 6.49.94-4.7 4.58 1.11 6.47L12 17.31 6.2 20.37l1.11-6.47-4.7-4.58 6.49-.94L12 2.5z"></path></svg><span>' + escapeHtml(ratingValue) + '</span></span>'
                    + '</p>'
                    + locationHtml
                    + '</div>'
                    + '</div>'
                    + '</div>'
                    + '<p class="search-trust-line">' + escapeHtml(renderTrustLine(item)) + '</p>'
                    + '<div class="search-skill-stack">' + renderSkillChips(item.matching_skills) + '</div>'
                    + badgesHtml
                    + '<div class="inline-actions match-actions search-actions">'
                    + '<a class="btn secondary" href="' + escapeHtml(item.profile_url) + '">View Profile</a>'
                    + '<a class="btn tertiary" href="' + escapeHtml(item.chat_url) + '">Chat</a>'
                    + '</div>';

                searchResults.appendChild(card);
            });
        }

        function syncActiveFilterTags() {
            if (!activeFilterChips) {
                return;
            }

            var chips = [];
            if (availableOnly) {
                chips.push("Available");
            }
            if (selectedLevel) {
                chips.push(selectedLevel);
            }
            if (selectedCategory) {
                chips.push(selectedCategory);
            }

            activeFilterChips.innerHTML = chips.map(function (label) {
                return '<span class="chip muted">' + escapeHtml(label) + '</span>';
            }).join(" ");
        }

        function syncFilterChipStyles() {
            if (chipAvailableOnly) {
                chipAvailableOnly.classList.toggle("is-active", availableOnly);
            }

            levelChips.forEach(function (chip) {
                chip.classList.toggle("is-active", chip.getAttribute("data-level") === selectedLevel);
            });

            categoryChips.forEach(function (chip) {
                chip.classList.toggle("is-active", chip.getAttribute("data-category") === selectedCategory);
            });

            syncActiveFilterTags();
        }

        function updateHistoryQuery() {
            var params = new URLSearchParams();
            if (queryValue) {
                params.set("q", queryValue);
            }
            if (availableOnly) {
                params.set("available_only", "1");
            }
            if (selectedLevel) {
                params.set("level", selectedLevel);
            }
            if (selectedCategory) {
                params.set("category", selectedCategory);
            }

            var query = params.toString();
            var path = window.location.pathname + (query ? "?" + query : "");
            window.history.replaceState({}, "", path);
        }

        function fetchLiveSearch() {
            if (!searchResults || !searchLoading || !searchEmptyState || !searchResultCopy) {
                return;
            }

            if (searchAbortController) {
                searchAbortController.abort();
            }
            searchAbortController = new AbortController();

            var params = new URLSearchParams();
            params.set("limit", "30");
            if (queryValue) {
                params.set("q", queryValue);
            }
            if (availableOnly) {
                params.set("available_only", "1");
            }
            if (selectedLevel) {
                params.set("level", selectedLevel);
            }
            if (selectedCategory) {
                params.set("category", selectedCategory);
            }

            updateHistoryQuery();
            searchLoading.hidden = false;
            searchEmptyState.hidden = true;
            searchResultCopy.textContent = "Searching...";

            fetch("/api/search-users?" + params.toString(), {
                headers: { "X-Requested-With": "XMLHttpRequest" },
                signal: searchAbortController.signal
            })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error("Search request failed.");
                    }
                    return response.json();
                })
                .then(function (payload) {
                    var results = Array.isArray(payload.results) ? payload.results : [];
                    renderResultCards(results);
                    searchLoading.hidden = true;

                    if (!results.length) {
                        searchEmptyState.hidden = false;
                        searchResultCopy.textContent = "No results";
                        return;
                    }

                    searchResultCopy.textContent = String(results.length) + (results.length === 1 ? " result" : " results");
                })
                .catch(function (error) {
                    if (error && error.name === "AbortError") {
                        return;
                    }
                    searchLoading.hidden = true;
                    searchEmptyState.hidden = false;
                    searchResultCopy.textContent = "Search unavailable";
                });
        }

        function scheduleLiveSearch() {
            if (searchDebounceTimer) {
                window.clearTimeout(searchDebounceTimer);
            }
            searchDebounceTimer = window.setTimeout(fetchLiveSearch, 260);
        }

        if (liveSearchInput) {
            liveSearchInput.addEventListener("input", function () {
                queryValue = (liveSearchInput.value || "").trim();
                scheduleLiveSearch();
            });
        }

        if (liveSearchClear && liveSearchInput) {
            liveSearchClear.addEventListener("click", function () {
                queryValue = "";
                liveSearchInput.value = "";
                liveSearchInput.focus();
                fetchLiveSearch();
            });
        }

        if (chipAvailableOnly) {
            chipAvailableOnly.addEventListener("click", function () {
                availableOnly = !availableOnly;
                syncFilterChipStyles();
                fetchLiveSearch();
            });
        }

        levelChips.forEach(function (chip) {
            chip.addEventListener("click", function () {
                selectedLevel = chip.getAttribute("data-level") || "";
                syncFilterChipStyles();
                fetchLiveSearch();
            });
        });

        categoryChips.forEach(function (chip) {
            chip.addEventListener("click", function () {
                selectedCategory = chip.getAttribute("data-category") || "";
                syncFilterChipStyles();
                fetchLiveSearch();
            });
        });

        syncFilterChipStyles();
        fetchLiveSearch();
    }

});
