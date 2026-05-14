// Claude Code Session Manager - Frontend Logic

const API = {
    projects: "/api/projects",
    projectSessions: (enc) => `/api/projects/${encodeURIComponent(enc)}/sessions`,
    sessionMessages: (enc, sid) => `/api/projects/${encodeURIComponent(enc)}/sessions/${encodeURIComponent(sid)}/messages`,
    launch: "/api/launch",
    deleteSession: (enc, sid) => `/api/projects/${encodeURIComponent(enc)}/sessions/${encodeURIComponent(sid)}`,
    search: (q) => `/api/search?q=${encodeURIComponent(q)}`,
    stats: "/api/stats",
    selectFolder: "/api/select-folder",
    customProjects: "/api/custom-projects",
    shutdown: "/api/shutdown",
    terminalConfig: "/api/terminal-config",
    activeSessions: "/api/active-sessions",
    exitSession: (sid) => `/api/sessions/${encodeURIComponent(sid)}/exit`,
};

let state = {
    projects: [],
    selectedProject: null,  // encoded
    selectedProjectPath: null,
    sessions: [],
    selectedSession: null,
    currentDetailEncoded: null,
    currentDetailSid: null,
    currentDetailOffset: 0,
    currentDetailTotal: 0,
    stats: null,
    dangerouslySkipPermissions: false,
    showingActiveSessions: false,
};

// === DOM refs ===
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const projectList = $("#projectList");
const sessionList = $("#sessionList");
const emptyState = $("#emptyState");
const breadcrumb = $("#breadcrumb");
const toolbarInfo = $("#toolbarInfo");
const statusBar = $("#statusBar");
const searchInput = $("#searchInput");
const searchClear = $("#searchClear");
const btnContinue = $("#btnContinue");
const btnNewSession = $("#btnNewSession");
const detailPanel = $("#detailPanel");
const detailMessages = $("#detailMessages");
const detailTitle = $("#detailTitle");
const refreshBtn = $("#refreshBtn");
const btnNewProject = $("#btnNewProject");
const btnShutdown = $("#btnShutdown");
const btnActiveSessions = $("#btnActiveSessions");

// === Init ===
async function init() {
    await loadProjects();
    await loadStats();
    await loadTerminalConfig();
    setupEventListeners();
    setupKeyboardShortcuts();
}

async function loadProjects() {
    try {
        const res = await fetch(API.projects);
        state.projects = await res.json();
        renderProjects();
        updateStatusBar();
    } catch (e) {
        console.error("Failed to load projects:", e);
        projectList.innerHTML = '<div class="loading-text">加载失败，请检查服务是否启动</div>';
    }
}

async function loadStats() {
    try {
        const res = await fetch(API.stats);
        state.stats = await res.json();
        updateStatusBar();
    } catch (e) {
        console.error("Failed to load stats:", e);
    }
}

async function loadTerminalConfig() {
    try {
        const res = await fetch(API.terminalConfig);
        state.terminalConfig = await res.json();
        updateStatusBar();
    } catch (e) {
        console.error("Failed to load terminal config:", e);
    }
}

// === Render Sidebar ===
function renderProjects() {
    projectList.innerHTML = "";

    if (state.projects.length === 0) {
        projectList.innerHTML = '<div class="loading-text">暂无项目记录</div>';
        return;
    }

    state.projects.forEach((proj) => {
        const item = document.createElement("div");
        item.className = "sidebar-item";
        if (state.selectedProject === proj.encoded) {
            item.classList.add("active");
        }
        item.dataset.encoded = proj.encoded;
        item.innerHTML = `
            <span class="sidebar-item-icon">📁</span>
            <div class="sidebar-item-content">
                <span class="sidebar-item-name">${escapeHtml(proj.projectName)}</span>
                <span class="sidebar-item-meta">${escapeHtml(proj.parentDir)}</span>
            </div>
            <span class="sidebar-item-count">${proj.sessionCount}</span>
        `;
        item.addEventListener("click", () => selectProject(proj));
        projectList.appendChild(item);
    });
}

// === Select Project ===
async function selectProject(proj) {
    state.selectedProject = proj.encoded;
    state.selectedProjectPath = proj.projectPath;
    state.selectedSession = null;

    // Update sidebar
    $$(".sidebar-item").forEach((el) => el.classList.remove("active"));
    const target = document.querySelector(`.sidebar-item[data-encoded="${CSS.escape(proj.encoded)}"]`);
    if (target) target.classList.add("active");

    // Update toolbar
    breadcrumb.textContent = `${proj.projectName} — ${proj.projectPath}`;
    btnContinue.disabled = false;
    btnNewSession.disabled = false;
    toolbarInfo.textContent = `${proj.sessionCount} 个会话`;

    // Load sessions
    await loadSessions(proj.encoded);

    // Close detail panel
    closeDetail();
}

// === Load Sessions ===
async function loadSessions(encoded) {
    sessionList.innerHTML = '<div class="loading-text">加载会话中...</div>';
    try {
        const res = await fetch(API.projectSessions(encoded));
        state.sessions = await res.json();
        renderSessions();
    } catch (e) {
        console.error("Failed to load sessions:", e);
        sessionList.innerHTML = '<div class="loading-text">加载失败</div>';
    }
}

// === Active Sessions ===
async function loadActiveSessions() {
    sessionList.innerHTML = '<div class="loading-text">加载活跃会话中...</div>';
    try {
        const res = await fetch(API.activeSessions);
        const activeSessions = await res.json();
        renderActiveSessions(activeSessions);
    } catch (e) {
        console.error("Failed to load active sessions:", e);
        sessionList.innerHTML = '<div class="loading-text">加载失败</div>';
    }
}

function renderActiveSessions(activeSessions) {
    if (activeSessions.length === 0) {
        sessionList.innerHTML = `
            <div class="empty-state">
                <p>当前没有活跃的会话</p>
            </div>`;
        return;
    }

    sessionList.innerHTML = "";
    activeSessions.forEach((s) => {
        const card = document.createElement("div");
        card.className = "session-card";
        card.innerHTML = `
            <div class="session-card-header">
                <div class="session-card-message">📝 ${escapeHtml(s.title)}</div>
                <div class="session-card-actions">
                    <button class="card-btn resume-btn" data-sid="${s.sessionId}" data-encoded="${s.encoded}">恢复</button>
                    <button class="card-btn exit-session-btn" data-sid="${s.sessionId}">退出</button>
                </div>
            </div>
            <div class="session-card-meta">
                <span><span class="status-dot active"></span> 活跃</span>
                <span>📁 ${escapeHtml(s.projectName)}</span>
                <span style="font-family:monospace;font-size:10px;">${s.sessionId}</span>
                <button class="copy-id-btn" data-sid="${s.sessionId}" title="复制会话ID">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                    </svg>
                </button>
            </div>
        `;

        card.querySelector(".resume-btn").addEventListener("click", (e) => {
            e.stopPropagation();
            launchClaude("resume", s.projectPath, s.sessionId, false);
        });

        card.querySelector(".exit-session-btn").addEventListener("click", (e) => {
            e.stopPropagation();
            exitSession(s.sessionId);
        });

        card.querySelector(".copy-id-btn").addEventListener("click", (e) => {
            e.stopPropagation();
            copySessionId(s.sessionId);
        });

        card.addEventListener("click", (e) => {
            if (e.target.closest(".card-btn") || e.target.closest(".copy-id-btn")) return;
            const proj = state.projects.find((p) => p.encoded === s.encoded);
            if (proj) {
                state.showingActiveSessions = false;
                updateActiveSessionsButton();
                selectProject(proj);
            }
        });

        sessionList.appendChild(card);
    });
}

function toggleActiveSessions() {
    state.showingActiveSessions = !state.showingActiveSessions;
    updateActiveSessionsButton();

    if (state.showingActiveSessions) {
        state.selectedProject = null;
        state.selectedProjectPath = null;
        $$(".sidebar-item").forEach((el) => el.classList.remove("active"));
        breadcrumb.textContent = "活跃会话 — 所有项目";
        btnContinue.disabled = true;
        btnNewSession.disabled = true;
        toolbarInfo.textContent = "";
        closeDetail();
        loadActiveSessions();
    } else {
        sessionList.innerHTML = `
            <div class="empty-state" id="emptyState">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" opacity="0.3">
                    <path d="M22,19a2,2,0,0,1-2,2H4a2,2,0,0,1-2-2V5a2,2,0,0,1,2-2H9l2,3h9a2,2,0,0,1,2,2Z"/>
                </svg>
                <p>选择左侧项目以查看会话记录</p>
            </div>`;
        breadcrumb.textContent = "选择左侧项目查看会话";
        toolbarInfo.textContent = "";
    }
}

function updateActiveSessionsButton() {
    if (!btnActiveSessions) return;
    if (state.showingActiveSessions) {
        btnActiveSessions.classList.add("active");
        btnActiveSessions.title = "返回项目会话视图";
    } else {
        btnActiveSessions.classList.remove("active");
        btnActiveSessions.title = "查看所有活跃会话";
    }
}

// === Render Sessions ===
function renderSessions() {
    if (state.sessions.length === 0) {
        sessionList.innerHTML = `
            <div class="empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" opacity="0.3">
                    <circle cx="12" cy="12" r="10"/><line x1="8" y1="15" x2="16" y2="15"/><line x1="9" y1="9" x2="15" y2="9"/>
                </svg>
                <p>该项目暂无会话记录</p>
            </div>`;
        return;
    }

    sessionList.innerHTML = "";
    state.sessions.forEach((session) => {
        const card = document.createElement("div");
        card.className = "session-card";
        if (state.selectedSession === session.sessionId) {
            card.classList.add("selected");
        }
        card.dataset.sessionId = session.sessionId;

        const timeStr = formatTime(session.timestamp);
        const statusClass = session.active ? "active" : "ended";
        const statusText = session.active ? "活跃" : "已结束";

        const exitBtnHtml = session.active
            ? `<button class="card-btn exit-session-btn" data-sid="${session.sessionId}">退出</button>`
            : "";

        card.innerHTML = `
            <div class="session-card-header">
                <div class="session-card-message">📝 ${escapeHtml(session.firstMessage)}</div>
                <div class="session-card-actions">
                    <button class="card-btn resume-btn" data-sid="${session.sessionId}">恢复</button>
                    <button class="card-btn fork-btn" data-sid="${session.sessionId}">分叉</button>
                    ${exitBtnHtml}
                    <button class="card-btn delete-btn" data-sid="${session.sessionId}">删除</button>
                </div>
            </div>
            <div class="session-card-meta">
                <span><span class="status-dot ${statusClass}"></span> ${statusText}</span>
                <span>🕐 ${timeStr}</span>
                <span>💬 ${session.messageCount} 条消息</span>
                <span style="font-family:monospace;font-size:10px;">${session.sessionId}</span>
                <button class="copy-id-btn" data-sid="${session.sessionId}" title="复制会话ID">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                    </svg>
                </button>
            </div>
        `;

        // Click card to view details
        card.addEventListener("click", (e) => {
            // Don't trigger if clicking buttons
            if (e.target.closest(".card-btn")) return;
            openDetail(session);
        });

        // Resume button
        card.querySelector(".resume-btn").addEventListener("click", (e) => {
            e.stopPropagation();
            launchClaude("resume", state.selectedProjectPath, session.sessionId, false);
        });

        // Fork button
        card.querySelector(".fork-btn").addEventListener("click", (e) => {
            e.stopPropagation();
            launchClaude("resume", state.selectedProjectPath, session.sessionId, true);
        });

        // Delete button
        card.querySelector(".delete-btn").addEventListener("click", (e) => {
            e.stopPropagation();
            deleteSession(session.sessionId);
        });

        // Exit button (active sessions only)
        const exitBtn = card.querySelector(".exit-session-btn");
        if (exitBtn) {
            exitBtn.addEventListener("click", (e) => {
                e.stopPropagation();
                exitSession(session.sessionId);
            });
        }

        // Copy ID button
        card.querySelector(".copy-id-btn").addEventListener("click", (e) => {
            e.stopPropagation();
            copySessionId(session.sessionId);
        });

        sessionList.appendChild(card);
    });

    updateStatusBar();
}

// === Launch Claude Code ===
async function launchClaude(action, projectPath, sessionId = "", fork = false) {
    try {
        const res = await fetch(API.launch, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                action,
                projectPath,
                sessionId,
                fork,
                dangerouslySkipPermissions: state.dangerouslySkipPermissions,
            }),
        });
        const data = await res.json();
        if (data.success) {
            showToast("Claude Code 已在新 Terminal 窗口中启动");
        } else {
            showToast(data.error || "启动失败", true);
        }
    } catch (e) {
        showToast("启动失败: " + e.message, true);
    }
}

// === Delete Session ===
async function deleteSession(sessionId) {
    if (!confirm(`确定要删除此会话吗？\n\n${sessionId}\n\n此操作不可撤销。`)) return;

    try {
        const res = await fetch(API.deleteSession(state.selectedProject, sessionId), {
            method: "DELETE",
        });
        const data = await res.json();
        if (data.success) {
            // Remove from local state
            state.sessions = state.sessions.filter((s) => s.sessionId !== sessionId);
            // Remove card from DOM
            const card = document.querySelector(`.session-card[data-session-id="${CSS.escape(sessionId)}"]`);
            if (card) card.remove();
            // Close detail panel if open for this session
            if (state.currentDetailSid === sessionId) closeDetail();
            showToast("会话已删除");
            updateStatusBar();
        } else {
            showToast(data.error || "删除失败", true);
        }
    } catch (e) {
        showToast("删除失败: " + e.message, true);
    }
}

// === Exit Session ===
async function exitSession(sessionId) {
    if (!confirm(`确定要退出此会话吗？\n\n${sessionId}\n\n这将终止正在运行的 Claude Code 进程。`)) return;

    try {
        const res = await fetch(API.exitSession(sessionId), { method: "POST" });
        const data = await res.json();
        if (data.success) {
            showToast("会话已退出");
            // Refresh current view
            if (state.showingActiveSessions) {
                loadActiveSessions();
            } else if (state.selectedProject) {
                loadSessions(state.selectedProject);
            }
            loadStats();
        } else {
            showToast(data.error || "退出失败", true);
        }
    } catch (e) {
        showToast("退出失败: " + e.message, true);
    }
}

// === Detail Panel ===
async function openDetail(session) {
    state.selectedSession = session.sessionId;
    state.currentDetailEncoded = state.selectedProject;
    state.currentDetailSid = session.sessionId;
    state.currentDetailOffset = 0;
    state.currentDetailTotal = 0;

    detailTitle.textContent = truncate(session.firstMessage, 60);
    detailPanel.style.display = "flex";
    detailMessages.innerHTML = '<div class="loading-text">加载消息中...</div>';
    $("#detailFooter").style.display = "none";

    // Update card selection
    $$(".session-card").forEach((c) => c.classList.remove("selected"));
    const card = document.querySelector(`.session-card[data-session-id="${CSS.escape(session.sessionId)}"]`);
    if (card) card.classList.add("selected");

    // Update detail panel buttons
    $("#btnResumeSession").onclick = () =>
        launchClaude("resume", state.selectedProjectPath, session.sessionId, false);
    $("#btnForkSession").onclick = () =>
        launchClaude("resume", state.selectedProjectPath, session.sessionId, true);

    try {
        const res = await fetch(`${API.sessionMessages(state.selectedProject, session.sessionId)}?limit=1000`);
        const data = await res.json();
        state.currentDetailOffset = data.offset + (data.messages || []).length;
        state.currentDetailTotal = data.total;
        renderMessages(data.messages || [], true);
    } catch (e) {
        detailMessages.innerHTML = '<div class="loading-text">加载失败</div>';
    }
}

async function loadMoreMessages() {
    const btn = $("#btnLoadMore");
    btn.disabled = true;
    btn.textContent = "加载中...";

    try {
        const res = await fetch(
            `${API.sessionMessages(state.currentDetailEncoded, state.currentDetailSid)}?limit=1000&offset=${state.currentDetailOffset}`
        );
        const data = await res.json();
        state.currentDetailOffset += (data.messages || []).length;
        renderMessages(data.messages || [], false);
    } catch (e) {
        showToast("加载失败", true);
        btn.disabled = false;
        btn.textContent = "加载更多";
    }
}

function renderMessages(messages, isFirstPage) {
    if (isFirstPage && messages.length === 0) {
        detailMessages.innerHTML = '<div class="loading-text">暂无消息</div>';
        return;
    }

    if (isFirstPage) {
        detailMessages.innerHTML = "";
    }

    messages.forEach((msg) => {
        const block = document.createElement("div");
        block.className = "message-block";
        let roleLabel, roleClass;
        if (msg.role === "user") {
            roleLabel = "你";
            roleClass = "user";
        } else if (msg.role === "assistant") {
            roleLabel = "Claude";
            roleClass = "assistant";
        } else if (msg.role === "tool") {
            roleLabel = "工具";
            roleClass = "tool";
        } else {
            roleLabel = msg.role;
            roleClass = "system";
        }
        block.innerHTML = `
            <div class="message-role ${roleClass}">${roleLabel}</div>
            <div class="message-content">${escapeHtml(truncate(msg.content, 500))}</div>
            ${msg.timestamp ? `<div class="message-time">${formatIsoTime(msg.timestamp)}</div>` : ""}
        `;
        detailMessages.appendChild(block);
    });

    // Update footer: show loaded/total and load-more button
    const loaded = state.currentDetailOffset;
    const total = state.currentDetailTotal;
    const footer = $("#detailFooter");
    const footerInfo = $("#detailFooterInfo");
    const loadMoreBtn = $("#btnLoadMore");

    if (loaded < total) {
        footer.style.display = "flex";
        footerInfo.textContent = `已加载 ${loaded} / ${total} 条消息`;
        loadMoreBtn.disabled = false;
        loadMoreBtn.textContent = "加载更多";
        loadMoreBtn.onclick = loadMoreMessages;
    } else if (total > 0) {
        footer.style.display = "flex";
        footerInfo.textContent = `共 ${total} 条消息`;
        loadMoreBtn.style.display = "none";
    } else {
        footer.style.display = "none";
    }
}

function closeDetail() {
    detailPanel.style.display = "none";
    state.selectedSession = null;
    state.currentDetailEncoded = null;
    state.currentDetailSid = null;
    state.currentDetailOffset = 0;
    state.currentDetailTotal = 0;
    $("#detailFooter").style.display = "none";
    $$(".session-card").forEach((c) => c.classList.remove("selected"));
}

// === Search ===
let searchTimeout;
searchInput.addEventListener("input", () => {
    const q = searchInput.value.trim();
    searchClear.style.display = q ? "block" : "none";

    clearTimeout(searchTimeout);
    if (q.length < 1) {
        if (state.selectedProject) {
            loadSessions(state.selectedProject);
        } else {
            renderProjects();
        }
        return;
    }

    searchTimeout = setTimeout(() => doSearch(q), 300);
});

searchClear.addEventListener("click", () => {
    searchInput.value = "";
    searchClear.style.display = "none";
    if (state.selectedProject) {
        loadSessions(state.selectedProject);
    } else {
        renderProjects();
    }
});

async function doSearch(query) {
    try {
        const res = await fetch(API.search(query));
        const results = await res.json();

        projectList.innerHTML = "";
        sessionList.innerHTML = "";
        breadcrumb.textContent = `搜索: "${query}" — ${results.length} 个结果`;
        emptyState.style.display = "none";

        if (results.length === 0) {
            sessionList.innerHTML = `
                <div class="empty-state">
                    <p>未找到匹配的对话</p>
                </div>`;
            return;
        }

        // Render search results as cards
        results.forEach((r) => {
            const card = document.createElement("div");
            card.className = "session-card";
            card.innerHTML = `
                <div class="session-card-header">
                    <div class="session-card-message">📝 ${escapeHtml(r.display)}</div>
                </div>
                <div class="session-card-meta">
                    <span>📁 ${escapeHtml(r.projectName)}</span>
                    <span>🕐 ${formatTime(r.timestamp)}</span>
                </div>
            `;
            card.addEventListener("click", () => {
                const proj = state.projects.find((p) => p.encoded === r.encoded);
                if (proj) {
                    searchInput.value = "";
                    searchClear.style.display = "none";
                    selectProject(proj);
                }
            });
            sessionList.appendChild(card);
        });
    } catch (e) {
        console.error("Search failed:", e);
    }
}

// === New Project ===
async function handleNewProject() {
    btnNewProject.disabled = true;
    try {
        // Step 1: Open native folder picker
        const pickerRes = await fetch(API.selectFolder, { method: "POST" });
        const pickerData = await pickerRes.json();
        if (!pickerData.success) {
            if (pickerData.error !== "用户取消选择") {
                showToast(pickerData.error || "文件夹选择失败", true);
            }
            return;
        }

        const folderPath = pickerData.path;

        // Step 2: Check if already in project list
        const existing = state.projects.find((p) => p.projectPath === folderPath);
        if (existing) {
            selectProject(existing);
            showToast("项目已存在，已切换到该项目");
            return;
        }

        // Step 3: Register as custom project
        const addRes = await fetch(API.customProjects, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: folderPath }),
        });
        const addData = await addRes.json();
        if (!addData.success) {
            showToast(addData.error || "添加项目失败", true);
            return;
        }

        // Step 4: Reload projects and select the new one
        await loadProjects();
        await loadStats();
        const newProj = state.projects.find((p) => p.projectPath === folderPath);
        if (newProj) {
            selectProject(newProj);
        }
        showToast(`已添加项目: ${folderPath.split("/").pop()}`);
    } catch (e) {
        showToast("操作失败: " + e.message, true);
    } finally {
        btnNewProject.disabled = false;
    }
}

// === Event Listeners ===
function setupEventListeners() {
    refreshBtn.addEventListener("click", async () => {
        await loadProjects();
        await loadStats();
        if (state.selectedProject) {
            await loadSessions(state.selectedProject);
        }
        showToast("已刷新");
    });

    btnNewProject.addEventListener("click", handleNewProject);

    if (btnActiveSessions) {
        btnActiveSessions.addEventListener("click", toggleActiveSessions);
    }

    btnShutdown.addEventListener("click", async () => {
        if (!confirm("确定要停止会话管理器服务吗？\n\n停止后需重新启动才能使用。")) return;
        btnShutdown.disabled = true;
        btnShutdown.textContent = "停止中...";
        try {
            await fetch(API.shutdown, { method: "POST" });
        } catch (e) {
            // 服务停止后请求会失败，这是预期行为
        }
        setTimeout(() => {
            document.body.innerHTML = `
                <div style="display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:16px;font-family:-apple-system,sans-serif;color:var(--text-secondary)">
                    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.4">
                        <rect x="6" y="6" width="12" height="12" rx="1"/>
                    </svg>
                    <p style="font-size:14px">会话管理器已停止</p>
                    <p style="font-size:12px;color:var(--text-tertiary)">可关闭此页面，或重新启动服务</p>
                </div>`;
        }, 1000);
    });

    btnContinue.addEventListener("click", () => {
        if (state.selectedProjectPath) {
            launchClaude("continue", state.selectedProjectPath);
        }
    });

    btnNewSession.addEventListener("click", () => {
        if (state.selectedProjectPath) {
            launchClaude("new", state.selectedProjectPath);
        }
    });

    const chkSkipPermissions = $("#chkSkipPermissions");
    if (chkSkipPermissions) {
        chkSkipPermissions.addEventListener("change", () => {
            state.dangerouslySkipPermissions = chkSkipPermissions.checked;
        });
    }

    $("#btnCloseDetail").addEventListener("click", closeDetail);

    // Close detail on Escape
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeDetail();
    });
}

function setupKeyboardShortcuts() {
    document.addEventListener("keydown", (e) => {
        const mod = e.metaKey || e.ctrlKey;
        if (mod && e.key === "k" || e.key === "f") {
            e.preventDefault();
            searchInput.focus();
        }
        if (mod && e.key === "c" && e.shiftKey) {
            e.preventDefault();
            if (state.selectedProjectPath) {
                launchClaude("continue", state.selectedProjectPath);
            }
        }
        if (mod && e.key === "n" && e.shiftKey) {
            e.preventDefault();
            if (state.selectedProjectPath) {
                launchClaude("new", state.selectedProjectPath);
            }
        }
        if (mod && e.key === "r") {
            e.preventDefault();
            refreshBtn.click();
        }
    });
}

// === Utilities ===
function formatTime(ms) {
    if (!ms) return "";
    const now = Date.now();
    const diff = now - ms;
    const mins = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (mins < 1) return "刚刚";
    if (mins < 60) return `${mins} 分钟前`;
    if (hours < 24) return `${hours} 小时前`;
    if (days < 7) return `${days} 天前`;

    const d = new Date(ms);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    const h = String(d.getHours()).padStart(2, "0");
    const min = String(d.getMinutes()).padStart(2, "0");

    if (y === new Date().getFullYear()) {
        return `${m}-${day} ${h}:${min}`;
    }
    return `${y}-${m}-${day}`;
}

function formatIsoTime(iso) {
    if (!iso) return "";
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        const m = String(d.getMonth() + 1).padStart(2, "0");
        const day = String(d.getDate()).padStart(2, "0");
        const h = String(d.getHours()).padStart(2, "0");
        const min = String(d.getMinutes()).padStart(2, "0");
        const s = String(d.getSeconds()).padStart(2, "0");
        return `${m}-${day} ${h}:${min}:${s}`;
    } catch {
        return iso;
    }
}

function truncate(str, len) {
    if (!str) return "";
    if (str.length <= len) return str;
    return str.substring(0, len) + "...";
}

async function copySessionId(sessionId) {
    try {
        await navigator.clipboard.writeText(sessionId);
        showToast("已复制会话 ID");
    } catch {
        // Fallback for older browsers
        const ta = document.createElement("textarea");
        ta.value = sessionId;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        showToast("已复制会话 ID");
    }
}

function escapeHtml(str) {
    if (!str) return "";
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

function showToast(msg, isError = false) {
    const existing = document.querySelector(".toast");
    if (existing) existing.remove();

    const toast = document.createElement("div");
    toast.className = "toast" + (isError ? " error" : "");
    toast.textContent = msg;
    document.body.appendChild(toast);

    setTimeout(() => toast.remove(), 3000);
}

function updateStatusBar() {
    const statusBarText = $("#statusBarText");
    if (!state.stats) return;
    let text = `${state.stats.totalProjects} 个项目 · ${state.stats.totalSessions} 个会话`;
    if (state.stats.activeSessions > 0) {
        text += ` · ${state.stats.activeSessions} 个活跃`;
    }
    if (state.selectedProject) {
        text += ` · 当前: ${state.sessions.length} 个会话`;
    }
    // Terminal info
    if (state.terminalConfig) {
        const t = state.terminalConfig;
        if (t.cmuxInstalled) {
            text += t.insideCmux ? " · 🖥 cmux" : " · 🖥 cmux(目录模式)";
        } else {
            text += " · 🖥 Terminal";
        }
    }
    if (statusBarText) statusBarText.textContent = text;
}

// === Start ===
init();
