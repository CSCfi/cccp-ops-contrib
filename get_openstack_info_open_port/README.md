# get_openstack_info_open_port

This script will check that a given port in a given global (public) IP is open, and then search for an instance in Openstack with that IP, and find which rule and security group allow this incoming traffic.

## Requirements

Check the requirements.txt file for Python modules required. They will be installed following the installation instructions.

## Installation

### Linux

  `sudo python3 setup.py install`

### Windows (from PowerShell)

  `& $(where.exe python).split()[0] setup.py install`

## Usage

  `get_openstack_info_open_port.py [--debug-level|-d CRITICAL|ERROR|WARNING|INFO|DEBUG|NOTSET] --ip|-i IP_ADDRESS --port|-p PORT`
