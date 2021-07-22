import setuptools
import os

requirements_filename = 'requirements.txt'
if os.access(requirements_filename, os.R_OK):
    with open(requirements_filename, 'r') as requirements_file:
        requirements_content = requirements_file.read()
    requirements = requirements_content.split()
else:
    requirements = list()

setuptools.setup(
    scripts=['get_openstack_info_open_port/get_openstack_info_open_port.py'],
    author="Antonio J. Delgado",
    version='0.0.1',
    name='get_openstack_info_open_port',
    author_email="antonio.delgado@csc.fi",
    url="",
    description="This script will check that a given port in a given global (public) IP is open, and then search for an instance in Openstack with that IP, and find which rule and security group allow this incoming traffic.",
    license="GPLv3",
    keywords=["openstack", "security groups", "security", "neutron", "nova", "rule", "firewall"],
    install_requires=requirements
)
