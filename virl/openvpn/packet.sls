
{% from "virl.jinja" import virl with context %}

{% if virl.openvpn_enable %}

vpn maximize:
  cmd.run:
    - names:
      - crudini --set /etc/virl.ini DEFAULT l2_network_gateway {{ virl.l2_address_iponly }}
      - crudini --set /etc/virl.ini DEFAULT l2_network_gateway2 {{ virl.l2_address2_iponly }}
      - crudini --set /etc/virl.ini DEFAULT l3_network_gateway {{ virl.l3_address_iponly }}
      - crudini --set /etc/virl/virl.cfg env virl_local_ip {{ virl.l2_address_iponly }}
      - crudini --set /etc/nova/nova.conf serial_console proxyclient_address {{ virl.l2_address_iponly }}
      - crudini --set /etc/nova/nova.conf DEFAULT serial_port_proxyclient_address {{ virl.l2_address_iponly }}
      - neutron --os-tenant-name admin --os-username admin --os-password {{ virl.ospassword }} --os-auth-url=http://127.0.1.1:5000/v2.0 subnet-update flat --gateway_ip {{ virl.l2_address_iponly }}
      - neutron --os-tenant-name admin --os-username admin --os-password {{ virl.ospassword }} --os-auth-url=http://127.0.1.1:5000/v2.0 subnet-update flat1 --gateway_ip {{ virl.l2_address2_iponly }}
      - neutron --os-tenant-name admin --os-username admin --os-password {{ virl.ospassword }} --os-auth-url=http://127.0.1.1:5000/v2.0 subnet-update ext-net --gateway_ip {{ virl.l3_address_iponly }}

ufw accepted ports:
  cmd.run:
    - unless: "/usr/sbin/ufw status | grep 1194/tcp"
    - names:
      - ufw allow in on {{ virl.publicport }} to any port 22 proto tcp
      - ufw allow in on {{ virl.publicport }} to any port 443 proto tcp
      - ufw allow in on {{ virl.publicport }} to any port 4505 proto tcp
      - ufw allow in on {{ virl.publicport }} to any port 4506 proto tcp      
      - ufw allow in on {{ virl.publicport }} to any port 1194 proto tcp
      - ufw allow from 10.0.0.0/8

ufw deny {{ virl.publicport }}:
  cmd.run:
    - require:
      - cmd: ufw accepted ports
    - name: ufw deny in on {{ virl.publicport }} to any

ufw accept all:
  cmd.run:
    - require:
      - cmd: ufw deny {{ virl.publicport }}
    - names: 
      - ufw allow from any to any
      - ufw default allow routed


adding local route to openvpn:
  file.append:
    - name: /etc/openvpn/server.conf
    - text: |
        push "route {{ virl.l2_network2_iponly }} {{ virl.l2_mask2 }} {{ virl.l2_address_iponly }}"
        push "route {{ virl.l3_network_iponly }} {{ virl.l3_mask }} {{ virl.l2_address_iponly }}"

adding nat to ufw:
  file.prepend:
    - name: /etc/ufw/before.rules
    - text:  |
        *nat
        :POSTROUTING ACCEPT [0:0]
        # translate outbound traffic from internal networks
        -A POSTROUTING -s {{ virl.l2_network }} -o {{ virl.publicport }} -j MASQUERADE
        -A POSTROUTING -s {{ virl.l2_network2 }} -o {{ virl.publicport }} -j MASQUERADE
        -A POSTROUTING -s {{ virl.l3_network }} -o {{ virl.publicport }} -j MASQUERADE
        # don't delete the 'COMMIT' line or these nat table rules won't
        # be processed
        COMMIT

ufw force enable:
  cmd.run:
    - order: last
    - names:
      - service neutron-l3-agent restart
      - service nova-serialproxy restart
      - service virl-std restart
      - service virl-uwm restart
      - service openvpn restart
      - ufw --force enable
      - ufw status verbose

{% endif %}
