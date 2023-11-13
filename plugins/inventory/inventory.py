# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)

__metaclass__ = type

DOCUMENTATION = '''
---
name: vmwarevcloud
plugin_type: inventory
short_description: vmware.Cloud inventory source

extends_documentation_fragment:
    - inventory_cache
    - constructed

description:
    - Get inventory hosts from Vmware Vcloud

options:
    plugin:
        description: Token that ensures this is a source file for the plugin.
        required: True
        choices: ['vmware.vcloud.inventory']
    filters:
        description: dictionary of filters
        type: dict
'''

EXAMPLES = '''
---
plugin: vmware.vcloud.inventory

keyed_groups:
  - key: labels.role
    separator: ''
  # Just for example
  - key: labels.project
    separator: ''
  - key: folderId
    separator: ''
    
filters:
  status: 'RUNNING'
  labels:
    "customer": "lekton"
'''

from ansible.plugins.inventory import BaseInventoryPlugin, Constructable, Cacheable
import requests
import os
import xml.etree.cElementTree as ET

class InventoryModule(BaseInventoryPlugin, Constructable, Cacheable):
    NAME = 'vmware.vcloud.inventory'

    def _init_client(self):
        self.credentials = {
            'base_url': '',
            'username': '',
            'password': '',
            'org': '',
            'headers': {
                'Accept': 'application/*+xml;version=37.0'
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
        url = href + '/metadata'
        r = requests.get(url, headers=self.credentials['headers'])
        vals = self.extract_from_tree(url=url).iter(
            '{http://www.vmware.com/vcloud/v1.5}MetadataEntry'
        )

        groups = []

        for val in vals:
            key = val.find('{http://www.vmware.com/vcloud/v1.5}Key').text

            if key == 'groups':
                for value in val.iter('{http://www.vmware.com/vcloud/v1.5}Value'):
                    groups = value.text.replace('\'', '').replace('"', '').strip('[]').split(', ')
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

                hostvar = {}

                host_name = host.get('name')
                inventory_hostname = f"{host_name}.{vapp_name}".format(host_name=host_name, vapp_name=vapp_name)

                ip_address = self.get_ip_address(host=host)
                # get only hosts with ansible_host key
                if ip_address['ansible_host']:
                    hostvar['ansible_host'] = ip_address['ansible_host']
                else:
                    break
                hostvar['tags'] = self.gather_meta_from(host.get('href'))

                self.inventory.add_host(inventory_hostname)

                for key in hostvar:
                    self.inventory.set_variable(inventory_hostname, key, hostvar[key])

                self._set_composite_vars(self.get_option('compose'), hostvar, inventory_hostname)
                self._add_host_to_composed_groups(self.get_option('groups'), hostvar, inventory_hostname)
                self._add_host_to_keyed_groups(self.get_option('keyed_groups'), hostvar, inventory_hostname)

    def parse(self, inventory, loader, path, cache=False):
        super(InventoryModule, self).parse(inventory, loader, path)
        self._read_config_data(path)

        self._init_client()
        self.read_credentials()
        self.authenticate_to_api()
        self._process_hosts()
