#!/usr/bin/python -t
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
# Copyright 2004 Duke University 
# Written by Seth Vidal


import os
import os.path
import sys
import time
import getopt
import random
import fcntl
import fnmatch
import re
import output

from urlgrabber.progress import TextMeter
import yum
import yum.Errors
import yum.misc
import rpmUtils.arch
from rpmUtils.miscutils import compareEVR
from yum.packages import parsePackages, returnBestPackages, YumInstalledPackage, YumLocalPackage
from yum.logger import Logger
from yum.config import yumconf
from yum import pgpmsg
from i18n import _
import callback
import urlgrabber
import urlgrabber.grabber

__version__ = '2.1.13'


class YumBaseCli(yum.YumBase, output.YumOutput):
    """This is the base class for yum cli.
       Inherits from yum.YumBase and output.YumOutput """
       
    def __init__(self):
        yum.YumBase.__init__(self)
        self.localPackages = [] # for local package handling - possibly needs
                                # to move to the lower level class

    def doRepoSetup(self, nosack=None):
        """grabs the repomd.xml for each enabled repository and sets up the basics
           of the repository"""
        self.log(2, 'Setting up Repos')
        if len(self.repos.listEnabled()) < 1:
            self.errorlog(0, 'No Repositories Available to Set Up')
            sys.exit(1)
        for repo in self.repos.listEnabled():
            if repo.repoXML is not None and len(repo.urls) > 0:
                continue
            try:
                repo.cache = self.conf.getConfigOption('cache')
                repo.baseurlSetup()
                repo.dirSetup()
                self.log(3, 'Baseurl(s) for repo: %s' % repo.urls)
            except yum.Errors.RepoError, e:
                self.errorlog(0, '%s' % e)
                sys.exit(1)
            try:
                repo.getRepoXML(text=repo)
            except yum.Errors.RepoError, e:
                self.errorlog(0, 'Cannot open/read repomd.xml file for repository: %s' % repo)
                self.errorlog(0, str(e))
                sys.exit(1)
        
        if not nosack: # so we can make the dirs and grab the repomd.xml but not import the md
            self.log(2, 'Reading repository metadata in from local files')
            self.doSackSetup()
    
        
    def getOptionsConfig(self, args):
        """parses command line arguments, takes cli args:
        sets up self.conf and self.cmds as well as logger objects 
        in base instance"""
        
        # setup our errorlog object 
        self.errorlog = Logger(threshold=2, file_object=sys.stderr)
    
        # our default config file location
        yumconffile = None
        if os.access("/etc/yum.conf", os.R_OK):
            yumconffile = "/etc/yum.conf"
    
        try:
            gopts, self.cmds = getopt.getopt(args, 'tCc:hR:e:d:y', ['help',
                                                            'version',
                                                            'installroot=',
                                                            'enablerepo=',
                                                            'disablerepo=',
                                                            'exclude=',
                                                            'obsoletes',
                                                            'rss-filename=',
                                                            'tolerant'])
        except getopt.error, e:
            self.errorlog(0, _('Options Error: %s') % e)
            self.usage()
    
        # get the early options out of the way
        # these are ones that:
        #  - we need to know about and do NOW
        #  - answering quicker is better
        #  - give us info for parsing the others
        
        # our sleep variable for the random start time
        sleeptime=0
        root = '/'
        installroot = None
        conffile = '/etc/yum.conf'
        
        try: 
            for o,a in gopts:
                if o == '--version':
                    print __version__
                    sys.exit(0)
                if o == '--installroot':
                    installroot = a
                if o == '-c':
                    conffile = a
            
            # if the conf file is inside the  installroot - use that.
            # otherwise look for it in the normal root
            if installroot:
                if os.access(installroot + '/' + conffile, os.R_OK):
                    conffile = installroot + '/' + conffile
                    
                root = installroot
                    
            try:
                self.conf = yumconf(configfile = conffile, root = root)
            except yum.Errors.ConfigError, e:
                self.errorlog(0, _('Config Error: %s') % e)
                sys.exit(1)
                
            # config file is parsed and moving us forward
            # set some things in it.
                
            # who are we:
            self.conf.setConfigOption('uid', os.geteuid())

            # version of yum
            self.conf.setConfigOption('yumversion', __version__)
            
            
            # we'd like to have a log object now
            self.log=Logger(threshold=self.conf.getConfigOption('debuglevel'), file_object = 
                                                                        sys.stdout)
            
            # syslog-style log
            if self.conf.getConfigOption('uid') == 0:
                logpath = os.path.dirname(self.conf.logfile)
                if not os.path.exists(logpath):
                    try:
                        os.makedirs(logpath, mode=0755)
                    except OSError, e:
                        self.errorlog(0, _('Cannot make directory for logfile %s' % logpath))
                        sys.exit(1)
                try:
                    logfd = os.open(self.conf.logfile, os.O_WRONLY |
                                    os.O_APPEND | os.O_CREAT, 0644)
                except OSError, e:
                    self.errorlog(0, _('Cannot open logfile %s' % self.conf.logfile))
                    sys.exit(1)

                logfile =  os.fdopen(logfd, 'a')
                fcntl.fcntl(logfd, fcntl.F_SETFD)
                self.filelog = Logger(threshold = 10, file_object = logfile, 
                                preprefix = self.printtime)
            else:
                self.filelog = Logger(threshold = 10, file_object = None, 
                                preprefix = self.printtime)
            
        
            # now the rest of the options
            for o,a in gopts:
                if o == '-d':
                    self.log.threshold=int(a)
                    self.conf.setConfigOption('debuglevel', int(a))
                elif o == '-e':
                    self.errorlog.threshold=int(a)
                    self.conf.setConfigOption('errorlevel', int(a))
                elif o == '-y':
                    self.conf.setConfigOption('assumeyes',1)
                elif o in ['-h', '--help']:
                    self.usage()
                elif o == '-C':
                    self.conf.setConfigOption('cache', 1)
                elif o == '-R':
                    sleeptime = random.randrange(int(a)*60)
                elif o == '--obsoletes':
                    self.conf.setConfigOption('obsoletes', 1)
                elif o == '--installroot':
                    self.conf.setConfigOption('installroot', a)
                elif o == '--rss-filename':
                    self.conf.setConfigOption('rss-filename', a)
                elif o == '--enablerepo':
                    try:
                        self.conf.repos.enableRepo(a)
                    except yum.Errors.ConfigError, e:
                        self.errorlog(0, _(e))
                        self.usage()
                elif o == '--disablerepo':
                    try:
                        self.conf.repos.disableRepo(a)
                    except yum.Errors.ConfigError, e:
                        self.errorlog(0, _(e))
                        self.usage()
                        
                elif o == '--exclude':
                    try:
                        excludelist = self.conf.getConfigOption('exclude')
                        excludelist.append(a)
                        self.conf.setConfigOption('exclude', excludelist)
                    except yum.Errors.ConfigError, e:
                        self.errorlog(0, _(e))
                        self.usage()
                
                            
        except ValueError, e:
            self.errorlog(0, _('Options Error: %s') % e)
            self.usage()
        
        # if we're below 2 on the debug level we don't need to be outputting
        # progress bars - this is hacky - I'm open to other options
        # One of these is a download
        if self.conf.getConfigOption('debuglevel') < 2 or not sys.stdout.isatty():
            self.conf.repos.setProgressBar(None)
            self.conf.repos.callback = None
        else:
            self.conf.repos.setProgressBar(TextMeter(fo=sys.stdout))
            self.conf.repos.callback = self.simpleProgressBar

        # setup our failure report for failover
        freport = (self.failureReport,(),{})
        self.conf.repos.setFailureCallback(freport)
        
        # setup our depsolve progress callback
        dscb = output.DepSolveProgressCallBack(self.log, self.errorlog)
        self.dsCallback = dscb
        
        # this is just a convenience reference
        self.repos = self.conf.repos
        
        # save our original args out
        self.args = args
        # save out as a nice command string
        self.cmdstring = 'yum '
        for arg in self.args:
            self.cmdstring += '%s ' % arg

        self.parseCommands() # before we exit check over the base command + args
                             # make sure they match
    
        # set our caching mode correctly
        
        if self.conf.getConfigOption('uid') != 0:
            self.conf.setConfigOption('cache', 1)
        # run the sleep - if it's unchanged then it won't matter
        time.sleep(sleeptime)


    def parseCommands(self):
        """reads self.cmds and parses them out to make sure that the requested 
        base command + argument makes any sense at all""" 

        self.log(3, 'Yum Version: %s' % self.conf.getConfigOption('yumversion'))
        self.log(3, 'COMMAND: %s' % self.cmdstring)
        self.log(3, 'Installroot: %s' % self.conf.getConfigOption('installroot'))
        if len(self.conf.getConfigOption('commands')) == 0 and len(self.cmds) < 1:
            self.cmds = self.conf.getConfigOption('commands')
        else:
            self.conf.setConfigOption('commands', self.cmds)
        if len(self.cmds) < 1:
            self.errorlog(0, _('You need to give some command'))
            self.usage()

        self.basecmd = self.cmds[0] # our base command
        self.extcmds = self.cmds[1:] # out extended arguments/commands
        
        if len(self.extcmds) > 0:
            self.log(3, 'Ext Commands:\n')
            for arg in self.extcmds:
                self.log(3, '   %s' % arg)
        
        if self.basecmd not in ['update', 'install','info', 'list', 'erase',
                                'grouplist', 'groupupdate', 'groupinstall',
                                'groupremove', 'groupinfo', 'makecache',
                                'clean', 'remove', 'provides', 'check-update',
                                'search', 'generate-rss', 'upgrade', 
                                'whatprovides', 'localinstall', 'localupdate',
                                'resolvedep']:
            self.usage()
            
    
        if self.conf.getConfigOption('uid') != 0:
            if self.basecmd in ['install', 'update', 'clean', 'upgrade','erase', 
                                'groupupdate', 'groupinstall', 'remove',
                                'groupremove', 'importkey', 'makecache', 
                                'localinstall', 'localupdate']:
                self.errorlog(0, _('You need to be root to perform this command.'))
                sys.exit(1)

        if self.basecmd in ['install', 'update', 'upgrade', 'groupinstall',
                            'groupupdate', 'localinstall', 'localupdate']:

            if not self.gpgKeyCheck():
                for repo in self.repos.listEnabled():
                    if repo.gpgcheck and repo.gpgkey == '':
                        msg = _("""
You have enabled checking of packages via GPG keys. This is a good thing. 
However, you do not have any GPG public keys installed. You need to download
the keys for packages you wish to install and install them.
You can do that by running the command:
    rpm --import public.gpg.key


Alternatively you can specify the url to the key you would like to use
for a repository in the 'gpgkey' option in a repository section and yum 
will install it for you.

For more information contact your distribution or package provider.
""")
                        self.errorlog(0, msg)
                        sys.exit(1)

                
        if self.basecmd in ['install', 'erase', 'remove', 'localinstall', 'localupdate']:
            if len(self.extcmds) == 0:
                self.errorlog(0, _('Error: Need to pass a list of pkgs to %s') % self.basecmd)
                self.usage()
    
        elif self.basecmd in ['provides', 'search', 'whatprovides']:
            if len(self.extcmds) == 0:
                self.errorlog(0, _('Error: Need an item to match'))
                self.usage()
            
        elif self.basecmd in ['groupupdate', 'groupinstall', 'groupremove', 'groupinfo']:
            if len(self.extcmds) == 0:
                self.errorlog(0, _('Error: Need a group or list of groups'))
                self.usage()
    
        elif self.basecmd == 'clean':
            if len(self.extcmds) == 0:
                self.errorlog(0,
                    _('Error: clean requires an option: headers, packages, cache, metadata, all'))
            for cmd in self.extcmds:
                if cmd not in ['headers', 'packages', 'metadata', 'cache', 'all']:
                    self.usage()
        elif self.basecmd == 'generate-rss':
            if len(self.extcmds) == 0:
                self.extcmds.insert(0, 'recent')
            
            if self.extcmds[0] not in ['updates', 'recent']:
                self.errorlog(0, _("Error: generate-rss takes no argument, 'updates' or 'recent'."))
                self.usage()
            
        elif self.basecmd in ['list', 'check-update', 'info', 'update', 'upgrade',
                              'generate-rss', 'grouplist', 'makecache',
                              'resolvedep']:
            pass
    
        else:
            self.usage()

    def doCommands(self):
        """calls the base command passes the extended commands/args out to be
        parsed. (most notably package globs). returns a numeric result code and
        an optional string
           0 = we're done, exit
           1 = we've errored, exit with error string
           2 = we've got work yet to do, onto the next stage"""
        
        # at this point we know the args are valid - we don't know their meaning
        # but we know we're not being sent garbage
        
        # setup our transaction sets (needed globally, here's a good a place as any)
        try:
            self.doTsSetup()
        except yum.Errors.YumBaseError, e:
            return 1, [str(e)]


        if self.basecmd == 'install':
            self.log(2, "Setting up Install Process")
            try:
                return self.installPkgs()
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]
        
        elif self.basecmd == 'update':
            self.log(2, "Setting up Update Process")
            try:
                return self.updatePkgs()
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]

            
        elif self.basecmd == 'upgrade':
            self.conf.setConfigOption('obsoletes', 1)
            self.log(2, "Setting up Upgrade Process")
            try:
                return self.updatePkgs()
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]
            
            
        elif self.basecmd in ['erase', 'remove']:
            self.log(2, "Setting up Remove Process")
            try:
                return self.erasePkgs()
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]
            

        elif self.basecmd in ['localinstall', 'localupdate']:
            self.log(2, "Setting up Local Package Process")
            updateonly=0
            if self.basecmd == 'localupdate': updateonly=1
                
            try:
                return self.localInstall(updateonly=updateonly)
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]

            
        elif self.basecmd in ['list', 'info']:
            try:
                ypl = self.returnPkgLists()
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]
            else:
                self.listPkgs(ypl.installed, 'Installed Packages', self.basecmd)
                self.listPkgs(ypl.available, 'Available Packages', self.basecmd)
                self.listPkgs(ypl.extras, 'Extra Packages', self.basecmd)
                self.listPkgs(ypl.updates, 'Updated Packages', self.basecmd)
                if len(ypl.obsoletes) > 0 and self.basecmd == 'list': 
                # if we've looked up obsolete lists and it's a list request
                    print 'Obsoleting Packages'
                    for obtup in ypl.obsoletesTuples:
                        self.updatesObsoletesList(obtup, 'obsoletes')
                else:
                    self.listPkgs(ypl.obsoletes, 'Obsoleting Packages', self.basecmd)
                self.listPkgs(ypl.recent, 'Recently Added Packages', self.basecmd)
                return 0, []

        elif self.basecmd == 'check-update':
            self.extcmds.insert(0, 'updates')
            result = 0
            try:
                ypl = self.returnPkgLists()
                if len(ypl.updates) > 0:
                    self.listPkgs(ypl.updates, '', outputType='list')
                    result = 100
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]
            else:
                return result, []
            
            
        elif self.basecmd == 'generate-rss':
            self.log(2, 'Setting up RSS Generation')
            titles = { 'recent': 'Recent Packages',
                       'updates': 'Updated Packages'}
            try:
                pkgtype = self.extcmds[0]
                ypl = self.returnPkgLists()
                this_pkg_list = getattr(ypl, pkgtype)
                if len(this_pkg_list) > 0:
                    needrepos = []
                    for po in this_pkg_list:
                        if po.repoid not in needrepos:
                            needrepos.append(po.repoid)

                    self.log(2, 'Importing Changelog Metadata')
                    self.repos.populateSack(with='otherdata', which=needrepos)
                    self.log(2, 'Generating RSS File for %s' % pkgtype)
                        
                    self.listPkgs(this_pkg_list, titles[pkgtype], outputType='rss')
                else:
                    self.errorlog(0, 'No Recent Packages')

            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]
            else:
                return 0, ['Done']
                
        elif self.basecmd == 'clean':
            self.conf.setConfigOption('cache', 1)
            hdrcode = pkgcode = xmlcode = piklcode = 0
            pkgresults = hdrresults = xmlresults = piklresults = []

            if 'all' in self.extcmds:
                self.log(2, 'Cleaning up Everything')
                pkgcode, pkgresults = self.cleanPackages()
                hdrcode, hdrresults = self.cleanHeaders()
                xmlcode, xmlresults = self.cleanMetadata()
                piklcode, piklresults = self.cleanPickles()
                
                code = hdrcode + pkgcode + xmlcode + piklcode
                results = hdrresults + pkgresults + xmlresults + piklresults
                return code, results
            if 'headers' in self.extcmds:
                self.log(2, 'Cleaning up Headers')
                hdrcode, hdrresults = self.cleanHeaders()
            if 'packages' in self.extcmds:
                self.log(2, 'Cleaning up Packages')
                pkgcode, pkgresults = self.cleanPackages()
            if 'metadata' in self.extcmds:
                self.log(2, 'Cleaning up xml metadata')
                xmlcode, xmlresults = self.cleanMetadata()
            if 'cache' in self.extcmds:
                self.log(2, 'Cleaning up pickled cache')
                piklcode, piklresults =  self.cleanPickles()
                
            code = hdrcode + pkgcode + xmlcode + piklcode
            results = hdrresults + pkgresults + xmlresults + piklresults
            return code, results
            
        
        elif self.basecmd in ['groupupdate', 'groupinstall', 'groupremove', 
                              'grouplist', 'groupinfo']:

            self.log(2, "Setting up Group Process")

            self.doRepoSetup(nosack=1)
            try:
                self.doGroupSetup()
            except yum.Errors.GroupsError:
                return 1, ['No Groups on which to run command']
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]
            
            if self.basecmd == 'grouplist':
                return self.returnGroupLists()
            
            elif self.basecmd == 'groupinstall':
                try:
                    return self.installGroups()
                except yum.Errors.YumBaseError, e:
                    return 1, [str(e)]
            
            elif self.basecmd == 'groupupdate':
                try:
                    return self.updateGroups()
                except yum.Errors.YumBaseError, e:
                    return 1, [str(e)]
            
            elif self.basecmd == 'groupremove':
                try:
                    return self.removeGroups()
                except yum.Errors.YumBaseError, e:
                    return 1, [str(e)]
            elif self.basecmd == 'groupinfo':
                try:
                    return self.returnGroupInfo()
                except yum.Errors.YumBaseError, e:
                    return 1, [str(e)]
            
        elif self.basecmd in ['search']:
            self.log(2, "Searching Packages: ")
            try:
                return self.search()
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]
        
        elif self.basecmd in ['provides', 'whatprovides']:
            self.log(2, "Searching Packages: ")
            try:
                return self.provides()
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]

        elif self.basecmd in ['resolvedep']:
            self.log(2, "Searching Packages for Dependency:")
            try:
                return self.resolveDepCli()
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]
        
        elif self.basecmd in ['makecache']:
            self.log(2, "Making cache files for all metadata files.")
            self.log(2, "This may take a while depending on the speed of this computer")
            self.log(3, '%s' % self.pickleRecipe())
            try:
                self.doRepoSetup(nosack=1)
                self.repos.populateSack(with='metadata', pickleonly=1)
                self.repos.populateSack(with='filelists', pickleonly=1)
                self.repos.populateSack(with='otherdata', pickleonly=1)
                
            except yum.Errors.YumBaseError, e:
                return 1, [str(e)]
            return 0, ['Metadata Cache Created']
            
        else:
            return 1, ['command not implemented/not found']

    def doTransaction(self):
        """takes care of package downloading, checking, user confirmation and actually
           RUNNING the transaction"""

        # output what will be done:
        self.log(1, self.listTransaction())
        
        # Check which packages have to be downloaded
        downloadpkgs = []
        for txmbr in self.tsInfo.getMembers():
            if txmbr.ts_state in ['i', 'u']:
                po = self.getPackageObject(txmbr.pkgtup)
                if po:
                    downloadpkgs.append(po)

        # Report the total download size to the user, so he/she can base
        # the answer on this info
        self.reportDownloadSize(downloadpkgs)
        
        # confirm with user
        if not self.conf.getConfigOption('assumeyes'):
            if not self.userconfirm():
                self.log(0, 'Exiting on user Command')
                return

        

        self.log(2, 'Downloading Packages:')
        problems = self.downloadPkgs(downloadpkgs) 

        if len(problems.keys()) > 0:
            errstring = ''
            errstring += 'Error Downloading Packages:\n'
            for key in problems.keys():
                errors = yum.misc.unique(problems[key])
                for error in errors:
                    errstring += '  %s: %s\n' % (key, error)
            raise yum.Errors.YumBaseError, errstring

        # Check GPG signatures
        if self.gpgsigcheck(downloadpkgs) != 0:
            return
        
        self.log(2, 'Running Transaction Test')
        tsConf = {}
        for feature in ['diskspacecheck']: # more to come, I'm sure
            tsConf[feature] = self.conf.getConfigOption(feature)
        
        testcb = callback.RPMInstallCallback(output=0)
        testcb.tsInfo = self.tsInfo
        # clean out the ts b/c we have to give it new paths to the rpms 
        del self.ts
        
        self.initActionTs()
        self.dsCallback = None # dumb, dumb dumb dumb!
        self.populateTs(keepold=0) # sigh
        tserrors = self.ts.test(testcb, conf=tsConf)
        del testcb
        self.log(2, 'Finished Transaction Test')
        if len(tserrors) > 0:
            errstring = 'Transaction Check Error: '
            for descr in tserrors:
                errstring += '  %s\n' % descr 
            
            raise yum.Errors.YumBaseError, errstring
        self.log(2, 'Transaction Test Succeeded')
        del self.ts
        
        self.initActionTs() # make a new, blank ts to populate
        self.populateTs(keepold=0) # populate the ts
        self.ts.check() #required for ordering
        self.ts.order() # order
        
        output = 1
        if self.conf.debuglevel < 2:
            output = 0
        cb = callback.RPMInstallCallback(output=output)
        cb.filelog = self.filelog # needed for log file output
        cb.tsInfo = self.tsInfo
        
        # run ts
        self.log(2, 'Running Transaction')
        
        errors = self.ts.run(cb.callback, '')
        if errors:
            errstring = 'Error in Transaction: '
            for descr in errors:
                errstring += '  %s\n' % str(descr)
            
            raise yum.Errors.YumBaseError, errstring

        # close things
        self.log(1, self.postTransactionOutput())

    def gpgsigcheck(self, pkgs):
        '''Perform GPG signature verification on the given packages, installing
        keys if possible

        Returns non-zero if execution should stop (user abort).
        Will raise YumBaseError if there's a problem
        '''
        for po in pkgs:
            result, errmsg = self.sigCheckPkg(po)

            if result == 0:
                # Verified ok, or verify not req'd
                continue            

            elif result == 1:
                # Key needs to be installed
                self.log(0, errmsg)
    
                # Bail if not -y and stdin isn't a tty as key import will
                # require user confirmation
                if not sys.stdin.isatty() and not \
                            self.conf.getConfigOption('assumeyes'):
                    raise yum.Errors.YumBaseError, \
                        'Refusing to automatically import keys when running ' \
                        'unattended.\nUse "-y" to override.'

                repo = self.repos.getRepo(po.repoid)
                keyurl = repo.gpgkey
                self.log(0, 'Retrieving GPG key from %s' % keyurl)

                # Go get the GPG key from the given URL
                try:
                    rawkey = urlgrabber.urlread(keyurl, limit=9999)
                except urlgrabber.grabber.URLGrabError, e:
                    raise yum.Errors.YumBaseError(
                            'GPG key retrieval failed: ' + str(e))

                # Parse the key
                try:
                    keyinfo = yum.misc.getgpgkeyinfo(rawkey)
                    keyid = keyinfo['keyid']
                    hexkeyid = yum.misc.keyIdToRPMVer(keyid).upper()
                    timestamp = keyinfo['timestamp']
                    userid = keyinfo['userid']
                except ValueError, e:
                    raise yum.Errors.YumBaseError, \
                            'GPG key parsing failed: ' + str(e)

                # Check if key is already installed
                if yum.misc.keyInstalled(keyid, timestamp) >= 0:
                    self.log(0, '\n')
                    raise yum.Errors.YumBaseError, \
                            'The GPG key at %s (0x%s) \n' \
                            'is already installed but is not the correct '\
                            'key for this package.\nCheck that this is the ' \
                            'correct key for the "%s" repository.' % \
                                (keyurl, hexkeyid, repo.name)

                # Try installing/updating GPG key
                self.log(0, 'Importing GPG key 0x%s "%s"' % (hexkeyid, userid))
                if not self.conf.getConfigOption('assumeyes'):
                    if not self.userconfirm():
                        self.log(0, 'Exiting on user command')
                        return 1
        
                # Import the key
                result = self.ts.pgpImportPubkey(yum.misc.procgpgkey(rawkey))
                if result != 0:
                    raise yum.Errors.YumBaseError, \
                            'Key import failed (code %d)' % result
                self.log(1, 'Key imported successfully')
    
                # Check if the key helped
                result, errmsg = self.sigCheckPkg(po)
                if result != 0:
                    self.log(0, "Key import didn't help, wrong key?")
                    raise yum.Errors.YumBaseError, errmsg

            else:
                # Fatal error
                raise yum.Errors.YumBaseError, errmsg

        return 0

    
    def installPkgs(self, userlist=None):
        """Attempts to take the user specified list of packages/wildcards
           and install them, or if they are installed, update them to a newer
           version. If a complete version number if specified, attempt to 
           downgrade them to the specified version"""
        # get the list of available packages
        # iterate over the user's list
        # add packages to Transaction holding class if they match.
        # if we've added any packages to the transaction then return 2 and a string
        # if we've hit a snag, return 1 and the failure explanation
        # if we've got nothing to do, return 0 and a 'nothing available to install' string
        
        oldcount = len(self.tsInfo)
        
        if not userlist:
            userlist = self.extcmds

        self.doRepoSetup()
        self.doRpmDBSetup()
        installed = self.rpmdb.getPkgList()
        avail = self.pkgSack.returnPackages()
        toBeInstalled = {} # keyed on name
        passToUpdate = [] # list of pkgtups to pass along to updatecheck

        for arg in userlist:
            if os.path.exists(arg) and arg[-4:] == '.rpm': # this is hurky, deal w/it
                val, msglist = self.localInstall(filelist=[arg])
                if val == 2: # we added it to the transaction set so don't try from the repos
                    continue 

            if arg[0] == '/':
                try:
                    mypkg = self.returnPackageByDep(arg)
                except yum.Errors.YumBaseError, e:
                    pass
                else:
                    arg = '%s:%s-%s-%s.%s' % (mypkg.epoch, mypkg.name, mypkg.version,
                                              mypkg.release, mypkg.arch)

            arglist = [arg]
            exactmatch, matched, unmatched = parsePackages(avail, arglist)
            if len(unmatched) > 0: # if we get back anything in unmatched, it fails
                self.errorlog(0, _('No Match for argument: %s') % arg)
                continue
            
            installable = yum.misc.unique(exactmatch + matched)
            exactarch = self.conf.getConfigOption('exactarch')
            
            # we look through each returned possibility and rule out the
            # ones that we obviously can't use
            for pkg in installable:
                if pkg.pkgtup() in installed:
                    self.log(6, 'Package %s is already installed, skipping' % pkg)
                    continue
                
                # everything installed that matches the name
                installedByKey = self.rpmdb.returnTupleByKeyword(name=pkg.name)
                comparable = []
                for instTup in installedByKey:
                    (n2, a2, e2, v2, r2) = instTup
                    if rpmUtils.arch.isMultiLibArch(a2) == rpmUtils.arch.isMultiLibArch(pkg.arch):
                        comparable.append(instTup)
                    else:
                        self.log(6, 'Discarding non-comparable pkg %s.%s' % (n2, a2))
                        continue
                        
                # go through each package 
                if len(comparable) > 0:
                    for instTup in comparable:
                        (n2, a2, e2, v2, r2) = instTup
                        rc = compareEVR((e2, v2, r2), (pkg.epoch, pkg.version, pkg.release))
                        if rc < 0: # we're newer - this is an update, pass to them
                            if exactarch:
                                if pkg.arch == a2:
                                    passToUpdate.append(pkg.pkgtup())
                            else:
                                passToUpdate.append(pkg.pkgtup())
                        elif rc == 0: # same, ignore
                            continue
                        elif rc > 0: # lesser, check if the pkgtup is an exactmatch
                                        # if so then add it to be installed,
                                        # the user explicitly wants this version
                                        # FIXME this is untrue if the exactmatch
                                        # does not include a version-rel section
                            if pkg.pkgtup() in exactmatch:
                                if not toBeInstalled.has_key(pkg.name): toBeInstalled[pkg.name] = []
                                toBeInstalled[pkg.name].append(pkg.pkgtup())
                else: # we've not got any installed that match n or n+a
                    self.log(4, 'No other %s installed, adding to list for potential install' % pkg.name)
                    if not toBeInstalled.has_key(pkg.name): toBeInstalled[pkg.name] = []
                    toBeInstalled[pkg.name].append(pkg.pkgtup())
        
        
        # this is where I could catch the installs of compat and multilib 
        # arches on a single yum install command. 
        pkglist = returnBestPackages(toBeInstalled)
        
        # This is where we need to do a lookup to find out if this install
        # is also an obsolete. if so then we need to mark it as such in the
        # tsInfo.
        if len(pkglist) > 0:
            self.log(3, 'reduced installs :')
        for pkgtup in pkglist:
            self.log(3,'   %s.%s %s:%s-%s' % pkgtup)
            po = self.getPackageObject(pkgtup)
            self.tsInfo.addInstall(po)

        if len(passToUpdate) > 0:
            self.log(3, 'potential updates :')
            updatelist = []
            for (n,a,e,v,r) in passToUpdate:
                self.log(3, '   %s.%s %s:%s-%s' % (n, a, e, v, r))
                pkgstring = '%s:%s-%s-%s.%s' % (e,n,v,r,a)
                updatelist.append(pkgstring)
            self.updatePkgs(userlist=updatelist, quiet=1)

        if len(self.tsInfo) > oldcount:
            return 2, ['Package(s) to install']
        return 0, ['Nothing to do']
        
        
    def updatePkgs(self, userlist=None, quiet=0):
        """take user commands and populate transaction wrapper with 
           packages to be updated"""
        
        # if there is no userlist, then do global update below
        # this is probably 90% of the calls
        # if there is a userlist then it's for updating pkgs, not obsoleting
        
        oldcount = len(self.tsInfo)
        if not userlist:
            userlist = self.extcmds
        self.doRepoSetup()
        avail = self.pkgSack.simplePkgList()
        self.doRpmDBSetup()
        installed = self.rpmdb.getPkgList()
        self.doUpdateSetup()
        updates = self.up.getUpdatesTuples()
        if self.conf.getConfigOption('obsoletes'):
            obsoletes = self.up.getObsoletesTuples(newest=1)
        else:
            obsoletes = []

        if len(userlist) == 0: # simple case - do them all
            for (obsoleting, installed) in obsoletes:
                obsoleting_pkg = self.getPackageObject(obsoleting)
                installed_pkg =  YumInstalledPackage(self.rpmdb.returnHeaderByTuple(installed)[0])
                self.tsInfo.addObsoleting(obsoleting_pkg, installed_pkg)
                self.tsInfo.addObsoleted(installed_pkg, obsoleting_pkg)
                                
            for (new, old) in updates:
                txmbrs = self.tsInfo.getMembers(pkgtup=old)

                if txmbrs and txmbrs[0].output_state == 'obsoleted':
                    self.log(5, 'Not Updating Package that is already obsoleted: %s.%s %s:%s-%s' % old)
                else:
                    updating_pkg = self.getPackageObject(new)
                    updated_pkg = YumInstalledPackage(self.rpmdb.returnHeaderByTuple(old)[0])
                    self.tsInfo.addUpdate(updating_pkg, updated_pkg)


        else:
            # go through the userlist - look for items that are local rpms. If we find them
            # pass them off to localInstall() and then move on
            localupdates = []
            for item in userlist:
                if os.path.exists(item) and item[-4:] == '.rpm': # this is hurky, deal w/it
                    localupdates.append(item)
            
            if len(localupdates) > 0:
                val, msglist = self.localInstall(filelist=localupdates, updateonly=1)
                for item in localupdates:
                    userlist.remove(item)
                
            # we've got a userlist, match it against updates tuples and populate
            # the tsInfo with the matches
            updatesPo = []
            for (new, old) in updates:
                (n,a,e,v,r) = new
                updatesPo.extend(self.pkgSack.searchNevra(name=n, arch=a, epoch=e, 
                                 ver=v, rel=r))
                                 
            exactmatch, matched, unmatched = yum.packages.parsePackages(updatesPo, userlist)
            for userarg in unmatched:
                if not quiet:
                    self.errorlog(1, 'Could not find update match for %s' % userarg)

            updateMatches = yum.misc.unique(matched + exactmatch)
            for po in updateMatches:
                for (new, old) in updates:
                    if po.pkgtup() == new:
                        updated_pkg = YumInstalledPackage(self.rpmdb.returnHeaderByTuple(old)[0])
                        self.tsInfo.addUpdate(po, updated_pkg)


        if len(self.tsInfo) > oldcount:
            change = len(self.tsInfo) - oldcount
            msg = '%d packages marked for Update/Obsoletion' % change
            return 2, [msg]
        else:
            return 0, ['No Packages marked for Update/Obsoletion']


        
    
    def erasePkgs(self, userlist=None):
        """take user commands and populate a transaction wrapper with packages
           to be erased/removed"""
        
        oldcount = len(self.tsInfo)
        
        if not userlist:
            userlist = self.extcmds
        
        self.doRpmDBSetup()
        installed = []
        for hdr in self.rpmdb.getHdrList():
            po = YumInstalledPackage(hdr)
            installed.append(po)
        
        if len(userlist) > 0: # if it ain't well, that'd be real _bad_ :)
            exactmatch, matched, unmatched = yum.packages.parsePackages(installed, userlist)
            erases = yum.misc.unique(matched + exactmatch)
        
        for pkg in erases:
            self.tsInfo.addErase(pkg)
        
        if len(self.tsInfo) > oldcount:
            change = len(self.tsInfo) - oldcount
            msg = '%d packages marked for removal' % change
            return 2, [msg]
        else:
            return 0, ['No Packages marked for removal']
    
    def localInstall(self, filelist=None, updateonly=0):
        """handles installs/updates of rpms provided on the filesystem in a 
           local dir (ie: not from a repo)"""
           
        # read in each package into a YumLocalPackage Object
        # append it to self.localPackages
        # check if it can be installed or updated based on nevra versus rpmdb
        # don't import the repos until we absolutely need them for depsolving
        
        oldcount = len(self.tsInfo)
        
        if not filelist:
            filelist = self.extcmds
        
        if len(filelist) == 0:
            return 0, ['No Packages Provided']
        
        self.doRpmDBSetup()
        installpkgs = []
        updatepkgs = []
        donothingpkgs = []
        
        for pkg in filelist:
            try:
                po = YumLocalPackage(ts=self.read_ts, filename=pkg)
            except yum.Errors.MiscError, e:
                self.errorlog(0, 'Cannot open file: %s. Skipping.' % pkg)
                continue
            self.log(2, 'Examining %s: %s' % (po.localpath, po))

            # everything installed that matches the name
            installedByKey = self.rpmdb.returnTupleByKeyword(name=po.name)
            # go through each package 
            if len(installedByKey) == 0: # nothing installed by that name
                if updateonly:
                    self.errorlog(2, 'Package %s not installed, cannot update it. Run yum install to install it instead.' % po.name)
                else:
                    installpkgs.append(po)
                continue

            for instTup in installedByKey:
                installed_pkg = YumInstalledPackage(self.rpmdb.returnHeaderByTuple(instTup)[0])
                (n, a, e, v, r) = po.pkgtup()
                (n2, a2, e2, v2, r2) = installed_pkg.pkgtup()
                rc = compareEVR((e2, v2, r2), (e, v, r))
                if rc < 0: # we're newer - this is an update, pass to them
                    if self.conf.exactarch:
                        if a == a2:
                            updatepkgs.append((po, installed_pkg))
                            continue
                        else:
                            donothingpkgs.append(po)
                            continue
                    else:
                        updatepkgs.append((po, installed_pkg))
                        continue
                elif rc == 0: # same, ignore
                    donothingpkgs.append(po)
                    continue
                elif rc > 0: 
                    donothingpkgs.append(po)
                    continue


        for po in installpkgs:
            self.log(2, 'Marking %s to be installed' % po.localpath)
            self.localPackages.append(po)
            self.tsInfo.addInstall(po)
        
        for (po, oldpo) in updatepkgs:
            self.log(2, 'Marking %s as an update to %s' % po.localpath, oldpo)
            self.localPackages.append(po)
            self.tsInfo.addUpdate(po, oldpo)
        
        for po in donothingpkgs:
            self.log(2, '%s: installed versions are equal or greater' % po.localpath)
        
        if len(self.tsInfo) > oldcount:
            
            return 2, ['Package(s) to install']
        return 0, ['Nothing to do']
        
            
        
        
    def returnPkgLists(self):
        """Returns packages lists based on arguments on the cli.returns a 
           GenericHolder instance with the following lists defined:
           available = list of packageObjects
           installed = list of packageObjects
           updates = tuples of packageObjects (updating, installed)
           extras = list of packageObjects
           obsoletes = tuples of packageObjects (obsoleting, installed)
           recent = list of packageObjects
           """
        
        special = ['available', 'installed', 'all', 'extras', 'updates', 'recent',
                   'obsoletes']
        
        pkgnarrow = 'all'
        if len(self.extcmds) > 0:
            if self.extcmds[0] in special:
                pkgnarrow = self.extcmds.pop(0)
            
        ypl = self.doPackageLists(pkgnarrow=pkgnarrow)
        
        # rework the list output code to know about:
        # obsoletes output
        # the updates format

        def _shrinklist(lst, args):
            if len(lst) > 0 and len(args) > 0:
                self.log(4, 'Matching packages for package list to user args')
                exactmatch, matched, unmatched = yum.packages.parsePackages(lst, args)
                return yum.misc.unique(matched + exactmatch)
            else:
                return lst
        
        ypl.updates = _shrinklist(ypl.updates, self.extcmds)
        ypl.installed = _shrinklist(ypl.installed, self.extcmds)
        ypl.available = _shrinklist(ypl.available, self.extcmds)
        ypl.recent = _shrinklist(ypl.recent, self.extcmds)
        ypl.extras = _shrinklist(ypl.extras, self.extcmds)
        ypl.obsoletes = _shrinklist(ypl.obsoletes, self.extcmds)
        
#        for lst in [ypl.obsoletes, ypl.updates]:
#            if len(lst) > 0 and len(self.extcmds) > 0:
#                self.log(4, 'Matching packages for tupled package list to user args')
#                for (pkg, instpkg) in lst:
#                    exactmatch, matched, unmatched = yum.packages.parsePackages(lst, self.extcmds)
                    
        return ypl

    def search(self, args=None):
        """cli wrapper method for module search function, searches simple
           text tags in a package object"""
        
        # call the yum module search function with lists of tags to search
        # and what to search for
        # display the list of matches
        if not args:
            args = self.extcmds
            
        searchlist = ['name', 'summary', 'description', 'packager', 'group', 'url']
        matching = self.searchPackages(searchlist, args, callback=self.matchcallback)

        if len(matching.keys()) == 0:
            return 0, ['No Matches found']
        return 0, []

    def provides(self, args=None):
        """use the provides methods in the rpmdb and pkgsack to produce a list 
           of items matching the provides strings. This is a cli wrapper to the 
           module"""
        if not args:
            args = self.extcmds
        
        matching = self.searchPackageProvides(args, callback=self.matchcallback)
        
        if len(matching.keys()) == 0:
            return 0, ['No Matches found']
        
        return 0, []
    
    def resolveDepCli(self, args=None):
        """returns a package (one per user arg) that provide the supplied arg"""
        
        if not args:
            args = self.extcmds
        
        for arg in args:
            try:
                pkg = self.returnPackageByDep(arg)
            except yum.Errors.YumBaseError, e:
                self.errorlog(0, _('No Package Found for %s') % arg)
            else:
                msg = '%s:%s-%s-%s.%s' % (pkg.epoch, pkg.name, pkg.version, pkg.release, pkg.arch)
                self.log(0, msg)

        return 0, []
            
    def returnGroupLists(self, userlist=None):

        uservisible=1
        if userlist is None:
            userlist = self.extcmds
            
        if len(userlist) > 0:
            if userlist[0] == 'hidden':
                uservisible=0

        installed, available = self.doGroupLists(uservisible=uservisible)

        if len(installed) > 0:
            self.log(2, 'Installed Groups:')
            for group in installed:
                self.log(2, '   %s' % group)
        
        if len(available) > 0:
            self.log(2, 'Available Groups:')
            for group in available:
                self.log(2, '   %s' % group)

            
        return 0, ['Done']
    
    def returnGroupInfo(self, userlist=None):
        """returns complete information on a list of groups"""
        if userlist is None:
            userlist = self.extcmds
        
        for group in userlist:
            if self.groupInfo.groupExists(group):
                self.displayPkgsInGroups(group)
            else:
                self.errorlog(1, 'Warning: Group %s does not exist.' % group)
        
        return 0, []
        
    def installGroups(self, grouplist=None):
        """for each group requested attempt to install all pkgs/metapkgs of default
           or mandatory. Also recurse lists of groups to provide for them too."""
        
        if grouplist is None:
            grouplist = self.extcmds
        
        self.doRepoSetup()
        pkgs = [] # package objects to be installed
        installed = self.rpmdb.getPkgList()
        availablepackages = {}
        for po in self.pkgSack.returnPackages():
            if po.pkgtup() not in installed:
                    availablepackages[po.name] = 1

        for group in grouplist:
            if not self.groupInfo.groupExists(group):
                self.errorlog(0, _('Warning: Group %s does not exist.') % group)
                continue
            pkglist = self.groupInfo.pkgTree(group)
            for pkg in pkglist:
                if availablepackages.has_key(pkg):
                    pkgs.append(pkg)
                    self.log(4, 'Adding package %s for groupinstall of %s.' % (pkg, group))

        if len(pkgs) > 0:
            self.log(2, 'Passing package list to Install Process')
            self.log(4, 'Packages being passed:')
            for pkg in pkgs:
                self.log(4, '%s' % pkg)
            return self.installPkgs(userlist=pkgs)
        else:
            return 0, ['No packages in any requested group available to install']

    
    def updateGroups(self, grouplist=None):
        """get list of any pkg in group that is installed, check to update it
           get list of any mandatory or default pkg attempt to update it if it is installed
           or install it if it is not installed"""

        if grouplist is None:
            grouplist = self.extcmds
        
        if len(grouplist) == 0:
            self.usage()
            
        self.doRepoSetup()
        self.doUpdateSetup()
        
        grouplist.sort()
        updatesbygroup = []
        installsbygroup = []
        updateablenames = []
        
        for group in grouplist:
            if not self.groupInfo.groupExists(group):
                self.errorlog(0, _('Warning: Group %s does not exist.') % group)
                continue

            required = self.groupInfo.requiredPkgs(group)
            all = self.groupInfo.pkgTree(group)
            for pkgn in all:
                if self.rpmdb.installed(name=pkgn):
                    if len(self.up.getUpdatesList(name=pkgn)) > 0:
                        updatesbygroup.append((group, pkgn))
                else:
                    if pkgn in required:
                        installsbygroup.append((group, pkgn))
        
        updatepkgs = []
        installpkgs = []
        for (group, pkg) in updatesbygroup:
            self.log(2, _('From %s updating %s') % (group, pkg))
            updatepkgs.append(pkg)
        for (group, pkg) in installsbygroup:
            self.log(2, _('From %s installing %s') % (group, pkg))
            installpkgs.append(pkg)

        

        if len(installpkgs) > 0:
            self.installPkgs(userlist=installpkgs)
        
        if len(updatepkgs) > 0:
            self.updatePkgs(userlist=updatepkgs, quiet=1)
        
        if len(self.tsInfo) > 0:
            return 2, ['Group updating']
        else:
            return 0, [_('Nothing in any group to update or install')]
    
    def removeGroups(self, grouplist=None):
        """Remove only packages of the named group(s). Do not recurse."""

        if grouplist is None:
            grouplist = self.extcmds
        
        if len(grouplist) == 0:
            self.usage()
        
        erasesbygroup = []
        for group in grouplist:
            if not self.groupInfo.groupExists(group):
                self.errorlog(0, _('Warning: Group %s does not exist.') % group)
                continue
        
            allpkgs = self.groupInfo.allPkgs(group)
            for pkg in allpkgs:
                if self.rpmdb.installed(name=pkg):
                    erasesbygroup.append((group, pkg))

        erases = []
        for (group, pkg) in erasesbygroup:
            self.log(2, _('From %s removing %s') % (group, pkg))
            erases.append(pkg)
        
        if len(erases) > 0:
            return self.erasePkgs(userlist=erases)
        else:
            return 0, ['No packages to remove from groups']



            
        
        
    def usage(self):
        print _("""
    Usage:  yum [options] < update | install | info | remove | list |
            clean | provides | search | check-update | groupinstall | 
            groupupdate | grouplist | groupinfo | groupremove | generate-rss |
            makecache | localinstall >
                
        Options:
        -c [config file] - specify the config file to use
        -e [error level] - set the error logging level
        -d [debug level] - set the debugging level
        -y - answer yes to all questions
        -R [time in minutes] - set the max amount of time to randomly run in
        -C run from cache only - do not update the cache
        --installroot=[path] - set the install root (default '/')
        --version - output the version of yum
        --rss-filename=[path/filename] - set the filename to generate rss to
        --exclude=package to exclude
        --disablerepo=repository id to disable (overrides config file)
        --enablerepo=repository id to enable (overrides config file)

        -h, --help  - this screen
    """)
        sys.exit(1)

           


        

