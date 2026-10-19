#!/usr/bin/env bash
# Forced SSH command: only these local-lab operations are permitted.
set -euo pipefail
role=$(cat /var/lib/smart-learning-local-setup/role)
guest_user=$(cat /var/lib/smart-learning-local-setup/guest-user)
stack=/opt/smart-learning-local/deploy/local-prototype/stack.sh
case "${SSH_ORIGINAL_COMMAND:-}" in
  status)
    free -m
    df -h /opt
    if [[ "$role" == server ]]; then bash "$stack" ps; systemctl is-active smart-learning-local-caddy; fi
    ;;
  verify-source)
    [[ "$role" == server ]] || exit 2
    cd /opt/smart-learning-local
    cat deploy-revision.txt
    sha256sum --quiet --strict -c deploy-manifest.sha256 && echo 'Deployed source matches its manifest.'
    ;;
  trust-client)
    [[ "$role" != server ]] || exit 2
    bash /usr/local/lib/smart-learning-local/trust-client.sh "/home/$guest_user/smart-learning-local-root.crt" "$guest_user"
    ;;
  reboot-guest)
    systemctl reboot
    ;;
  restart-app|stop-ollama|start-ollama|capture-start|capture-stop)
    [[ "$role" == server ]] || exit 2
    case "$SSH_ORIGINAL_COMMAND" in
      restart-app) bash "$stack" restart backend worker ;;
      stop-ollama) bash "$stack" stop ollama ;;
      start-ollama) bash "$stack" start ollama ;;
      capture-start)
        evidence=/var/lib/smart-learning-local-evidence
        install -d -o "$guest_user" -g "$(id -gn "$guest_user")" -m 0700 "$evidence"
        for capture in https http; do
          if systemctl is-active --quiet "smart-learning-local-$capture-capture"; then continue; fi
          if [[ "$capture" == https ]]; then interface=enp0s8; port=443; else interface=lo; port=8080; fi
          systemd-run --unit="smart-learning-local-$capture-capture" --collect \
            tcpdump -Z "$guest_user" -i "$interface" -nn -s 0 -U \
            -w "$evidence/$capture.pcap" "tcp port $port"
        done
        ;;
      capture-stop)
        systemctl stop smart-learning-local-https-capture smart-learning-local-http-capture
        ;;
    esac
    ;;
  *) echo 'This key permits only local-lab status, source verification, trust, restart, model recovery, and packet capture.' >&2; exit 2 ;;
esac
