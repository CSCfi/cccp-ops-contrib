#!/usr/bin/env python
#
# Drain a node from VMs
#
# === Authors
#
# Oscar Kraemer <oscar.kraemer@csc.fi>
# Jukka Nousiainen <jukka.nousiainen@csc.fi>

import os
import sys
import syslog
import argparse
from pprint import pprint

from keystoneauth1 import session
from keystoneauth1.identity import v3
from keystoneclient.v3 import client as keystoneclient_v3
from novaclient import client
from novaclient import exceptions as nova_exceptions
from datetime import timedelta
from datetime import datetime
import datetime
import time
#import smtplib
#from email.mime.text import MIMEText

#import itertools
#import operator

from threading import Thread
import logging
TEMPDIR = '%s/log' % os.environ.get('HOME', '')
if not os.path.exists(TEMPDIR):
    os.mkdir(TEMPDIR)
LOGFILE = "%s/migration_loggs.log" % TEMPDIR
TIMEOUT_SECONDS = 30
# Setup logging
log = logging.getLogger('log')
log.setLevel(logging.INFO)
fh = logging.FileHandler(LOGFILE, mode='a')
fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
log.addHandler(fh)
log.info('script started')
nova = None

def timeStr():
    return str(datetime.datetime.now().strftime("%H:%M:%S "))

def getGredentials():
    """
    Load login information from environment
    :returns: credentials
    :rtype: dict
    """
    cred = dict()
    cred['auth_url'] = os.environ.get('OS_AUTH_URL', '').replace("v2.0", "v3")
    cred['username'] = os.environ.get('OS_USERNAME')
    cred['password'] = os.environ.get('OS_PASSWORD')
    if 'OS_PROJECT_ID' in os.environ:
        cred['project_id'] = os.environ.get('OS_PROJECT_ID')
    if 'OS_TENANT_ID' in os.environ:
        cred['project_id'] = os.environ.get('OS_TENANT_ID')
    cred['user_domain_name'] = os.environ.get('OS_USER_DOMAIN_NAME', 'default')
    for key in cred:
        if not cred[key]:
            print(timeStr() + 'Credentials not loaded to environment: did you load the rc file?')
            exit(1)
    return cred

def parseCommand(argv=None):
   if argv==None:
       argv = sys.argv

   parser = argparse.ArgumentParser(description='''Migrate away instances from nodes.

The script will take into account different types of instances and states.
The script will also logs what happens and rapport changes clearly.
There will be _no_ migrations in parallel.''',
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="""Examples:
To empty three nodes:
python """ + argv[0] + """ -y host1 -y host2 -y host3""")

   parser.add_argument('-y', '--hypervisor', dest='hypervisors', type=str,
                       help='Hypervisor name to empty', action='append', required=False)
   parser.add_argument('--flavors' , dest='flavors', type=str, action='append',
                       help='Flavors that you want to migrate')
   parser.add_argument('--max-instances', dest='max_instances_to_migrate',
                       type=int, help='Max number of instances to migrate', default=-1)
   parser.add_argument('--allow-block-migration', dest='allow_block_migration',
                       default=False, help='Allow migration of local disk backed instances a.k.a'
                       + 'block migration.', action='store_true')
   parser.add_argument('--allow-live-block-migration', dest='allow_live_block_migration',
                       default=False, help='Allow live block migration.\nWarning this can be quite'
                       + 'dangerous', action='store_true')
   parser.add_argument('--stop-paused-instances', dest='stop_paused_instances',
                       default=False, help='Stop paused instances.', action='store_true')
   parser.add_argument('--stop-suspended-instances', dest='stop_suspended_instances',
                       default=False, help='Stop suspended instances.', action='store_true')
   parser.add_argument('-i', '--instance', dest='instances', type=str,
                       help='Instance to migrate', action='append', required=False)

   flavors = []
   args = parser.parse_args(argv[1:])
   if args.flavors:
       flavors = args.flavors
       log_and_print( str(args.flavors))
   log_and_print( str(args.hypervisors))
   log_and_print( "Max instances to migrate: " + str(args.max_instances_to_migrate))
   log_and_print("Allow block migration: " + str(args.allow_block_migration))
   log_and_print("Allow live block migration: " + str(args.allow_live_block_migration))
   log_and_print("Stop paused instances: " + str(args.stop_paused_instances))
   log_and_print("Stop suspended instances: " + str(args.stop_suspended_instances))

   if args.hypervisors == None and args.instances == None:
       failure("You need to specify at least hypervisors or instances")

   return ( args.hypervisors,
            flavors,
            args.max_instances_to_migrate,
            args.allow_block_migration,
            args.allow_live_block_migration,
            args.stop_paused_instances,
            args.stop_suspended_instances,
            args.instances)

def failure(text='Script Failed!', rc=5):
    print( timeStr() + text)
    log.error(text)
    exit(rc)

def log_and_print(text, log_level='info'):
    print( timeStr() + ' ' + log_level + ' ' + text)
    if log_level == 'info':
        log.info( timeStr() + text )
    elif log_level == 'warning':
        log.warning( timeStr() + text )
    elif log_level == 'error' or log_level == 'failure':
        failure( text, 6 )
    else:
        failure(text, 7 )

def log_instance_state(instance):
    i_id = str(instance.id)
    i_status = str(instance.status)
    i_power_state = str(getattr(instance, 'OS-EXT-STS:power_state'))
    i_vm_state = str(getattr(instance, 'OS-EXT-STS:vm_state'))
    i_task_state =  str(getattr( instance, 'OS-EXT-STS:task_state'))
    i_host = str(getattr( instance, 'OS-EXT-SRV-ATTR:host'))
    i_virsh_name = str(getattr(instance, 'OS-EXT-SRV-ATTR:instance_name'))
    i_name = str(getattr(instance, 'name' ))

    log_message = "%s Status: %s, power_state: %s, vm_state: %s, task_state: %s, on host: %s, instance name: %s, virsh name: %s" % (
                  i_id, i_status, i_power_state, i_vm_state, i_task_state, i_host, i_virsh_name, i_name )
    log_and_print(log_message)

def getHypervisorUUID(hypervisors):
   all_hypervisors = nova.hypervisors.list()
#   pprint(all_hypervisors[0].__dict__)
   fail = False
   hypervisor_list = []
   for h in set(hypervisors):
       for a in all_hypervisors:
           if h == str(a.hypervisor_hostname):
               hypervisor_list.append(a)
               log.info( h + ' uuid is: ' + a.hypervisor_hostname)
               break

       # TODO: This is a bad check since it will fail if the len() == 0
       if not h == hypervisor_list[-1].hypervisor_hostname:
           log.error(str( h ) + ' does not exist')
           fail = True
   if fail:
       exit(2)
   return hypervisor_list

def liveMigrateInstance(instance, allow_live_block_migration):
    log_and_print(instance.id + " Trying to live migrate with --allow-live-block-migration set as: " + str(allow_live_block_migration) )
    try:
        instance.live_migrate(block_migration=allow_live_block_migration)
        monitoringMigration(instance, 'MIGRATING', 'ACTIVE')
    except nova_exceptions.BadRequest as e:
        if 'is not on shared storage' in str(e):
            return 'is_block_storage'
        if 'No valid host was found. There are not enough hosts available' in str(e):
            print(timeStr() + instance.id + " No valid host found" )
            log.warning(instance.id + ' ' + str(e))
            return False
        else:
            print(timeStr() + instance.id + " Live migration failed: " + str( e ) )
            log_instance_state(instance)
            failure(instance.id + " Unexpected error: " + str(e))
            return False
    return True

def monitoringMigration(instance, expected_status, expected_result):

    status = expected_status
    log_and_print(str(instance.id) + ' Starting monitoring migration')
    server_migration_status = nova.server_migrations.list(instance.id)
    if not len(server_migration_status) == 0:
        pprint (server_migration_status.__dict__)
        pprint (server_migration_status)
        log_and_print(str(instance.id) + " Migration destination: " + str(server_migration_status[0]._info['dest_compute']))
    else:
#        pprint(instance.__dict__)
        sys.stdout.write(str(instance.id) + ' ' + str(status) + " progress: ")

    while expected_status == status:
        instance = nova.servers.get(instance.id)
        status = instance.status
        server_migration_status = nova.server_migrations.list(instance.id)
        if not len(server_migration_status) == 0:
            # For blockstorage migrations
            sms = server_migration_status[0]._info
            data_to_log = str(' disk_processed_bytes: '   + str(sms['disk_processed_bytes']) +
                              ' disk_remaining_bytes: '   + str(sms['disk_remaining_bytes']) +
                              ' disk_total_bytes: '       + str(sms['disk_total_bytes']) +
                              ' memory_processed_bytes: ' + str(sms['memory_processed_bytes']) +
                              ' memory_remaining_bytes: ' + str(sms['memory_remaining_bytes']) +
                              ' memory_total_bytes: '     + str(sms['memory_total_bytes'] ))
            log_and_print(instance.id + data_to_log)
        else:
            # For cold migrations
            sys.stdout.write( ' ' + str(instance._info['progress']) )
            sys.stdout.flush()
        if not 'progress' in instance._info:
            failure(str(instance.id + " 'progress' key is missing. Migration seemed to " +
                    "have failed OpenStack probably thinks that it is on the new host " +
                    "even if it is on the old host"), 5)
        time.sleep(1)
    sys.stdout.write( ' ' + status + '\n')
    sys.stdout.flush()
    log_instance_state(instance)

    if status == 'ERROR':
       log_and_print(instance.id + " Something failed, status: " + str(status), 'error')
    elif status == expected_result:
       log.info(instance.id + " Instance migration complete: " + instance.status + " on hypervisor: " +
                str(getattr(instance, 'OS-EXT-SRV-ATTR:host')))
       return instance
    else:
       log_and_print(instance.id + " Unexpected state, status: " + str(status), 'error')
    return 0


def coldMigrateInstance(instance):
    log_and_print(instance.id + " Trying to cold migrate instance")
    instance.migrate()
    # When doing non-block-migration the expected result is VERIFY_RESIZE.
    monitoringMigration(instance, 'RESIZE', 'VERIFY_RESIZE')
    instance = nova.servers.get(instance.id)
    instance.confirm_resize()
    sys.stdout.write(instance.id + " VERIFY_RESIZE..")
    time_out_time=datetime.datetime.now() + datetime.timedelta(seconds=TIMEOUT_SECONDS)
    while datetime.datetime.now() < time_out_time:
        instance = nova.servers.get(instance.id)
        if instance.status == 'VERIFY_RESIZE':
            sys.stdout.write('.')
            sys.stdout.flush()
            continue
        elif instance.status == 'SHUTOFF':
            log_and_print( instance.id + " resized/block migration completed" )
            sys.stdout.write('.' + instance.status + '\n' + instance.id + ' Migration completed\n')
            sys.stdout.flush()
            return True
        else:
            log_and_print( instance.id + " Migration did not complete successfully", 'warning')
            log_and_print("Instance " + instance.id + " resize confirm failed", 'error')
    failure('The instance ' + instance.id + ' did not get resize_confirmed before this timed out', 33)


def migrateInstance(instance, allow_live_block_migration, stop_paused_instances, stop_suspended_instances):

    success = False
    instance_virsh_name = getattr(instance, 'OS-EXT-SRV-ATTR:instance_name')
    instace_host_name = getattr(instance, 'OS-EXT-SRV-ATTR:host' )
    instance_name = getattr(instance, 'name' )
    log_and_print(instance.id + ' virsh name: ' + str(instance_virsh_name) + " Instance to migrate next" )
    log_and_print(instance.id + ' Instances virsh name: ' + str(instance_virsh_name) + ' On host: ' + str(instace_host_name ) + ' Instance name: ' + str(instance_name))

    if ( instance.status == 'ACTIVE'
         and getattr(instance, 'OS-EXT-STS:power_state') == 1
         and getattr(instance, 'OS-EXT-STS:vm_state') == 'active'
         and getattr(instance, 'OS-EXT-STS:task_state') == None ):
        # Live migration only

        # Power_states
        # 1 == running?
        # 4 == shutdown
        # I'm not aware of a method to check if the instance is on shared storage or not
        pprint (allow_live_block_migration)

        success = liveMigrateInstance(instance, allow_live_block_migration)

    elif ( instance.status == 'SHUTOFF'
           and getattr(instance, 'OS-EXT-STS:power_state') == 4
           and getattr(instance, 'OS-EXT-STS:vm_state') == 'stopped'
           and getattr(instance, 'OS-EXT-STS:task_state') == None ):
        success = coldMigrateInstance(instance)
    elif ( instance.status == 'PAUSED'
           and getattr(instance, 'OS-EXT-STS:power_state') == 3
           and getattr(instance, 'OS-EXT-STS:vm_state') == 'paused'
           and getattr(instance, 'OS-EXT-STS:task_state') == None
           and stop_paused_instances):
        nova.servers.unpause(instance)
        # Wait time for instance to unpause 60s (default timeout) + 30s
        wait_for_instance_status(nova, instance, 'ACTIVE', 90)
        nova.servers.stop(instance)
        # Wait time for instance to shutoff 300s (timeout we have before force shutoff) + 30s
        wait_for_instance_status(nova, instance, 'SHUTOFF', 330)
        success = coldMigrateInstance(instance)
    elif ( instance.status == 'SUSPENDED'
           and getattr(instance, 'OS-EXT-STS:power_state') == 4
           and getattr(instance, 'OS-EXT-STS:vm_state') == 'suspended'
           and getattr(instance, 'OS-EXT-STS:task_state') == None
           and stop_suspended_instances):
        nova.servers.resume(instance)
        # Wait time for instance to resume 60s (default timeout) + 30s
        wait_for_instance_status(nova, instance, 'ACTIVE', 90)
        nova.servers.stop(instance)
        # Wait time for instance to shutoff 300s (timeout we have before force shutoff) + 30s
        wait_for_instance_status(nova, instance, 'SHUTOFF', 330)
        success = coldMigrateInstance(instance)
    else:
        log_instance_state(instance)
        log_and_print( instance.id + " Instance won't be migrated, because of its status: " +
                     instance.status + ' . Continuing...', 'warning')
        pprint(instance.__dict__)
        success = False

    return success

def wait_for_instance_status(nova, instance, desired_status, timeout=60, interval=3):
    """Wait for the instance to reach the desired status."""
    start_time = time.time()
    instance = nova.servers.get(instance.id)
    log_and_print(instance.id + ' Instance current status: ' + instance.status + '. Setting instance status to ' + desired_status + '.')
    while True:
        instance = nova.servers.get(instance.id)
        if instance.status == desired_status:
            log_and_print(instance.id + ' Instance is now ' + desired_status + '.')
            return True
        elif instance.status == 'ERROR':
            failure(' ERROR ' + instance.id + ' Instance status: ' + instance.status, 1)
        elif time.time() - start_time > timeout:
            failure(' ERROR ' + instance.id + ' Timed out after ' + str(timeout) + ' seconds waiting for the instance to reach ' + desired_status + '. Current status: ' + instance.status, 1)
        time.sleep(interval)

def drainHypervisor(node, flavors, max_instances_to_migrate, allow_block_migration, allow_live_block_migration, stop_paused_instances, stop_suspended_instances):
    instances = getInstances(node, flavors)
    # Create a list of instace uuid from a list of instance objects
    list_of_instance_uuids = list(map(lambda x: x.id, instances))
    pprint (allow_live_block_migration)
    migrateInstances(instances, flavors, max_instances_to_migrate, allow_block_migration, allow_live_block_migration, stop_paused_instances, stop_suspended_instances)
    instances = getInstances(node, flavors)
    list_of_instance_uuids = list(map(lambda x: x.id, instances))
    log_and_print("Instances still on " + node.hypervisor_hostname + ": " + str(list_of_instance_uuids))


def migrateInstances(list_of_instance_uuids, flavors, max_instances_to_migrate, allow_block_migration, allow_live_block_migration, stop_paused_instances, stop_suspended_instances):
    if allow_live_block_migration:
        allow_live_block_migration = None
    pprint("Live block migrate: " + str(allow_live_block_migration))
    log_and_print("Instances to migrate: " + str(list_of_instance_uuids))

    for i in list_of_instance_uuids:
        instance = nova.servers.get(i)
        node_list = getHypervisorUUID([getattr(instance, 'OS-EXT-SRV-ATTR:host')])
        node = node_list[0]
        if max_instances_to_migrate == 0:
           log_and_print("Maximum number of instances to migrate have been reached", 'info')
           break
        # Make an educated guess about hypervisor type. Ceph-backed instances see hundreds of TBs in local_gb.
        # Local disk/LVM backed instances see only their local disks' capacity in local_gb.
        # TODO: This can be removed when https://bugs.launchpad.net/nova/+bug/1732428 is resolved.
        if node.local_gb < 10000 and not allow_block_migration:
            log_and_print("Instance " + instance.name + " on hypervisor " + node.hypervisor_hostname +
                          " is most likely local disk backed. Need --allow-block-migration to continue.")
            continue
        log_instance_state(instance)
        max_instances_to_migrate = max_instances_to_migrate - 1
        success =  migrateInstance(instance, allow_live_block_migration, stop_paused_instances, stop_suspended_instances)
        instance = nova.servers.get(instance.id)
        log_instance_state(instance)
        if success and node.hypervisor_hostname == getattr(instance, 'OS-EXT-SRV-ATTR:host'):
            failure(instance.id + " is on the same hypervisor after migration even if there were no failures")
        elif success == False:
            log_and_print(str(instance.id) + ' Did not get migrated' )
        else:
            log_and_print(str(instance.id) + ' Seems to have migrated successfully. Success: ' + str(success) )


def getInstances(host, flavors=None):

    instances_on_host = nova.servers.list(detailed=True, search_opts={
                                          "all_tenants": True,
                                          "host": host.hypervisor_hostname } )
    if flavors:
        instaces_to_return = list(filter(lambda x: x.flavor['id'] in flavors, instances_on_host))
        return instaces_to_return
    return instances_on_host

def getFlavorIDs(flavor_name):
    flavors = nova.flavors.list()
    flavor_ids = []
    for fl in flavors:
        if fl.name in flavor_name or fl.id in flavor_name:
            flavor_ids.append(fl.id)
    log_and_print("Flavors to migrate: " + str( flavor_ids ))
    return flavor_ids

def test():
    flavor_ids = getFlavorID(['standard.tiny', 'standard.small'])
    getInstances('hypervisor.domain.tld', flavor_ids )
    exit(4)
    pprint( instances )
    pprint( instances[0].__dict__ )
    exit(5)

def main(argv=None):
   global nova
   keystone_session = session.Session(auth=v3.Password(**getGredentials()))
   keystoneclient_v3.Client(session=keystone_session)
   nova = client.Client("2.26", session=keystone_session)
#   test()
   (hypervisors, flavors, max_instances_to_migrate, allow_block_migration, allow_live_block_migration, stop_paused_instances, stop_suspended_instances, instances) = parseCommand()
   if instances:
        migrateInstances(instances,
                         flavors=[],
                         max_instances_to_migrate=-1,
                         allow_block_migration=allow_block_migration,
                         allow_live_block_migration=allow_live_block_migration,
                         stop_paused_instances=stop_paused_instances,
                         stop_suspended_instances=stop_suspended_instances)
   else:
       nodes = getHypervisorUUID(hypervisors)
       flavor_ids = getFlavorIDs(flavors)
       # This script apperently only drain one node at the time. Note that this might
       # be annoying with max_instances_to_migrate
       drainHypervisor(nodes[0], flavor_ids, max_instances_to_migrate, allow_block_migration, allow_live_block_migration, stop_paused_instances, stop_suspended_instances)

print(timeStr() + " Script started, logs will be stored: " + LOGFILE )
if __name__ == "__main__":
    main()
