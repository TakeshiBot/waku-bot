#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly ROOT_DIR
export WAKU_PROJECT_NAME="${WAKU_PROJECT_NAME:-waku-bot}"
export WAKU_IMAGE_NAME="${WAKU_IMAGE_NAME:-waku-bot:local}"
export WAKU_PORT="${WAKU_PORT:-8180}"
export WAKU_BIND_ADDRESS="${WAKU_BIND_ADDRESS:-127.0.0.1}"
readonly HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-240}"
readonly MIN_FREE_GB="${MIN_FREE_GB:-5}"
COMPOSE=(docker compose --project-directory "$ROOT_DIR" -p "$WAKU_PROJECT_NAME" -f "$ROOT_DIR/docker-compose.yml")
RESUME_ON_EXIT=0
cd -- "$ROOT_DIR"

log() { printf '\n[waku] %s\n' "$*"; }
die() { log "Lỗi: $*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "Thiếu lệnh '$1'."; }
compose() { "${COMPOSE[@]}" "$@"; }
cleanup() {
  local result=$?
  trap - EXIT
  if ((RESUME_ON_EXIT)); then
    log "Khởi động lại container đã tạm dừng để sao lưu."
    compose start waku || result=1
  fi
  exit "$result"
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
Waku — triển khai trên Linux VPS bằng Docker Compose

  bash run.sh                   Menu khi chạy trong terminal; deploy nếu không có TTY
  bash run.sh init              Chuẩn bị cấu hình và thư mục dữ liệu
  bash run.sh deploy            Build source hiện tại, kiểm tra rồi triển khai
  bash run.sh update            git pull --ff-only rồi deploy
  bash run.sh start             Chạy image đã build, không build lại
  bash run.sh restart           Kiểm tra cấu hình rồi tạo lại container
  bash run.sh stop              Dừng bot, giữ cấu hình và dữ liệu
  bash run.sh status            Trạng thái container
  bash run.sh logs [số-dòng]     Theo dõi log (mặc định 100 dòng cuối)
  bash run.sh check             Kiểm tra cấu hình bằng image đã build
  bash run.sh backup            Tạm dừng bot, sao lưu rồi chạy lại nếu trước đó đang chạy
  bash run.sh disk              Dung lượng Docker và ổ đĩa
  bash run.sh miniapp [domain]   Nginx + HTTPS, bật Mini App và chạy lại bot
  bash run.sh all [domain]       Deploy rồi thiết lập Mini App
  bash run.sh help              Hướng dẫn

Tương thích cú pháp cũ: --deploy, --setup-miniapp, --all, --help.
deploy dùng cache Docker; không tự git pull, xóa dữ liệu hay prune Docker.

Tùy chọn qua biến môi trường (không cần chỉnh nếu dùng mặc định):
  WAKU_PROJECT_NAME   Project Compose (waku-bot)
  WAKU_IMAGE_NAME     Image local (waku-bot:local)
  WAKU_PORT          Cổng HTTP trên VPS (8180)
  WAKU_BIND_ADDRESS  Địa chỉ HTTP trên VPS (127.0.0.1)
  HEALTH_TIMEOUT     Thời gian chờ bot sẵn sàng, giây (240)
  MIN_FREE_GB        Dung lượng trống tối thiểu trước build, GiB (5)
  WAKU_MINIAPP_DOMAIN, CERTBOT_EMAIL   Domain và email cho HTTPS

init giữ settings.toml bằng symlink tới config/settings.toml để /config lưu được
trong Docker. Dữ liệu ở data/, logs/, .agentfs/; bản sao lưu ở .backups/.
Mini App cần Nginx + Certbot trên VPS, DNS trỏ về VPS và cổng 80/443 truy cập được.
Các lệnh hệ thống dùng sudo -n: không hỏi mật khẩu, cần quyền NOPASSWD
hoặc phiên sudo còn hiệu lực. Script không thay đổi sudoers.
EOF
}

validate_options() {
  [[ "$WAKU_PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "WAKU_PROJECT_NAME không hợp lệ."
  if [[ ! "$WAKU_PORT" =~ ^[0-9]{1,5}$ ]] || ((10#$WAKU_PORT < 1 || 10#$WAKU_PORT > 65535)); then
    die "WAKU_PORT phải từ 1 đến 65535."
  fi
  [[ "$WAKU_BIND_ADDRESS" == 127.0.0.1 || "$WAKU_BIND_ADDRESS" == 0.0.0.0 ]] || die "WAKU_BIND_ADDRESS phải là 127.0.0.1 hoặc 0.0.0.0."
  [[ "$HEALTH_TIMEOUT" =~ ^[1-9][0-9]{0,5}$ ]] || die "HEALTH_TIMEOUT phải là số giây dương."
  [[ "$MIN_FREE_GB" =~ ^[0-9]{1,3}$ ]] || die "MIN_FREE_GB phải là số nguyên từ 0 đến 999."
  # Normalize leading zeroes so Compose/Dynaconf and Bash use the same port.
  WAKU_PORT=$((10#$WAKU_PORT))
}

lock_project() {
  require flock
  exec 9>"$ROOT_DIR/.run.lock"
  flock -n 9 || die "Đang có một run.sh khác xử lý project này."
}

prepare_files() {
  local name source target stamp
  [[ -f settings.ex.toml ]] || die "Không tìm thấy settings.ex.toml."
  mkdir -p config data logs .agentfs .backups
  chmod 700 config .backups
  stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  for name in settings.toml settings.dev.toml; do
    source="$ROOT_DIR/$name"
    target="$ROOT_DIR/config/$name"
    if [[ -L "$source" ]]; then
      [[ "$(readlink -f -- "$source")" == "$target" && -f "$target" ]] || die "$name là symlink không trỏ tới config/$name; hãy kiểm tra trước khi triển khai."
      continue
    fi
    if [[ -e "$source" ]]; then
      [[ -f "$source" ]] || die "$name phải là file, không phải thư mục."
      [[ ! -e "$target" ]] || die "Có cả $name và config/$name. Hãy giữ cấu hình muốn chạy trong config/$name và chuyển file còn lại ra ngoài."
      cp -p -- "$source" "$ROOT_DIR/.backups/${name%.toml}-init-${stamp}.toml"
      chmod 600 "$ROOT_DIR/.backups/${name%.toml}-init-${stamp}.toml"
      mv -- "$source" "$target"
    elif [[ ! -e "$target" && "$name" == settings.toml ]]; then
      cp -- settings.ex.toml "$target"
      log "Đã tạo cấu hình mẫu. Điền token và owners trong settings.toml trước khi deploy."
    fi
    if [[ -f "$target" ]]; then
      chmod 600 "$target"
      ln -s -- "config/$name" "$source"
    fi
  done
}

require_docker() {
  require docker
  docker compose version >/dev/null 2>&1 || die "Cần Docker Compose plugin (docker compose)."
  docker info >/dev/null 2>&1 || die "Không kết nối được Docker daemon. Kiểm tra Docker hoặc chạy bằng user có quyền Docker."
  compose config --quiet || die "docker-compose.yml hoặc biến môi trường không hợp lệ."
}

require_wait() {
  local help
  help="$(compose up --help)"
  [[ "$help" == *--wait-timeout* ]] || die "Hãy cập nhật Docker Compose để hỗ trợ --wait và --wait-timeout."
}

check_config() {
  [[ -f config/settings.toml ]] || die "Chưa có cấu hình. Chạy: bash run.sh init"
  docker image inspect "$WAKU_IMAGE_NAME" >/dev/null 2>&1 || die "Chưa có image $WAKU_IMAGE_NAME. Chạy: bash run.sh deploy"
  log "Kiểm tra cấu hình; không kết nối Telegram/Discord."
  compose run --rm --no-deps -T --entrypoint /app/.venv/bin/python waku scripts/docker_config.py "$@"
}

check_build_space() {
  local docker_root free_bytes required_bytes
  require df
  require awk
  docker_root="$(docker info --format '{{.DockerRootDir}}')"
  [[ -d "$docker_root" ]] || docker_root="$ROOT_DIR"
  free_bytes="$(df -PB1 "$docker_root" | awk 'NR == 2 {print $4}')"
  [[ "$free_bytes" =~ ^[0-9]+$ ]] || die "Không đọc được dung lượng ổ đĩa Docker."
  required_bytes=$((10#$MIN_FREE_GB * 1024 * 1024 * 1024))
  ((free_bytes >= required_bytes)) || die "Docker còn ít hơn ${MIN_FREE_GB} GiB trống. Kiểm tra bằng 'bash run.sh disk' và giải phóng dung lượng trước khi build."
}

backup_files() {
  local archive running
  require tar
  archive="$ROOT_DIR/.backups/waku-$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz"
  running="$(compose ps --status running -q waku)"
  if [[ -n "$running" ]]; then
    RESUME_ON_EXIT=1
    compose stop -t 60 waku
  fi
  log "Sao lưu cấu hình, dữ liệu, session, log và workspace AI."
  tar -czf "$archive" -C "$ROOT_DIR" config data logs .agentfs
  chmod 600 "$archive"
  log "Bản sao lưu: $archive"
  log "Nếu dùng database ngoài SQLite local, cần sao lưu database đó riêng."
}

start_waku() {
  require_wait
  # --force-recreate also refreshes the environment and bind mounts after edits.
  RESUME_ON_EXIT=0
  if ! compose up -d --no-build --force-recreate --wait --wait-timeout "$HEALTH_TIMEOUT" waku; then
    compose ps -a waku >&2 || true
    die "Bot chưa sẵn sàng. Xem 'bash run.sh logs'; cấu hình và dữ liệu vẫn được giữ."
  fi
  log "Bot đã sẵn sàng. HTTP: http://127.0.0.1:${WAKU_PORT}; xem log: bash run.sh logs"
}

deploy() {
  prepare_files
  require_docker
  require_wait
  check_build_space
  export WAKU_COMMIT WAKU_VERSION
  WAKU_COMMIT="$(git rev-parse HEAD 2>/dev/null || printf unknown)"
  WAKU_VERSION="$(git describe --tags --always --dirty 2>/dev/null || printf local)"
  log "Build cả bot và Mini App trong Docker; container hiện tại tiếp tục chạy trong lúc build."
  compose build waku
  check_config
  if [[ -n "$(compose ps -a -q waku)" ]] || [[ -n "$(find data .agentfs -type f -print -quit)" ]]; then
    backup_files
  fi
  start_waku
}

update_source() {
  require git
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "update cần checkout Git. Nếu tải source ZIP, dùng deploy."
  [[ -z "$(git status --porcelain --untracked-files=normal)" ]] || die "Source có thay đổi local. Commit/stash chúng trước; script không reset hay xóa thay đổi."
  git rev-parse --verify '@{upstream}' >/dev/null 2>&1 || die "Nhánh hiện tại chưa có upstream để git pull."
  log "Cập nhật source bằng git pull --ff-only."
  git pull --ff-only
  deploy
}

as_root() {
  if ((EUID == 0)); then
    "$@"
  else
    require sudo
    # Use per-command NOPASSWD rules without requiring sudo -v permission.
    # Never retry a failed system command or read/store a sudo password.
    sudo -n -- "$@"
  fi
}

reload_nginx() {
  if command -v systemctl >/dev/null 2>&1; then
    as_root systemctl reload nginx
  else
    as_root nginx -s reload
  fi
}

valid_domain() {
  local domain="$1" label
  local -a labels
  [[ ${#domain} -le 253 && "$domain" == *.* && "$domain" != *. ]] || return 1
  IFS=. read -r -a labels <<< "$domain"
  for label in "${labels[@]}"; do
    [[ ${#label} -le 63 && "$label" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$ ]] || return 1
  done
  [[ "${labels[-1]}" =~ ^[a-zA-Z]{2,63}$ ]]
}

setup_miniapp() {
  local domain="${1:-${WAKU_MINIAPP_DOMAIN:-}}" email="${CERTBOT_EMAIL:-}"
  local site backup="" temporary existing
  local marker='# Managed by waku-bot run.sh'
  require nginx
  require certbot
  require curl
  if [[ -z "$domain" && -t 0 ]]; then read -r -p "Domain Mini App (ví dụ panel.example.com): " domain; fi
  domain="${domain,,}"
  valid_domain "$domain" || die "Cần domain hợp lệ; truyền vào 'bash run.sh miniapp panel.example.com'."
  if [[ -z "$email" && -t 0 ]]; then read -r -p "Email nhận thông báo chứng chỉ HTTPS: " email; fi
  [[ "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || die "Cần email hợp lệ trong CERTBOT_EMAIL."
  [[ "$WAKU_BIND_ADDRESS" == 127.0.0.1 || "$WAKU_BIND_ADDRESS" == 0.0.0.0 ]] || die "Thiết lập Mini App này dùng backend IPv4; đặt WAKU_BIND_ADDRESS=127.0.0.1."
  require_docker
  check_config
  as_root nginx -t
  site="/etc/nginx/conf.d/waku-${domain}.conf"
  if as_root test -e "$site"; then
    as_root grep -Fqx "$marker" "$site" || die "$site không do run.sh quản lý; không ghi đè."
    backup="${site}.backup-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    as_root cp -p -- "$site" "$backup"
  else
    existing="$(as_root nginx -T 2>&1)"
    if grep -Eq "server_name[^;]*[[:space:]]${domain//./\\.}([[:space:];]|$)" <<< "$existing"; then
      die "Nginx đã có server_name cho $domain ở cấu hình khác; hãy sử dụng cấu hình đó."
    fi
  fi
  temporary="$(mktemp "$ROOT_DIR/.dev-notes-nginx-XXXXXX")"
  cat > "$temporary" <<EOF
${marker}
server {
    listen 80;
    listen [::]:80;
    server_name ${domain};
    client_max_body_size 20m;
    location / {
        proxy_pass http://127.0.0.1:${WAKU_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
EOF
  as_root mkdir -p /etc/nginx/conf.d
  as_root install -m 644 -- "$temporary" "$site"
  rm -f -- "$temporary"
  if ! as_root nginx -t; then
    if [[ -n "$backup" ]]; then as_root cp -p -- "$backup" "$site"; else as_root rm -f -- "$site"; fi
    die "Nginx không chấp nhận cấu hình mới; đã phục hồi file trước đó."
  fi
  reload_nginx
  as_root certbot --nginx --non-interactive --agree-tos --redirect --email "$email" -d "$domain"
  # Change settings only after HTTPS setup succeeds; the editor preserves comments
  # and performs the same validated atomic save as the bot's /config menu.
  check_config --miniapp-url "https://${domain}"
  start_waku
  curl --fail --silent --show-error --max-time 20 --output /dev/null "https://${domain}/"
  log "Mini App: https://${domain}/ — đăng ký URL này và short name trong BotFather."
}

menu() {
  local choice
  printf '\nWaku Docker\n  1) Triển khai source hiện tại\n  2) Cập nhật từ Git và triển khai\n  3) Khởi động lại\n  4) Dừng bot\n  5) Xem trạng thái\n  6) Xem log\n  7) Sao lưu\n  8) Thiết lập Mini App + HTTPS\n  9) Chuẩn bị cấu hình\n  0) Thoát\n\n'
  read -r -p "Chọn [0-9]: " choice
  case "$choice" in
    1) main deploy ;; 2) main update ;; 3) main restart ;; 4) main stop ;;
    5) main status ;; 6) main logs ;; 7) main backup ;; 8) main miniapp ;;
    9) main init ;; 0) return ;; *) die "Lựa chọn không hợp lệ." ;;
  esac
}

main() {
  local action="${1:-}" lines
  if (($#)); then shift; fi
  case "$action" in
    --help|-h) action=help ;; --deploy) action=deploy ;;
    --setup-miniapp) action=miniapp ;; --all) action=all ;;
  esac
  case "$action" in
    help) usage; return ;;
    '') if [[ -t 0 ]]; then menu; return; else action=deploy; fi ;;
    init|deploy|update|start|restart|stop|status|logs|check|backup|disk|miniapp|all) ;;
    *) usage >&2; die "Lệnh không hợp lệ: $action" ;;
  esac
  case "$action" in
    logs) (($# <= 1)) || die "logs chỉ nhận số dòng." ;;
    miniapp|all) (($# <= 1)) || die "$action chỉ nhận domain." ;;
    *) (($# == 0)) || die "$action không nhận tham số thêm." ;;
  esac
  validate_options
  case "$action" in
    status|logs|disk) ;;
    *) lock_project ;;
  esac
  case "$action" in
    init) prepare_files; log "Sửa cấu hình: nano settings.toml; triển khai: bash run.sh deploy" ;;
    deploy) deploy ;;
    update) update_source ;;
    start|restart) prepare_files; require_docker; check_config; start_waku ;;
    stop) require_docker; compose stop -t 60 waku ;;
    status) require_docker; compose ps -a waku ;;
    logs)
      lines="${1:-100}"
      [[ "$lines" =~ ^[1-9][0-9]{0,5}$ ]] || die "Số dòng log phải là số nguyên dương."
      require_docker; compose logs --follow --tail "$lines" waku ;;
    check) require_docker; check_config ;;
    backup) prepare_files; require_docker; backup_files ;;
    disk) require_docker; docker system df; df -h "$ROOT_DIR" ;;
    miniapp) setup_miniapp "${1:-}" ;;
    all) deploy; setup_miniapp "${1:-}" ;;
  esac
}

main "$@"
