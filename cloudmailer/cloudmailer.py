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

import functools
import os
import configparser
import sys
import syslog
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

import itertools
from threading import Thread
import time

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
        print("Get All Servers")
        self.all_servers = self.nova.servers.list(search_opts={"all_tenants": 1})
        print("All Servers received")
        self.all_server_groups = self.nova.server_groups.list(all_projects=True)
        print("Get All Users")
        self.all_users = self.keystone_v3.users.list()
        print("Get All Projects")
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
                exit(1)
        return cred

    def getUserEmail(self, user):
        for i in list(self.all_users):
           if i.id == user:
               return i.email
        return None

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

    def getRoleAssignment(self, project):
        users = list(set( map ( lambda z:
            z.user['id'], filter ( lambda y:
            y.scope['project']['id'] == project, filter(lambda x :
            'project' in  x.scope , self.all_assignments )))))
        return users

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
        exit(1)

    def getProjectInfo(self, tenant_id):
        # Get project info. Name, memberemails
        project = {}
        emails = []
        users = []

        if tenant_id is None:
            print ("Undefined input project ID while retrieving project data! Possibly trying to retrieve project data from the wrong domain.")
            return {"name": None, "emails": emails, "servers": []}
        users = self.getRoleAssignment(tenant_id)
        for user in users:
            email = self.getUserEmail(user)
            if email:
                emails.append(email)

        if len(emails) == 0:
            print(tenant_id + " does not have any emails")

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

    if (not TEMPDIR or not HOST_SCHEDULE or not AFFECTED_VMS or not MAIL_SERVER or not MAIL_FROM or not MAIL_BCC):
        print( 'You need to set all variables, TEMPDIR=%s, HOST_GROUP_DEBUG=%s,'
               ' AFFECTED_VMS=%s, HOST_SCHEDULE=%s, MAIL_SERVER=%s, MAIL_FROM=%s '
               % (str(TEMPDIR), str(HOST_GROUP_DEBUG), str(AFFECTED_VMS), str(HOST_SCHEDULE), str(MAIL_SERVER), str(MAIL_FROM) ))
        print("Reading configuration failed")
        exit(2)

def listFile(textfile):
    # Reads a file. Each line is returned as an entry.
    lfile = open(textfile, "r")
    items = list(map(str.strip, lfile.readlines()))
    lfile.close()
    return items

def getServergroupsAndVms(data,nodelist):

    nodes = {}
    servergroups = {}
    for node in nodelist:
        nodes[node] = []

    allservers = data.all_servers
    server_groups = data.all_server_groups


    # Generates a servergroup/hypervisor <-> servergroup/instance mapping.
    for group in server_groups:
        grouphosts = []
        for member in group.members:
            for server in allservers:
                if server.id == member:
                    host = getattr(server, "OS-EXT-SRV-ATTR:host")
                    break
            if host in nodes:
                #Just handle hypervisors we're maintaining
                grouphosts.append(host)
                nodes[host].append(group.id)
        if len(grouphosts) > 0:
            servergroups[group.id] = grouphosts
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
    return list(map(list, itertools.zip_longest(*node_batch_list, fillvalue="skip")))

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
    for para in itertools.zip_longest(*hostgroups, fillvalue="skip"):
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

def sendMails(send_emails, subject, template, projects):

    print(str(len(projects)) + " projects to send email to.")
    ask_for_verification = True
    smtpconn = smtplib.SMTP(MAIL_SERVER, 25)

    for project in projects.keys():
        if len("".join(projects[project]["emails"])) == 0:
            print("Project %s has no email recipients. PLEASE NOTE that this project will not receive any email!" % projects[project]["name"])
            continue

        projmail = ""
        for line in template:
            try:
                if line.find("PROJECT-NAME") != -1:
                    projmail = projmail + projects[project]["name"] + "\n"
                    projmail = projmail + "-"*len(projects[project]["name"]) + "\n"
                elif line.find("LIST-OF-MACHINES") != -1:
                    for machine in projects[project]["servers"]:
                        projmail = projmail + machine
                else:
                    projmail = projmail + line
            except Exception as e:
                print ("There was an issue with this project:")
                pprint.pprint(projects[project])
                print( e )
                if send_emails:
                    send_emails = False
                    print( "Emails won't be sent because of exception")
                continue
        #projmail_utf8 = projmail.encode("utf-8","ignore")
        emails_to = ",".join(projects[project]["emails"])
        emailcopy = open("%s/%s" %(TEMPDIR, projects[project]["name"]), "w")
        emailcopy.write("From: %s\n" % MAIL_FROM)
        emailcopy.write("To: %s\n" % emails_to)
        emailcopy.write("Subject: %s\n" % subject)
        emailcopy.write(projmail)
        emailcopy.close()


        if send_emails and len(projects[project]["emails"]) > 0:
            if ask_for_verification:
                askToContinue('Are you sure that you want to send the emails?', 'Yes I am sure')
                ask_for_verification = False
            print ("Really sending emails to: %s" % ",".join(projects[project]["emails"]))
            for email_address in projects[project]["emails"]:
                msg = MIMEText(projmail)
                msg["Subject"] = subject
                msg["To"] = email_address
#                print(msg)
                smtpconn.sendmail(MAIL_FROM, email_address, msg.as_string())
            if MAIL_BCC:
                msg = MIMEText("Mail sent to: " + emails_to + "\n------\n\n" + projmail)
                msg["Subject"] = subject + " - " + projects[project]["name"]
                msg['To'] = MAIL_BCC
#                print(msg)
                smtpconn.sendmail(MAIL_FROM, MAIL_BCC, msg.as_string())

    smtpconn.quit()

def askToContinue(question, required_answer='Yes'):
    answer = raw_input(question + ' Required answer: "' + required_answer + '"')
    if not required_answer == answer:
       print ("You did not write \"" + required_answer + "\". Exiting.")
       exit(0)

def main(argv=None):

    if argv is None:
        argv = sys.argv

    readConfiguration()
    parser = argparse.ArgumentParser(description='Send mails about hypervisor/VM troubles.',
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="""Examples:
NB! please run this with 'python cloudmailer.py ...' so that it uses your current virtualenv

Notify customers using a list of VMs:
python cloudmailer.py  -m 'cPouta: VMs have been migrated' -t mail_template.txt -n  -v vmuuidlist

Notify customers using a list of hypervisors:
python cloudmailer.py  -m 'cPouta: Failed disk on hypervisor, VMs lost' -t mail_template.txt -n -y hypervisorlist

Notify customers of particular computing projects
python cloudmailer.py  -m 'ePouta: VM connection downtime due to maintenance' -t mail_template.txt -n -p projectlist

Schedule downtime for VMs on specfic hypervisors (120 min interval):
python cloudmailer.py  -m "cPouta: Virtual machine downtime schedule." -t mail_template.txt -s -y hypevisotlist -d "2018-07-28 08:00" -i 120
""")
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
        return 22

    if not os.path.exists(TEMPDIR):
        os.makedirs(TEMPDIR)

    try:
        templf = open(args.template, "r")
        template = templf.readlines()
        templf.close()
    except IOError:
        print ("Error opening template file %s. It needs to be a path to a file" % args.template)
        return 1

    if args.hypervisors:
        try:
            hosts = listFile(args.hypervisors)
        except IOError:
            print ("Error opening hostfile %s" % args.hypervisors )
            return 1
        data = OpenStackDataStorage()
        data.mapAffectedServersToRoleAssignments(hosts)
        # Generate a list of batches of nodes to be rebooted
        node_batch_list = getBatchList(data, hosts, args.max_upgrades_at_once)
        # Transpose + pad
        hostgroups = nodeBatchListToHostGroups(node_batch_list)
        # Write intermediary scheduling result to a file
        hostGroupsToFile(hostgroups)
        vms = listVMsInHosts(data,hostgroups)

    if args.vms:
        if args.schedule:
            print ("Cloudmailer does not currently support scheduling downtime on a VM basis. Only hypervisor based downtimes can be scheduled.")
            return 1
        try:
            vmids = listFile(args.vms)
        except IOError:
            print ("Error opening vmfile %s" % args.vms)
            return 1
        data = OpenStackDataStorage()
        data.mapAffectedServersToRoleAssignments(instances=vmids)
        hostgroups = [["fakehostname"]]
        vms = { "fakehostname" : data.getVMsByID(vmids) }

    if args.projects:
        if args.schedule:
            print ("Cloudmailer does not currently support scheduling downtime on a project basis. Only hypervisor based downtimes can be scheduled.")
            return 1
        try:
            projectnames = listFile(args.projects)
            data = OpenStackDataStorage()
            data.mapAffectedProjectsToRoleAssignments(projectnames)
            mails = notifyProjectMembers(data, projectnames)
            # Send emails if the yes-please-really-send-the-emails argument was given
            sendMails(args.sendemail, args.mailsubject, template, mails)
            exit(0)
        except IOError:
            print ("Error opening projectfile %s" % args.projects)
            return 1

    if args.schedule:
        try:
            starttime = datetime.strptime(args.date, '%Y-%m-%d %H:%M')
        except ValueError:
            print ('Starttime %s does not match the format %Y-%m-%d %H:%M')
            return 1
    writeAffectedVMs(vms)
    # Generate emails
    if args.schedule:
        mails = scheduleReboot(data, hostgroups, vms, starttime, args.interval)
    if args.notify:
        mails = notifyVMOwnerProjectMembers(data, hostgroups, vms)
    # Send emails if the yes-please-really-send-the-emails argument was given
    sendMails(args.sendemail, args.mailsubject, template, mails)

if __name__ == "__main__":
    main()
