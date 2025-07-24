#!/bin/bash
# NVIDIA Jetson Orin RTSP Server IPTables 防火牆配置
# 版本: 1.0
# 用途: 為 RTSP 服務器配置安全的防火牆規則

set -e  # 遇到錯誤時退出

# 顏色定義
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 網路配置
RTSP_PORT=8554
RTP_PORT_RANGE="5004:65535"
SSH_PORT=22

# IP 地址配置（根據您的架構）
EO_CAMERA_IP="192.168.144.108"
IR_CAMERA_IP="192.168.144.6"
AI_MODULE_IP="192.168.144.100"
LOCAL_NETWORK="192.168.144.0/24"
MANAGEMENT_NETWORK="192.168.1.0/24"

# 日誌函數
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 檢查是否為 root 用戶
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "此腳本需要 root 權限運行"
        echo "請使用: sudo $0"
        exit 1
    fi
}

# 備份當前規則
backup_rules() {
    log_info "備份當前 iptables 規則..."
    iptables-save > /tmp/iptables_backup_$(date +%Y%m%d_%H%M%S).rules
    log_info "規則已備份到 /tmp/"
}

# 清除所有現有規則
clear_rules() {
    log_info "清除現有 iptables 規則..."
    
    # 設置預設政策為 ACCEPT（避免鎖定）
    iptables -P INPUT ACCEPT
    iptables -P FORWARD ACCEPT
    iptables -P OUTPUT ACCEPT
    
    # 清除所有規則
    iptables -F
    iptables -X
    iptables -t nat -F
    iptables -t nat -X
    iptables -t mangle -F
    iptables -t mangle -X
    iptables -t raw -F
    iptables -t raw -X
}

# 設置基本規則
setup_basic_rules() {
    log_info "設置基本防火牆規則..."
    
    # 允許本地環回
    iptables -A INPUT -i lo -j ACCEPT
    iptables -A OUTPUT -o lo -j ACCEPT
    
    # 允許已建立的連接
    iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
    iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED -j ACCEPT
    
    # 允許所有出站連接
    iptables -A OUTPUT -j ACCEPT
}

# 設置 SSH 規則
setup_ssh_rules() {
    log_info "設置 SSH 訪問規則..."
    
    # 允許來自管理網路的 SSH
    iptables -A INPUT -p tcp -s $MANAGEMENT_NETWORK --dport $SSH_PORT -j ACCEPT
    iptables -A INPUT -p tcp -s $LOCAL_NETWORK --dport $SSH_PORT -j ACCEPT
    
    # SSH 暴力破解保護
    iptables -A INPUT -p tcp --dport $SSH_PORT -m conntrack --ctstate NEW -m recent --set
    iptables -A INPUT -p tcp --dport $SSH_PORT -m conntrack --ctstate NEW -m recent --update --seconds 60 --hitcount 4 -j DROP
}

# 設置 RTSP 服務器規則
setup_rtsp_rules() {
    log_info "設置 RTSP 服務器規則..."
    
    # RTSP 控制連接 (TCP 8554)
    log_info "  - 允許 RTSP 控制端口 $RTSP_PORT/tcp"
    iptables -A INPUT -p tcp --dport $RTSP_PORT -j ACCEPT
    
    # RTP/RTCP 數據傳輸 (UDP 5004-65535)
    log_info "  - 允許 RTP/RTCP 數據端口 $RTP_PORT_RANGE/udp"
    iptables -A INPUT -p udp --dport $RTP_PORT_RANGE -j ACCEPT
    
    # 允許 RTSP 服務器主動連接攝像頭
    log_info "  - 允許連接到 EO 攝像頭 ($EO_CAMERA_IP:554)"
    iptables -A OUTPUT -p tcp -d $EO_CAMERA_IP --dport 554 -j ACCEPT
    
    log_info "  - 允許連接到 IR 攝像頭 ($IR_CAMERA_IP:8554)"
    iptables -A OUTPUT -p tcp -d $IR_CAMERA_IP --dport 8554 -j ACCEPT
    
    # 允許與 AI 模組的雙向通信
    log_info "  - 允許 AI 模組通信 ($AI_MODULE_IP:8555)"
    iptables -A INPUT -p tcp -s $AI_MODULE_IP --sport 8555 -j ACCEPT
    iptables -A OUTPUT -p tcp -d $AI_MODULE_IP --dport 8555 -j ACCEPT
}

# 設置安全規則
setup_security_rules() {
    log_info "設置安全規則..."
    
    # 防止 SYN flood 攻擊
    iptables -A INPUT -p tcp --syn -m limit --limit 1/s --limit-burst 3 -j ACCEPT
    iptables -A INPUT -p tcp --syn -j DROP
    
    # 防止 ping flood
    iptables -A INPUT -p icmp --icmp-type echo-request -m limit --limit 1/s -j ACCEPT
    iptables -A INPUT -p icmp --icmp-type echo-request -j DROP
    
    # 允許必要的 ICMP
    iptables -A INPUT -p icmp --icmp-type echo-reply -j ACCEPT
    iptables -A INPUT -p icmp --icmp-type destination-unreachable -j ACCEPT
    iptables -A INPUT -p icmp --icmp-type time-exceeded -j ACCEPT
    
    # 記錄並丟棄無效包
    iptables -A INPUT -m conntrack --ctstate INVALID -j LOG --log-prefix "INVALID: "
    iptables -A INPUT -m conntrack --ctstate INVALID -j DROP
}

# 設置客戶端訪問規則
setup_client_rules() {
    log_info "設置客戶端訪問規則..."
    
    # 允許本地網路訪問 RTSP 服務
    iptables -A INPUT -p tcp -s $LOCAL_NETWORK --dport $RTSP_PORT -j ACCEPT
    iptables -A INPUT -p tcp -s $MANAGEMENT_NETWORK --dport $RTSP_PORT -j ACCEPT
    
    # 特定設備訪問（可選，更嚴格的安全）
    # iptables -A INPUT -p tcp -s 192.168.1.100 --dport $RTSP_PORT -j ACCEPT  # 客戶端設備
}

# 設置日誌記錄
setup_logging() {
    log_info "設置日誌記錄..."
    
    # 記錄被拒絕的連接（限制頻率避免日誌洪水）
    iptables -A INPUT -j LOG --log-prefix "IPTABLES-DROPPED: " --log-level 4 -m limit --limit 5/min
    iptables -A INPUT -j DROP
}

# 設置預設政策
setup_default_policies() {
    log_info "設置預設政策..."
    
    # 預設拒絕所有入站連接
    iptables -P INPUT DROP
    iptables -P FORWARD DROP
    
    # 預設允許出站連接
    iptables -P OUTPUT ACCEPT
}

# 顯示規則
show_rules() {
    log_info "當前 iptables 規則："
    echo -e "${BLUE}========================= INPUT 鏈 =========================${NC}"
    iptables -L INPUT -n --line-numbers
    echo -e "${BLUE}========================= OUTPUT 鏈 ========================${NC}"
    iptables -L OUTPUT -n --line-numbers
    echo -e "${BLUE}========================= 統計信息 =========================${NC}"
    iptables -L -n -v | head -20
}

# 保存規則
save_rules() {
    log_info "保存 iptables 規則..."
    
    # 不同系統的保存方法
    if command -v netfilter-persistent &> /dev/null; then
        # Debian/Ubuntu with netfilter-persistent
        netfilter-persistent save
    elif command -v iptables-save &> /dev/null; then
        # 手動保存
        iptables-save > /etc/iptables/rules.v4
    else
        log_warn "無法自動保存規則，請手動執行:"
        echo "iptables-save > /etc/iptables/rules.v4"
    fi
}

# 創建啟動腳本
create_startup_script() {
    log_info "創建開機啟動腳本..."
    
    cat > /etc/systemd/system/iptables-rtsp.service << EOF
[Unit]
Description=RTSP Server IPTables Rules
Before=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/rtsp-iptables.sh load
ExecStop=/usr/local/bin/rtsp-iptables.sh clear
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

    # 複製腳本到系統位置
    cp "$0" /usr/local/bin/rtsp-iptables.sh
    chmod +x /usr/local/bin/rtsp-iptables.sh
    
    # 啟用服務
    systemctl enable iptables-rtsp.service
    log_info "開機啟動服務已配置"
}

# 測試規則
test_rules() {
    log_info "測試防火牆規則..."
    
    echo -e "${YELLOW}測試項目:${NC}"
    echo "1. 本地連接測試:"
    echo "   telnet localhost $RTSP_PORT"
    echo ""
    echo "2. SSH 連接測試:"
    echo "   ssh user@$(hostname -I | cut -d' ' -f1)"
    echo ""
    echo "3. RTSP 流測試:"
    echo "   ffplay rtsp://$(hostname -I | cut -d' ' -f1):$RTSP_PORT/Camera_EO"
    echo ""
    echo "4. 查看連接狀態:"
    echo "   netstat -tulpn | grep $RTSP_PORT"
}

# 主函數
main() {
    case "${1:-setup}" in
        "setup")
            check_root
            log_info "開始配置 RTSP 服務器防火牆..."
            backup_rules
            clear_rules
            setup_basic_rules
            setup_ssh_rules
            setup_rtsp_rules
            setup_security_rules
            setup_client_rules
            setup_logging
            setup_default_policies
            show_rules
            save_rules
            create_startup_script
            test_rules
            log_info "防火牆配置完成！"
            ;;
        "load")
            check_root
            log_info "加載 RTSP 防火牆規則..."
            if [[ -f /etc/iptables/rules.v4 ]]; then
                iptables-restore < /etc/iptables/rules.v4
                log_info "規則加載完成"
            else
                log_error "找不到保存的規則文件"
                exit 1
            fi
            ;;
        "clear")
            check_root
            clear_rules
            log_info "防火牆規則已清除"
            ;;
        "show")
            show_rules
            ;;
        "test")
            test_rules
            ;;
        "help"|"--help"|"-h")
            echo "用法: $0 [setup|load|clear|show|test|help]"
            echo ""
            echo "命令:"
            echo "  setup  - 配置 RTSP 服務器防火牆規則 (預設)"
            echo "  load   - 從文件加載已保存的規則"
            echo "  clear  - 清除所有防火牆規則"
            echo "  show   - 顯示當前規則"
            echo "  test   - 顯示測試命令"
            echo "  help   - 顯示此幫助信息"
            ;;
        *)
            log_error "未知命令: $1"
            echo "使用 '$0 help' 查看可用命令"
            exit 1
            ;;
    esac
}

# 執行主函數
main "$@"