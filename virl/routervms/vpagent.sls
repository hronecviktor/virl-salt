{% from "virl.jinja" import virl with context %}

include:
  - virl.routervms.virl-core-sync

{% if virl.vpagent and virl.vpagentpref %}

vpagent:
  glance.image_present:
    - profile: virl
    - name: 'vpagent'
    - container_format: bare
    - min_disk: 2
    - min_ram: 0
    - is_public: True
    - checksum: 3ded7914ddbedc644ea18493edda5b2d
    - protected: False
    - disk_format: qcow2
    - copy_from: salt://images/salt/vios-tpgen_adventerprisek9-m.15.4.20140131.qcow2
    - property-config_disk_type: disk
    - property-hw_cdrom_bus: ide
    - property-hw_disk_bus: virtio
    - property-hw_vif_model: e1000
    - property-release: 15.4
    - property-serial: 1
    - property-subtype: IOSv

vpagent flavor delete:
  cmd.run:
    - name: 'source /usr/local/bin/virl-openrc.sh ;nova flavor-delete "vpagent"'
    - onlyif: source /usr/local/bin/virl-openrc.sh ;nova flavor-show "vpagent"
    - onchanges:
      - glance: vpagent

vpagent flavor create:
  module.run:
    - name: nova.flavor_create
    - m_name: 'vpagent'
    - ram: 512
    - disk: 0
    - vcpus: 1
  {% if virl.mitaka %}
    - profile: virl
  {% endif %}
    - onchanges:
      - glance: vpagent
    - require:
      - cmd: vpagent flavor delete

{% else %}

vpagent gone:
  glance.image_absent:
  - profile: virl
  - name: 'vpagent'

vpagent flavor absent:
  cmd.run:
    - name: 'source /usr/local/bin/virl-openrc.sh ;nova flavor-delete "vpagent"'
    - onlyif: source /usr/local/bin/virl-openrc.sh ;nova flavor-list | grep -w "vpagent"
{% endif %}
