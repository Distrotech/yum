#!/usr/bin/python
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
# Copyright 2002 Duke University 


import os
import sys
import getopt
import clientStuff
import nevral
import pkgaction
import callback
import time
import random
import locale
import rpm
import rpmUtils

from logger import Logger
from config import yumconf
from i18n import _

def parseCmdArgs(args):
    
    # setup our errorlog object 
    errorlog=Logger(threshold=10, file_object=sys.stderr)

    # our default config file location
    yumconffile=None
    if os.access("/etc/yum.conf", os.R_OK):
        yumconffile="/etc/yum.conf"
        
    try:
        gopts, cmds = getopt.getopt(args, 'Cc:hR:e:d:y', ['help'])
    except getopt.error, e:
        errorlog(0, _('Options Error: %s') % e)
        usage()
   
    try: 
        for o,a in gopts:
            if o == '-R':
                sleeptime=random.randrange(int(a)*60)
                # debug print sleeptime
                time.sleep(sleeptime)

        for o,a in gopts:
            if o == '-c':
                yumconffile=a

        if yumconffile:
            conf=yumconf(configfile=yumconffile)
        else:
            errorlog(0, _('Cannot find any conf file.'))
            sys.exit(1)
        # who are we:
        conf.uid=os.geteuid()
        # we'd like to have a log object now
        log=Logger(threshold=conf.debuglevel, file_object=sys.stdout)
        # syslog-style log
        if conf.uid == 0:
            logfile=open(conf.logfile,"a")
            filelog=Logger(threshold=10, file_object=logfile,preprefix=clientStuff.printtime())
        else:
            filelog=Logger(threshold=10, file_object=None,preprefix=clientStuff.printtime())

        for o,a in gopts:
            if o =='-d':
                log.threshold=int(a)
                conf.debuglevel=int(a)
            if o =='-e':
                errorlog.threshold=int(a)
                conf.errorlevel=int(a)
            if o =='-y':
                conf.assumeyes=1
            if o in ('-h', '--help'):
                usage()
            if o =='-C':
                conf.cache=1
    except ValueError, e:
        errorlog(0, _('Options Error: %s') % e)
        usage()
        
    return (log, errorlog, filelog, conf, cmds)
    
def main(args):
    """This does all the real work"""

    locale.setlocale(locale.LC_ALL, '')

    if len(args) < 1:
        usage()
    (log, errorlog, filelog, conf, cmds) = parseCmdArgs(args)
    if conf.commands != None and len(cmds) < 1:
        cmds = conf.commands

    if len (cmds) < 1:
        errorlog(0, _('Options Error: no commands found'))
        usage()

    if cmds[0] not in ('update', 'upgrade', 'install','info', 'list', 'erase',\
                       'grouplist','groupupdate','groupinstall','clean', \
                       'remove', 'provides', 'check-update'):
        usage()
    process=cmds[0]
    
    # some misc speedups/sanity checks
    if conf.uid != 0:
        conf.cache=1
    if process == 'clean':
        conf.cache=1
        
    # push the logs into the other namespaces
    pkgaction.log = log
    clientStuff.log = log
    nevral.log = log
    rpmUtils.log = log

    pkgaction.errorlog = errorlog
    clientStuff.errorlog = errorlog
    nevral.errorlog = errorlog
    rpmUtils.errorlog = errorlog
    
    pkgaction.filelog = filelog
    clientStuff.filelog = filelog
    nevral.filelog = filelog
    rpmUtils.filelog = filelog
    
    # push the conf file into the other namespaces
    nevral.conf = conf
    clientStuff.conf = conf
    pkgaction.conf = conf
    callback.conf = conf
    rpmUtils.conf = conf
    
    # get our ts together that we'll use all over the place
    ts = rpmUtils.Rpm_Ts_Work()
    ts.sigChecking('none')
    clientStuff.ts = ts
    pkgaction.ts = ts
    nevral.ts = ts
    rpmUtils.ts = ts
    
    # make remote nevral class
    HeaderInfo = nevral.nevral()
    
    # get the package info file
    clientStuff.get_package_info_from_servers(conf, HeaderInfo)
    
    # make local nevral class
    rpmDBInfo = nevral.nevral()
    clientStuff.rpmdbNevralLoad(rpmDBInfo)

    # create transaction set nevral class
    tsInfo = nevral.nevral()
    #################################################################################
    # generate all the lists we'll need to quickly iterate through the lists.
    #  uplist == list of updated packages
    #  newlist == list of uninstall/available NEW packages (ones we don't have at all)
    #  nulist == combination of the two
    #  obslist == packages obsoleting a package we have installed
    ################################################################################
    log(2, _('Finding updated packages'))
    (uplist, newlist, nulist) = clientStuff.getupdatedhdrlist(HeaderInfo, rpmDBInfo)
    log(2, _('Downloading needed headers'))
    clientStuff.download_headers(HeaderInfo, nulist)
    if cmds[0] == 'upgrade':
        log(2, _('Finding obsoleted packages'))
        obsdict=clientStuff.returnObsoletes(HeaderInfo, rpmDBInfo, nulist)
        obslist=obsdict.keys()
    else:
        obsdict={}
        obslist=[]
        

    log(3, 'nulist = %s' % len(nulist))
    log(3, 'uplist = %s' % len(uplist))
    log(3, 'newlist = %s' % len(newlist))
    log(3, 'obslist = %s' % len(obslist))
    
    ##################################################################
    # at this point we have all the prereq info we could ask for. we 
    # know whats in the rpmdb whats available, whats updated and what 
    # obsoletes. We should be able to do everything we want from here 
    # w/o getting anymore header info
    ##################################################################

    clientStuff.take_action(cmds, nulist, uplist, newlist, obslist, tsInfo,\
                            HeaderInfo, rpmDBInfo, obsdict)
    # back from taking actions - if we've not exited by this point then we have
    # an action that will install/erase/update something
    
    # at this point we should have a tsInfo nevral with all we need to complete our task.
    # if for some reason we've gotten all the way through this step with 
    # an empty tsInfo then exit and be confused :)
    if len(tsInfo.NAkeys()) < 1:
        log(2, _('No actions to take'))
        sys.exit(0)
        
    
    if process not in ('erase', 'remove'):
        # put available pkgs in tsInfonevral in state 'a'
        for (name, arch) in nulist:
            if not tsInfo.exists(name, arch):
                ((e, v, r, a, l, i), s)=HeaderInfo._get_data(name, arch)
                log(6,'making available: %s' % name)
                tsInfo.add((name, e, v, r, arch, l, i), 'a')

    log(2, _('Resolving dependencies'))
    (code, msgs) = tsInfo.resolvedeps(rpmDBInfo)
    if code == 1:
        for msg in msgs:
            print msg
        sys.exit(1)
    log(2, _('Dependencies resolved'))
    
    # prompt for use permission to do stuff in tsInfo - list all the actions 
    # (i, u, e, ed, ud,iu(installing, but marking as 'u' in the actual ts, just 
    # in case)) confirm w/the user
    
    (i_list, u_list, e_list, ud_list, ed_list)=clientStuff.actionslists(tsInfo)
    
    clientStuff.printactions(i_list, u_list, e_list, ud_list, ed_list)
    if conf.assumeyes==0:
        if clientStuff.userconfirm():
            errorlog(1, _('Exiting on user command.'))
            sys.exit(1)

    # FIXME this shouldn't be here - should be somewhere else for doing this sort of action
    # Test run for disk space checks
    tstest = clientStuff.create_final_ts(tsInfo)
    log(2, _('Calculating available disk space - this could take a bit'))
    tstest.setFlags(rpm.RPMTRANS_FLAG_TEST)
    tstest.setProbFilter(~rpm.RPMPROB_FILTER_DISKSPACE)
    tserrors = tstest.run(callback.install_callback, '')
    if tserrors:
        log(2, 'Error: Disk space Error')
        errorlog(0, _('You appear to have insufficient disk space to handle these packages'))
        sys.exit(1)
    tstest.closeDB()
    del tstest
    
    # FIXME the actual run should probably be elsewhere and this should be
    # inside a try, except set
    tsfin = clientStuff.create_final_ts(tsInfo)
    
    if conf.uid == 0:
        # sigh - the magical "order" command - nice of this not to really be 
        # documented anywhere.
        tsfin.order()
        errors = tsfin.run(callback.install_callback, '')
        if errors:
            errorlog(0, _('Errors installing:'))
            for error in errors:
                errorlog(0, error)
            sys.exit(1)
        tsfin.closeDB()
        del tsfin
        
        # Check to see if we've got a new kernel and put it in the right place in grub/lilo
        pkgaction.kernelupdate(tsInfo)
        
        # log what we did and also print it out
        clientStuff.filelogactions(i_list, u_list, e_list, ud_list, ed_list)
        clientStuff.shortlogactions(i_list, u_list, e_list, ud_list, ed_list)
        
    else:
        errorlog(1, _('You\'re not root, we can\'t install things'))
        sys.exit(0)
        
    log(2, _('Transaction(s) Complete'))
    sys.exit(0)


def usage():
    print _("""
    Usage:  yum [options] <update | upgrade | install | info | erase | list |
            clean | provides | check-update>
                
         Options:
          -c [config file] - specify the config file to use
          -e [error level] - set the error logging level
          -d [debug level] - set the debugging level
          -y answer yes to all questions
          -R [time in minutes] - set the max amount of time to randonly run in.
          -C run from cache only - do not update the cache
          -h, --help this screen
    """)
    sys.exit(1)
    
if __name__ == "__main__":
    main()

