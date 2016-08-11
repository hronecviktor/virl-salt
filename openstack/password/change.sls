{% set mypassword = salt['pillar.get']('virl:mysql_password', salt['grains.get']('mysql_password', 'password')) %}
{% set int_ip = salt['pillar.get']('virl:internalnet_ip', salt['grains.get']('internalnet_ip', '172.16.10.250' )) %}
{% set accounts = ['root','keystone', 'nova', 'glance', 'cinder', 'neutron', 'quantum', 'dash', 'heat' ] %}
{% set uwmpassword = salt['pillar.get']('virl:uwmadmin_password', salt['grains.get']('uwmadmin_password', 'password')) %}
{% set mitaka = salt['pillar.get']('virl:mitaka', salt['grains.get']('mitaka', false)) %}

debconf-change:
  file.managed:
    - name: /tmp/debconf-change
    - unless: mysql -u root -p{{ mypassword }} -e 'quit'
    - contents: |
        mysql-server mysql-server/root_password password MYPASS
        mysql-server mysql-server/root_password_again password MYPASS
{% if mitaka %}
        mysql-server-5.7 mysql-server/root_password password MYPASS
        mysql-server-5.7 mysql-server/root_password_again password MYPASS
{% else %}
        mysql-server-5.5 mysql-server/root_password password MYPASS
        mysql-server-5.5 mysql-server/root_password_again password MYPASS
{% endif %}

debconf-change-replace:
  file.replace:
    - name: /tmp/debconf-change
    - pattern: 'MYPASS'
    - repl: {{ mypassword }}
    - onchanges:
      - file: debconf-change

debconf-change-set:
  cmd.run:
    - onchanges:
      - file: debconf-change-replace
    - name: |
        debconf-set-selections /tmp/debconf-change
        rm /tmp/debconf-change

debconf-change-noninteractive:
  cmd.run:
{% if mitaka %}
    - name: dpkg-reconfigure -f noninteractive mysql-server-5.7
{% else %}
    - name: dpkg-reconfigure -f noninteractive mysql-server-5.5
{% endif %}
    - onchanges:
      - cmd: debconf-change-set

{% for user in accounts %}
{{ user }}-mysql:
  mysql_user.present:
{% if mitaka %}
    - password_column: authentication_string
{% endif %}
    - name: {{ user }}
    - host: 'localhost'
    - password: {{ mypassword }}
  mysql_database:
    - present
    - name: {{ user }}
  mysql_grants.present:
    - grant: all privileges
    - database: "{{ user }}.*"
    - user: {{ user }}

{{ user }}-mysql-nonlocal:
  mysql_user.present:
{% if mitaka %}
    - password_column: authentication_string
{% endif %}
    - name: {{ user }}
    - host: {{ int_ip }}
    - password: {{ mypassword }}
{% endfor %}

uwmadmin change:
  cmd.run:
    - names:
      - '/usr/local/bin/virl_uwm_server set-password -u uwmadmin -p {{ uwmpassword }} -P {{ uwmpassword }}'
      - crudini --set /etc/virl/virl.cfg env virl_openstack_password {{ uwmpassword }}
      - crudini --set /etc/virl/virl.cfg env virl_std_password {{ uwmpassword }}
    - onlyif: 'test -e /var/local/virl/servers.db'
