import setuptools
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
    install_requires=[
       'click >= 6.7',
       'click_config_file==0.6.0',
       'python-openstackclient==5.5.0',
       'python-neutronclient==7.5.0',
       'icmplib==2.1.1'
    ]
)
