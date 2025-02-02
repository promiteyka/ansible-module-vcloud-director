# Copyright © 2018 VMware, Inc. All Rights Reserved.
# SPDX-License-Identifier: BSD-2-Clause OR GPL-3.0-only

# !/usr/bin/python

ANSIBLE_METADATA = {
    'metadata_version': '1.1',
    'status': ['preview'],
    'supported_by': 'community'
}

DOCUMENTATION = '''
---
module: vcd_vapp_network
short_description: Ansible Module to manage (create/delete) Networks in vApps in vCloud Director.
version_added: "2.4"
description:
    - "Ansible Module to manage (create/delete) Networks in vApps."

options:
    user:
        description:
            - vCloud Director user name
        required: false
    password:
        description:
            - vCloud Director user password
        required: false
    host:
        description:
            - vCloud Director host address
        required: false
    org:
        description:
            - Organization name on vCloud Director to access
        required: false
    api_version:
        description:
            - Pyvcloud API version
        required: false
    verify_ssl_certs:
        description:
            - whether to use secure connection to vCloud Director host
        required: false
    network:
        description:
            - Network name
        required: true
    vapp:
        description:
            - vApp name
        required: true
    vdc:
        description:
            - VDC name
        required: true
    fence_mode:
        description:
            - Network fence mode ('bridged'/'isolated'/'natRouted').
        required: false
    parent_network:
        description:
            - VDC parent network to connect to
        required: false
    ip_scope:
        description:
            - IP scope for 'isolated' and 'natRouted' networks,
              if fence_mode is 'natRouted', parent_network is required
        required: false
    ip_range_start:
        description:
            - first ip of pool range
        required: false
    ip_range_end:
        description:
            - last ip of pool range, set to ip_range_start if omitted
        required: false
    dns1, dns2:
        description:
            - DNS1 and DNS2 of network
        required: false
    dns_suffix:
        description:
            - dns suffix of network
        required: false
    nat_state, fw_state:
        description:
            - state of nat and firewall services for 'natRouted' networks ('enabled'/'disabled')
    state:
        description:
            - state of network ('present'/'absent').
        required: true
author:
    - mtaneja@vmware.com
'''

EXAMPLES = '''
- name: Test with a message
  vcd_vapp_network:
    user: terraform
    password: abcd
    host: csa.sandbox.org
    org: Terraform
    api_version: 30
    verify_ssl_certs: False
    network = "vapp1_net"
    vapp = "vapp1"
    vdc = "vdc1"
    ip_scope: "192.168.0.0/24"
    parent_network: "org_net"
    fence_mode: "natRouted"
    nat_state: "disabled"
    dns1: "8.8.8.8"
    dns_suffix: "test.net"
    state = "present"
'''

RETURN = '''
msg: success/failure message corresponding to vapp network state
changed: true if resource has been changed else false
'''

from lxml import etree
from ipaddress import ip_network
from pyvcloud.vcd.org import Org
from pyvcloud.vcd.vdc import VDC
from pyvcloud.vcd.client import E
from pyvcloud.vcd.vapp import VApp
from pyvcloud.vcd.client import NSMAP
from pyvcloud.vcd.client import E_OVF
from pyvcloud.vcd.client import FenceMode
from pyvcloud.vcd.client import EntityType
from pyvcloud.vcd.client import RelationType
from ansible_collections.vmware.vcloud.plugins.module_utils.vcd import VcdAnsibleModule
from pyvcloud.vcd.exceptions import EntityNotFoundException, OperationNotSupportedException


VAPP_NETWORK_STATES = ['present', 'absent']


def vapp_network_argument_spec():
    return dict(
        network=dict(type='str', required=True),
        vapp=dict(type='str', required=True),
        vdc=dict(type='str', required=True),
        fence_mode=dict(type='str', required=False, default=FenceMode.BRIDGED.value),
        parent_network=dict(type='str', required=False, default=None),
        ip_scope=dict(type='str', required=False, default=None),
        ip_range_start=dict(type='str', required=False, default=None),
        ip_range_end=dict(type='str', required=False, default=None),
        nat_state=dict(type='str', required=False, default='enabled'),
        fw_state=dict(type='str', required=False, default='enabled'),
        dns1=dict(type='str', required=False, default=''),
        dns2=dict(type='str', required=False, default=''),
        dns_suffix=dict(type='str', required=False, default=''),
        state=dict(choices=VAPP_NETWORK_STATES, required=True),
    )


class VappNetwork(VcdAnsibleModule):
    def __init__(self, **kwargs):
        super(VappNetwork, self).__init__(**kwargs)
        vapp_resource = self.get_resource()
        self.vapp = VApp(self.client, resource=vapp_resource)

    def manage_states(self):
        state = self.params.get('state')
        if state == "present":
            return self.add_network()

        if state == "absent":
            return self.delete_network()

    def get_resource(self):
        vapp = self.params.get('vapp')
        vdc = self.params.get('vdc')
        org_resource = Org(self.client, resource=self.client.get_org())
        vdc_resource = VDC(self.client, resource=org_resource.get_vdc(vdc))
        vapp_resource_href = vdc_resource.get_resource_href(name=vapp, entity_type=EntityType.VAPP)
        vapp_resource = self.client.get_resource(vapp_resource_href)

        return vapp_resource

    def get_network(self):
        network_name = self.params.get('network')
        networks = self.vapp.get_all_networks()
        for network in networks:
            if network.get('{'+NSMAP['ovf']+'}name') == network_name:
                return network
        raise EntityNotFoundException('Can\'t find the specified vApp network')

    def delete_network(self):
        network_name = self.params.get('network')
        response = dict()
        response['changed'] = False

        try:
            self.get_network()
        except EntityNotFoundException:
            response['warnings'] = 'Vapp Network {} is not present.'.format(network_name)
        else:
            network_config_section = self.vapp.resource.NetworkConfigSection
            for network_config in network_config_section.NetworkConfig:
                if network_config.get('networkName') == network_name:
                    network_config_section.remove(network_config)
            delete_network_task = self.client.put_linked_resource(
                self.vapp.resource.NetworkConfigSection, RelationType.EDIT,
                EntityType.NETWORK_CONFIG_SECTION.value,
                network_config_section)
            self.execute_task(delete_network_task)
            response['msg'] = 'Vapp Network {} has been deleted.'.format(network_name)
            response['changed'] = True

        return response

    def add_network(self):
        network_name = self.params.get('network')
        fence_mode = self.params.get('fence_mode')
        parent_network = self.params.get('parent_network')
        ip_scope = self.params.get('ip_scope')
        ip_range_start = self.params.get('ip_range_start')
        ip_range_end = self.params.get('ip_range_end')
        dns1 = self.params.get('dns1')
        dns2 = self.params.get('dns2')
        dns_suffix = self.params.get('dns_suffix')
        nat_state = self.params.get('nat_state')
        fw_state = self.params.get('fw_state')

        response = dict()
        response['changed'] = False

        try:
            self.get_network()
        except EntityNotFoundException:
            network_config_section = self.vapp.resource.NetworkConfigSection
            config = E.Configuration()
            if parent_network:
                vdc = self.params.get('vdc')
                org_resource = Org(self.client, resource=self.client.get_org())
                vdc_resource = VDC(self.client, resource=org_resource.get_vdc(vdc))
                orgvdc_networks = vdc_resource.list_orgvdc_network_resources(parent_network)
                parent = next((network for network in orgvdc_networks if network.get('name') == parent_network), None)
                if parent:
                    if ip_scope:
                        scope = E.IpScope(
                            E.IsInherited('false'),
                            E.Gateway(str(ip_network(ip_scope, strict=False).network_address+1)),
                            E.Netmask(str(ip_network(ip_scope, strict=False).netmask)),
                            E.Dns1(dns1),
                            E.Dns2(dns2))
                        if ip_range_start:
                            if not ip_range_end:
                                ip_range_end = ip_range_start
                            ip_range = E.IpRange(
                                E.StartAddress(ip_range_start),
                                E.EndAddress(ip_range_end))
                            scope.append(E.IpRanges(ip_range))
                        config.append(E.IpScopes(scope))
                    config.append(E.ParentNetwork(href=parent.get('href')))
                else:
                    raise EntityNotFoundException('Parent network \'%s\' does not exist'.format(parent_network))
            elif ip_scope:
                scope = E.IpScope(
                    E.IsInherited('false'),
                    E.Gateway(str(ip_network(ip_scope, strict=False).network_address+1)),
                    E.Netmask(str(ip_network(ip_scope, strict=False).netmask)),
                    E.Dns1(dns1),
                    E.Dns2(dns2),
                    E.DnsSuffix(dns_suffix))
                if ip_range_start:
                    if not ip_range_end:
                        ip_range_end = ip_range_start
                    ip_range = E.IpRange(
                        E.StartAddress(ip_range_start),
                        E.EndAddress(ip_range_end))
                    scope.append(E.IpRanges(ip_range))
                config.append(E.IpScopes(scope))
            else:
                raise VappNetworkCreateError('Either parent_network or ip_scope must be set')
            config.append(E.FenceMode(fence_mode))

            features = E.Features()
            if fw_state == 'disabled':
                features.append(E.FirewallService(E.IsEnabled('false')))
            if nat_state == 'disabled':
                features.append(E.NatService(E.IsEnabled('false')))
            config.append(features)

            network_config = E.NetworkConfig(config, networkName=network_name)
            network_config_section.append(network_config)

            add_network_task = self.client.put_linked_resource(
                self.vapp.resource.NetworkConfigSection, RelationType.EDIT,
                EntityType.NETWORK_CONFIG_SECTION.value,
                network_config_section)
            self.execute_task(add_network_task)
            response['msg'] = 'Vapp Network {} has been added'.format(network_name)
            response['changed'] = True
        else:
            response['warnings'] = 'Vapp Network {} is already present.'.format(network_name)

        return response


def main():
    argument_spec = vapp_network_argument_spec()
    response = dict(
        msg=dict(type='str')
    )
    module = VappNetwork(argument_spec=argument_spec, supports_check_mode=True)

    try:
        if not module.params.get('state'):
            raise Exception('Please provide the state for the resource.')

        response = module.manage_states()
        module.exit_json(**response)

    except Exception as error:
        response['msg'] = error
        module.fail_json(**response)


if __name__ == '__main__':
    main()