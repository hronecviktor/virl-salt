{% from "virl.jinja" import virl with context %}

include:
  - virl.routervms.virl-core-sync

{% if virl.iosxrv9000 and virl.iosxrv9000pref %}

iosxrv 9000:
  glance.image_present:
  - profile: virl
  - name: 'IOS XRv 9000'
  - container_format: bare
  - min_disk: 55
  - min_ram: 0
  - is_public: True
  - checksum: {{ salt['pillar.get']('routervm_checksums:iosxr9000')}}
  - protected: False
  - disk_format: qcow2
  - copy_from: salt://images/salt/{{ salt['pillar.get']('routervm_files:iosxr9000')}}
  - property-config_disk_type: cdrom
  - property-hw_cdrom_bus: ide
  - property-hw_disk_bus: virtio
  - property-hw_vif_model: virtio
  - property-hw_cpu_mode: host-passthrough
  - property-release: {{ salt['pillar.get']('version:iosxr9000')}}
  - property-serial: 4
  - property-subtype: 'IOS XRv 9000'

iosxrv 9000 flavor delete:
  cmd.run:
    - name: 'source /usr/local/bin/virl-openrc.sh ;nova flavor-delete "IOS XRv 9000"'
    - onlyif: 'source /usr/local/bin/virl-openrc.sh ;nova flavor-show "IOS XRv 9000"'
    - onchanges:
      - glance: iosxrv 9000

iosxrv 9000 flavor create:
  module.run:
    - name: nova.flavor_create
    - m_name: 'IOS XRv 9000'
    - ram: 8192
    - disk: 0
    - vcpus: 2
  {% if virl.mitaka %}
    - profile: virl
  {% endif %}
    - onchanges:
      - glance: iosxrv 9000
    - require:
      - cmd: iosxrv 9000 flavor delete

{% else %}

iosxrv 9000 gone:
  glance.image_absent:
  - profile: virl
  - name: 'IOS XRv 9000'

iosxrv 9000 flavor absent:
  cmd.run:
    - name: 'source /usr/local/bin/virl-openrc.sh ;nova flavor-delete "IOS XRv 9000"'
    - onlyif: source /usr/local/bin/virl-openrc.sh ;nova flavor-list | grep -w "IOS XRv 9000"
{% endif %}
