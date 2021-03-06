# Copyright (C) 2011 Midokura KK
# Copyright (C) 2011 Nicira, Inc
# Copyright 2011 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""VIF drivers for libvirt."""

import copy

import os
from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import strutils

from nova import exception
from nova.i18n import _
from nova.i18n import _LE
from nova.network import linux_net
from nova.network import model as network_model
from nova import objects
from nova import utils
from nova.virt.libvirt import config as vconfig
from nova.virt.libvirt import designer
from nova.virt import osinfo

LOG = logging.getLogger(__name__)

libvirt_vif_opts = [
    cfg.BoolOpt('use_virtio_for_bridges',
                default=True,
                help='Use virtio for bridge interfaces with KVM/QEMU'),
]

CONF = cfg.CONF
CONF.register_opts(libvirt_vif_opts, 'libvirt')
CONF.import_opt('use_ipv6', 'nova.netconf')

# vhostuser queues support
MIN_LIBVIRT_VHOSTUSER_MQ = (1, 2, 17)


def is_vif_model_valid_for_virt(virt_type, vif_model):
    valid_models = {
        'qemu': [network_model.VIF_MODEL_VIRTIO,
                 network_model.VIF_MODEL_NE2K_PCI,
                 network_model.VIF_MODEL_PCNET,
                 network_model.VIF_MODEL_RTL8139,
                 network_model.VIF_MODEL_E1000,
                 network_model.VIF_MODEL_SPAPR_VLAN],
        'kvm': [network_model.VIF_MODEL_VIRTIO,
                network_model.VIF_MODEL_NE2K_PCI,
                network_model.VIF_MODEL_PCNET,
                network_model.VIF_MODEL_RTL8139,
                network_model.VIF_MODEL_E1000,
                network_model.VIF_MODEL_SPAPR_VLAN],
        'xen': [network_model.VIF_MODEL_NETFRONT,
                network_model.VIF_MODEL_NE2K_PCI,
                network_model.VIF_MODEL_PCNET,
                network_model.VIF_MODEL_RTL8139,
                network_model.VIF_MODEL_E1000],
        'lxc': [],
        'uml': [],
        }

    if vif_model is None:
        return True

    if virt_type not in valid_models:
        raise exception.UnsupportedVirtType(virt=virt_type)

    return vif_model in valid_models[virt_type]


class LibvirtGenericVIFDriver(object):
    """Generic VIF driver for libvirt networking."""

    def _normalize_vif_type(self, vif_type):
        return vif_type.replace('2.1q', '2q')

    def get_vif_devname(self, vif):
        if 'devname' in vif:
            return vif['devname']
        return ("nic" + vif['id'])[:network_model.NIC_NAME_LEN]

    def get_vif_devname_with_prefix(self, vif, prefix):
        devname = self.get_vif_devname(vif)
        return prefix + devname[3:]

    def get_base_config(self, instance, vif, image_meta,
                        inst_type, virt_type):
        conf = vconfig.LibvirtConfigGuestInterface()
        # Default to letting libvirt / the hypervisor choose the model
        model = None
        driver = None
        vhost_queues = None

        # If the user has specified a 'vif_model' against the
        # image then honour that model
        if image_meta:
            #model = osinfo.HardwareProperties(image_meta).network_model
            model = image_meta.properties.get('hw_vif_model', None)
            disable_csum = image_meta.properties.get('hw_vif_disable_csum', True)
            #if disable_csum.lower() in ('yes', '1', 'true'):
            conf.disable_csum = disable_csum
        # Else if the virt type is KVM/QEMU, use virtio according
        # to the global config parameter
        if (model is None and
            virt_type in ('kvm', 'qemu') and
                    CONF.libvirt.use_virtio_for_bridges):
            model = network_model.VIF_MODEL_VIRTIO

        # Workaround libvirt bug, where it mistakenly
        # enables vhost mode, even for non-KVM guests
        if (model == network_model.VIF_MODEL_VIRTIO and
            virt_type == "qemu"):
            driver = "qemu"

        if not is_vif_model_valid_for_virt(virt_type,
                                           model):
            raise exception.UnsupportedHardware(model=model,
                                                virt=virt_type)
        if (virt_type == 'kvm' and
            model == network_model.VIF_MODEL_VIRTIO):
            vhost_drv, vhost_queues = self._get_virtio_mq_settings(image_meta,
                                                                   inst_type)
            driver = vhost_drv or driver

        designer.set_vif_guest_frontend_config(
            conf, vif['address'], model, driver, vhost_queues)

        return conf

    def _get_virtio_mq_settings(self, image_meta, flavor):
        """A methods to set the number of virtio queues,
           if it has been requested in extra specs.
        """
        driver = None
        vhost_queues = None
        if not isinstance(image_meta, objects.ImageMeta):
            image_meta = objects.ImageMeta.from_dict(image_meta)
        img_props = image_meta.properties
        if img_props.get('hw_vif_multiqueue_enabled'):
            driver = 'vhost'
            vhost_queues = flavor.vcpus

        return (driver, vhost_queues)

    def get_bridge_name(self, vif):
        return vif['network']['bridge']

    def get_ovs_interfaceid(self, vif):
        return vif.get('ovs_interfaceid') or vif['id']

    def get_br_name(self, iface_id):
        return ("qbr" + iface_id)[:network_model.NIC_NAME_LEN]

    def get_veth_pair_names(self, iface_id):
        return (("qvb%s" % iface_id)[:network_model.NIC_NAME_LEN],
                ("qvo%s" % iface_id)[:network_model.NIC_NAME_LEN])

    def get_firewall_required(self, vif):
        if vif.is_neutron_filtering_enabled():
            return False
        if CONF.firewall_driver != "nova.virt.firewall.NoopFirewallDriver":
            return True
        return False

    def get_config_bridge(self, instance, vif, image_meta,
                          inst_type, virt_type, host):
        """Get VIF configurations for bridge type."""
        conf = self.get_base_config(instance, vif, image_meta,
                                    inst_type, virt_type)

        designer.set_vif_host_backend_bridge_config(
            conf, self.get_bridge_name(vif),
            self.get_vif_devname(vif))

        mac_id = vif['address'].replace(':', '')
        name = "nova-instance-" + instance.name + "-" + mac_id
        if self.get_firewall_required(vif):
            conf.filtername = name
        designer.set_vif_bandwidth_config(conf, inst_type)

        return conf

    def get_config_ovs_bridge(self, instance, vif, image_meta,
                              inst_type, virt_type, host):
        conf = self.get_base_config(instance, vif, image_meta,
                                    inst_type, virt_type)

        designer.set_vif_host_backend_ovs_config(
            conf, self.get_bridge_name(vif),
            self.get_ovs_interfaceid(vif),
            self.get_vif_devname(vif))

        designer.set_vif_bandwidth_config(conf, inst_type)

        return conf

    def get_config_ovs_hybrid(self, instance, vif, image_meta,
                              inst_type, virt_type, host):
        newvif = copy.deepcopy(vif)
        newvif['network']['bridge'] = self.get_br_name(vif['id'])
        return self.get_config_bridge(instance, newvif, image_meta,
                                      inst_type, virt_type, host)

    def get_config_ovs(self, instance, vif, image_meta,
                       inst_type, virt_type, host):
        if self.get_firewall_required(vif) or vif.is_hybrid_plug_enabled():
            return self.get_config_ovs_hybrid(instance, vif,
                                              image_meta,
                                              inst_type,
                                              virt_type,
                                              host)
        else:
            return self.get_config_ovs_bridge(instance, vif,
                                              image_meta,
                                              inst_type,
                                              virt_type,
                                              host)

    def get_config_ivs_hybrid(self, instance, vif, image_meta,
                              inst_type, virt_type, host):
        newvif = copy.deepcopy(vif)
        newvif['network']['bridge'] = self.get_br_name(vif['id'])
        return self.get_config_bridge(instance,
                                      newvif,
                                      image_meta,
                                      inst_type,
                                      virt_type,
                                      host)

    def get_config_ivs_ethernet(self, instance, vif, image_meta,
                                inst_type, virt_type, host):
        conf = self.get_base_config(instance,
                                    vif,
                                    image_meta,
                                    inst_type,
                                    virt_type)

        dev = self.get_vif_devname(vif)
        designer.set_vif_host_backend_ethernet_config(conf, dev)

        return conf

    def get_config_ivs(self, instance, vif, image_meta,
                       inst_type, virt_type, host):
        if self.get_firewall_required(vif) or vif.is_hybrid_plug_enabled():
            return self.get_config_ivs_hybrid(instance, vif,
                                              image_meta,
                                              inst_type,
                                              virt_type,
                                              host)
        else:
            return self.get_config_ivs_ethernet(instance, vif,
                                                image_meta,
                                                inst_type,
                                                virt_type,
                                                host)

    def get_config_802qbg(self, instance, vif, image_meta,
                          inst_type, virt_type, host):
        conf = self.get_base_config(instance, vif, image_meta,
                                    inst_type, virt_type)

        params = vif["qbg_params"]
        designer.set_vif_host_backend_802qbg_config(
            conf, vif['network'].get_meta('interface'),
            params['managerid'],
            params['typeid'],
            params['typeidversion'],
            params['instanceid'])

        designer.set_vif_bandwidth_config(conf, inst_type)

        return conf

    def get_config_802qbh(self, instance, vif, image_meta,
                          inst_type, virt_type, host):
        conf = self.get_base_config(instance, vif, image_meta,
                                    inst_type, virt_type)

        profile = vif["profile"]
        vif_details = vif["details"]
        net_type = 'direct'
        if vif['vnic_type'] == network_model.VNIC_TYPE_DIRECT:
            net_type = 'hostdev'

        designer.set_vif_host_backend_802qbh_config(
            conf, net_type, profile['pci_slot'],
            vif_details[network_model.VIF_DETAILS_PROFILEID])

        designer.set_vif_bandwidth_config(conf, inst_type)

        return conf

    def get_config_hw_veb(self, instance, vif, image_meta,
                            inst_type, virt_type, host):
        conf = self.get_base_config(instance, vif, image_meta,
                                    inst_type, virt_type)

        profile = vif["profile"]
        vif_details = vif["details"]
        net_type = 'direct'
        if vif['vnic_type'] == network_model.VNIC_TYPE_DIRECT:
            net_type = 'hostdev'

        designer.set_vif_host_backend_hw_veb(
            conf, net_type, profile['pci_slot'],
            vif_details[network_model.VIF_DETAILS_VLAN])

        designer.set_vif_bandwidth_config(conf, inst_type)

        return conf

    def get_config_macvtap(self, instance, vif, image_meta,
                           inst_type, virt_type, host):
        conf = self.get_base_config(instance, vif, image_meta,
                                    inst_type, virt_type)

        vif_details = vif['details']
        macvtap_src = vif_details.get(network_model.VIF_DETAILS_MACVTAP_SOURCE)
        macvtap_mode = vif_details.get(network_model.VIF_DETAILS_MACVTAP_MODE)
        phys_interface = vif_details.get(
                                    network_model.VIF_DETAILS_PHYS_INTERFACE)

        missing_params = []
        if macvtap_src is None:
            missing_params.append(network_model.VIF_DETAILS_MACVTAP_SOURCE)
        if macvtap_mode is None:
            missing_params.append(network_model.VIF_DETAILS_MACVTAP_MODE)
        if phys_interface is None:
            missing_params.append(network_model.VIF_DETAILS_PHYS_INTERFACE)

        if len(missing_params) > 0:
            raise exception.VifDetailsMissingMacvtapParameters(
                                                vif_id=vif['id'],
                                                missing_params=missing_params)

        designer.set_vif_host_backend_direct_config(
            conf, macvtap_src, macvtap_mode)

        designer.set_vif_bandwidth_config(conf, inst_type)

        return conf

    def get_config_iovisor(self, instance, vif, image_meta,
                           inst_type, virt_type, host):
        conf = self.get_base_config(instance, vif, image_meta,
                                    inst_type, virt_type)

        dev = self.get_vif_devname(vif)
        designer.set_vif_host_backend_ethernet_config(conf, dev)

        designer.set_vif_bandwidth_config(conf, inst_type)

        return conf

    def get_config_midonet(self, instance, vif, image_meta,
                           inst_type, virt_type, host):
        conf = self.get_base_config(instance, vif, image_meta,
                                    inst_type, virt_type)

        dev = self.get_vif_devname(vif)
        designer.set_vif_host_backend_ethernet_config(conf, dev)

        return conf

    def get_config_tap(self, instance, vif, image_meta,
                       inst_type, virt_type, host):
        conf = self.get_base_config(instance, vif, image_meta,
                                    inst_type, virt_type)

        dev = self.get_vif_devname(vif)
        designer.set_vif_host_backend_ethernet_config(conf, dev)

        return conf

    def _get_vhostuser_settings(self, vif):
        vif_details = vif['details']
        mode = vif_details.get(network_model.VIF_DETAILS_VHOSTUSER_MODE,
                               'server')
        sock_path = vif_details.get(network_model.VIF_DETAILS_VHOSTUSER_SOCKET)
        if sock_path is None:
            raise exception.VifDetailsMissingVhostuserSockPath(
                                                        vif_id=vif['id'])
        return mode, sock_path

    def get_config_vhostuser(self, instance, vif, image_meta,
                            inst_type, virt_type, host):
        conf = self.get_base_config(instance, vif, image_meta,
                                    inst_type, virt_type)
        mode, sock_path = self._get_vhostuser_settings(vif)
        designer.set_vif_host_backend_vhostuser_config(conf, mode, sock_path)
        # (vladikr) Not setting up driver and queues for vhostuser
        # as queues are not supported in Libvirt until version 1.2.17
        if not host.has_min_version(MIN_LIBVIRT_VHOSTUSER_MQ):
            LOG.debug('Queues are not a vhostuser supported feature.')
            conf.driver_name = None
            conf.vhost_queues = None

        return conf

    def get_config_ib_hostdev(self, instance, vif, image_meta,
                              inst_type, virt_type, host):
        conf = vconfig.LibvirtConfigGuestHostdevPCI()
        pci_slot = vif['profile']['pci_slot']
        designer.set_vif_host_backend_ib_hostdev_config(conf, pci_slot)
        return conf

    def get_config_vrouter(self, instance, vif, image_meta,
                           inst_type, virt_type, host):
        conf = self.get_base_config(instance, vif, image_meta,
                                    inst_type, virt_type)
        dev = self.get_vif_devname(vif)
        designer.set_vif_host_backend_ethernet_config(conf, dev)

        designer.set_vif_bandwidth_config(conf, inst_type)
        return conf

    def get_config(self, instance, vif, image_meta,
                   inst_type, virt_type, host):
        vif_type = vif['type']

        LOG.debug('vif_type=%(vif_type)s instance=%(instance)s '
                  'vif=%(vif)s virt_type%(virt_type)s',
                  {'vif_type': vif_type, 'instance': instance,
                   'vif': vif, 'virt_type': virt_type})

        if vif_type is None:
            raise exception.NovaException(
                _("vif_type parameter must be present "
                  "for this vif_driver implementation"))
        vif_slug = self._normalize_vif_type(vif_type)
        func = getattr(self, 'get_config_%s' % vif_slug, None)
        if not func:
            raise exception.NovaException(
                _("Unexpected vif_type=%s") % vif_type)
        return func(instance, vif, image_meta,
                    inst_type, virt_type, host)

    def plug_bridge(self, instance, vif):
        """Ensure that the bridge exists, and add VIF to it."""
        network = vif['network']
        if (not network.get_meta('multi_host', False) and
                    network.get_meta('should_create_bridge', False)):
            if network.get_meta('should_create_vlan', False):
                iface = CONF.vlan_interface or \
                        network.get_meta('bridge_interface')
                LOG.debug('Ensuring vlan %(vlan)s and bridge %(bridge)s',
                          {'vlan': network.get_meta('vlan'),
                           'bridge': self.get_bridge_name(vif)},
                          instance=instance)
                linux_net.LinuxBridgeInterfaceDriver.ensure_vlan_bridge(
                                             network.get_meta('vlan'),
                                             self.get_bridge_name(vif),
                                             iface)
            else:
                iface = CONF.flat_interface or \
                            network.get_meta('bridge_interface')
                LOG.debug("Ensuring bridge %s",
                          self.get_bridge_name(vif), instance=instance)
                linux_net.LinuxBridgeInterfaceDriver.ensure_bridge(
                                        self.get_bridge_name(vif),
                                        iface)

    def plug_ovs_bridge(self, instance, vif):
        """No manual plugging required."""
        pass

    def _plug_bridge_with_port(self, instance, vif, port):
        iface_id = self.get_ovs_interfaceid(vif)
        br_name = self.get_br_name(vif['id'])
        v1_name, v2_name = self.get_veth_pair_names(vif['id'])

        if not linux_net.device_exists(br_name):
            utils.execute('brctl', 'addbr', br_name, run_as_root=True)
            utils.execute('brctl', 'setfd', br_name, 0, run_as_root=True)
            utils.execute('brctl', 'stp', br_name, 'off', run_as_root=True)
            utils.execute('tee',
                          ('/sys/class/net/%s/bridge/multicast_snooping' %
                           br_name),
                          process_input='0',
                          run_as_root=True,
                          check_exit_code=[0, 1])
            disv6 = '/proc/sys/net/ipv6/conf/%s/disable_ipv6' % br_name
            if os.path.exists(disv6):
                utils.execute('tee',
                              disv6,
                              process_input='1',
                              run_as_root=True,
                              check_exit_code=[0, 1])

        if not linux_net.device_exists(v2_name):
            mtu = vif['network'].get_meta('mtu')
            linux_net._create_veth_pair(v1_name, v2_name, mtu)
            utils.execute('ip', 'link', 'set', br_name, 'up', run_as_root=True)
            utils.execute('brctl', 'addif', br_name, v1_name, run_as_root=True)
            if port == 'ovs':
                linux_net.create_ovs_vif_port(self.get_bridge_name(vif),
                                              v2_name, iface_id,
                                              vif['address'], instance.uuid,
                                              mtu)
            elif port == 'ivs':
                linux_net.create_ivs_vif_port(v2_name, iface_id,
                                              vif['address'], instance.uuid)

    def plug_ovs_hybrid(self, instance, vif):
        """Plug using hybrid strategy

        Create a per-VIF linux bridge, then link that bridge to the OVS
        integration bridge via a veth device, setting up the other end
        of the veth device just like a normal OVS port.  Then boot the
        VIF on the linux bridge using standard libvirt mechanisms.
        """
        self._plug_bridge_with_port(instance, vif, port='ovs')

    def plug_ovs(self, instance, vif):
        if self.get_firewall_required(vif) or vif.is_hybrid_plug_enabled():
            self.plug_ovs_hybrid(instance, vif)
        else:
            self.plug_ovs_bridge(instance, vif)

    def plug_ivs_ethernet(self, instance, vif):
        iface_id = self.get_ovs_interfaceid(vif)
        dev = self.get_vif_devname(vif)
        linux_net.create_tap_dev(dev)
        linux_net.create_ivs_vif_port(dev, iface_id, vif['address'],
                                      instance.uuid)

    def plug_ivs_hybrid(self, instance, vif):
        """Plug using hybrid strategy (same as OVS)

        Create a per-VIF linux bridge, then link that bridge to the OVS
        integration bridge via a veth device, setting up the other end
        of the veth device just like a normal IVS port.  Then boot the
        VIF on the linux bridge using standard libvirt mechanisms.
        """
        self._plug_bridge_with_port(instance, vif, port='ivs')

    def plug_ivs(self, instance, vif):
        if self.get_firewall_required(vif) or vif.is_hybrid_plug_enabled():
            self.plug_ivs_hybrid(instance, vif)
        else:
            self.plug_ivs_ethernet(instance, vif)

    def plug_ib_hostdev(self, instance, vif):
        fabric = vif.get_physical_network()
        if not fabric:
            raise exception.NetworkMissingPhysicalNetwork(
                network_uuid=vif['network']['id']
            )
        pci_slot = vif['profile']['pci_slot']
        device_id = instance['uuid']
        vnic_mac = vif['address']
        try:
            utils.execute('ebrctl', 'add-port', vnic_mac, device_id,
                          fabric, network_model.VIF_TYPE_IB_HOSTDEV,
                          pci_slot, run_as_root=True)
        except processutils.ProcessExecutionError:
            LOG.exception(
                _LE("Failed while plugging ib hostdev vif"),
                instance=instance
            )

    def plug_802qbg(self, instance, vif):
        pass

    def plug_802qbh(self, instance, vif):
        pass

    def plug_hw_veb(self, instance, vif):
        if vif['vnic_type'] == network_model.VNIC_TYPE_MACVTAP:
            linux_net.set_vf_interface_vlan(
                vif['profile']['pci_slot'],
                mac_addr=vif['address'],
                vlan=vif['details'][network_model.VIF_DETAILS_VLAN])

    def plug_macvtap(self, instance, vif):
        vif_details = vif['details']
        vlan = vif_details.get(network_model.VIF_DETAILS_VLAN)
        if vlan:
            vlan_name = vif_details.get(
                                    network_model.VIF_DETAILS_MACVTAP_SOURCE)
            phys_if = vif_details.get(network_model.VIF_DETAILS_PHYS_INTERFACE)
            linux_net.LinuxBridgeInterfaceDriver.ensure_vlan(
                vlan, phys_if, interface=vlan_name)

    def plug_midonet(self, instance, vif):
        """Plug into MidoNet's network port

        Bind the vif to a MidoNet virtual port.
        """
        dev = self.get_vif_devname(vif)
        port_id = vif['id']
        try:
            linux_net.create_tap_dev(dev)
            utils.execute('mm-ctl', '--bind-port', port_id, dev,
                          run_as_root=True)
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while plugging vif"), instance=instance)

    def plug_iovisor(self, instance, vif):
        """Plug using PLUMgrid IO Visor Driver

        Connect a network device to their respective
        Virtual Domain in PLUMgrid Platform.
        """
        dev = self.get_vif_devname(vif)
        iface_id = vif['id']
        linux_net.create_tap_dev(dev)
        net_id = vif['network']['id']
        tenant_id = instance.project_id
        try:
            utils.execute('ifc_ctl', 'gateway', 'add_port', dev,
                          run_as_root=True)
            utils.execute('ifc_ctl', 'gateway', 'ifup', dev,
                          'access_vm', iface_id, vif['address'],
                          'pgtag2=%s' % net_id, 'pgtag1=%s' % tenant_id,
                          run_as_root=True)
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while plugging vif"), instance=instance)

    def plug_tap(self, instance, vif):
        """Plug a VIF_TYPE_TAP virtual interface."""
        dev = self.get_vif_devname(vif)
        mac = vif['details'].get(network_model.VIF_DETAILS_TAP_MAC_ADDRESS)
        linux_net.create_tap_dev(dev, mac)
        network = vif.get('network')
        mtu = network.get_meta('mtu') if network else None
        linux_net._set_device_mtu(dev, mtu)

    def plug_vhostuser_fp(self, instance, vif):
        """Create a fp netdevice interface with a vhostuser socket"""
        dev = self.get_vif_devname(vif)
        if linux_net.device_exists(dev):
            return

        ovs_plug = vif['details'].get(
                                network_model.VIF_DETAILS_VHOSTUSER_OVS_PLUG,
                                False)
        sockmode_qemu, sockpath = self._get_vhostuser_settings(vif)
        sockmode_port = 'client' if sockmode_qemu == 'server' else 'server'

        try:
            linux_net.create_fp_dev(dev, sockpath, sockmode_port)

            if ovs_plug:
                if vif.is_hybrid_plug_enabled():
                    self.plug_ovs_hybrid(instance, vif)
                    utils.execute('brctl', 'addif',
                                  self.get_br_name(vif['id']),
                                  dev, run_as_root=True)
                else:
                    iface_id = self.get_ovs_interfaceid(vif)
                    mtu = vif['network'].get_meta('mtu')
                    linux_net.create_ovs_vif_port(self.get_bridge_name(vif),
                                                  dev, iface_id,
                                                  vif['address'],
                                                  instance.uuid, mtu)
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while plugging vif"), instance=instance)

    def plug_vhostuser_ovs(self, instance, vif):
        """Plug a VIF_TYPE_VHOSTUSER into an ovs bridge"""
        iface_id = self.get_ovs_interfaceid(vif)
        port_name = os.path.basename(
            vif['details'][network_model.VIF_DETAILS_VHOSTUSER_SOCKET])
        mtu = vif['network'].get_meta('mtu')
        linux_net.create_ovs_vif_port(
            self.get_bridge_name(vif),
            port_name, iface_id, vif['address'],
            instance.uuid, mtu,
            interface_type=network_model.OVS_VHOSTUSER_INTERFACE_TYPE)

    def plug_vhostuser(self, instance, vif):
        fp_plug = vif['details'].get(
                                network_model.VIF_DETAILS_VHOSTUSER_FP_PLUG,
                                False)
        ovs_plug = vif['details'].get(
                                network_model.VIF_DETAILS_VHOSTUSER_OVS_PLUG,
                                False)
        if fp_plug:
            self.plug_vhostuser_fp(instance, vif)
        elif ovs_plug:
            self.plug_vhostuser_ovs(instance, vif)

    def plug_vrouter(self, instance, vif):
        """Plug into Contrail's network port

        Bind the vif to a Contrail virtual port.
        """
        dev = self.get_vif_devname(vif)
        ip_addr = '0.0.0.0'
        ip6_addr = None
        subnets = vif['network']['subnets']
        for subnet in subnets:
            if not subnet['ips']:
                continue
            ips = subnet['ips'][0]
            if not ips['address']:
                continue
            if (ips['version'] == 4):
                if ips['address'] is not None:
                    ip_addr = ips['address']
            if (ips['version'] == 6):
                if ips['address'] is not None:
                    ip6_addr = ips['address']

        ptype = 'NovaVMPort'
        if (cfg.CONF.libvirt.virt_type == 'lxc'):
            ptype = 'NameSpacePort'

        cmd_args = ("--oper=add --uuid=%s --instance_uuid=%s --vn_uuid=%s "
                    "--vm_project_uuid=%s --ip_address=%s --ipv6_address=%s"
                    " --vm_name=%s --mac=%s --tap_name=%s --port_type=%s "
                    "--tx_vlan_id=%d --rx_vlan_id=%d" % (vif['id'],
                    instance.uuid, vif['network']['id'],
                    instance.project_id, ip_addr, ip6_addr,
                    instance.display_name, vif['address'],
                    vif['devname'], ptype, -1, -1))
        try:
            linux_net.create_tap_dev(dev)
            utils.execute('vrouter-port-control', cmd_args, run_as_root=True)
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while plugging vif"), instance=instance)

    def plug(self, instance, vif):
        vif_type = vif['type']

        LOG.debug('vif_type=%(vif_type)s instance=%(instance)s '
                  'vif=%(vif)s',
                  {'vif_type': vif_type, 'instance': instance,
                   'vif': vif})

        if vif_type is None:
            raise exception.VirtualInterfacePlugException(
                _("vif_type parameter must be present "
                  "for this vif_driver implementation"))
        vif_slug = self._normalize_vif_type(vif_type)
        func = getattr(self, 'plug_%s' % vif_slug, None)
        if not func:
            raise exception.VirtualInterfacePlugException(
                _("Plug vif failed because of unexpected "
                  "vif_type=%s") % vif_type)
        func(instance, vif)

    def unplug_bridge(self, instance, vif):
        """No manual unplugging required."""
        pass

    def unplug_binding_failed(self, instance, vif):
        """No manual unplugging required."""
        pass

    def unplug_ovs_bridge(self, instance, vif):
        """No manual unplugging required."""
        pass

    def unplug_ovs_hybrid(self, instance, vif):
        """UnPlug using hybrid strategy

        Unhook port from OVS, unhook port from bridge, delete
        bridge, and delete both veth devices.
        """
        try:
            br_name = self.get_br_name(vif['id'])
            v1_name, v2_name = self.get_veth_pair_names(vif['id'])

            if linux_net.device_exists(br_name):
                utils.execute('brctl', 'delif', br_name, v1_name,
                              run_as_root=True)
                utils.execute('ip', 'link', 'set', br_name, 'down',
                              run_as_root=True)
                utils.execute('brctl', 'delbr', br_name,
                              run_as_root=True)

            linux_net.delete_ovs_vif_port(self.get_bridge_name(vif),
                                          v2_name)
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while unplugging vif"),
                          instance=instance)

    def unplug_ovs(self, instance, vif):
        if self.get_firewall_required(vif) or vif.is_hybrid_plug_enabled():
            self.unplug_ovs_hybrid(instance, vif)
        else:
            self.unplug_ovs_bridge(instance, vif)

    def unplug_ivs_ethernet(self, instance, vif):
        """Unplug the VIF by deleting the port from the bridge."""
        try:
            linux_net.delete_ivs_vif_port(self.get_vif_devname(vif))
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while unplugging vif"),
                          instance=instance)

    def unplug_ivs_hybrid(self, instance, vif):
        """UnPlug using hybrid strategy (same as OVS)

        Unhook port from IVS, unhook port from bridge, delete
        bridge, and delete both veth devices.
        """
        try:
            br_name = self.get_br_name(vif['id'])
            v1_name, v2_name = self.get_veth_pair_names(vif['id'])

            utils.execute('brctl', 'delif', br_name, v1_name, run_as_root=True)
            utils.execute('ip', 'link', 'set', br_name, 'down',
                          run_as_root=True)
            utils.execute('brctl', 'delbr', br_name, run_as_root=True)
            linux_net.delete_ivs_vif_port(v2_name)
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while unplugging vif"),
                          instance=instance)

    def unplug_ivs(self, instance, vif):
        if self.get_firewall_required(vif) or vif.is_hybrid_plug_enabled():
            self.unplug_ivs_hybrid(instance, vif)
        else:
            self.unplug_ivs_ethernet(instance, vif)

    def unplug_ib_hostdev(self, instance, vif):
        fabric = vif.get_physical_network()
        if not fabric:
            raise exception.NetworkMissingPhysicalNetwork(
                network_uuid=vif['network']['id']
            )
        vnic_mac = vif['address']
        try:
            utils.execute('ebrctl', 'del-port', fabric, vnic_mac,
                          run_as_root=True)
        except Exception:
            LOG.exception(_LE("Failed while unplugging ib hostdev vif"))

    def unplug_802qbg(self, instance, vif):
        pass

    def unplug_802qbh(self, instance, vif):
        pass

    def unplug_hw_veb(self, instance, vif):
        if vif['vnic_type'] == network_model.VNIC_TYPE_MACVTAP:
            # The ip utility doesn't accept the MAC 00:00:00:00:00:00.
            # Therefore, keep the MAC unchanged.  Later operations on
            # the same VF will not be affected by the existing MAC.
            linux_net.set_vf_interface_vlan(vif['profile']['pci_slot'],
                                            mac_addr=vif['address'])

    def unplug_macvtap(self, instance, vif):
        pass

    def unplug_midonet(self, instance, vif):
        """Unplug from MidoNet network port

        Unbind the vif from a MidoNet virtual port.
        """
        dev = self.get_vif_devname(vif)
        port_id = vif['id']
        try:
            utils.execute('mm-ctl', '--unbind-port', port_id,
                          run_as_root=True)
            linux_net.delete_net_dev(dev)
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while unplugging vif"),
                          instance=instance)

    def unplug_tap(self, instance, vif):
        """Unplug a VIF_TYPE_TAP virtual interface."""
        dev = self.get_vif_devname(vif)
        try:
            linux_net.delete_net_dev(dev)
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while unplugging vif"),
                          instance=instance)

    def unplug_iovisor(self, instance, vif):
        """Unplug using PLUMgrid IO Visor Driver

        Delete network device and to their respective
        connection to the Virtual Domain in PLUMgrid Platform.
        """
        dev = self.get_vif_devname(vif)
        try:
            utils.execute('ifc_ctl', 'gateway', 'ifdown',
                          dev, run_as_root=True)
            utils.execute('ifc_ctl', 'gateway', 'del_port', dev,
                          run_as_root=True)
            linux_net.delete_net_dev(dev)
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while unplugging vif"),
                          instance=instance)

    def unplug_vhostuser_fp(self, instance, vif):
        """Delete a fp netdevice interface with a vhostuser socket"""
        dev = self.get_vif_devname(vif)
        ovs_plug = vif['details'].get(
                        network_model.VIF_DETAILS_VHOSTUSER_OVS_PLUG,
                        False)

        try:
            if ovs_plug:
                if vif.is_hybrid_plug_enabled():
                    self.unplug_ovs_hybrid(instance, vif)
                else:
                    linux_net.delete_ovs_vif_port(self.get_bridge_name(vif),
                                                  dev, False)
            linux_net.delete_fp_dev(dev)
        except processutils.ProcessExecutionError:
            LOG.exception(_LE("Failed while unplugging vif"),
                          instance=instance)

    def unplug_vhostuser_ovs(self, instance, vif):
        """Unplug a VIF_TYPE_VHOSTUSER into an ovs bridge"""
        port_name = os.path.basename(
            vif['details'][network_model.VIF_DETAILS_VHOSTUSER_SOCKET])
        linux_net.delete_ovs_vif_port(self.get_bridge_name(vif),
                                      port_name)

    def unplug_vhostuser(self, instance, vif):
        fp_plug = vif['details'].get(
                        network_model.VIF_DETAILS_VHOSTUSER_FP_PLUG,
                        False)
        ovs_plug = vif['details'].get(
                        network_model.VIF_DETAILS_VHOSTUSER_OVS_PLUG,
                        False)
        if fp_plug:
            self.unplug_vhostuser_fp(instance, vif)
        elif ovs_plug:
            self.unplug_vhostuser_ovs(instance, vif)

    def unplug_vrouter(self, instance, vif):
        """Unplug Contrail's network port

        Unbind the vif from a Contrail virtual port.
        """
        dev = self.get_vif_devname(vif)
        cmd_args = ("--oper=delete --uuid=%s" % (vif['id']))
        try:
            utils.execute('vrouter-port-control', cmd_args, run_as_root=True)
            linux_net.delete_net_dev(dev)
        except processutils.ProcessExecutionError:
            LOG.exception(
                _LE("Failed while unplugging vif"), instance=instance)

    def unplug(self, instance, vif):
        vif_type = vif['type']

        LOG.debug('vif_type=%(vif_type)s instance=%(instance)s '
                  'vif=%(vif)s',
                  {'vif_type': vif_type, 'instance': instance,
                   'vif': vif})

        if vif_type is None:
            raise exception.NovaException(
                _("vif_type parameter must be present "
                  "for this vif_driver implementation"))
        vif_slug = self._normalize_vif_type(vif_type)
        func = getattr(self, 'unplug_%s' % vif_slug, None)
        if not func:
            raise exception.NovaException(
                _("Unexpected vif_type=%s") % vif_type)
        func(instance, vif)
