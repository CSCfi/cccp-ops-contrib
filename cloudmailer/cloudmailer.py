#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Mail users about cloud stuff
#
# === Authors
#
# Kalle Happonen <kalle.happonen@csc.fi>
# Johan Guldmyr <johan.guldmyr@csc.fi>
# Oscar Kraemer <oscar.kraemer@csc.fi>
# Jukka Nousiainen <jukka.nousiainen@csc.fi>
# Antonio J. Delgado <antonio.delgado@csc.fi>

import functools
import os
import configparser
import sys
import argparse
import pprint


from keystoneauth1 import session
from keystoneauth1.identity import v3
from keystoneclient.v3 import client as keystoneclient_v3
from novaclient import client

from datetime import timedelta
from datetime import datetime

import smtplib
from email.mime.text import MIMEText
from threading import Thread
import time

# Hack how to get zip_longest to work in both python 3 and python 2
try:
    from itertools import zip_longest
except ImportError:
    from itertools import izip_longest as zip_longest

tool_description = 'Send mails about hypervisor/VM troubles.'
epilog = """Examples:
NB! please run this with 'python cloudmailer.py ...' so that it uses your current virtualenv

Notify customers using a list of VMs:
python cloudmailer.py  -m 'cPouta: VMs have been migrated' -t templates/mail_template.txt -n  -v vmuuidlist

Notify customers using a list of hypervisors:
python cloudmailer.py  -m 'cPouta: Failed disk on hypervisor, VMs lost' -t templates/mail_template.txt -n -y hypervisorlist

Notify customers of particular computing projects
python cloudmailer.py  -m 'ePouta: VM connection downtime due to maintenance' -t templates/mail_template.txt -n -p projectlist

Schedule downtime for VMs on specfic hypervisors (120 min interval):
python cloudmailer.py  -m "cPouta: Virtual machine downtime schedule." -t templates/mail_template.txt -s -y hypevisotlist -d "2018-07-28 08:00" -i 120
"""

TEMPDIR = "temporary_files"
HOST_SCHEDULE = "%s/host_schedule" % TEMPDIR
HOST_GROUP_DEBUG = "%s/host_group_debug" % TEMPDIR
AFFECTED_VMS = "%s/affected_vms" % TEMPDIR
MAIL_FROM = False
MAIL_SERVER = False
MAIL_BCC = False
CONFIG_FILES = [ 'cloudmailer.cfg' , 'cloudmailer-example.cfg' ]

# TODO make MAX_UPGRADE_AT_ONCE a flag
MAX_UPGRADE_AT_ONCE = 10

START_TIME=time.time()

def tt(text='text'):
   # Useful when benchmarking
   print (str(time.time() - START_TIME) + ' ' + str(text))

class OpenStackDataStorage():

    def __init__(self):
        keystone_session = session.Session(auth=v3.Password(**self.getCredentials()))
        self.keystone_v3 = keystoneclient_v3.Client(session=keystone_session)
        self.nova = client.Client("2.1", session=keystone_session)
        self.getBaselineData()

    def getBaselineData(self):
        # To-Do: Create a cache that expires after a few hours?
        print("Getting all Servers")
        self.all_servers = self.nova.servers.list(search_opts={"all_tenants": 1})
        print("All Servers received")
        self.all_server_groups = self.nova.server_groups.list(all_projects=True)
        print("Getting all Users")
        self.all_users = self.keystone_v3.users.list()
        print("Getting all Projects")
        self.project_dict = {}
        for project in self.keystone_v3.projects.list():
             self.project_dict[ project.id ] = project.name
        print("All Projects received")

    def mapAffectedServersToRoleAssignments(self, hypervisors=None, instances=[]):
        affected_servers = []
        if instances:
            affected_servers = self.getVMsByID( instances )
        else:
            # First map unlist a list of list. Second map get all affected instances.
            list(map( affected_servers.extend, map(self.getServers, hypervisors) ))
        self.all_assignments = self.getRoleAssignments(vms=affected_servers)

    def mapAffectedProjectsToRoleAssignments(self, projectnames):
        project_ids = []
        for projectname in projectnames:
            project_ids.append(self.getProjectID(projectname))
        self.all_assignments = self.getRoleAssignments(projects=project_ids)

    def getCredentials(self):
        """
        Load login information from environment

        :returns: credentials
        :rtype: dict
        """
        cred = dict()
        if not 'OS_AUTH_URL' in os.environ:
                print('Credentials not loaded to environment: did you load the rc file?')
                sys.exit(1)
        cred['auth_url'] = os.environ.get('OS_AUTH_URL').replace("v2.0", "v3")
        cred['username'] = os.environ.get('OS_USERNAME')
        cred['password'] = os.environ.get('OS_PASSWORD')
        if 'OS_PROJECT_ID' in os.environ:
            cred['project_id'] = os.environ.get('OS_PROJECT_ID')
        if 'OS_TENANT_ID' in os.environ:
            cred['project_id'] = os.environ.get('OS_TENANT_ID')
        cred['user_domain_name'] = os.environ.get('OS_USER_DOMAIN_NAME', 'default')
        for key in cred:
            if not cred[key]:
                print('Credentials not loaded to environment: did you load the rc file?')
                sys.exit(1)
        return cred

    def getProjectRoleAssignmentThread(self, keystone_v3, tenant_assignments , tenant_id, i ):
        try:
            tenant_assignments[i] = keystone_v3.role_assignments.list(project=tenant_id, effective=True)
        except Exception as e:
            # Some thread failed when this excpet wasn't here
            tenant_assignments[i] = keystone_v3.role_assignments.list(project=tenant_id, effective=True)

    def getRoleAssignments(self, vms=None, projects=None):
        # By threading this the script runtime decreased from 5:30 to 0:46 when scheduling half of cPouta
        # Before threading the runtime increased about 2 seconds per instance.
        project_set = set()

        if vms:
            for server in vms:
                project_set.add(server._info['tenant_id'])

        elif projects:
            for project_id in projects:
                project_set.add(project_id)

        thread_list = [None] * len(project_set)
        result_list = [None] * len(project_set)

        print('Start requesting Project Role Assignments')
        for i, tenant_id in zip(range(len(project_set)), list(project_set)):
            thread_list[i] = Thread( target=self.getProjectRoleAssignmentThread,
                                     args=(self.keystone_v3, result_list, tenant_id, i ) )
            thread_list[i].start()
        print('Threads created')
        for t in thread_list:
             t.join()
        print('Role Assignments received')
        all_assignments = []
        list(map(all_assignments.extend,filter(None,result_list)))
        return all_assignments

    def get_emails_for_project(self, project_id):
        """get all users email that are in a project"""
        user_ids = list(set( map ( lambda z:
            z.user['id'], filter ( lambda y:
            y.scope['project']['id'] == project_id, filter(lambda x :
            'project' in  x.scope , self.all_assignments )))))


        emails = [ user.email for user in self.all_users if user.id in user_ids ]
        if len(emails) == 0:
            print(f"{project_id} does not have any emails")
        return emails

    def getProjectName(self, tenant_id):
        # I don't know if this is actually necessary to have try-statement
        try:
            return self.project_dict[tenant_id]
        except Exception as e:
            print ( "Something went wrong for tenant: " + str(tenant_id) + " exception: " + str(e) )
            return tenant_id

    def getProjectID(self, tenant_name):
        for uuid, name in self.project_dict.items():
            if name == tenant_name:
                 return uuid
        # If we don't find a tenant with the tenant_name we want this script to fail.
        print ( "Failure: Something went wrong for tenant: " + str(tenant_name) + " project name does not exist" )
        sys.exit(1)

    def getProjectInfo(self, tenant_id):
        # Get project info. Name, memberemails
        if tenant_id is None:
            print ("Undefined input project ID while retrieving project data! " \
                   "Possibly trying to retrieve project data from the wrong domain.")
            return {"name": None, "emails": [], "servers": []}
        emails = self.get_emails_for_project(tenant_id)

        name = self.getProjectName(tenant_id)
        project = {"name": name, "emails": emails, "servers": []}
        return project

    def getServers(self, host):
        instance_list = []
        for server in list(self.all_servers):
            if host == getattr(server, "OS-EXT-SRV-ATTR:host"):
                instance_list.append(server)
        return instance_list

    def getVMsByID(self,uuids):
        servers = []
        for uuid in uuids:
            for server in self.all_servers:
                if uuid == server.id:
                    servers.append(server)
        return servers

def readConfiguration():
    global TEMPDIR, HOST_SCHEDULE, HOST_GROUP_DEBUG, AFFECTED_VMS,MAIL_FROM, MAIL_SERVER, MAIL_BCC, MAX_UPGRADE_AT_ONCE
    config = configparser.ConfigParser()
    for f in CONFIG_FILES:
        if os.path.isfile(f):
            config.read(f)
            if config.has_option('DEFAULT', 'TEMPDIR'):
                TEMPDIR = config.get('DEFAULT', 'TEMPDIR')
            if config.has_option('DEFAULT', 'HOST_SCHEDULE'):
                HOST_SCHEDULE = config.get('DEFAULT', 'HOST_SCHEDULE')
            if config.has_option('DEFAULT', 'HOST_GROUP_DEBUG'):
                HOST_GROUP_DEBUG = config.get('DEFAULT', 'HOST_GROUP_DEBUG')
            if config.has_option('DEFAULT', 'AFFECTED_VMS'):
                AFFECTED_VMS = config.get('DEFAULT', 'AFFECTED_VMS')
            if config.has_option('DEFAULT', 'MAIL_SERVER'):
                MAIL_SERVER = config.get('DEFAULT', 'MAIL_SERVER')
            if config.has_option('DEFAULT', 'MAIL_FROM'):
                MAIL_FROM = config.get('DEFAULT', 'MAIL_FROM')
            if config.has_option('DEFAULT', 'MAIL_BCC'):
                MAIL_BCC = config.get('DEFAULT', 'MAIL_BCC')
            if config.has_option('DEFAULT', 'MAX_UPGRADE_AT_ONCE'):
                MAX_UPGRADE_AT_ONCE = config.get('DEFAULT', 'MAX_UPGRADE_AT_ONCE')
            break

    all_present = True
    if not TEMPDIR:
        all_present = False
        print('Missing required parameter TEMPDIR.')
    if not HOST_SCHEDULE:
        all_present = False
        print('Missing required parameter HOST_SCHEDULE.')
    if not AFFECTED_VMS:
        all_present = False
        print('Missing required parameter AFFECTED_VMS.')
    if not MAIL_SERVER:
        all_present = False
        print('Missing required parameter MAIL_SERVER.')
    if not MAIL_FROM:
        all_present = False
        print('Missing required parameter MAIL_FROM.')
    if not all_present:
        print('Error, there are missing variables:')
        print('You need to set all required variables in your configuration file:')
        print(f"TEMPDIR={TEMPDIR}")
        print(f"HOST_GROUP_DEBUG={HOST_GROUP_DEBUG}")
        print(f"AFFECTED_VMS={AFFECTED_VMS}")
        print(f"HOST_SCHEDULE={HOST_SCHEDULE}")
        print(f"MAIL_SERVER={MAIL_SERVER}")
        print(f"MAIL_FROM={MAIL_FROM}")
        print('You can make a copy of cloudmailer-example.cfg to cloudmailer.cfg and edit this last file to customize the tool.')
        print(f"Reading configuration from {', '.join(CONFIG_FILES)} failed.\n")
        usage()
        sys.exit(2)

def listFile(textfile):
    # Reads a file. Each line is returned as an entry.
    try:
        lfile = open(textfile, "r")
        items = list(map(str.strip, lfile.readlines()))
        lfile.close()
        return items
    except IOError as error:
        print(f"Error reading file '{textfile}'. {error}")
        sys.exit(3)

# getServergroupsAndVms
#
# The function creates two maps, nodes and servergroups
# For each node, the map nodes describes the list of server groups which have at least one server hosted in the node.
# For each server group, the map servergroups describes the list of nodes which host at least one server of the server group.
#
# input
# data: information about the Openstack platform retrieved from API call
# nodelist: list of nodes we obtained in input
#
# output
# nodes: dict containing [node->list of server groups] mappings
# servergroups: dict containing [server group->list of nodes] mappings
def getServergroupsAndVms(data,nodelist):
    nodes = {}
    servergroups = {}
    for node in nodelist:
        nodes[node] = []

    # allservers is a collection containing all the servers in the Openstack platform
    allservers = data.all_servers
    # server_groups is a collection containing all the server groups in the Openstack platform
    server_groups = data.all_server_groups

    # Generates a servergroup/hypervisor <-> servergroup/instance mapping.
    for group in server_groups:
        # prepare an empty list, which will contain the nodes hosting the servers of the server group
        grouphosts = []
        for member in group.members:
            # initialize a flag that tells if we found the server in the list of servers given by the Openstack platform
            server_found = False
            host = None
            for server in allservers:
                if server.id == member:             
                    server_found = True
                    # obtain the node in which the server is hosted
                    host = getattr(server, "OS-EXT-SRV-ATTR:host")
                    # exit from the closest for-loop because our lookup is terminated
                    break
            # if the server in the server group is in the list of the servers given by the Openstack platform
            if(server_found == True):
                # if the node in which the virtual machine is hosted is one of the nodes in input
                if host in nodes:
                    #Just handle hypervisors we're maintaining
                    #
                    # add the node to the list of nodes hosting at least one server of the server group
                    grouphosts.append(host)
                    # add the server group to the list of server groups which have at least one server hosted in the node
                    nodes[host].append(group.id)
            #else:
                # this is one of the cases in which the server group has still members that have already been deleted
                # these cases started to appear after Queens upgrade
                #
                #print("The virtual machine " + str(member) + " of the group " + str(group) + "is not contained in data.all_servers")
                #
        # if there is at least one node hosting servers of the server group
        if len(grouphosts) > 0:
            # add the list of nodes to the map servergroups, using the server group as the key
            servergroups[group.id] = grouphosts
    # provide the two maps obtained in output
    return (nodes, servergroups)

def getProjectsAndVms(data, nodelist):
# This can be used instead of getServergroupsAndVms() if you want
# to schedule only one reboot per project instead of per server-
# group
  nodes = {}
  servers_in_project = {}

  for node in nodelist:
      nodes[node] = []
  all_servers = data.all_servers
  for server in list(all_servers):
      project = getattr(server, "tenant_id")
      if project not in servers_in_project:
          servers_in_project[project] = []
      servers_in_project[project].append(server.id)

      host = getattr(server, "OS-EXT-SRV-ATTR:host")
      if host in nodes:
          nodes[host].append(project)

  return (nodes, servers_in_project)

def getHypervisorWithMostGroups(nodes):
# We want to upgrade the nodes with the most limitings first.
# So that we don't get stuck with a lot of singe node batches
# in the end.
    max_value = -1
    node_name = 0
    for i in nodes:
        if len(nodes[i]) > max_value:
            node_name = i
            max_value = len(nodes[i])
    return node_name


def getNodeWithoutGroups(groups, local_nodes):
# For finding a node for the batch that does only have new groups.
    while True:
        temp_node = getHypervisorWithMostGroups(local_nodes)
        if temp_node == 0:
            return 0
        found_it = True
        for group in groups:
            if group in local_nodes[temp_node]:
                found_it = False
                break
        if found_it:
            return temp_node
        local_nodes.pop(temp_node, None)

def getBatchList(data, hosts, max_upgrades_at_once):
    (nodes, servergroups) = getServergroupsAndVms(data, hosts)
    remaining_nodes = dict(nodes)
    groups_in_batch = []
    node_batch = []
    node_batch_list = []
    while True:
        # Every iteration tries to find a new instance to add to the current batch.
        # If there is no match, the batch is added to node_batch_list
        node = getNodeWithoutGroups(list(groups_in_batch), dict(remaining_nodes))
        if len(node_batch) == 0 and node == 0:
        # Found all nodes
            break
        if node != 0:
        # When a node is found, it is added to the batch.
            node_batch.append(node)
            remaining_nodes.pop(node, None)
            groups_in_batch.extend(nodes[node])
        if node == 0 or len(node_batch) == max_upgrades_at_once:
            # When the batch is complete, the batch is added the node_batch_list.
            node_batch_list.append(node_batch)
            node_batch = []
            groups_in_batch = []

    return node_batch_list

# Convert to matrix based on longest entry, then transpose
def nodeBatchListToHostGroups(node_batch_list):
    return list(map(list, zip_longest(*node_batch_list, fillvalue="skip")))

# For outputting the node list generated by parser.py to a file for verification
# and/or debug purposes.
def hostGroupsToFile(hostgs):
    hgdebug = open(HOST_GROUP_DEBUG, "w")
    for row in hostgs:
        rowstr = ""
        for host in row:
            rowstr += host + " "
        hgdebug.write(rowstr + "\n")
    hgdebug.close()

def listVMsInHosts(data, hostgroups):
    # The virtual machines on the hosts are looked up.
    # The function returns a list of dicts with VMs.
    hostdict = {}
    hostl = functools.reduce(lambda x,y: x+y, hostgroups)
    for host in hostl:
        if host != "":
            hostdict[host] = data.getServers(host)
    return hostdict


def writeAffectedVMs(vms):

    vmfile = open(AFFECTED_VMS, "w")

    for hyper in vms:
        hostvms = [virtmach.id for virtmach in vms[hyper]]
        for vm in hostvms:
            vmfile.write(vm + "\n")

    vmfile.close()


def notifyVMOwnerProjectMembers(data, hostgroups, hostdict):
    # Writes a notify message for the hosts
    # This will return a dictionary of projects that contains what server name and server id

    hostl = functools.reduce(lambda x,y: x+y, hostgroups)
    projects = {}

    for host in hostl:
        if host != "skip":
            for server in hostdict[host]:
		# TOOD should geberate a dictionary of projects with data that should be parsed into the emails
                projlist = projects.get(server.tenant_id,
                                        data.getProjectInfo(server.tenant_id))
                projlist["servers"].append("%s %s\n" % (server.name, server.id))
                projlist["servers"].sort()
                projects[server.tenant_id] = projlist
    return projects

def notifyProjectMembers(data, projectnames):
    # This will return a dictionary of projects, including user email addresses.

    projects = {}

    for projectname in projectnames:
        project_id = data.getProjectID(projectname)
        projlist = projects.get(project_id, data.getProjectInfo(project_id))
        projects[projectname] = projlist
    return projects

# TODO: This needs some more love. At the moment it seems to schedule the reboot based of list of batches
# where every element containing a list of nodes that can be rebooted at the same time.
# Suggestions for improvement: Instead of query keystone for project name after every server it could do it
# once for every project. Also this could be split in to two functions one for deciding the time and one
# for gathering the projects.
def scheduleReboot(data,hostgroups, hostdict, starttime, interval):
    # Schedules a reboot for hosts.
    # hostgroups is a list of lists of hosts
    # hosdict is a dict with VM information.
    #    Different lists can be done in parallel.
    # starttime is a datetime object with the first scheduled time
    # interval is the time between scheduled downtime in minutes
    interval = timedelta(minutes=interval)
    hour = timedelta(hours=1)
    day = timedelta(days=1)
    # This is the reference start time of the rolling upgrades.
    reboot = starttime
    # Used for arithmetic when fast forwarding to the next day.
    starthour = reboot.hour
    # All nodes found empty are scheduled to this exact timeslot.

    projects = {}
    hostboot = []
    # Ensure all host group lists are of even length, then schedule reboots.
    for para in zip_longest(*hostgroups, fillvalue="skip"):
        for host in para:
            if host == "skip":
                continue
            if len(hostdict[host]) == 0:
                # Host is empty, reboot at any time
                hostboot.append("%s: %s\n" %("ANY TIME - EMPTY", host))
            else:
                hostboot.append("%s: %s\n" %(reboot.strftime("%Y-%m-%d %H:%M"), host))
                for server in hostdict[host]:
                    projlist = projects.get(server.tenant_id,
                                            data.getProjectInfo( server.tenant_id))
                    projlist["servers"].append("%s %s %s\n" %
                                               (reboot.strftime("%Y-%m-%d %H:%M"),
                                                server.name,
                                                server.id))
                    projlist["servers"].sort()
                    projects[server.tenant_id] = projlist

        reboot = reboot + interval
        # Don't skip lunch
        if reboot.hour == 11:
            reboot += hour
        # Don't work late
        if reboot.hour == 18:
            reboot += day
            reboot -= (reboot.hour - starthour) * hour
            # Don't work weekends
            if reboot.isoweekday() > 5:
                reboot += 2 * day

    hostboot.sort()
    reboot_schedule_file = open(HOST_SCHEDULE, "w")
    for host in hostboot:
        reboot_schedule_file.write(host)
    reboot_schedule_file.close()
    return projects

def send_mails_to_list_of_emails(smtpclient, subject, email_address_list, mail_str):
    """Function that actually send the emails to its recipients"""
    for email_address in email_address_list:
        msg = MIMEText(mail_str)
        msg["Subject"] = subject
        msg["To"] = email_address
        print (f"Sending email to: {email_address}")
        smtpclient.sendmail(MAIL_FROM, email_address, msg.as_string())
        print (f"Email to {email_address} sent successfully")

def generate_mail_text(project_dict, template):
    """genereate project email text from template"""
    projmail = ""
    for line in template:
        try:
            if line.find("PROJECT-NAME") != -1:
                projmail = projmail + project_dict["name"] + "\n"
                projmail = projmail + "-"*len(project_dict["name"]) + "\n"
            elif line.find("LIST-OF-MACHINES") != -1:
                for machine in project_dict["servers"]:
                    projmail = projmail + machine
            else:
                projmail = projmail + line
        except Exception as e:
            print ("There was an issue with this project:")
            pprint.pprint(project_dict)
            print( e )
            return False
    return projmail

def write_copy_of_email_to_file(project_name, emails_str, subject, projmail_str):
    """Write the project emails to file for review"""
    file_name = f"{TEMPDIR}/{project_name}"
    print(f"Creating file '{file_name}' ...")
    with open(file_name, "w", encoding='UTF-8') as emailcopy:
        emailcopy.write(f"From: {MAIL_FROM}\n")
        emailcopy.write(f"To: {emails_str}\n")
        if MAIL_BCC:
            emailcopy.write(f"Bcc: {MAIL_BCC}\n")
        emailcopy.write(f"Subject: {subject}\n")
        emailcopy.write(projmail_str)

def generate_and_send_emails(send_emails, subject, template, projects):
    """This function does the following:
    1. Genereate mail for each project
    2. Write the generated mails to file
    3. Send emails to customers if send_emails == True
    4. Send emails to BCC id send_eamils === True and MAIL_BCC
        projects{ tenant_id:{ 'name': str(), 'emails': ["example@example.org"] }}
    """

    print(str(len(projects)) + " projects to send email to.")
    ask_for_verification = True
    print(f"Establishing a connection with mail server '{MAIL_SERVER}:25'...")
    smtpconn = smtplib.SMTP(MAIL_SERVER, 25)
    notified_admin = False

    for project in projects:
        print(project)
        project_name = projects[project]["name"]
        project_email_address_list = projects[project]['emails']
        print(f"Processing project '{project_name}'...")
        if len(project_email_address_list) == 0:
            print(f"Project {project_name} has no email recipients. PLEASE NOTE that this " \
                  "project will not receive any email!")
            continue
        projmail_str = generate_mail_text(projects[project], template)
        if not projmail_str:
            send_emails = False
            print( "Emails won't be sent for {project_name} because of exception")
            continue
        project_email_address_list = projects[project]["emails"]
        emails_str = ', '.join(project_email_address_list)
        write_copy_of_email_to_file(project_name, emails_str, subject, projmail_str)

        if send_emails:
            if ask_for_verification:
                ask_to_continue('Are you sure that you want to send the emails?', 'Yes I am sure')
                ask_for_verification = False

            print(f"Really sending emails to: {emails_str}")
            send_mails_to_list_of_emails(smtpconn, subject, project_email_address_list,
                                         projmail_str)

            if MAIL_BCC:
                print (f"Really sending BCC emails to: { MAIL_BCC}")
                bcc_subject =  subject + " - " + project_name

                bcc_message = f"Mail sent to: {emails_str} \n------\n\n {projmail_str}"
                send_mails_to_list_of_emails(smtpconn, bcc_subject, MAIL_BCC.split(","),
                                             bcc_message)

        elif notified_admin is False:
            print("Attention!!! Not sending emails right now. Please, check the created files " \
                  "and when you are sure execute this same command with " \
                  "\"--I-am-sure-that-I-want-to-send-emails\" parameter.")
            notified_admin = True
    smtpconn.quit()

def ask_to_continue(question, required_answer='Yes'):
    if sys.version_info[0] == 3: # Python 3
        answer = input(question + ' Required answer: "' + required_answer + '"')
    elif sys.version_info[0] == 2: # Python 2
        answer = raw_input(question + ' Required answer: "' + required_answer + '"')
    if not str(required_answer) == str(answer):
       print ("You did not write \"" + required_answer + "\". Exiting.")
       sys.exit(0)

def usage():
    print('Usage:')
    print(tool_description)
    print(epilog)
    print('-m|--mailsubject\n  Subject of the mail')
    print('-t|--template\n  Path to a mail template file. See templates/ subdirectory')
    print('-n|--notify\n  Notify the customers, but do not schedule anything')
    print('-s|--schedule\n  Schedule downtime slots for te VMs.')
    print('-y|--hypervisors\n  Text file that contains the newline separated list of affected hypervisors.')
    print('-v|--vms\n  Text file that contains the newline separated list of affected vms.')
    print('-p|--projects\n  Text file that contains the newline separated list of affected project names.')
    print('--I-am-sure-that-I-want-to-send-emails\n  Actually send out eMails')
    print('-d|--date\n  The date of the first scheduled downtime in the following format "YYYY-mm-DD HH:MM"')
    print('-i|--interval\n  Minutes between the scheduled downtimes (int, default=60)')
    print("--max\n  Maximum numbers of instances that will get upgraded at once (int, default=%s)" % MAX_UPGRADE_AT_ONCE)

def read_args(argv=None):
    """read the flags from the command"""
    if argv is None:
        argv = sys.argv

    readConfiguration()
    parser = argparse.ArgumentParser(description=tool_description,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=epilog)
    parser.add_argument('-m', '--mailsubject', dest="mailsubject",
                        required=True, help="Subject of the mail")
    parser.add_argument('-t', '--template', dest='template',
                        required=True, help='Path to a mail template file. See templates/ subdirectory')

    pgroup = parser.add_mutually_exclusive_group(required=True)
    pgroup.add_argument('-n', '--notify', dest='notify', action='store_true',
                        help='Notify the customers, but do not schedule anything')
    pgroup.add_argument('-s', '--schedule', dest='schedule', action='store_true',
                        help='Schedule downtime slots for te VMs.')

    pgroup2 = parser.add_mutually_exclusive_group(required=True)
    pgroup2.add_argument('-y', '--hypervisors', dest='hypervisors',
                         help='Text file that contains the newline separated list of affected hypervisors.')
    pgroup2.add_argument('-v', '--vms', dest='vms',
                         help='Text file that contains the newline separated list of affected vms.')
    pgroup2.add_argument('-p', '--projects', dest='projects',
                         help='Text file that contains the newline separated list of affected project names.')

    parser.add_argument('--I-am-sure-that-I-want-to-send-emails', dest='sendemail', action='store_true',
                        help='Actually send out eMails')
    parser.add_argument('-d', '--date', dest='date',
                        help='The date of the first scheduled downtime in the following format "2018-07-04 08:00"')
    parser.add_argument('-i', '--interval', dest='interval', type=int, default=60,
                        help='Minutes between the scheduled downtimes')
    parser.add_argument('--max', dest='max_upgrades_at_once', type=int, default=MAX_UPGRADE_AT_ONCE,
                        help='Maximum numbers of instances that will get upgraded at once, defualt: ' + str(MAX_UPGRADE_AT_ONCE) )

    args = parser.parse_args(argv[1:])

    if args.schedule and not args.date:
        print ("When scheduling you need to also set a --date")
        sys.exit(22)
    # Make sure that the args date is set and have the correct format when scheduling
    if args.schedule:
        try:
            datetime.strptime(args.date, '%Y-%m-%d %H:%M')
        except ValueError:
            print ('Starttime %s does not match the format %Y-%m-%d %H:%M')
            sys.exit(1)

    if args.schedule and args.vms:
        print ("Cloudmailer does not currently support scheduling downtime on a VM basis. " \
               "Only hypervisor based downtimes can be scheduled.")
        sys.exit(1)

    if args.projects and  args.schedule:
        print ("Cloudmailer does not currently support scheduling downtime on a project basis. " \
               "Only hypervisor based downtimes can be scheduled.")
        sys.exit(1)

    return args

def get_template(template_path):
    """Read template from file"""
    try:
        # One should probably define encoding here
        with open(template_path) as template_file:
             template = template_file.readlines()
    except FileNotFoundError:
        print(f"Template path does not exist: {template_path}")
        sys.exit(1)
    return template




def main(argv=None):
    """main function"""

    args = read_args(argv)

    if not os.path.exists(TEMPDIR):
        os.makedirs(TEMPDIR)

    template = get_template(args.template)

    if args.hypervisors:
        try:
            hosts = listFile(args.hypervisors)
        except IOError:
            print (f"Error opening hostfile {args.hypervisors}" )
            sys.exit(1)
        data = OpenStackDataStorage()
        data.mapAffectedServersToRoleAssignments(hosts)
        # Generate a list of batches of nodes to be rebooted
        node_batch_list = getBatchList(data, hosts, args.max_upgrades_at_once)
        # Transpose + pad
        hostgroups = nodeBatchListToHostGroups(node_batch_list)
        # Write intermediary scheduling result to a file
        hostGroupsToFile(hostgroups)
        vms = listVMsInHosts(data,hostgroups)
        writeAffectedVMs(vms)

    elif args.vms:
        try:
            vmids = listFile(args.vms)
        except IOError:
            print (f"Error opening vmfile {args.vms}")
            sys.exit(1)
        data = OpenStackDataStorage()
        data.mapAffectedServersToRoleAssignments(instances=vmids)
        hostgroups = [["fakehostname"]]
        vms = { "fakehostname" : data.getVMsByID(vmids) }
        writeAffectedVMs(vms)

    elif args.projects:
        projectnames = listFile(args.projects)
        data = OpenStackDataStorage()
        data.mapAffectedProjectsToRoleAssignments(projectnames)
        mails = notifyProjectMembers(data, projectnames)
        # Send emails if the yes-please-really-send-the-emails argument was given
        generate_and_send_emails(args.sendemail, args.mailsubject, template, mails)
        sys.exit(0)


    if args.schedule:
        starttime = datetime.strptime(args.date, '%Y-%m-%d %H:%M')
        mails = scheduleReboot(data, hostgroups, vms, starttime, args.interval)
    elif args.notify:
        mails = notifyVMOwnerProjectMembers(data, hostgroups, vms)

    generate_and_send_emails(args.sendemail, args.mailsubject, template, mails)

if __name__ == "__main__":
    main()
