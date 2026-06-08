// state variables
let accessToken = null;
let currentFolderId = 'root';
let folderPath = [{ id: 'root', name: '내 드라이브' }];
let uploadQueue = [];
let isUploading = false;

// DOM Elements
const authBtn = document.getElementById('authBtn');
const connectionStatus = document.getElementById('connectionStatus');
const profileSection = document.getElementById('profileSection');
const userAvatar = document.getElementById('userAvatar');
const userName = document.getElementById('userName');
const userEmail = document.getElementById('userEmail');

const btnShowUpload = document.getElementById('btnShowUpload');
const btnShowSettings = document.getElementById('btnShowSettings');
const dashboardView = document.getElementById('dashboardView');
const settingsView = document.getElementById('settingsView');

const clientIdInput = document.getElementById('clientId');
const apiKeyInput = document.getElementById('apiKey');
const saveSettingsBtn = document.getElementById('saveSettingsBtn');

const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const uploadQueueContainer = document.getElementById('uploadQueue');
const startUploadBtn = document.getElementById('startUploadBtn');
const clearQueueBtn = document.getElementById('clearQueueBtn');

const breadcrumbs = document.getElementById('breadcrumbs');
const newFolderBtn = document.getElementById('newFolderBtn');
const refreshBtn = document.getElementById('refreshBtn');
const folderList = document.getElementById('folderList');
const toast = document.getElementById('toast');

// Initialize Lucide Icons
lucide.createIcons();

// Load Credentials from localStorage
function loadCredentials() {
    const savedClientId = localStorage.getItem('gdrive_client_id');
    const savedApiKey = localStorage.getItem('gdrive_api_key');
    if (savedClientId) clientIdInput.value = savedClientId;
    if (savedApiKey) apiKeyInput.value = savedApiKey;
}

// Save Credentials to localStorage
saveSettingsBtn.addEventListener('click', () => {
    const clientId = clientIdInput.value.trim();
    const apiKey = apiKeyInput.value.trim();

    if (!clientId) {
        showToast('클라이언트 ID를 입력해 주세요.', 'error');
        return;
    }

    localStorage.setItem('gdrive_client_id', clientId);
    localStorage.setItem('gdrive_api_key', apiKey);
    showToast('설정이 저장되었습니다!', 'success');
    
    // Attempt initialization now that we have a client ID
    initTokenClient(false);
    
    // Switch to upload view
    showView('upload');
});

// Navigation between views
btnShowUpload.addEventListener('click', () => showView('upload'));
btnShowSettings.addEventListener('click', () => showView('settings'));

function showView(view) {
    if (view === 'upload') {
        dashboardView.classList.remove('hidden');
        settingsView.classList.add('hidden');
        btnShowUpload.classList.add('active');
        btnShowSettings.classList.remove('active');
    } else {
        dashboardView.classList.add('hidden');
        settingsView.classList.remove('hidden');
        btnShowUpload.classList.remove('active');
        btnShowSettings.classList.add('active');
    }
}

// Toast Notification helper
function showToast(message, type = 'success') {
    toast.textContent = message;
    toast.className = `toast show toast-${type}`;
    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

// Connection Status helper
function updateConnectionStatus(connected) {
    if (connected) {
        connectionStatus.textContent = '연결됨';
        connectionStatus.className = 'status-connected';
        authBtn.innerHTML = '<i data-lucide="log-out"></i> 로그아웃';
        newFolderBtn.removeAttribute('disabled');
        refreshBtn.removeAttribute('disabled');
        lucide.createIcons();
    } else {
        connectionStatus.textContent = '연결 안 됨';
        connectionStatus.className = 'status-disconnected';
        authBtn.innerHTML = '<i data-lucide="log-in"></i> 구글 로그인';
        profileSection.style.display = 'none';
        newFolderBtn.setAttribute('disabled', 'true');
        refreshBtn.setAttribute('disabled', 'true');
        folderList.innerHTML = `<div class="empty-state"><i data-lucide="lock"></i><p>먼저 로그인을 해주세요.</p></div>`;
        lucide.createIcons();
    }
}

// Google Auth Handlers
let tokenClient = null;
let gisInitialized = false;

// Called when Google GSI script finishes loading
window.gisLoaded = function() {
    gisInitialized = true;
    initTokenClient(false);
};

function initTokenClient(showError = false) {
    if (!gisInitialized) {
        if (showError) showToast('구글 API 라이브러리가 로드되지 않았습니다. 잠시 후 다시 시도해 주세요.', 'error');
        return false;
    }

    const clientId = localStorage.getItem('gdrive_client_id');
    if (!clientId) {
        if (showError) {
            showToast('구글 로그인 전 API 설정을 마쳐주세요.', 'error');
            showView('settings');
        }
        return false;
    }

    try {
        tokenClient = google.accounts.oauth2.initTokenClient({
            client_id: clientId,
            scope: 'https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/drive.metadata.readonly https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/userinfo.email',
            callback: (tokenResponse) => {
                if (tokenResponse.error) {
                    console.error(tokenResponse);
                    showToast('로그인에 실패했습니다.', 'error');
                    return;
                }
                accessToken = tokenResponse.access_token;
                updateConnectionStatus(true);
                fetchUserInfo();
                loadFolderContents(currentFolderId);
                showToast('성공적으로 로그인되었습니다.', 'success');
            },
        });
        return true;
    } catch (e) {
        console.error('GIS 초기화 에러:', e);
        if (showError) showToast('인증 클라이언트 설정에 실패했습니다. Client ID가 정확한지 확인해주세요.', 'error');
        return false;
    }
}

authBtn.addEventListener('click', () => {
    if (accessToken) {
        // Sign out
        google.accounts.oauth2.revokeToken(accessToken, () => {
            accessToken = null;
            updateConnectionStatus(false);
            showToast('로그아웃되었습니다.', 'success');
        });
    } else {
        // Sign in
        if (!gisInitialized) {
            showToast('구글 API 클라이언트를 불러오는 중입니다. 잠시 후 다시 시도해 주세요.', 'error');
            return;
        }
        // Force showing error if the client id isn't set when the user manually clicks
        if (initTokenClient(true)) {
            tokenClient.requestAccessToken({ prompt: 'consent' });
        }
    }
});

// Fetch User Info
async function fetchUserInfo() {
    try {
        const response = await fetch('https://www.googleapis.com/oauth2/v2/userinfo', {
            headers: { 'Authorization': `Bearer ${accessToken}` }
        });
        if (response.ok) {
            const data = await response.json();
            profileSection.style.display = 'flex';
            userAvatar.src = data.picture || 'https://via.placeholder.com/40';
            userName.textContent = data.name || '구글 사용자';
            userEmail.textContent = data.email || '';
        }
    } catch (err) {
        console.error('사용자 정보를 가져오는 데 실패했습니다.', err);
    }
}

// Load Folder Contents
async function loadFolderContents(folderId) {
    if (!accessToken) return;

    folderList.innerHTML = `<div class="empty-state"><i data-lucide="loader-2" class="animate-spin"></i><p>로딩 중...</p></div>`;
    lucide.createIcons();

    const apiKey = localStorage.getItem('gdrive_api_key');
    let url = `https://www.googleapis.com/drive/v3/files?q=${encodeURIComponent(`'${folderId}' in parents and trashed = false`)}&fields=files(id,name,mimeType,size,modifiedTime)&orderBy=folder,name`;
    if (apiKey) {
        url += `&key=${apiKey}`;
    }

    try {
        const response = await fetch(url, {
            headers: { 'Authorization': `Bearer ${accessToken}` }
        });
        
        if (!response.ok) {
            throw new Error('API 응답 실패');
        }

        const data = await response.json();
        renderExplorer(data.files || []);
    } catch (err) {
        console.error(err);
        showToast('파일 목록 로드 중 오류가 발생했습니다. 권한 설정이나 API Key를 확인하세요.', 'error');
        folderList.innerHTML = `<div class="empty-state"><i data-lucide="alert-triangle"></i><p>목록을 불러오지 못했습니다.</p></div>`;
        lucide.createIcons();
    }
}

// Render Folder List in Explorer
function renderExplorer(items) {
    if (items.length === 0) {
        folderList.innerHTML = `<div class="empty-state"><i data-lucide="folder-open"></i><p>빈 폴더입니다.</p></div>`;
        lucide.createIcons();
        return;
    }

    folderList.innerHTML = '';
    
    // Sort items so folders come first
    items.sort((a, b) => {
        const aIsFolder = a.mimeType === 'application/vnd.google-apps.folder';
        const bIsFolder = b.mimeType === 'application/vnd.google-apps.folder';
        if (aIsFolder && !bIsFolder) return -1;
        if (!aIsFolder && bIsFolder) return 1;
        return a.name.localeCompare(b.name);
    });

    items.forEach(item => {
        const isFolder = item.mimeType === 'application/vnd.google-apps.folder';
        const el = document.createElement('div');
        el.className = `explorer-item ${isFolder ? 'is-folder' : ''}`;
        
        const date = new Date(item.modifiedTime).toLocaleDateString('ko-KR', {
            year: 'numeric',
            month: 'short',
            day: 'numeric'
        });

        const iconName = isFolder ? 'folder' : 'file';
        
        el.innerHTML = `
            <div class="explorer-item-info">
                <i data-lucide="${iconName}" class="explorer-item-icon"></i>
                <div>
                    <span class="explorer-item-name" title="${item.name}">${item.name}</span>
                    <div style="font-size: 11px; color: var(--text-dim);">${date} ${!isFolder && item.size ? '• ' + formatBytes(item.size) : ''}</div>
                </div>
            </div>
            <div class="explorer-item-actions">
                ${!isFolder ? `<button class="action-icon-btn download-btn" data-id="${item.id}" data-name="${item.name}" title="다운로드"><i data-lucide="download"></i></button>` : ''}
                <button class="action-icon-btn delete-btn" data-id="${item.id}" data-name="${item.name}" title="휴지통으로 이동"><i data-lucide="trash-2"></i></button>
            </div>
        `;

        if (isFolder) {
            el.addEventListener('click', (e) => {
                // If action buttons were clicked, ignore
                if (e.target.closest('.action-icon-btn')) return;
                navigateToFolder(item.id, item.name);
            });
        }

        folderList.appendChild(el);
    });

    // Wire up actions
    document.querySelectorAll('.download-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            downloadFile(btn.dataset.id, btn.dataset.name);
        });
    });

    document.querySelectorAll('.delete-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (confirm(`"${btn.dataset.name}" 파일을 휴지통으로 이동하시겠습니까?`)) {
                deleteFile(btn.dataset.id);
            }
        });
    });

    lucide.createIcons();
}

// Navigation breadcrumbs & history
function navigateToFolder(id, name) {
    currentFolderId = id;
    // check if it's already in the path, if so, truncate to it
    const index = folderPath.findIndex(item => item.id === id);
    if (index !== -1) {
        folderPath = folderPath.slice(0, index + 1);
    } else {
        folderPath.push({ id, name });
    }
    renderBreadcrumbs();
    loadFolderContents(currentFolderId);
}

// Render Breadcrumbs path
function renderBreadcrumbs() {
    breadcrumbs.innerHTML = '';
    folderPath.forEach((item, idx) => {
        const isLast = idx === folderPath.length - 1;
        const span = document.createElement('span');
        span.className = `breadcrumb-item ${isLast ? 'active' : ''}`;
        span.textContent = item.name;
        span.dataset.id = item.id;
        
        if (!isLast) {
            span.addEventListener('click', () => {
                navigateToFolder(item.id, item.name);
            });
        }
        breadcrumbs.appendChild(span);

        if (!isLast) {
            const separator = document.createElement('span');
            separator.className = 'breadcrumb-separator';
            separator.textContent = ' / ';
            breadcrumbs.appendChild(separator);
        }
    });
}

// Refresh folder view
refreshBtn.addEventListener('click', () => {
    loadFolderContents(currentFolderId);
});

// Create Folder
newFolderBtn.addEventListener('click', async () => {
    const name = prompt('새 폴더 이름을 입력하세요:');
    if (!name || !name.trim()) return;

    try {
        const response = await fetch('https://www.googleapis.com/drive/v3/files', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${accessToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                name: name.trim(),
                mimeType: 'application/vnd.google-apps.folder',
                parents: [currentFolderId]
            })
        });

        if (response.ok) {
            showToast('새 폴더가 생성되었습니다.', 'success');
            loadFolderContents(currentFolderId);
        } else {
            throw new Error('폴더 생성 실패');
        }
    } catch (err) {
        showToast('폴더를 생성하지 못했습니다.', 'error');
        console.error(err);
    }
});

// File Upload Handler (Multipart Upload)
function uploadSingleFile(queueItem) {
    return new Promise((resolve, reject) => {
        const { file, id } = queueItem;
        updateQueueItemStatus(id, 'uploading', '업로드 중...');

        const metadata = {
            name: file.name,
            mimeType: file.type || 'application/octet-stream',
            parents: [currentFolderId]
        };

        const boundary = '-------314159265358979323846';
        const delimiter = "\r\n--" + boundary + "\r\n";
        const close_delim = "\r\n--" + boundary + "--";

        const reader = new FileReader();
        reader.readAsArrayBuffer(file);
        reader.onload = function(e) {
            const fileData = e.target.result;
            const metadataPart = delimiter +
                'Content-Type: application/json; charset=UTF-8\r\n\r\n' +
                JSON.stringify(metadata) +
                '\r\n' + delimiter;
            const fileHeader = 'Content-Type: ' + (file.type || 'application/octet-stream') + '\r\n\r\n';

            const blobParts = [
                new TextEncoder().encode(metadataPart),
                new TextEncoder().encode(fileHeader),
                new Uint8Array(fileData),
                new TextEncoder().encode(close_delim)
            ];

            const body = new Blob(blobParts, { type: 'multipart/related; boundary=' + boundary });

            const xhr = new XMLHttpRequest();
            xhr.open('POST', 'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart');
            xhr.setRequestHeader('Authorization', 'Bearer ' + accessToken);
            xhr.setRequestHeader('Content-Type', 'multipart/related; boundary=' + boundary);

            xhr.upload.onprogress = function(event) {
                if (event.lengthComputable) {
                    const percentComplete = (event.loaded / event.total) * 100;
                    updateQueueItemProgress(id, percentComplete);
                }
            };

            xhr.onload = function() {
                if (xhr.status >= 200 && xhr.status < 300) {
                    updateQueueItemStatus(id, 'success', '완료');
                    updateQueueItemProgress(id, 100);
                    showToast(`"${file.name}" 업로드 성공`, 'success');
                    resolve();
                } else {
                    updateQueueItemStatus(id, 'error', '실패');
                    showToast(`"${file.name}" 업로드 실패`, 'error');
                    reject(new Error('Upload failed'));
                }
            };

            xhr.onerror = function() {
                updateQueueItemStatus(id, 'error', '오류');
                showToast('네트워크 오류가 발생했습니다.', 'error');
                reject(new Error('Network error'));
            };

            xhr.send(body);
        };
    });
}

// Batch upload executor
async function processQueue() {
    if (isUploading) return;
    const pendingItems = uploadQueue.filter(item => item.status === 'waiting');
    if (pendingItems.length === 0) return;

    isUploading = true;
    startUploadBtn.setAttribute('disabled', 'true');

    for (const item of pendingItems) {
        try {
            await uploadSingleFile(item);
        } catch (e) {
            console.error(e);
        }
    }

    isUploading = false;
    startUploadBtn.removeAttribute('disabled');
    loadFolderContents(currentFolderId);
}

startUploadBtn.addEventListener('click', () => {
    if (!accessToken) {
        showToast('로그인이 필요합니다.', 'error');
        return;
    }
    processQueue();
});

// File queue management
function addFilesToQueue(files) {
    if (uploadQueue.length === 0) {
        uploadQueueContainer.innerHTML = '';
        clearQueueBtn.classList.remove('hidden');
    }

    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const id = 'upload_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
        const item = {
            id,
            file,
            status: 'waiting',
            progress: 0
        };
        uploadQueue.push(item);
        renderQueueItem(item);
    }
    startUploadBtn.removeAttribute('disabled');
}

function renderQueueItem(item) {
    const el = document.createElement('div');
    el.id = item.id;
    el.className = 'queue-item';
    el.innerHTML = `
        <div class="queue-item-header">
            <div class="queue-file-details">
                <span class="queue-file-name" title="${item.file.name}">${item.file.name}</span>
                <span class="queue-file-size">${formatBytes(item.file.size)}</span>
            </div>
            <span class="queue-item-status status-waiting">대기중</span>
            <button class="remove-queue-item" data-id="${item.id}"><i data-lucide="x"></i></button>
        </div>
        <div class="queue-item-progress">
            <div class="progress-fill" style="width: 0%"></div>
        </div>
    `;

    el.querySelector('.remove-queue-item').addEventListener('click', (e) => {
        const btn = e.currentTarget;
        removeQueueItem(btn.dataset.id);
    });

    uploadQueueContainer.appendChild(el);
    lucide.createIcons();
}

function removeQueueItem(id) {
    uploadQueue = uploadQueue.filter(item => item.id !== id);
    const el = document.getElementById(id);
    if (el) el.remove();

    if (uploadQueue.length === 0) {
        resetQueueUI();
    }
}

function resetQueueUI() {
    uploadQueueContainer.innerHTML = `
        <div class="empty-state">
            <i data-lucide="inbox"></i>
            <p>대기 중인 파일이 없습니다.</p>
        </div>
    `;
    clearQueueBtn.classList.add('hidden');
    startUploadBtn.setAttribute('disabled', 'true');
    lucide.createIcons();
}

clearQueueBtn.addEventListener('click', () => {
    uploadQueue = [];
    resetQueueUI();
});

function updateQueueItemProgress(id, progress) {
    const item = uploadQueue.find(i => i.id === id);
    if (item) {
        item.progress = progress;
        const el = document.getElementById(id);
        if (el) {
            const bar = el.querySelector('.progress-fill');
            if (bar) bar.style.width = `${progress}%`;
        }
    }
}

function updateQueueItemStatus(id, status, text) {
    const item = uploadQueue.find(i => i.id === id);
    if (item) {
        item.status = status;
        const el = document.getElementById(id);
        if (el) {
            const statusBadge = el.querySelector('.queue-item-status');
            if (statusBadge) {
                statusBadge.textContent = text;
                statusBadge.className = `queue-item-status status-${status}`;
            }
            if (status === 'success' || status === 'error') {
                const bar = el.querySelector('.progress-fill');
                if (bar) bar.classList.add(status);
            }
        }
    }
}

// Download File Action
async function downloadFile(fileId, fileName) {
    if (!accessToken) return;
    showToast('다운로드를 요청하고 있습니다...', 'success');

    try {
        const response = await fetch(`https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`, {
            headers: { 'Authorization': `Bearer ${accessToken}` }
        });

        if (!response.ok) throw new Error('파일 다운로드 실패');
        
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = fileName;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        showToast('다운로드 완료!', 'success');
    } catch (err) {
        console.error(err);
        showToast('파일 다운로드에 실패했습니다.', 'error');
    }
}

// Delete (Trash) File Action
async function deleteFile(fileId) {
    if (!accessToken) return;

    try {
        const response = await fetch(`https://www.googleapis.com/drive/v3/files/${fileId}`, {
            method: 'PATCH',
            headers: {
                'Authorization': `Bearer ${accessToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                trashed: true
            })
        });

        if (response.ok) {
            showToast('파일을 휴지통으로 이동했습니다.', 'success');
            loadFolderContents(currentFolderId);
        } else {
            throw new Error('파일 삭제/휴지통 이동 실패');
        }
    } catch (err) {
        console.error(err);
        showToast('휴지통으로 이동하지 못했습니다.', 'error');
    }
}

// Drag & Drop Listeners
dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
});

dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('dragover');
});

dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    if (e.dataTransfer.files.length > 0) {
        addFilesToQueue(e.dataTransfer.files);
    }
});

dropZone.addEventListener('click', () => {
    fileInput.click();
});

fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        addFilesToQueue(e.target.files);
    }
});

// Format byte size to readable strings
function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

// Initial credentials load
loadCredentials();
