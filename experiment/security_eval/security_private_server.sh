#!/bin/bash
# Disposable, private MariaDB server for the security demonstrations (experiment/security_eval/security_evaluation.py).
# Runs as YOUR user (no sudo), with a temporary data directory, listening on 127.0.0.1:3307 only.
# It never touches the system MariaDB server, its configuration, databases or accounts.
#
#   experiment/security_eval/security_private_server.sh start   # install a fresh data dir, start the server, create the test DB and accounts
#   experiment/security_eval/security_private_server.sh status
#   experiment/security_eval/security_private_server.sh stop    # stop the server, delete its directory and the credentials file
#
# Accounts (inside this private server only; random passwords go to a mode-600 file outside the repository):
#   qm_sectest_ro  SELECT only on querymind_sectest        - what QueryMind's normal account looks like
#   qm_sectest_rw  ALL on querymind_sectest.* (this server) - ONLY to show the rollback layer; not a QueryMind account
set -euo pipefail
umask 077
PORT="${QM_SEC_PORT:-3307}"
DIR="${QM_SEC_DIR:-${TMPDIR:-/tmp}/querymind-sectest-$USER}"
ENV_FILE="${QM_SEC_ENV_FILE:-$HOME/.config/querymind/sectest.env}"
DB=querymind_sectest
SOCK="${QM_SEC_SOCKET:-${TMPDIR:-/tmp}/qmsec-$USER.sock}"   # short path: unix sockets are limited to about 107 characters
PIDFILE="$DIR/mysqld.pid"

server_args=(--no-defaults --datadir="$DIR/data" --socket="$SOCK" --pid-file="$PIDFILE" --log-error="$DIR/error.log"
             --innodb-buffer-pool-size=32M --innodb-log-file-size=16M --performance-schema=OFF --max-connections=30
             --character-set-server=utf8mb4 --collation-server=utf8mb4_general_ci)

wait_for_socket() {
  for _ in $(seq 1 90); do
    mariadb-admin --no-defaults -S "$SOCK" -u root ping >/dev/null 2>&1 && return 0
    mariadb-admin --no-defaults -S "$SOCK" -u root --password="${ROOT_PW:-}" ping >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "Server did not start; see $DIR/error.log" >&2
  tail -n 20 "$DIR/error.log" >&2 || true
  return 1
}

stop_server() {
  if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")"
    for _ in $(seq 1 60); do kill -0 "$(cat "$PIDFILE")" 2>/dev/null || return 0; sleep 1; done
    kill -9 "$(cat "$PIDFILE")" 2>/dev/null || true
  fi
}

case "${1:-}" in
  start)
    [[ ! -e "$DIR" ]] || { echo "$DIR already exists; run stop first." >&2; exit 1; }
    [[ ! -e "$ENV_FILE" ]] || { echo "$ENV_FILE already exists; run stop first." >&2; exit 1; }
    ! ss -ltn | grep -q ":$PORT " || { echo "port $PORT is already in use." >&2; exit 1; }
    mkdir -p "$DIR/data" "$(dirname "$ENV_FILE")"
    chmod 700 "$DIR"
    mariadb-install-db --no-defaults --datadir="$DIR/data" --auth-root-authentication-method=normal \
        --skip-test-db >"$DIR/install.log" 2>&1 || { echo "mariadb-install-db failed:" >&2; tail -n 15 "$DIR/install.log" >&2; exit 1; }
    ROOT_PW="$(openssl rand -base64 24 | tr -d '/+=')"
    PW_RO="$(openssl rand -base64 24 | tr -d '/+=')"
    PW_RW="$(openssl rand -base64 24 | tr -d '/+=')"
    # phase 1: unix socket only (no network), so nothing can connect while the root account is still open
    setsid mariadbd "${server_args[@]}" --skip-networking >"$DIR/server.out" 2>&1 &
    wait_for_socket
    mariadb --no-defaults -S "$SOCK" -u root <<SQL
ALTER USER 'root'@'localhost' IDENTIFIED BY '$ROOT_PW';
DELETE FROM mysql.global_priv WHERE User='';
CREATE DATABASE $DB CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE TABLE $DB.accounts (id INT PRIMARY KEY, owner VARCHAR(50) NOT NULL, balance DECIMAL(10,2) NOT NULL) ENGINE=InnoDB;
INSERT INTO $DB.accounts VALUES (1,'Alice',100.00),(2,'Bob',250.50),(3,'Carol',75.25),(4,'Dave',0.00),(5,'Erin',999.99);
CREATE TABLE $DB.audit_log (id INT PRIMARY KEY AUTO_INCREMENT, event VARCHAR(100) NOT NULL) ENGINE=InnoDB;
INSERT INTO $DB.audit_log (event) VALUES ('created'),('seeded'),('ready');
CREATE TABLE $DB.legacy_notes (id INT PRIMARY KEY, note VARCHAR(100)) ENGINE=MyISAM;
INSERT INTO $DB.legacy_notes VALUES (1,'first'),(2,'second');
CREATE USER 'qm_sectest_ro'@'localhost' IDENTIFIED BY '$PW_RO';
GRANT SELECT ON $DB.* TO 'qm_sectest_ro'@'localhost';
CREATE USER 'qm_sectest_rw'@'localhost' IDENTIFIED BY '$PW_RW';
GRANT ALL PRIVILEGES ON $DB.* TO 'qm_sectest_rw'@'localhost';
FLUSH PRIVILEGES;
SQL
    stop_server
    # phase 2: TCP on 127.0.0.1 only
    setsid mariadbd "${server_args[@]}" --bind-address=127.0.0.1 --port="$PORT" >"$DIR/server.out" 2>&1 &
    ROOT_PW="$ROOT_PW" wait_for_socket
    printf 'export QM_SEC_HOST=127.0.0.1\nexport QM_SEC_PORT=%s\nexport QM_SEC_DB=%s\nexport QM_SEC_RO_USER=qm_sectest_ro\nexport QM_SEC_RO_PASSWORD=%s\nexport QM_SEC_RW_USER=qm_sectest_rw\nexport QM_SEC_RW_PASSWORD=%s\n' \
      "$PORT" "$DB" "$PW_RO" "$PW_RW" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    unset ROOT_PW PW_RO PW_RW
    echo "OK: private server running (pid $(cat "$PIDFILE")) on 127.0.0.1:$PORT; data in $DIR; credentials in $ENV_FILE (mode $(stat -c %a "$ENV_FILE"))."
    ;;
  status)
    if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then echo "running (pid $(cat "$PIDFILE")), directory $DIR"; else echo "not running"; fi
    ss -ltn 2>/dev/null | grep ":$PORT " || true
    ;;
  stop)
    stop_server
    rm -rf "$DIR"; rm -f "$SOCK"
    rm -f "$ENV_FILE" "$ENV_FILE.new"
    echo "OK: private server stopped; $DIR and $ENV_FILE removed."
    ;;
  *)
    echo "usage: $0 start|status|stop" >&2; exit 2 ;;
esac
