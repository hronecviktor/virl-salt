{% set mypassword = salt['grains.get']('mysql_password', 'password') %}
{% set keystone_service_token = salt['grains.get']('keystone_service_token', 'fkgjhsdflkjh') %}

keystone-pkgs:
  pkg.installed:
    - order: 1
    - names:
      - keystone

keystone_token:
  openstack_config.present:
    - filename: /etc/keystone/keystone.conf
    - section: 'DEFAULT'
    - parameter: 'admin_token'
    - value: '{{ keystone_service_token }}'
    - require:
      - pkg: keystone-pkgs

/etc/keystone/keystone.conf:
  openstack_config.present:
    - filename: /etc/keystone/keystone.conf
    - section: 'database'
    - parameter: 'connection'
    - value: ' mysql://keystone:{{ mypassword }}@localhost/keystone'
    - require:
      - pkg: keystone-pkgs

logdir:
  file.replace:
    - name: /etc/keystone/keystone.conf
    - pattern: '#log_dir=<None>'
    - repl:  'log_dir = /var/log/keystone'
    - require:
      - pkg: keystone-pkgs

keystone db-sync:
  cmd.run:
    - name: su -s /bin/sh -c "keystone-manage db_sync" keystone
    - require:
      - pkg: keystone-pkgs

/usr/bin/keystone-manage token_flush >/var/log/keystone/keystone-tokenflush.log 2>&1:
  cron.present:
    - user: root
    - hour: 5
    - require:
      - cmd: keystone db-sync

key-db-sync:
  cmd.run:
    - order: last
    - name: service keystone restart
    - require:
      - cmd: keystone db-sync