## Install docker with registry running in container and docker-py for docker API
#{% set registry_ip = salt['pillar.get']('virl:l2_address2', salt['grains.get']('l2_address2', '172.16.2.254/xx' )).split('/')[0] %}
#{% set registry_port = salt['pillar.get']('virl:docker_registry_port', salt['grains.get']('docker_registry_port', '19397' )) %}



{% from "virl.jinja" import virl with context %}

include:
  - virl.docker.install
  - virl.docker.config

# docker-py:

docker-py:
  pip.installed:
    - name: docker-py
    {% if virl.proxy %}
    - proxy: {{ virl.http_proxy }}
    {% endif %}

# add registry into docker:

registry_remove:
  cmd.script:
    - source: salt://virl/files/remove_docker_registry.sh
    - env:
      - REGISTRY_ID: "{{ virl.registry_docker_ID }}"
      - REGISTRY_IP: "{{ virl.registry_ip }}"
      - REGISTRY_PORT: "{{ virl.registry_port }}"
    - require:
      - pkg: docker_install
      - module: docker_restart

registry_load:
  # state docker.loaded is buggy -> file.managed and cmd.run
  file.managed:
    - name: /var/cache/virl/docker/registry.tar
    - makedirs: True
    - source: salt://images/salt/{{ virl.registry_file }}
    - source_hash: {{ virl.registry_file_hash }}
    - unless: docker images -q | grep {{ virl.registry_docker_ID }}
  cmd.run:
    - names:
      - docker load -i /var/cache/virl/docker/registry.tar
    - unless: docker images -q | grep '{{ virl.registry_docker_ID }}'

registry_tag:
  cmd.run:
    - names:
      - docker tag -f {{ virl.registry_docker_ID }} registry:{{ virl.registry_version }}
    - unless: docker images | grep '^registry *{{ virl.registry_version }} *{{ virl.registry_docker_ID }}'
    - require:
      - cmd: registry_load

registry_run:
  # dockerio.running replaced by cmd.run due to API problems of dockerio/docker-py used versions
  cmd.run:
    - names:
      - docker run -d -p {{ virl.registry_ip }}:{{ virl.registry_port }}:5000 -e REGISTRY_STORAGE_DELETE_ENABLED=true -e REGISTRY_STORAGE_FILESYSTEM_ROOTDIRECTORY=/var/lib/registry -v /var/local/virl/docker:/var/lib/registry --restart=always --name registry registry:{{ virl.registry_version }}
    - require:
      - cmd: registry_tag
    # - unless: docker ps | grep "{{ registry_ip }}:{{ registry_port }}->5000/tcp"

# Docker tap-counter
tap_counter_remove:
  cmd.run:
    - names:
      - docker rmi {{ virl.registry_ip }}:{{ virl.registry_port }}/virl-tap-counter:latest || true
      - docker rmi virl-tap-counter:latest || true
      - docker rmi {{ virl.tapcounter_docker_ID }} || true
    - require:
      - pkg: docker_install
      - module: docker_restart

tap_counter_load:
  # state docker.loaded is buggy -> file.managed and cmd.run
  file.managed:
    - name: /var/cache/virl/docker/docker-tap-counter.tar
    - makedirs: True
    - source: salt://images/salt/docker-tap-counter.tar
    - source_hash: {{ virl.tap_counter_file_hash }}
    - unless: docker images -q | grep {{ virl.tapcounter_docker_ID }}
  cmd.run:
    - names:
      - docker load -i /var/cache/virl/docker/docker-tap-counter.tar
    - unless: docker images -q | grep '{{ virl.tapcounter_docker_ID }}'

tap_counter_tag:
  cmd.run:
    - names:
      - docker tag {{ virl.tapcounter_docker_ID }} virl-tap-counter:latest
    - unless: docker images | grep '^virl-tap-counter *latest *{{ virl.tapcounter_docker_ID }}'
    - require:
      - cmd: tap_counter_load

virl-tap-counter:latest:
  # this remembers previously used registry IP:port and restores it,
  # don't include them or it will cause issues when IP/port changes
# removed altogether, it dockerng is not available until later if docker-py was not installed
#  dockerng.image_present:
#    - load: salt://images/salt/docker-tap-counter.tar
#    - force: True
  cmd.run:
    - names:
      - docker tag -f {{ virl.tapcounter_docker_ID }} {{ virl.registry_ip }}:{{ virl.registry_port }}/virl-tap-counter:latest
      - docker push {{ virl.registry_ip }}:{{ virl.registry_port }}/virl-tap-counter:latest
    - require:
      - cmd: registry_run

virl_tap_counter_clean:
  cmd.run:
    - names:
      - docker rmi {{ virl.registry_ip }}:{{ virl.registry_port }}/virl-tap-counter:latest
      - docker rmi virl-tap-counter:latest
      - docker rmi {{ virl.tapcounter_docker_ID }} || true
    - require:
    #  - cmd: virl-tap-counter:latest
       - cmd: tap_counter_tag
