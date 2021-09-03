#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
#
# This script is licensed under GNU GPL version 2.0 or above
# (c) 2021 Antonio J. Delgado
# __description__

import sys
import os
import logging
import click
import click_config_file
from logging.handlers import SysLogHandler
from keystoneauth1 import session
from keystoneauth1.identity import v3
from keystoneclient.v3 import client as keystoneclient_v3
from novaclient import client as nova
from neutronclient.neutron import client as neutron
import ipaddress
import json
import socket
from icmplib import ping, multiping, traceroute, resolve

class CustomFormatter(logging.Formatter):
    """Logging colored formatter, adapted from https://stackoverflow.com/a/56944256/3638629"""

    grey = '\x1b[38;21m'
    blue = '\x1b[38;5;39m'
    yellow = '\x1b[38;5;226m'
    red = '\x1b[38;5;196m'
    bold_red = '\x1b[31;1m'
    reset = '\x1b[0m'

    def __init__(self, fmt):
        super().__init__()
        self.fmt = fmt
        self.FORMATS = {
            logging.DEBUG: self.grey + self.fmt + self.reset,
            logging.INFO: self.blue + self.fmt + self.reset,
            logging.WARNING: self.yellow + self.fmt + self.reset,
            logging.ERROR: self.red + self.fmt + self.reset,
            logging.CRITICAL: self.bold_red + self.fmt + self.reset
        }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

def jprint(object):
    print(json.dumps(object, indent=2))
    
class get_openstack_info_open_port:

    def __init__(self, debug_level, log_file, ip, port, method, protocol):
        ''' Initial function called when object is created '''
        self.config = dict()
        self.config['debug_level'] = debug_level
        if log_file is None:
            log_file = os.path.join(os.environ.get('HOME', os.environ.get('USERPROFILE', os.getcwd())), 'log', 'get_openstack_info_open_port.log')
        self.config['log_file'] = log_file
        self.ip = ip
        self.protocol = protocol
        self._init_log()
        self._os_auth()
        self.all_floating_ips = self.neutron.list_floatingips(floating_ip_address=self.ip)['floatingips']
        self._log.debug(f"Found {len(self.all_floating_ips)} floating IP.")
        if protocol == 'ICMP':
            lport = list()
            lport.append(port[0])
            port = lport
        for self.port in port:
            self.rule_found = False
            if self._check_known_ip():
                self._log.debug(f"IP found in your environment. Testing that '{self.ip} {self.protocol}/{self.port}' is actually open...")
                if self._test_open_port(self.ip, self.port, self.protocol):
                    self._log.info(f"Port '{self.port}' reachable.")
                    self._check_openstack()
                    if method == 'IP':
                        self._check_with_ip_method()
                    else:
                        self._check_with_instance_method()
                    if not self.rule_found:
                        self._log.info(f"No rule found that allow all incoming traffic to IP ̣{self.ip} and port {self.port}")
                        continue
                else:
                    self._log.info(f"Port '{port}' NOT reachable.")
            else:
                self._log.info(f"The IP '{self.ip}' couldn't be found in your OpenStack environment.")
                sys.exit(6)

    def _check_with_ip_method(self):
        self._log.debug(f"Looking for floating IP '{self.ip}' in OpenStack's region '{os.environ['OS_REGION_NAME']}' as user '{os.environ['OS_USERNAME']}' with port '{self.port}' open to incoming connections...")
        self.floatingip = self._get_openstack_floating_ip()
        self.project_id = self.floatingip['project_id']
        port_id = self.floatingip['port_id']
        port = self.neutron.show_port(port_id)
        self.server_id = port['port']['device_id']
        server = self.nova.servers.get(self.server_id).to_dict()
        self.server_name = server['name']
        # The best solution to get the project name would be to call the Placement API with the ID, but there is no Python module ready yet
        self.project_name = ', '.join(server['addresses'].keys())
        port = self.neutron.show_port(self.floatingip['port_id'])['port']
        for security_group in port['security_groups']:
            self._check_security_group(security_group)      

    def _check_with_instance_method(self):
        self._log.debug(f"Looking for instance with IP '{self.ip}' in OpenStack's region '{os.environ['OS_REGION_NAME']}' as user '{os.environ['OS_USERNAME']}' with port '{self.port}' open to incoming connections...")
        self._get_openstack_instances()
        self._locate_affected_instance()
        self._check_affected_security_groups()

    def _check_known_ip(self):
        for ip in self.all_floating_ips:
            if self.ip == ip['floating_ip_address']:
                return True
        return False
        
    def _check_security_group(self, security_group_id):
        self._check_security_group_rules(self.neutron.show_security_group(security_group_id)['security_group']['security_group_rules'])

    def _check_security_group_rules(self, security_group_rules):
        for rule in security_group_rules:
            remote_ip_prefix = rule.get('remote_ip_prefix', '')
            if rule.get('direction', '') != 'ingress' or not remote_ip_prefix:
                continue
            netmask = remote_ip_prefix.split("/")[1]
            if netmask != '0':
                continue
            port_range_max = rule.get('port_range_max', 0) 
            port_range_min = rule.get('port_range_min', 0)  
            if port_range_max is not None and port_range_min is not None and port_range_max <= self.port  and port_range_min >= self.port:
                self._log.warn(f"Attention! Rule '{rule['id']}' in security group '{rule['security_group_id']}', project '{self.project_name}' ({self.project_id})' allows incoming traffic from everywhere to instance '{self.server_name}' ({self.server_id}).")
                self.rule_found = True
        
    def _test_open_port(self, ip, port, protocol):
        if protocol == 'TCP':
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            result = sock.connect_ex((ip, port))
            sock.close()
            if result == 0:
                return True
            else:
                return False
        elif protocol == 'UDP':
            self._log.debug("UDP is not connection oriented so it's not possible to scan and trust the result, so we assume is open.")
            return True
        elif protocol == 'ICMP':
            try:
                sock = ping(ip, privileged=False)
            except:
                self._log.error('Check that you can send ICMP packets. You can set extend the group range able to do it with: echo \'net.ipv4.ping_group_range = 0 2147483647\' | sudo tee -a /etc/sysctl.conf; sudo sysctl -p')
                sys.exit(7)
            if sock.is_alive:
                return True
            else:
                return False         
            
    def _get_openstack_floating_ip(self):
        for ip in self.all_floating_ips:
            if ip['floating_ip_address'] == self.ip:
                return ip
       
    def _get_credentials(self):
        """
        Load login information from environment
        :returns: credentials
        :rtype: dict
        """
        cred = dict()
        cred['auth_url'] = os.environ.get('OS_AUTH_URL', '').replace("v2.0", "v3")
        cred['username'] = os.environ.get('OS_USERNAME', '')
        cred['password'] = os.environ.get('OS_PASSWORD', '')
        cred['project_id'] = os.environ.get('OS_PROJECT_ID', os.environ.get('OS_TENANT_ID', ''))
        cred['user_domain_name'] = os.environ.get('OS_USER_DOMAIN_NAME', 'default')
        for key in cred:
            if cred[key] == '':
                self._log.critical(
                    f"Credentials not loaded to environment ({key} = '{cred[key]}'): did you load the rc file?")
                exit(1)
        return cred

    def _os_auth(self):
        self.keystone_session = session.Session(
            auth=v3.Password(**self._get_credentials()))
        self.keystone_v3 = keystoneclient_v3.Client(
            session=self.keystone_session)
        self.nova = nova.Client("2.1", session=self.keystone_session)
        self.neutron = neutron.Client("2.0", session=self.keystone_session)
        
    def _check_openstack(self):
        ''' Check that OpenStack variables are loaded '''
        if 'OS_USERNAME' not in os.environ:
            self._log.error("You don't seem to have loaded a source file with credentials to OpenStack. Please do so, and try again.")
            sys.exit(1)


    def _get_openstack_instances(self):
        self.server_list = self.nova.servers.list(detailed=True, search_opts={"all_tenants": True})
        self._log.debug(f"Obtained {len(self.server_list)} servers.")
        
        
    def _locate_affected_instance(self):
        self.affected_instance = None
        for server in self.server_list:
            server_info = server.to_dict()
            for project in server_info['addresses'].keys():
                for address in server_info['addresses'][project]:
                    if address['addr'] == self.ip:
                        self.project_name = project
                        self.project_id =server_info['tenant_id']
                        self.server_name = server_info['name']
                        self.server_id = server_info['id']
                        self._log.debug(f"Located instance '{self.server_name}' with ID '{self.server_id}' and IP '{address['addr']}' in project '{self.project_name}'")
                        self.affected_instance = server_info
        if self.affected_instance is None:
            self._log.error(f"Not found any instance with IP '{self.ip}'")
            sys.ext(2)
            
    def _check_affected_security_groups(self):
        all_security_groups_in_project = self.neutron.list_security_groups(tenant_id=self.affected_instance['tenant_id'])['security_groups']
        for security_group in self.affected_instance['security_groups']:
            self._log.debug(f"Looking for security group '{security_group['name']}'...")
            for existing_sec_group in all_security_groups_in_project:
                if existing_sec_group['name'] == security_group['name']:
                    self._check_security_group_rules(existing_sec_group['security_group_rules'])
               
        
    def _init_log(self):
        ''' Initialize log object '''
        self._log = logging.getLogger("get_openstack_info_open_port")
        self._log.setLevel(logging.DEBUG)

        sysloghandler = SysLogHandler()
        sysloghandler.setLevel(logging.DEBUG)
        self._log.addHandler(sysloghandler)

        streamhandler = logging.StreamHandler(sys.stdout)
        streamhandler.setLevel(logging.getLevelName(self.config.get("debug_level", 'INFO')))
        formatter = '%(asctime)s | %(levelname)8s | %(message)s'
        formatter = '[%(levelname)s] %(message)s'
        streamhandler.setFormatter(CustomFormatter(formatter))
        self._log.addHandler(streamhandler)

        if 'log_file' in self.config:
            log_file = self.config['log_file']
        else:
            home_folder = os.environ.get('HOME', os.environ.get('USERPROFILE', ''))
            log_folder = os.path.join(home_folder, "log")
            log_file = os.path.join(log_folder, "get_openstack_info_open_port.log")

        if not os.path.exists(os.path.dirname(log_file)):
            os.mkdir(os.path.dirname(log_file))

        filehandler = logging.handlers.RotatingFileHandler(log_file, maxBytes=102400000)
        # create formatter
        formatter = logging.Formatter('%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
        filehandler.setFormatter(formatter)
        filehandler.setLevel(logging.DEBUG)
        self._log.addHandler(filehandler)
        return True

def validate_global_ip(ctx, param, value):
    ''' Check if a parameter is a valid global IP address '''
    try:
        anip = ipaddress.ip_address(value)
        if not anip.is_global:
            raise click.BadParameter(f"{value} is not a valid Global (public) IP address.")
        else:
            return value
    except:
        raise click.BadParameter(f"{value} is not a valid Global (public) IP address.")
        
    
@click.command()
@click.option("--debug-level", "-d", default="INFO",
    type=click.Choice(
        ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"],
        case_sensitive=False,
    ), help='Set the debug level for the standard output.')
@click.option('--log-file', '-l', help="File to store all debug messages.")
@click.option('--ip', '-i', required=True, callback=validate_global_ip, help="IP address with the open port.")
@click.option('--port', '-p', required=True, multiple=True, type=click.IntRange(1, 65535), help="Open port, between 0 and 65535.")
@click.option('--method', '-m',
              type=click.Choice(
                ["IP", "INSTANCE"],
                case_sensitive=False,
              ),default='IP', help='Method used to find the security group rule.')
@click.option('--protocol', '-t',
              type=click.Choice(
                ["TCP", "UDP", "ICMP"],
                case_sensitive=False,
              ),default='TCP', help='Protocol of the port.')
#@click.option("--dummy","-n" is_flag=True, help="Don't do anything, just show what would be done.") # Don't forget to add dummy to parameters of main function
@click_config_file.configuration_option()
def __main__(debug_level, log_file, ip, port, method, protocol):
    object = get_openstack_info_open_port(debug_level, log_file, ip, port, method, protocol)
    

if __name__ == "__main__":
    __main__()

