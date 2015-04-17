
neutron linuxbridge unhold:
  module.run:
    - name: pkg.unhold
    - m_name: neutron-plugin-linuxbridge-agent
    - prereq:
      - pkg: neutron pull to latest

neutron pull to latest:
  pkg.latest:
    - require:
      - module: neutron linuxbridge unhold
    - pkgs:
      - neutron-common
      - neutron-dhcp-agent
      - neutron-l3-agent
      - neutron-metadata-agent
      - neutron-plugin-linuxbridge-agent
      - neutron-plugin-linuxbridge
      - neutron-plugin-ml2
      - neutron-server
      - python-neutron
  module.run:
    - name: pkg.held
    - m_name: neutron-plugin-linuxbridge-agent