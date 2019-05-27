# Node drainer

## Overview
The main benefits with this script compared with other nova commands.
- Only one migration at the time.
- If any migration fails no new migration will be started.
- It logs what happens and errors.
- It confirms that the migration completed before continueing with the next migration.
- You can schedule multiple hypervisors to be drain in sequens.
- One does not need to specifiy what kind of migration that shall be done.

## What is the script able to do.
This scirpt is meant to drain nodes of instances. At the moment it is able to do following migrations:
- Migrating Ceph backed instances
   * live-migration
   * cold-migration
- Migrating local storage backed instances
   * cold-migration
- Able to migrate specific flavors away from a host.

## What won't it do.
- live-migrate local disk backed instances. Since we found it way to dangerous.

## Example on output

```
$ python node-drainer.py -y mynode33.cloud1.example.org
18:09:28  Script started, logs will be stored: log_files/migration_loggs.log
18:09:28 ['mynode33.cloud1.example.org']
18:09:33 Instances to migrate: [u'8994fbd8-2693-4bd0-8760-fc36e659dfa6', u'f6a9deb5-5101-499f-b295-a9eceabe7fc6', u'dfd33d60-a0d8-4abd-8983-d37a0d0264be' ]
18:09:34 8994fbd8-2693-4bd0-8760-fc36e659dfa6 No valid host found
f6a9deb5-5101-499f-b295-a9eceabe7fc6 MIGRATING progress:  0 0 0 0 0 0 0 0 0 0 ACTIVE
dfd33d60-a0d8-4abd-8983-d37a0d0264be MIGRATING progress:  0 0 0 23 23 23 23 0 ACTIVE
18:09:19 Instances still on mynode33.cloud1.example.org: [u'8994fbd8-2693-4bd0-8760-fc36e659dfa6']

```


## Limitations and improvements
- It does not disable hypervisors.
- Check if any instance is migrating on the hypervisor, if so do not start a new migration.
- It would be nice if there were a flag for setting a sleep between every migration.
- It would be nice if one could specify specific instance to migrate.
- It would be nice to specify a destination
- Print name and project name of instance to migrate


## Issues found when developing live block migration
- Sometime migration can fail when undefining a instance on the source host when compliting (live block-migration)

## Links
https://docs.openstack.org/python-novaclient/2.30.3/ref/v2/servers.html



