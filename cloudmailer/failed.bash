#!/bin/bash
# This can be used to check which hypervisors should NOT be upgraded
# if one hypervisor in an anti-affinity server group has failed.

# Fail on error, no point to continue if something fails
set -e

# Hypervisor that failed, for example: io23.pouta.csc.fi
hypervisor=$1

#openstack hypervisor show $hypervisor > /dev/null ||exit 1
# All servers on that hypervisors
instances=$(openstack server list --host $hypervisor --all -c ID -f value |sed "s/\ /\|/g" )

# All instances in server groups that instances are a part of
all_instances=$(openstack server group list --all --long -c Members |grep -E "$instances" )

# Creating a regex for the next query
all_instances_regex=$(echo $all_instances| sed "s/|//g" |sed "s/\,//g" |xargs | sed "s/\ /\|/g")

# Printing all hypervisors that are a part of all affected instances' server groups
openstack server list --all --long -c Host -c ID -f value |grep -E "$all_instances_regex" | cut -d ' ' -f 2|sort |uniq
