#!/bin/sh
# Runs in a platform UI before its run.sh renders the nginx configuration (docker-compose.yml): every page of the UI loads
# hosts.js first, which does nothing unless the UI is in a persona's tab of the Lab UI (lab_ui/server/personas.py).
conf=/etc/nginx/conf.d/default.conf
grep -q gravitee-lab-hosts.js "$conf" && exit 0
sed -i \
  -e '/location = \/50x.html {/i location = /gravitee-lab-hosts.js { alias /gravitee-lab-hosts.js; }' \
  -e '/location \/\([a-z]*\/\)\{0,1\} {/a sub_filter "<head>" "<head><script src=/gravitee-lab-hosts.js></script>";' \
  "$conf"
