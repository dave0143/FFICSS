如需啟動時間校正功能需
系統權限要求
由於需要修改系統時間，需要確保程序有 sudo 權限。建議在 /etc/sudoers 中添加：
jetson ALL=(ALL) NOPASSWD: /bin/date, /usr/bin/timedatectl, /sbin/hwclock