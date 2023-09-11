# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)

__metaclass__ = type

DOCUMENTATION = '''
---
name: vmware vcloud
plugin_type: inventory
short_description: vmware vcloud inventory source
requirements:
    - pyvcloud

extends_documentation_fragment:
    - inventory_cache
    - constructed

description:
    - Get inventory hosts from vmware vcloud
'''

from ansible.errors import AnsibleError
from ansible.plugins.inventory import BaseInventoryPlugin, Constructable, Cacheable
from ansible.utils.display import Display
from ansible.module_utils._text import to_native
import json
import requests
import os
import argparse
import xml.etree.cElementTree as ET
from time import time


display = Display()


class InventoryModule(BaseInventoryPlugin, Constructable, Cacheable):
    NAME = 'vmwarevcloud'

    def _init_client(self):
        self.credentials = {
            'base_url': '',
            'username': '',
            'password': '',
            'org': '',
            'headers': {
                'Accept': 'application/*+xml;version=30.0'
            },
        }

    def read_credentials(self):
        self.credentials['base_url'] = os.environ.get('VCD_URL', '')

        if not self.credentials['base_url'].strip():
            print('Missing VCD_URL environment variable!')
            exit(1)


        self.credentials['username'] = os.environ.get('VCD_USER', '')

        if not self.credentials['username'].strip():
            print('Missing VCD_USER environment variable!')
            exit(1)

        self.credentials['password'] = os.environ.get('VCD_PASSWORD', '')

        if not self.credentials['password'].strip():
            print('Missing VCD_PASSWORD environment variable!')
            exit(1)

        self.credentials['org'] = os.environ.get('VCD_ORG', '')

        if not self.credentials['org'].strip():
            print('Missing VCD_ORG environment variable!')
            exit(1)

    def authenticate_to_api(self):
        url = self.credentials['base_url'] + '/api/sessions'

        session = requests.post(
            url,
            headers=self.credentials['headers'],
            auth=(self.credentials['username'] + '@' + self.credentials['org'], self.credentials['password'])
        )
        self.credentials['headers']['x-vcloud-authorization'] = session.headers['x-vcloud-authorization']

    def gather_vapp_list(self):
        url = self.credentials['base_url'] + '/api/vApps/query'

        return self.extract_from_tree(
            url
        ).findall(
            '{http://www.vmware.com/vcloud/v1.5}VAppRecord'
        )

    def gather_hosts_from(self, href):
        return self.extract_from_tree(
            href
        ).iter(
            '{http://www.vmware.com/vcloud/v1.5}Vm'
        )

    def gather_meta_from(self, href):
        vals = self.extract_from_tree(
            href + '/metadata'
        ).iter(
            '{http://www.vmware.com/vcloud/v1.5}MetadataEntry'
        )

        groups = []

        for val in vals:
            key = val.find('{http://www.vmware.com/vcloud/v1.5}Key').text

            if key == 'ansible_groups':
                for value in val.iter('{http://www.vmware.com/vcloud/v1.5}Value'):
                    groups.extend(value.text.split(','))

        return groups

    def extract_from_tree(self, url):
        response = ET.fromstring(
            requests.get(
                url,
                headers=self.credentials['headers']
            ).content
        )
        return response

    def get_ip_address(self, host):
        result = {
            'ansible_host': self.search_within_attrs(
                host,
                '{http://www.vmware.com/vcloud/v1.5}IpAddress',
                True,
                ''
            )
        }
        return result

    def search_within_attrs(self, root, tag, text, attr):
        for elem in root.iter(tag):
            if text:
                return elem.text
            else:
                return elem.get(attr)

    def _process_hosts(self):
        for vapp in self.gather_vapp_list():
            vapp_name = vapp.get('name')

            for host in self.gather_hosts_from(vapp.get('href')):
                host_name = host.get('name')
                inventory_hostname = f"{host_name}.{vapp_name}".format(host_name=host_name, vapp_name=vapp_name)
                # get only hosts with ansible_host key
                ip_address = self.get_ip_address(host=host)
                if ip_address['ansible_host']:
                    self.inventory.add_host(inventory_hostname)
                    self.inventory.set_variable(inventory_hostname, 'ansible_host', ip_address['ansible_host'])
                else:
                    break


                # for metadata in self.gather_meta_from(host.get('href')):
                #     self.inventory[metadata]['hosts'].append(host.get('name'))
                #
                #     self.inventory['server']['hosts'].append(host.get('name'))
                #     self.inventory[vapp_name]['hosts'].append(host.get('name'))
                #     self.inventory['_meta']['hostvars'][host_name] = self.merge_available_attrs(host)


    def parse(self, inventory, loader, path, cache=False):
        super(InventoryModule, self).parse(inventory, loader, path)
        self._init_client()
        self.read_credentials()
        self.authenticate_to_api()
        self._process_hosts()

