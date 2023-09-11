#!/usr/bin/env python3

import json
import requests
import os
import argparse
import xml.etree.cElementTree as ET
from time import time

GLOBALS = {
    'ansible_become': 'true',
    'ansible_python_interpreter': '/usr/bin/python3',
    'ansible_ssh_common_args': '-o StrictHostKeyChecking=no -o ProxyCommand="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -W %h:%p $ANSIBLE_REMOTE_USER@public-jumphost-ip"'
}

OVERRIDE = {
    'jumphost': {
        'ansible_host': 'public-jumphost-ip',
        'ansible_ssh_common_args': '-o StrictHostKeyChecking=no'
    },
    'server-01': {
        'ansible_host': '10.10.0.21'
    },
    'server-02': {
        'ansible_host': '10.10.0.22'
    }
}

class VcdInventory(object):
    def _empty_inventory(self):
        return {
            'server': {
                'children': [],
                'hosts': []
            },
            '_meta': {
                'hostvars': {}
            }
        }

    def __init__(self):
        self.inventory = self._empty_inventory()

        self.credentials = {
            'base_url': '',
            'username': '',
            'password': '',
            'org': '',
            'headers': {
                'Accept': 'application/*+xml;version=30.0'
            },
        }

        self.parse_cli_args()
        self.read_credentials()
        self.configure_cache()

        if self.args.refresh_cache:
            self.call_update_cache()
        elif not self.is_cache_valid():
            self.call_update_cache()

        if self.args.host:
            data_to_print = self.host_information()
        elif self.args.list:
            if self.inventory == self._empty_inventory():
                data_to_print = self.inventory_from_cache()
            else:
                data_to_print = self.json_format_dict(self.inventory, True)

        print(data_to_print)

    def parse_cli_args(self):
        parser = argparse.ArgumentParser(description='Produce an Ansible Inventory file based on vCD')

        parser.add_argument(
            '--list',
            action='store_true',
            default=True,
            help='List instances, used as default action as well'
        )

        parser.add_argument(
            '--host',
            action='store',
            default=False,
            help='Get all the variables about a specific instance'
        )

        parser.add_argument(
            '--refresh-cache',
            action='store_true',
            default=False,
            help='Force refresh of cache by making API requests'
        )

        self.args = parser.parse_args()

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

    def configure_cache(self):
        cache_dir = os.path.expanduser('~/.vcd/cache')

        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)

        cache_name = self.credentials.get('org')
        cache_name += '-' + self.credentials.get('username')

        self.cache_path_file = os.path.join(cache_dir, '%s.cache' % cache_name)
        self.cache_max_age = 900

    def is_cache_valid(self):
        if os.path.isfile(self.cache_path_file):
            mod_time = os.path.getmtime(self.cache_path_file)
            current_time = time()

            return (mod_time + self.cache_max_age) > current_time

        return False

    def write_to_cache(self, data, filename):
        with open(filename, 'w') as f:
            f.write(self.json_format_dict(data, True))

    def gather_vapp_list(self):
        url = self.credentials['base_url'] + '/vApps/query'

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
        return ET.fromstring(
            requests.get(
                url,
                headers=self.credentials['headers']
            ).content
        )

    def merge_available_attrs(self, host):
        result = {
            'ansible_host': self.search_within_attrs(
                host,
                '{http://www.vmware.com/vcloud/v1.5}IpAddress',
                True,
                ''
            )
        }

        result.update(GLOBALS)

        if host.get('name') in OVERRIDE.keys():
            result.update(OVERRIDE[host.get('name')])

        return result

    def search_within_attrs(self, root, tag, text, attr):
        for elem in root.iter(tag):
            if text:
                return elem.text
            else:
                return elem.get(attr)

    def call_update_cache(self):
        self.authenticate_to_api()

        for vapp in self.gather_vapp_list():
            vapp_name = vapp.get('name')
            self.inventory['server']['children'].append(vapp_name)

            if not vapp_name in self.inventory.keys():
                self.inventory[vapp_name] = {
                    'hosts': []
                }

            for host in self.gather_hosts_from(vapp.get('href')):
                host_name = host.get('name')

                for metadata in self.gather_meta_from(host.get('href')):
                    if not metadata in self.inventory.keys():
                        self.inventory[metadata] = {
                            'hosts': []
                        }

                    self.inventory[metadata]['hosts'].append(host.get('name'))

                self.inventory['server']['hosts'].append(host.get('name'))
                self.inventory[vapp_name]['hosts'].append(host.get('name'))
                self.inventory['_meta']['hostvars'][host_name] = self.merge_available_attrs(host)

        self.write_to_cache(self.inventory, self.cache_path_file)

    def authenticate_to_api(self):
        url = self.credentials['base_url'] + '/sessions'

        session = requests.post(
            url,
            headers=self.credentials['headers'],
            auth=(self.credentials['username'] + '@' + self.credentials['org'], self.credentials['password'])
        )

        self.credentials['headers']['x-vcloud-authorization'] = session.headers['x-vcloud-authorization']

    def inventory_from_cache(self):
        with open(self.cache_path_file, 'r') as f:
            return f.read()

    def json_format_dict(self, data, pretty=False):
        if pretty:
            return json.dumps(data, sort_keys=True, indent=2)
        else:
            return json.dumps(data)

    def host_information(self):
        self.inventory = json.loads(self.inventory_from_cache())

        if self.args.host not in self.inventory['_meta']['hostvars'].keys():
            self.call_update_cache()

        if self.args.host not in self.inventory['_meta']['hostvars'].keys():
            return self.json_format_dict({}, True)

        return self.json_format_dict(self.inventory['_meta']['hostvars'][self.args.host], True)

if __name__ == '__main__':
    VcdInventory()