# get_openstack_info_open_port

This script will check that a given port in a given global (public) IP is open, and then search for an instance in Openstack with that IP, and find which rule and security group allow this incoming traffic.

## Requirements

You will need Python3.
Check the requirements.txt file for Python modules required. They will be installed following the installation instructions.

## Installation

### Linux

  `./install.sh`

### Windows (from PowerShell)

This has NOT been tested.

  ```
  $python_exe = $(where.exe python).split()[0]
  & $python_exe -m pip upgrade
  & $python_exe setup.py install
  ```

## Usage

  `get_openstack_info_open_port.py --ip|-i IP_ADDRESS --port|-p PORT [--protocol|-t TCP|UDP|ICMP] [--method|-m IP|INSTANCE] [--log-file|-l LOG_FILE] [--debug-level|-d CRITICAL|ERROR|WARNING|INFO|DEBUG|NOTSET] [--config CONFIG_FILE]`
