# Cloud mailer

Cloud mailer is a tool to find virtual machines running on specific compute
hosts, and mailing all those users with a template email. One email per
project is created. All project members get the mail, and the mail contains
all the affected virtual machines of the project.

Cloud mailer can also send the mails based on VM or project UUIDs.

## Requirements

 - python-openstackclient
 - python-novaclient

## Files

Please see the templates/ directory for ready a template email.

If you write new templates, any line with the following texts will be replaced.

   PROJECT-NAME - Name of the project where the VMs reside
   LIST-OF-MACHINES - List of affected machines and scheduled service break
   information.

## Hardcoded values

We have some hardcoded values, e.g. from address and smtp server. External
users of the script, please override accordingly.

## Usage

Please run "python cloudmailer.py -h" for command help.

## Directories

 - temporary_files/

   This directory contains all the temporary data for a run. All generated
   emails are saved here with a recipient list.

   - host_schedule

     This directory also contains the file "host_schedule", which contains the
     break times for each server, if you used the "-s" flag when running the
     command. It's a good file. You should use the file. So, you know, you are
     also aware of when to maintain which server, not only the customer.

   - affected_vms

     Another good file. It contains the UUIDs of the affected VMs, and can
     later on be used as input (please make a copy of it, and use the copy with
     the -v flag) to cloudmailer to follow up on a previous issue.

 - templates/

   This directory contains email template (just the message). It is a good idea
   to run ```aspell --check $FILENAME``` against all template changes so that there
   is less chance for a typo to end up in the email.
