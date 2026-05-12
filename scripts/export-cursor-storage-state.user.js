// ==UserScript==
// @name         Export Cursor Storage State
// @namespace    http://tampermonkey.net/
// @version      1.0
// @description  Export cookies + localStorage from cursor.com for invoice-maid
// @author       invoice-maid
// @match        https://cursor.com/*
// @match        https://*.cursor.com/*
// @grant        none
// ==/UserScript==

(function() {
    'use strict';

    // 添加一个浮动按钮
    const button = document.createElement('button');
    button.textContent = '📋 Export Storage State';
    button.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        z-index: 999999;
        padding: 12px 20px;
        background: #4CAF50;
        color: white;
        border: none;
        border-radius: 6px;
        font-size: 14px;
        font-weight: bold;
        cursor: pointer;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    `;
    button.onmouseover = () => button.style.background = '#45a049';
    button.onmouseout = () => button.style.background = '#4CAF50';

    button.onclick = async () => {
        try {
            // 1. 获取所有 cookies（只能拿到非 httpOnly 的）
            const cookiesStr = document.cookie;
            const cookies = cookiesStr.split(';').map(c => {
                const [name, ...valueParts] = c.trim().split('=');
                const value = valueParts.join('=');
                return {
                    name: name,
                    value: value,
                    domain: '.cursor.com',
                    path: '/',
                    expires: -1,  // -1 表示 session cookie
                    httpOnly: false,  // 我们只能拿到非 httpOnly 的
                    secure: true,
                    sameSite: 'Lax'
                };
            }).filter(c => c.name && c.value);

            // 2. 获取 localStorage
            const localStorageItems = [];
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                const value = localStorage.getItem(key);
                localStorageItems.push({ name: key, value: value });
            }

            // 3. 拼成 Playwright storage_state 格式
            const storageState = {
                cookies: cookies,
                origins: [
                    {
                        origin: 'https://cursor.com',
                        localStorage: localStorageItems
                    }
                ]
            };

            // 4. 输出到控制台
            console.log('=== Cursor Storage State ===');
            console.log(JSON.stringify(storageState, null, 2));
            console.log('=== End ===');

            // 5. 复制到剪贴板
            const json = JSON.stringify(storageState);
            await navigator.clipboard.writeText(json);

            // 6. 显示成功提示
            button.textContent = '✅ 已复制到剪贴板！';
            button.style.background = '#2196F3';
            setTimeout(() => {
                button.textContent = '📋 Export Storage State';
                button.style.background = '#4CAF50';
            }, 3000);

            // 7. 弹窗提示
            alert('✅ Storage State 已复制到剪贴板！\n\n下一步：\n1. 粘贴到文本编辑器保存为 cursor-storage-state.json\n2. 运行命令创建 Cursor 账号（见控制台）');

            // 8. 在控制台输出下一步命令
            console.log('\n下一步命令（替换 YOUR_JWT 和文件路径）：');
            console.log(`
curl -X POST "https://invoice-maid.helixzz.com/api/v1/email-accounts" \\
  -H "Authorization: Bearer YOUR_JWT" \\
  -H "Content-Type: application/json" \\
  -d @- <<'EOF'
{
  "email": "你的公司邮箱",
  "type": "cursor",
  "host": "cursor.com",
  "port": 443,
  "username": "你的公司邮箱",
  "password": "",
  "playwright_storage_state": ${json}
}
EOF
            `.trim());

        } catch (err) {
            console.error('导出失败:', err);
            alert('❌ 导出失败: ' + err.message);
        }
    };

    // 等页面加载完再添加按钮
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => document.body.appendChild(button));
    } else {
        document.body.appendChild(button);
    }
})();
